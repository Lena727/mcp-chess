"""A small, dependency-free chess rules engine.

Implements the full FIDE Laws of Chess move rules: castling (incl. "through
check"), en passant, promotion, check/checkmate/stalemate, the 50/75-move
rules, threefold/fivefold repetition and dead positions by insufficient
material. Correctness is verified by perft tests (see tests/test_perft.py).

Board representation: a flat list of 64 chars, index = rank * 8 + file
(a1 = 0, h8 = 63). Uppercase = white, lowercase = black, "." = empty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

FILES = "abcdefgh"
EMPTY = "."

KNIGHT_STEPS = [(1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2)]
KING_STEPS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
ROOK_DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]
BISHOP_DIRS = [(1, 1), (1, -1), (-1, 1), (-1, -1)]

PIECE_VALUES = {"p": 1, "n": 3, "b": 3, "r": 5, "q": 9, "k": 0}


def _build_targets(steps):
    table = []
    for sq in range(64):
        f, r = sq % 8, sq // 8
        table.append([(r + dr) * 8 + f + df for df, dr in steps if 0 <= f + df < 8 and 0 <= r + dr < 8])
    return table


def _build_rays(dirs):
    table = []
    for sq in range(64):
        f, r = sq % 8, sq // 8
        rays = []
        for df, dr in dirs:
            ray, nf, nr = [], f + df, r + dr
            while 0 <= nf < 8 and 0 <= nr < 8:
                ray.append(nr * 8 + nf)
                nf, nr = nf + df, nr + dr
            rays.append(ray)
        table.append(rays)
    return table


KNIGHT_TARGETS = _build_targets(KNIGHT_STEPS)
KING_TARGETS = _build_targets(KING_STEPS)
ROOK_RAYS = _build_rays(ROOK_DIRS)
BISHOP_RAYS = _build_rays(BISHOP_DIRS)

# Squares whose vacating/capture removes a castling right.
CASTLE_RIGHTS_LOST = {0: "Q", 4: "KQ", 7: "K", 56: "q", 60: "kq", 63: "k"}


def sq_name(sq: int) -> str:
    return FILES[sq % 8] + str(sq // 8 + 1)


def sq_index(name: str) -> int:
    if len(name) != 2 or name[0] not in FILES or name[1] not in "12345678":
        raise ValueError(f"bad square: {name!r}")
    return FILES.index(name[0]) + 8 * (int(name[1]) - 1)


def color_of(piece: str) -> str:
    return "w" if piece.isupper() else "b"


@dataclass(frozen=True, slots=True)
class Move:
    frm: int
    to: int
    promo: str = ""  # lowercase piece letter: q r b n

    def uci(self) -> str:
        return sq_name(self.frm) + sq_name(self.to) + self.promo

    def __str__(self) -> str:
        return self.uci()


class IllegalMove(ValueError):
    pass


class Position:
    __slots__ = ("board", "turn", "castling", "ep", "halfmove", "fullmove")

    def __init__(self, board, turn, castling, ep, halfmove, fullmove):
        self.board: list[str] = board
        self.turn: str = turn  # "w" | "b"
        self.castling: str = castling  # subset of "KQkq", "" if none
        self.ep: int | None = ep  # en passant target square
        self.halfmove: int = halfmove
        self.fullmove: int = fullmove

    # ---------------------------------------------------------------- FEN
    @classmethod
    def from_fen(cls, fen: str) -> "Position":
        parts = fen.split()
        if len(parts) == 4:
            parts += ["0", "1"]
        if len(parts) != 6:
            raise ValueError("FEN must have 6 fields")
        placement, turn, castling, ep, half, full = parts
        rows = placement.split("/")
        if len(rows) != 8:
            raise ValueError("FEN board must have 8 ranks")
        board = [EMPTY] * 64
        for i, row in enumerate(rows):
            rank, f = 7 - i, 0
            for ch in row:
                if ch.isdigit():
                    f += int(ch)
                elif ch.lower() in "pnbrqk":
                    if f > 7:
                        raise ValueError("FEN rank too long")
                    board[rank * 8 + f] = ch
                    f += 1
                else:
                    raise ValueError(f"bad FEN char {ch!r}")
            if f != 8:
                raise ValueError("FEN rank has wrong length")
        if turn not in ("w", "b"):
            raise ValueError("FEN side to move must be w or b")
        if castling != "-" and (not set(castling) <= set("KQkq") or len(set(castling)) != len(castling)):
            raise ValueError("bad FEN castling field")
        if board.count("K") != 1 or board.count("k") != 1:
            raise ValueError("each side needs exactly one king")
        if any(board[sq].lower() == "p" for sq in list(range(8)) + list(range(56, 64))):
            raise ValueError("pawns cannot stand on the first or last rank")
        # Drop castling rights that the piece placement makes impossible.
        rights = "".join(
            c for c in "KQkq" if c in castling and board[{"K": 4, "Q": 4, "k": 60, "q": 60}[c]] == ("K" if c.isupper() else "k")
            and board[{"K": 7, "Q": 0, "k": 63, "q": 56}[c]] == ("R" if c.isupper() else "r")
        )
        pos = cls(board, turn, rights, None if ep == "-" else sq_index(ep), int(half), int(full))
        if pos.is_attacked(pos.king_square(pos.opponent), pos.turn):
            raise ValueError("side not to move is in check")
        return pos

    def fen(self) -> str:
        rows = []
        for rank in range(7, -1, -1):
            row, empty = "", 0
            for f in range(8):
                p = self.board[rank * 8 + f]
                if p == EMPTY:
                    empty += 1
                else:
                    row += (str(empty) if empty else "") + p
                    empty = 0
            rows.append(row + (str(empty) if empty else ""))
        ep = sq_name(self.ep) if self.ep is not None else "-"
        return f"{'/'.join(rows)} {self.turn} {self.castling or '-'} {ep} {self.halfmove} {self.fullmove}"

    def copy(self) -> "Position":
        return Position(self.board[:], self.turn, self.castling, self.ep, self.halfmove, self.fullmove)

    # ------------------------------------------------------------ queries
    @property
    def opponent(self) -> str:
        return "b" if self.turn == "w" else "w"

    def king_square(self, color: str) -> int:
        return self.board.index("K" if color == "w" else "k")

    def is_attacked(self, sq: int, by: str) -> bool:
        """Is square `sq` attacked by any piece of color `by`?"""
        b = self.board
        if by == "w":
            pawn, knight, bishop, rook, queen, king = "PNBRQK"
        else:
            pawn, knight, bishop, rook, queen, king = "pnbrqk"
        f = sq % 8
        # Pawns attack diagonally forward, so look one rank "behind" sq.
        src_rank_offset = -8 if by == "w" else 8
        for df in (-1, 1):
            if 0 <= f + df < 8:
                s = sq + src_rank_offset + df
                if 0 <= s < 64 and b[s] == pawn:
                    return True
        for s in KNIGHT_TARGETS[sq]:
            if b[s] == knight:
                return True
        for s in KING_TARGETS[sq]:
            if b[s] == king:
                return True
        for ray in ROOK_RAYS[sq]:
            for s in ray:
                p = b[s]
                if p != EMPTY:
                    if p == rook or p == queen:
                        return True
                    break
        for ray in BISHOP_RAYS[sq]:
            for s in ray:
                p = b[s]
                if p != EMPTY:
                    if p == bishop or p == queen:
                        return True
                    break
        return False

    def in_check(self) -> bool:
        return self.is_attacked(self.king_square(self.turn), self.opponent)

    # ------------------------------------------------------ move generation
    def pseudo_legal_moves(self) -> list[Move]:
        b, us = self.board, self.turn
        moves: list[Move] = []
        own = str.isupper if us == "w" else str.islower

        for sq in range(64):
            p = b[sq]
            if p == EMPTY or not own(p):
                continue
            kind = p.lower()
            if kind == "p":
                self._pawn_moves(sq, moves)
            elif kind == "n" or kind == "k":
                for t in (KNIGHT_TARGETS if kind == "n" else KING_TARGETS)[sq]:
                    if b[t] == EMPTY or not own(b[t]):
                        moves.append(Move(sq, t))
            else:
                rays = []
                if kind in "rq":
                    rays += ROOK_RAYS[sq]
                if kind in "bq":
                    rays += BISHOP_RAYS[sq]
                for ray in rays:
                    for t in ray:
                        if b[t] == EMPTY:
                            moves.append(Move(sq, t))
                        else:
                            if not own(b[t]):
                                moves.append(Move(sq, t))
                            break
        self._castling_moves(moves)
        return moves

    def _pawn_moves(self, sq: int, moves: list[Move]) -> None:
        b, us = self.board, self.turn
        step, start_rank, last_rank = (8, 1, 7) if us == "w" else (-8, 6, 0)
        f = sq % 8

        def add(to):
            if to // 8 == last_rank:
                moves.extend(Move(sq, to, pr) for pr in "qrbn")
            else:
                moves.append(Move(sq, to))

        one = sq + step
        if b[one] == EMPTY:
            add(one)
            if sq // 8 == start_rank and b[one + step] == EMPTY:
                moves.append(Move(sq, one + step))
        for df in (-1, 1):
            if 0 <= f + df < 8:
                t = one + df
                if (b[t] != EMPTY and color_of(b[t]) != us) or t == self.ep:
                    add(t)

    def _castling_moves(self, moves: list[Move]) -> None:
        b, them = self.board, self.opponent
        if self.turn == "w":
            king_sq, rights = 4, ("K", "Q")
        else:
            king_sq, rights = 60, ("k", "q")
        if not any(r in self.castling for r in rights):
            return
        if self.is_attacked(king_sq, them):
            return
        if rights[0] in self.castling and b[king_sq + 1] == EMPTY and b[king_sq + 2] == EMPTY:
            if not self.is_attacked(king_sq + 1, them):
                moves.append(Move(king_sq, king_sq + 2))  # destination checked in legal_moves
        if rights[1] in self.castling and b[king_sq - 1] == EMPTY and b[king_sq - 2] == EMPTY and b[king_sq - 3] == EMPTY:
            if not self.is_attacked(king_sq - 1, them):
                moves.append(Move(king_sq, king_sq - 2))

    def legal_moves(self) -> list[Move]:
        us, them = self.turn, self.opponent
        result = []
        for m in self.pseudo_legal_moves():
            nxt = self.push(m)
            if not nxt.is_attacked(nxt.king_square(us), them):
                result.append(m)
        return result

    # ---------------------------------------------------------- make move
    def is_capture(self, m: Move) -> bool:
        return self.board[m.to] != EMPTY or (m.to == self.ep and self.board[m.frm].lower() == "p")

    def is_castling(self, m: Move) -> bool:
        return self.board[m.frm].lower() == "k" and abs(m.to - m.frm) == 2

    def push(self, m: Move) -> "Position":
        """Return the position after move `m` (no legality check)."""
        b = self.board[:]
        piece = b[m.frm]
        kind = piece.lower()
        capture = b[m.to] != EMPTY
        b[m.to] = piece
        b[m.frm] = EMPTY
        ep = None
        if kind == "p":
            if m.to == self.ep:  # en passant: remove the pawn behind the target square
                b[m.to + (-8 if self.turn == "w" else 8)] = EMPTY
                capture = True
            elif abs(m.to - m.frm) == 16:
                ep = (m.frm + m.to) // 2
            if m.promo:
                b[m.to] = m.promo.upper() if self.turn == "w" else m.promo
        elif kind == "k" and abs(m.to - m.frm) == 2:
            if m.to > m.frm:  # king side: rook h -> f
                b[m.frm + 1], b[m.frm + 3] = b[m.frm + 3], EMPTY
            else:  # queen side: rook a -> d
                b[m.frm - 1], b[m.frm - 4] = b[m.frm - 4], EMPTY
        castling = self.castling
        for sq in (m.frm, m.to):
            lost = CASTLE_RIGHTS_LOST.get(sq)
            if lost:
                castling = "".join(c for c in castling if c not in lost)
        return Position(
            b,
            self.opponent,
            castling,
            ep,
            0 if (kind == "p" or capture) else self.halfmove + 1,
            self.fullmove + (1 if self.turn == "b" else 0),
        )

    # ------------------------------------------------------- draw helpers
    def repetition_key(self) -> str:
        """Identity of a position for repetition purposes (FIDE 9.2.3):
        placement, side to move, castling rights, and en passant only if an
        en passant capture is actually legal."""
        ep = "-"
        if self.ep is not None and any(m.to == self.ep and self.board[m.frm].lower() == "p" for m in self.legal_moves()):
            ep = sq_name(self.ep)
        return f"{''.join(self.board)} {self.turn} {self.castling} {ep}"

    def insufficient_material(self) -> bool:
        """Dead position by material: K v K, K+minor v K, or only bishops
        (any number, either side) that all stand on same-colored squares."""
        others = [(sq, p) for sq, p in enumerate(self.board) if p != EMPTY and p.lower() != "k"]
        if not others:
            return True
        if len(others) == 1 and others[0][1].lower() in "nb":
            return True
        if all(p.lower() == "b" for _, p in others):
            colors = {(sq % 8 + sq // 8) % 2 for sq, _ in others}
            return len(colors) == 1
        return False

    # -------------------------------------------------------------- SAN
    def san(self, m: Move, legal: list[Move] | None = None) -> str:
        """Standard Algebraic Notation for a legal move `m`."""
        if legal is None:
            legal = self.legal_moves()
        piece = self.board[m.frm]
        kind = piece.lower()
        if kind == "k" and abs(m.to - m.frm) == 2:
            s = "O-O" if m.to > m.frm else "O-O-O"
        else:
            capture = self.is_capture(m)
            if kind == "p":
                s = (FILES[m.frm % 8] + "x" if capture else "") + sq_name(m.to)
                if m.promo:
                    s += "=" + m.promo.upper()
            else:
                s = kind.upper()
                rivals = [o.frm for o in legal if o.to == m.to and o.frm != m.frm and self.board[o.frm] == piece]
                if rivals:
                    if all(r % 8 != m.frm % 8 for r in rivals):
                        s += FILES[m.frm % 8]
                    elif all(r // 8 != m.frm // 8 for r in rivals):
                        s += str(m.frm // 8 + 1)
                    else:
                        s += sq_name(m.frm)
                s += ("x" if capture else "") + sq_name(m.to)
        nxt = self.push(m)
        if nxt.in_check():
            s += "#" if not nxt.legal_moves() else "+"
        return s

    def parse_move(self, text: str, legal: list[Move] | None = None) -> Move:
        """Parse SAN (lenient: optional x/+/#/=, 0-0, e8Q) or UCI (e2e4, e7e8q)."""
        if legal is None:
            legal = self.legal_moves()
        raw = text.strip()
        if not raw:
            raise IllegalMove("empty move")
        # UCI / long algebraic: e2e4, e2-e4, Ng1-f3, e7e8q
        m = re.fullmatch(r"([KQRBN])?([a-h][1-8])[-x]?([a-h][1-8])=?([qrbnQRBN])?[+#!?]*", raw)
        if m:
            frm, to, promo = sq_index(m.group(2)), sq_index(m.group(3)), (m.group(4) or "").lower()
            for mv in legal:
                if mv.frm == frm and mv.to == to and mv.promo == promo:
                    if m.group(1) and self.board[frm].upper() != m.group(1):
                        break
                    return mv
            if not any(mv.frm == frm and mv.to == to for mv in legal):
                raise IllegalMove(f"{raw} is not legal here")
            if not promo:
                raise IllegalMove(f"{raw}: promotion piece required (e.g. {raw}=Q)")
            raise IllegalMove(f"{raw} is not legal here")

        def norm(s: str) -> str:
            s = s.replace("0", "O")
            s = re.sub(r"[x+#!?=\-\s]", "", s)
            # lowercase promotion piece after a last-rank square: e8q -> e8Q
            return re.sub(r"([a-h][18])([qrbn])$", lambda g: g.group(1) + g.group(2).upper(), s)

        want = norm(raw)
        matches = [mv for mv in legal if norm(self.san(mv, legal)) == want]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise IllegalMove(f"{raw} is ambiguous")
        # Under-disambiguated piece move, e.g. "Nd2" when both knights can go there.
        if re.fullmatch(r"[KQRBN][a-h][1-8]", want):
            rivals = [mv for mv in legal if self.board[mv.frm].upper() == want[0] and sq_name(mv.to) == want[1:]]
            if len(rivals) > 1:
                options = " ".join(self.san(mv, legal) for mv in rivals)
                raise IllegalMove(f"{raw} is ambiguous: {options}")
        # Pawn move to the last rank without a promotion piece.
        if re.fullmatch(r"([a-h]x?)?[a-h][18]", want.replace("x", "")) and any(norm(self.san(mv, legal))[:-1] == want for mv in legal):
            raise IllegalMove(f"{raw}: promotion piece required (e.g. {raw}=Q)")
        raise IllegalMove(f"{raw} is not legal here")

    # ------------------------------------------------------------- misc
    def ascii(self, flip: bool = False) -> str:
        ranks = range(8) if flip else range(7, -1, -1)
        files = range(7, -1, -1) if flip else range(8)
        lines = [f"{r + 1} " + " ".join(self.board[r * 8 + f] for f in files) for r in ranks]
        lines.append("  " + " ".join(FILES[f] for f in files))
        return "\n".join(lines)

    def material(self) -> tuple[int, int]:
        w = sum(PIECE_VALUES[p.lower()] for p in self.board if p.isupper())
        b = sum(PIECE_VALUES[p] for p in self.board if p.islower())
        return w, b


def perft(pos: Position, depth: int) -> int:
    if depth == 0:
        return 1
    moves = pos.legal_moves()
    if depth == 1:
        return len(moves)
    return sum(perft(pos.push(m), depth - 1) for m in moves)


# ---------------------------------------------------------------- Game
class Game:
    """A position plus history, and game-termination rules.

    Automatic endings (FIDE 5.2, 9.6): checkmate, stalemate, insufficient
    material, fivefold repetition, 75-move rule. Claimable draws: threefold
    repetition and the 50-move rule (see `claimable_draw` / `claim_draw`).
    """

    def __init__(self, fen: str = START_FEN):
        self.start_fen = fen
        self.pos = Position.from_fen(fen)
        self.moves: list[Move] = []
        self.sans: list[str] = []
        self.keys: list[str] = [self.pos.repetition_key()]
        self.result: str | None = None  # "1-0" | "0-1" | "1/2-1/2"
        self.reason: str | None = None
        self._legal: list[Move] | None = None
        self._update_status()

    @property
    def legal(self) -> list[Move]:
        if self._legal is None:
            self._legal = [] if self.result else self.pos.legal_moves()
        return self._legal

    def legal_san(self) -> list[str]:
        return [self.pos.san(m, self.legal) for m in self.legal]

    def play(self, text_or_move: str | Move) -> str:
        """Play a move (SAN/UCI string or Move). Returns its SAN."""
        if self.result:
            raise IllegalMove(f"game is over ({self.result}, {self.reason})")
        legal = self.legal
        if isinstance(text_or_move, Move):
            if text_or_move not in legal:
                raise IllegalMove(f"{text_or_move} is not legal here")
            mv = text_or_move
        else:
            mv = self.pos.parse_move(text_or_move, legal)
        san = self.pos.san(mv, legal)
        self.pos = self.pos.push(mv)
        self.moves.append(mv)
        self.sans.append(san)
        self.keys.append(self.pos.repetition_key())
        self._legal = None
        self._update_status()
        return san

    def repetitions(self) -> int:
        return self.keys.count(self.keys[-1])

    def _update_status(self) -> None:
        legal = self.pos.legal_moves()
        self._legal = legal
        if not legal:
            if self.pos.in_check():
                self.finish("0-1" if self.pos.turn == "w" else "1-0", "checkmate")
            else:
                self.finish("1/2-1/2", "stalemate")
        elif self.pos.insufficient_material():
            self.finish("1/2-1/2", "insufficient material")
        elif self.repetitions() >= 5:
            self.finish("1/2-1/2", "fivefold repetition")
        elif self.pos.halfmove >= 150:
            self.finish("1/2-1/2", "75-move rule")

    def claimable_draw(self) -> str | None:
        if self.result:
            return None
        if self.repetitions() >= 3:
            return "threefold repetition"
        if self.pos.halfmove >= 100:
            return "50-move rule"
        return None

    def finish(self, result: str, reason: str) -> None:
        self.result, self.reason = result, reason
        self._legal = []

    def pgn(self, headers: dict[str, str] | None = None) -> str:
        hdr = {"Event": "MCP Chess", "Site": "local", "White": "?", "Black": "?", "Result": self.result or "*"}
        hdr.update(headers or {})
        if self.start_fen != START_FEN:
            hdr.update({"SetUp": "1", "FEN": self.start_fen})
        lines = [f'[{k} "{v}"]' for k, v in hdr.items()]
        first = Position.from_fen(self.start_fen)
        num, black = first.fullmove, first.turn == "b"
        tokens = []
        if black and self.sans:
            tokens.append(f"{num}...")
        for san in self.sans:
            if not black:
                tokens.append(f"{num}.")
            tokens.append(san)
            if black:
                num += 1
            black = not black
        tokens.append(self.result or "*")
        body, line = [], ""
        for t in tokens:
            if len(line) + len(t) + 1 > 80:
                body.append(line)
                line = t
            else:
                line = f"{line} {t}" if line else t
        body.append(line)
        return "\n".join(lines) + "\n\n" + "\n".join(body) + "\n"
