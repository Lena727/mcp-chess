"""MCP server + web board.

One process hosts every game. Agents connect over MCP (streamable HTTP at
/mcp), humans and spectators use the web board at /. Run:  mcp-chess
"""

from __future__ import annotations

import argparse
import json
import logging
import random
from importlib import resources
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from .analysis import Analyzer, UciEngine, find_engine
from .games import COLORS, GameError, Lobby, Match

INSTRUCTIONS = (
    "Chess host. new_game or join_game gives you a seat key: pass it to every call. "
    "Moves are SAN (Nf3, exd5, O-O, e8=Q) or UCI (g1f3); choose from the legal list, illegal moves are rejected. "
    "move() waits for the opponent's reply and returns the new state, so one call per turn is enough."
)

WAIT_SECONDS = 50.0
BASE_URL = "http://127.0.0.1:8765"

analyzer: Analyzer | None = None
lobby = Lobby(on_move=lambda m: analyzer.schedule(m) if analyzer else None)
mcp = MCPServer("chess", instructions=INSTRUCTIONS)

Seat = Annotated[str, Field(description="your seat key")]


def _err(e: GameError) -> str:
    return f"error: {e}"


async def _after_wait(key: str) -> str:
    ready = await lobby.wait_turn(key, WAIT_SECONDS)
    state = lobby.state_text(key)
    return state if ready else state + "\n(still waiting; call state with wait=true)"


# ------------------------------------------------------------------ tools
@mcp.tool(structured_output=False)
async def new_game(
    color: Annotated[str, Field(description="white | black | random")] = "white",
    opponent: Annotated[str, Field(description="agent | human")] = "agent",
    fen: Annotated[str, Field(description="optional start position")] = "",
    name: Annotated[str, Field(description="your display name")] = "",
) -> str:
    """Create a game and take a seat. Does not wait: first tell the user how the opponent joins."""
    color = color.strip().lower()
    if color == "random":
        color = random.choice(["white", "black"])
    if color not in ("white", "black"):
        return "error: color must be white, black or random"
    me = color[0]
    kinds = {me: "agent", "wb".replace(me, ""): opponent.strip().lower()}
    try:
        match = lobby.create(kinds["w"], kinds["b"], fen)
    except GameError as e:
        return _err(e)
    seat = lobby.claim(match, me, name or "agent")
    opp = match.seats["wb".replace(me, "")]
    if opp.kind == "human":
        how = f"human plays at {BASE_URL}/?seat={opp.key}  (give the user this link)"
    else:
        how = f"other agent calls join_game game={match.id}; watch at {BASE_URL}/?game={match.id}"
    return f"seat={seat.key}\n{how}\n" + lobby.state_text(seat.key)


@mcp.tool(structured_output=False)
async def join_game(
    game: Annotated[str, Field(description="game id; empty = newest open game")] = "",
    name: Annotated[str, Field(description="your display name")] = "",
) -> str:
    """Take the free agent seat of a game, then wait until it is your turn."""
    try:
        seat = lobby.join(game, "agent", name or "agent")
    except GameError as e:
        return _err(e)
    return f"seat={seat.key}\n" + await _after_wait(seat.key)


@mcp.tool(structured_output=False)
async def move(seat: Seat, move: Annotated[str, Field(description="SAN or UCI")], wait: bool = True) -> str:
    """Play your move. With wait=true returns after the opponent replies."""
    try:
        san = lobby.move(seat, move)
    except GameError as e:
        return _err(e)
    _, match = lobby.seat(seat)
    if match.game.result or not wait:
        return f"played {san}\n" + lobby.state_text(seat)
    return f"played {san}\n" + await _after_wait(seat)


@mcp.tool(structured_output=False)
async def state(seat: Seat, wait: bool = False, board: bool = False, pgn: bool = False) -> str:
    """Current state. wait=true blocks until your turn. board=ascii diagram, pgn=full move list."""
    try:
        if wait:
            ready = await lobby.wait_turn(seat, WAIT_SECONDS)
            text = lobby.state_text(seat, board, pgn)
            return text if ready else text + "\n(still waiting; call again)"
        return lobby.state_text(seat, board, pgn)
    except GameError as e:
        return _err(e)


@mcp.tool(structured_output=False)
async def action(
    seat: Seat, action: Annotated[str, Field(description="resign | offer_draw | accept_draw | decline_draw | claim_draw")]
) -> str:
    """Resign, offer/accept/decline a draw, or claim a draw (threefold / 50-move)."""
    try:
        return lobby.action(seat, action) + "\n" + lobby.state_text(seat)
    except GameError as e:
        return _err(e)


