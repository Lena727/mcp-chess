# mcp-chess ♞

**Play chess against your AI agent, or put two agents against each other and watch them play.**

`mcp-chess` is a small MCP server with its own rules engine and a live web board.
The server, not the model, decides what is legal, so an agent can't make up a move,
move out of turn, or play for its opponent.

- **Complete FIDE rules:** castling (including "through check"), en passant, promotion to any piece, checkmate and stalemate, the 50-move and 75-move rules, threefold and fivefold repetition, and insufficient material. Perft-verified against the standard test positions.
- **Built so agents can't hallucinate a move:** every response lists the legal moves, an illegal move is rejected along with that list, and each seat key can move only its own color.
- **Low token use:** 5 tools (about 600 tokens of schema), plain-text answers, and one tool call per turn, because `move` blocks until the opponent has replied.
- **Human vs agent:** click to move on the web board. The agent is waiting inside `move()` and replies right away.
- **Agent vs agent spectator mode:** an optional Stockfish eval bar, material count, and `?!` / `?` / `??` marks on inaccuracies, mistakes and blunders. Players never see the evaluation.
- No dependencies apart from `mcp` + `uvicorn`. The engine is about 500 lines of plain Python.

## Quick start

**One command** (needs [uv](https://docs.astral.sh/uv/)). It downloads, starts the server and opens the board in your browser:

```bash
uvx --from git+https://github.com/Lena727/mcp-chess mcp-chess
```

**Or one click:** download the repo ([zip](https://github.com/Lena727/mcp-chess/archive/refs/heads/main.zip)) and double-click `start.bat` (Windows), or run `./start.sh` (macOS/Linux). If `uv` is missing, the launcher installs it first.

The board runs at http://127.0.0.1:8765 and the MCP endpoint at http://127.0.0.1:8765/mcp.

Connect your agent (streamable HTTP, a one-time setup):

```bash
# Claude Code
claude mcp add --transport http chess http://127.0.0.1:8765/mcp
# Codex CLI (~/.codex/config.toml)
[mcp_servers.chess]
url = "http://127.0.0.1:8765/mcp"
```

Other clients: `{ "mcpServers": { "chess": { "type": "http", "url": "http://127.0.0.1:8765/mcp" } } }`.
Clients that only support stdio (e.g. Claude Desktop) can bridge with `npx mcp-remote http://127.0.0.1:8765/mcp`.

### You vs your agent

Either tell the agent *"Start a chess game against me, I'm white"* (it calls `new_game(opponent="human")` and gives you a link),
or open http://127.0.0.1:8765, click **Play white vs agent** and tell your agent *"join chess game g1 and play it"*.

### Agent vs agent

Open two agent sessions (two terminals, two different models, whatever you like):

- Agent A: *"Start a chess game against another agent and play it to the end."*
- Agent B: *"Join the open chess game and play it to the end."*

Then watch at `http://127.0.0.1:8765/?game=g1`. For live evaluation, install [Stockfish](https://stockfishchess.org/download/) and put it on `PATH`, or run `mcp-chess --stockfish /path/to/stockfish`.

## Tools

| tool | what it does |
|---|---|
| `new_game(color, opponent, fen?, name?)` | creates a game and returns your **seat key**, plus a link or game id for the opponent |
| `join_game(game?, name?)` | takes the free agent seat (in the newest open game if no id is given) and waits for your turn |
| `move(seat, move, wait=true)` | plays SAN (`Nf3`, `exd5`, `O-O`, `e8=Q`) or UCI (`g1f3`), then **waits for the reply** |
| `state(seat, wait?, board?, pgn?)` | current state, with an optional ASCII board or full PGN |
| `action(seat, action)` | `resign`, `offer_draw`, `accept_draw`, `decline_draw`, `claim_draw` |

A typical `move` response, covering your move, the opponent's reply and your next options (about 100 tokens):

```
played e4
g2 you=white move=2 YOUR TURN
last: 1...e5
fen: rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2
legal(29): Nc3 Na3 Qe2 Qf3 Qg4 Qh5 Ke2 Be2 Bd3 Bc4 Bb5 Ba6 Nh3 Ne2 Nf3 a3 a4 b3 b4 c3 c4 d3 d4 f3 f4 g3 g4 h3 h4
```

An illegal move costs a single short round trip:

```
error: illegal: Ke2 is not legal here. legal: Nc3 Na3 ...
```

## Invariants (why agents can't cheat or hallucinate)

1. The engine checks every move against the current position. Only legal moves are applied.
2. Moves are accepted only from the seat whose turn it is. The seat key is the only credential.
3. Automatic endings (mate, stalemate, dead position, fivefold, 75-move) can't be skipped. Threefold and 50-move draws must be claimed, as in FIDE rules.
4. The engine evaluation is never returned through MCP tools. In games with a human player, the web board hides it until the game ends.

## Development

```bash
uv run pytest                 # rules, notation, draws, lobby, analysis
PERFT_DEEP=1 uv run pytest    # + deeper perft (start pos d4, kiwipete d3, ...)
```

Project layout:

```
src/mcp_chess/engine.py    rules engine: move generation, SAN/UCI, FEN, PGN, draw rules
src/mcp_chess/games.py     lobby: seats, turn enforcement, waiting, draw offers
src/mcp_chess/analysis.py  optional UCI engine (Stockfish) analysis for spectators
src/mcp_chess/server.py    MCP tools + JSON API + CLI
src/mcp_chess/web/         single-file web board (no build step)
```

Options: `mcp-chess --host 127.0.0.1 --port 8765 --stockfish PATH --depth 14 --wait 50 --no-engine --no-browser`.
`--wait` is the longest time a tool call blocks waiting for the opponent. Lower it if your MCP client times out sooner.

## Roadmap

- [ ] save finished games as PGN files
- [ ] clocks / time controls
- [ ] eval graph for spectators
- [ ] stdio transport that proxies to a running board

## License

MIT
