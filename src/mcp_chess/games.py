"""Game hosting: seats, turn enforcement, waiting for the opponent, draws.

Invariants enforced here (the agent cannot break them, whatever it says):
  * a move is only accepted from the seat whose turn it is;
  * a move is only accepted if it is legal in the current position;
  * a seat key is the only credential: an agent can never move for the
    opponent, and the engine evaluation is never exposed to players.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

from .engine import Game, IllegalMove, Position

COLORS = {"w": "white", "b": "black"}
OTHER = {"w": "b", "b": "w"}


class GameError(Exception):
    """Error to report back to the caller verbatim."""


@dataclass
class Seat:
    key: str
    game_id: str
    color: str  # "w" | "b"
    kind: str  # "agent" | "human"
    joined: bool = False
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or self.kind


@dataclass
class Match:
    id: str
    game: Game
    seats: dict[str, Seat]
    created: float = field(default_factory=time.time)
    version: int = 0
    draw_offer: str | None = None  # color that offered
    fens: list[str] = field(default_factory=list)  # fen before each ply, plus current
    evals: dict[int, dict] = field(default_factory=dict)  # ply -> analysis of fens[ply]
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def bump(self) -> None:
        self.version += 1
        self.changed.set()
        self.changed = asyncio.Event()

    @property
    def has_human(self) -> bool:
        return any(s.kind == "human" for s in self.seats.values())

    @property
    def ply(self) -> int:
        return len(self.game.sans)


class Lobby:
    def __init__(self, on_move=None):
        self.matches: dict[str, Match] = {}
        self.seats: dict[str, Seat] = {}
        self._next = 1
        self.on_move = on_move  # callback(match) after every change of position

    # ------------------------------------------------------------ setup
    def create(self, white: str, black: str, fen: str = "") -> Match:
        for kind in (white, black):
            if kind not in ("agent", "human"):
                raise GameError("player kind must be 'agent' or 'human'")
        try:
            game = Game(fen.strip()) if fen.strip() else Game()
        except ValueError as e:
            raise GameError(f"bad FEN: {e}") from None
        gid = f"g{self._next}"
        self._next += 1
        seats = {}
        for color, kind in (("w", white), ("b", black)):
            seat = Seat(secrets.token_urlsafe(6), gid, color, kind)
            seats[color] = seat
            self.seats[seat.key] = seat
        match = Match(gid, game, seats, fens=[game.pos.fen()])
        self.matches[gid] = match
        if self.on_move:
            self.on_move(match)
        return match

    def import_pgn(self, text: str) -> Match:
        """Load a finished (or any) game for review; both seats are closed."""
        try:
            game, headers = Game.from_pgn(text)
        except (ValueError, KeyError) as e:
            raise GameError(f"bad PGN: {e}") from None
        match = self.create("agent", "agent", game.start_fen)
        pos, fens = Position.from_fen(game.start_fen), [game.start_fen]
        for mv in game.moves:
            pos = pos.push(mv)
            fens.append(pos.fen())
        match.game, match.fens = game, fens
        for color, tag in (("w", "White"), ("b", "Black")):
            match.seats[color].joined = True
            match.seats[color].name = headers.get(tag, "?")[:40]
        match.bump()
        if self.on_move:
            self.on_move(match)
        return match

    def seat(self, key: str) -> tuple[Seat, Match]:
        seat = self.seats.get((key or "").strip())
        if not seat:
            raise GameError("unknown seat key")
        return seat, self.matches[seat.game_id]

    def claim(self, match: Match, color: str, name: str = "") -> Seat:
        seat = match.seats[color]
        seat.joined = True
        if name:
            seat.name = name[:40]
        match.bump()
        return seat

    def join(self, game_id: str = "", kind: str = "agent", name: str = "") -> Seat:
        """Take a free seat of the given kind (in `game_id`, or the newest game with one)."""
        if game_id:
            match = self.matches.get(game_id.strip())
            if not match:
                raise GameError(f"no game {game_id!r}")
            candidates = [match]
        else:
            candidates = sorted(self.matches.values(), key=lambda m: m.created, reverse=True)
        for match in candidates:
            if match.game.result:
                continue
            for color in ("w", "b"):
                seat = match.seats[color]
                if seat.kind == kind and not seat.joined:
                    return self.claim(match, color, name)
        raise GameError(f"no free {kind} seat" + (f" in {game_id}" if game_id else "; create one with new_game"))

    # ----------------------------------------------------------- playing
    def move(self, key: str, text: str) -> str:
        seat, match = self.seat(key)
        game = match.game
        if game.result:
            raise GameError(f"game over: {game.result} ({game.reason})")
        if game.pos.turn != seat.color:
            raise GameError(f"not your turn ({COLORS[game.pos.turn]} to move)")
        try:
            san = game.play(text)
        except IllegalMove as e:
            raise GameError(f"illegal: {e}. legal: {' '.join(game.legal_san())}") from None
        seat.joined = True
        if match.draw_offer == OTHER[seat.color]:
            match.draw_offer = None  # moving declines the opponent's offer
        match.fens.append(game.pos.fen())
        match.bump()
        if self.on_move:
            self.on_move(match)
        return san

    def action(self, key: str, action: str) -> str:
        seat, match = self.seat(key)
        game, me, opp = match.game, seat.color, OTHER[seat.color]
        if game.result:
            raise GameError(f"game over: {game.result} ({game.reason})")
        action = action.strip().lower().replace("-", "_").replace(" ", "_")
        if action == "resign":
            game.finish("0-1" if me == "w" else "1-0", f"{COLORS[me]} resigned")
        elif action in ("offer_draw", "draw"):
            if match.draw_offer == opp:
                game.finish("1/2-1/2", "agreement")
            else:
                match.draw_offer = me
        elif action == "accept_draw":
            if match.draw_offer != opp:
                raise GameError("no draw offer from opponent")
            game.finish("1/2-1/2", "agreement")
        elif action == "decline_draw":
            if match.draw_offer == opp:
                match.draw_offer = None
        elif action == "claim_draw":
            if game.pos.turn != me:
                raise GameError("you can claim a draw only on your turn")
            reason = game.claimable_draw()
            if not reason:
                raise GameError("no draw to claim (needs threefold repetition or 50 moves without capture/pawn move)")
            game.finish("1/2-1/2", reason)
        else:
            raise GameError("action must be resign | offer_draw | accept_draw | decline_draw | claim_draw")
        match.bump()
        return {"resign": "resigned", "accept_draw": "draw agreed", "claim_draw": f"draw claimed ({game.reason})"}.get(
            action, "draw agreed" if game.result else "draw offered" if match.draw_offer == me else "ok"
        )

    async def wait_turn(self, key: str, timeout: float) -> bool:
        """Block until it is this seat's turn, the game ends, or timeout. True if ready."""
        seat, match = self.seat(key)
        deadline = time.monotonic() + timeout
        while not (match.game.result or match.game.pos.turn == seat.color):
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            try:
                await asyncio.wait_for(match.changed.wait(), left)
            except asyncio.TimeoutError:
                return False
        return True

    async def wait_change(self, match: Match, since: int, timeout: float) -> None:
        if match.version != since:
            return
        try:
            await asyncio.wait_for(match.changed.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    # --------------------------------------------------------- rendering
    def state_text(self, key: str, board: bool = False, pgn: bool = False) -> str:
        """Compact, token-lean state for an agent. One line per fact."""
        seat, match = self.seat(key)
        game, me = match.game, seat.color
        pos = game.pos
        lines = []
        head = f"{match.id} you={COLORS[me]} move={pos.fullmove}"
        if game.result:
            outcome = "draw" if game.result == "1/2-1/2" else ("you won" if game.result == ("1-0" if me == "w" else "0-1") else "you lost")
            head += f" GAME OVER {game.result} {game.reason} ({outcome})"
        elif pos.turn == me:
            head += " YOUR TURN" + (" (in check)" if pos.in_check() else "")
        else:
            head += f" waiting for {COLORS[pos.turn]}"
            if not match.seats[OTHER[me]].joined:
                head += " (opponent has not joined yet)"
        lines.append(head)
        if game.sans:
            n = len(game.sans)
            first_black = Position.from_fen(game.start_fen).turn == "b"
            last_mover_black = (n % 2 == 1) == first_black
            num = pos.fullmove - (1 if last_mover_black else 0)
            lines.append(f"last: {num}{'...' if last_mover_black else '.'}{game.sans[-1]}")
        lines.append(f"fen: {pos.fen()}")
        if board:
            lines.append(pos.ascii(flip=me == "b"))
        if not game.result:
            if match.draw_offer == OTHER[me]:
                lines.append("opponent offers a draw: action accept_draw, or just move to decline")
            claim = game.claimable_draw()
            if claim and pos.turn == me:
                lines.append(f"draw claimable ({claim}): action claim_draw")
            if pos.turn == me:
                legal = game.legal_san()
                lines.append(f"legal({len(legal)}): {' '.join(legal)}")
        if pgn:
            lines.append(game.pgn(self.pgn_headers(match)))
        return "\n".join(lines)

    def pgn_headers(self, match: Match) -> dict[str, str]:
        return {
            "Event": f"MCP Chess {match.id}",
            "Date": time.strftime("%Y.%m.%d", time.localtime(match.created)),
            "White": match.seats["w"].label,
            "Black": match.seats["b"].label,
            **({"Termination": match.game.reason} if match.game.reason else {}),
        }