# --------------------------------------------------------------- web API
def _match_json(match: Match, seat_key: str | None) -> dict:
    game, pos = match.game, match.game.pos
    me = None
    if seat_key:
        seat = lobby.seats.get(seat_key)
        if seat and seat.game_id == match.id:
            me = seat.color
    show_eval = analyzer is not None and (not match.has_human or game.result is not None)
    w, b = pos.material()
    data = {
        "id": match.id,
        "version": match.version,
        "fen": pos.fen(),
        "turn": pos.turn,
        "check": pos.in_check(),
        "sans": game.sans,
        "ucis": [m.uci() for m in game.moves],
        "result": game.result,
        "reason": game.reason,
        "players": {c: {"kind": s.kind, "joined": s.joined, "name": s.label} for c, s in match.seats.items()},
        "you": me,
        "draw_offer": match.draw_offer,
        "claimable": game.claimable_draw(),
        "material": {"w": w, "b": b},
        "pgn": game.pgn(lobby.pgn_headers(match)),
        "engine": analyzer.engine.name if analyzer else None,
        "evals": {str(k): v for k, v in match.evals.items()} if show_eval else {},
    }
    if me and me == pos.turn and not game.result:
        data["legal"] = [m.uci() for m in game.legal]
    return data


def _list_json() -> list[dict]:
    out = []
    for m in sorted(lobby.matches.values(), key=lambda m: m.created, reverse=True):
        out.append({
            "id": m.id,
            "players": {c: {"kind": s.kind, "joined": s.joined, "name": s.label} for c, s in m.seats.items()},
            "moves": len(m.game.sans),
            "result": m.game.result,
            "reason": m.game.reason,
        })
    return out


async def _body(request: Request) -> dict:
    try:
        return await request.json()
    except (json.JSONDecodeError, ValueError):
        return {}


@mcp.custom_route("/", methods=["GET"], include_in_schema=False)
async def index(request: Request) -> Response:
    html = resources.files("mcp_chess").joinpath("web/index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@mcp.custom_route("/api/games", methods=["GET", "POST"], include_in_schema=False)
async def api_games(request: Request) -> Response:
    if request.method == "GET":
        return JSONResponse(_list_json())
    body = await _body(request)
    try:
        match = lobby.create(body.get("white", "agent"), body.get("black", "agent"), body.get("fen", ""))
    except GameError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    human = next((s for s in match.seats.values() if s.kind == "human"), None)
    if human:
        lobby.claim(match, human.color, body.get("name") or "human")
    return JSONResponse({"id": match.id, "seat": human.key if human else None})


@mcp.custom_route("/api/game", methods=["GET"], include_in_schema=False)
async def api_game(request: Request) -> Response:
    q = request.query_params
    seat_key = q.get("seat")
    gid = q.get("id")
    if seat_key and not gid:
        seat = lobby.seats.get(seat_key)
        if seat:
            gid = seat.game_id
            if not seat.joined and seat.kind == "human":
                lobby.claim(lobby.matches[gid], seat.color, "human")
    match = lobby.matches.get(gid or "")
    if not match:
        return JSONResponse({"error": "no such game"}, status_code=404)
    if "since" in q:
        await lobby.wait_change(match, int(q["since"]), 25.0)
    return JSONResponse(_match_json(match, seat_key))


@mcp.custom_route("/api/move", methods=["POST"], include_in_schema=False)
async def api_move(request: Request) -> Response:
    body = await _body(request)
    try:
        seat, _ = lobby.seat(body.get("seat", ""))
        if seat.kind != "human":
            raise GameError("web moves are only for human seats")
        if body.get("action"):
            lobby.action(seat.key, body["action"])
        else:
            lobby.move(seat.key, body.get("move", ""))
    except GameError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------- main
def main() -> None:
    global analyzer, WAIT_SECONDS, BASE_URL
    p = argparse.ArgumentParser(prog="mcp-chess", description="Chess MCP server with a live web board.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--stockfish", metavar="PATH", help="UCI engine for spectator analysis (or env STOCKFISH_PATH)")
    p.add_argument("--no-engine", action="store_true", help="disable analysis even if stockfish is on PATH")
    p.add_argument("--depth", type=int, default=14, help="analysis depth (default 14)")
    p.add_argument("--wait", type=float, default=50.0, help="max seconds a tool call waits for the opponent")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    WAIT_SECONDS = args.wait
    BASE_URL = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    path = None if args.no_engine else find_engine(args.stockfish)
    if path:
        analyzer = Analyzer(UciEngine(path, depth=args.depth))
    print(f"mcp-chess  board: {BASE_URL}/   mcp: {BASE_URL}/mcp   analysis: {path or 'off'}", flush=True)

    import uvicorn

    app = mcp.streamable_http_app(host=args.host)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        if analyzer:
            analyzer.engine.close()


if __name__ == "__main__":
    main()
