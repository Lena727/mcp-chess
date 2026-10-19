"""Optional spectator analysis with a UCI engine (e.g. Stockfish).

Evaluations are shown only in the web viewer and never returned to players
through MCP tools, so they cannot be used to cheat.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import shutil
import subprocess
import threading

from .engine import Move, Position, sq_index

log = logging.getLogger(__name__)


def find_engine(explicit: str | None) -> str | None:
    for candidate in (explicit, os.environ.get("STOCKFISH_PATH"), "stockfish"):
        if candidate:
            path = shutil.which(candidate) or (candidate if os.path.isfile(candidate) else None)
            if path:
                return path
    return None


def win_chance(ev: dict) -> float:
    """White's winning chances in [-1, 1] (lichess formula)."""
    if ev.get("mate") is not None:
        if ev["mate"] == 0:  # side to move is already mated
            return 1.0 if ev.get("winner") == "w" else -1.0
        return 1.0 if ev["mate"] > 0 else -1.0
    return 2 / (1 + math.exp(-0.00368208 * ev["cp"])) - 1


def classify(before: dict, after: dict, mover: str) -> str:
    """Judge a move by how much it lowered the mover's winning chances."""
    sign = 1 if mover == "w" else -1
    drop = sign * (win_chance(before) - win_chance(after))
    if drop >= 0.3:
        return "blunder"
    if drop >= 0.2:
        return "mistake"
    if drop >= 0.1:
        return "inaccuracy"
    return ""


class UciEngine:
    """Minimal synchronous UCI client; calls are serialized with a lock."""

    def __init__(self, path: str, depth: int = 14, threads: int = 1, hash_mb: int = 64):
        self.path, self.depth = path, depth
        self.options = {"Threads": threads, "Hash": hash_mb}
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.name = os.path.basename(path)

    def _send(self, cmd: str) -> None:
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()

    def _read_until(self, prefix: str) -> list[str]:
        lines = []
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("engine process exited")
            line = line.strip()
            lines.append(line)
            if line.startswith(prefix):
                return lines

    def _start(self) -> None:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [self.path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, creationflags=flags,
        )
        self._send("uci")
        for line in self._read_until("uciok"):
            if line.startswith("id name "):
                self.name = line[8:]
        for k, v in self.options.items():
            self._send(f"setoption name {k} value {v}")
        self._send("isready")
        self._read_until("readyok")

    def analyse(self, fen: str) -> dict:
        """Return {"cp"|"mate": white-POV score, "best": uci, "depth": n}."""
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            self._send(f"position fen {fen}")
            self._send(f"go depth {self.depth}")
            score, depth = None, 0
            lines = self._read_until("bestmove")
            for line in lines:
                parts = line.split()
                if parts and parts[0] == "info" and "score" in parts:
                    i = parts.index("score")
                    if "lowerbound" in parts or "upperbound" in parts:
                        continue
                    score = (parts[i + 1], int(parts[i + 2]))
                    if "depth" in parts:
                        depth = int(parts[parts.index("depth") + 1])
            best = lines[-1].split()[1] if len(lines[-1].split()) > 1 else None
        stm_white = fen.split()[1] == "w"
        sign = 1 if stm_white else -1
        out = {"best": None if best in (None, "(none)") else best, "depth": depth}
        if score is None:
            out["cp"] = 0
        elif score[0] == "mate":
            out["mate"] = sign * score[1]
            if score[1] == 0:
                out["winner"] = "b" if stm_white else "w"
        else:
            out["cp"] = sign * score[1]
        return out

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self._send("quit")
                self.proc.wait(timeout=2)
            except Exception:
                self.proc.kill()


def best_san(fen: str, uci: str | None) -> str | None:
    if not uci:
        return None
    pos = Position.from_fen(fen)
    try:
        return pos.san(Move(sq_index(uci[:2]), sq_index(uci[2:4]), uci[4:5]))
    except (ValueError, IndexError):
        return None


class Analyzer:
    """Analyses every new position of every match in the background."""

    def __init__(self, engine: UciEngine):
        self.engine = engine
        self.cache: dict[str, dict] = {}

    def schedule(self, match) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._run(match))

    async def _run(self, match) -> None:
        for ply, fen in enumerate(list(match.fens)):
            if ply in match.evals:
                continue
            try:
                ev = self.cache.get(fen) or await asyncio.to_thread(self.engine.analyse, fen)
            except Exception as e:  # engine missing/crashed: keep the game going
                log.warning("analysis failed: %s", e)
                return
            self.cache[fen] = ev
            ev = dict(ev, best_san=best_san(fen, ev.get("best")))
            if ply > 0 and (ply - 1) in match.evals:
                prev = match.evals[ply - 1]
                mover = fen.split()[1] == "w" and "b" or "w"
                played = match.game.moves[ply - 1].uci() if ply - 1 < len(match.game.moves) else None
                ev["judgement"] = "" if played == prev.get("best") else classify(prev, ev, mover)
            match.evals[ply] = ev
            match.bump()
