"""Perft: count leaf nodes of the legal move tree and compare with known values
(https://www.chessprogramming.org/Perft_Results). Any rules bug in move
generation, castling, en passant, promotion or check detection shows up here."""

import os

import pytest

from mcp_chess.engine import START_FEN, Position, perft

DEEP = os.environ.get("PERFT_DEEP") == "1"

CASES = [
    # (name, fen, depth, nodes)
    ("start", START_FEN, 3, 8_902),
    ("kiwipete", "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", 2, 2_039),
    ("pos3", "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", 4, 43_238),
    ("pos4", "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1", 3, 9_467),
    ("pos4-mirrored", "r2q1rk1/pP1p2pp/Q4n2/bbp1p3/Np6/1B3NBn/pPPP1PPP/R3K2R b KQ - 0 1", 3, 9_467),
    ("pos5", "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", 2, 1_486),
    ("pos6", "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10", 2, 2_079),
    # Tricky edge cases (en passant discovered check, castling through attack, promotions).
    ("ep-pin", "8/8/8/K2pP2r/8/8/8/7k w - d6 0 1", 1, 6),
    ("promo", "n1n5/PPPk4/8/8/8/8/4Kppp/5N1N b - - 0 1", 3, 9_483),
]

DEEP_CASES = [
    ("start", START_FEN, 4, 197_281),
    ("kiwipete", "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", 3, 97_862),
    ("pos5", "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", 3, 62_379),
    ("pos6", "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10", 3, 89_890),
]


@pytest.mark.parametrize("name,fen,depth,nodes", CASES, ids=[c[0] for c in CASES])
def test_perft(name, fen, depth, nodes):
    assert perft(Position.from_fen(fen), depth) == nodes


@pytest.mark.skipif(not DEEP, reason="set PERFT_DEEP=1 to run deep perft")
@pytest.mark.parametrize("name,fen,depth,nodes", DEEP_CASES, ids=[c[0] for c in DEEP_CASES])
def test_perft_deep(name, fen, depth, nodes):
    assert perft(Position.from_fen(fen), depth) == nodes
