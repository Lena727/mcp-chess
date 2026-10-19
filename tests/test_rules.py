import pytest

from mcp_chess.engine import Game, IllegalMove, Position


def play(*moves, fen=None):
    g = Game(fen) if fen else Game()
    for m in moves:
        g.play(m)
    return g


def test_fools_mate():
    g = play("f3", "e5", "g4", "Qh4")
    assert g.sans[-1] == "Qh4#"
    assert (g.result, g.reason) == ("0-1", "checkmate")
    with pytest.raises(IllegalMove):
        g.play("e4")


def test_stalemate():
    g = play("Qb6", fen="k7/8/8/1Q6/8/8/8/K7 w - - 0 1")
    assert (g.result, g.reason) == ("1/2-1/2", "stalemate")


@pytest.mark.parametrize("text", ["Nf3", "Ng1f3", "g1f3", "Ng1-f3", "  Nf3+  ", "Nf3!?"])
def test_parse_variants(text):
    assert Game().play(text) == "Nf3"


def test_castling_notation_and_rights():
    fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
    assert play("0-0", fen=fen).sans == ["O-O"]
    assert play("e1c1", fen=fen).sans == ["O-O-O"]
    g = play("Rh2", "Ra7", fen=fen)
    assert g.pos.castling == "Qk"


def test_cannot_castle_through_check():
    g = Game("4kr2/8/8/8/8/8/8/R3K2R w KQ - 0 1")  # f8 rook attacks f1
    assert "O-O" not in g.legal_san()
    assert "O-O-O" in g.legal_san()


def test_en_passant_and_san():
    g = play("e4", "a6", "e5", "d5")
    assert "exd6" in g.legal_san()
    g.play("exd6")
    assert g.pos.board[35] == "."  # d5 pawn removed


def test_promotion():
    fen = "1n5k/P7/8/8/8/8/8/K7 w - - 0 1"
    g = Game(fen)
    assert {"a8=Q", "axb8=N", "axb8=Q+"} <= set(g.legal_san())
    with pytest.raises(IllegalMove, match="promotion"):
        Game(fen).play("a8")
    with pytest.raises(IllegalMove, match="promotion"):
        Game(fen).play("a7a8")
    assert Game(fen).play("a8q") == "a8=Q"
    assert Game(fen).play("axb8=N") == "axb8=N"


def test_disambiguation():
    g = Game("k7/8/8/8/8/8/8/KN3N2 w - - 0 1")
    assert "Nbd2" in g.legal_san() and "Nfd2" in g.legal_san()
    with pytest.raises(IllegalMove, match="ambiguous"):
        g.play("Nd2")
    g = Game("7k/8/8/R7/8/8/8/R5K1 w - - 0 1")
    assert {"R5a3", "R1a3"} <= set(g.legal_san())
    g = Game("7k/8/8/8/Q1Q5/8/8/2Q3K1 w - - 0 1")  # a4, c4, c1 all reach c2
    assert {"Qac2", "Qc4c2", "Q1c2"} <= set(g.legal_san())
    g = Game("7k/8/8/8/Q1Q5/8/Q7/6K1 w - - 0 1")  # a4, c4, a2 all reach b3
    assert "Qa4b3" in g.legal_san()


def test_pinned_piece_cannot_move():
    g = Game("4k3/4r3/8/8/8/8/4N3/4K3 w - - 0 1")
    assert not any(s.startswith("N") for s in g.legal_san())


def test_illegal_into_check():
    with pytest.raises(IllegalMove, match="not legal"):
        Game("4k3/8/8/8/8/8/r7/4K3 w - - 0 1").play("Kd2")


def test_threefold_claim_and_fivefold_auto():
    g = Game()
    shuffle = ["Nf3", "Nf6", "Ng1", "Ng8"]
    for m in shuffle * 2:
        g.play(m)
    assert g.claimable_draw() == "threefold repetition"
    assert g.result is None
    for m in shuffle * 2:
        g.play(m)
    assert (g.result, g.reason) == ("1/2-1/2", "fivefold repetition")


def test_50_and_75_move_rules():
    g = Game("4k3/8/8/8/8/8/8/R3K3 w - - 99 80")
    g.play("Ra2")
    assert g.claimable_draw() == "50-move rule" and g.result is None
    g = Game("4k3/8/8/8/8/8/8/R3K3 w - - 149 80")
    g.play("Ra2")
    assert g.reason == "75-move rule"


def test_mate_beats_75_move_rule():
    g = Game("7k/8/6K1/8/8/8/8/R7 w - - 149 80")
    g.play("Ra8")
    assert g.reason == "checkmate"


@pytest.mark.parametrize(
    "fen,dead",
    [
        ("4k3/8/8/8/8/8/8/4K3 w - - 0 1", True),
        ("4k3/8/8/8/8/8/8/4KN2 w - - 0 1", True),
        ("4kb2/8/8/8/8/8/8/2B1K3 w - - 0 1", True),  # bishops both on dark squares
        ("4k1b1/8/8/8/8/8/8/2B1K3 w - - 0 1", False),  # opposite colors
        ("4k3/8/8/8/8/8/8/3NKN2 w - - 0 1", False),
        ("4k3/8/8/8/8/8/4P3/4K3 w - - 0 1", False),
    ],
)
def test_insufficient_material(fen, dead):
    assert Position.from_fen(fen).insufficient_material() is dead


def test_ep_square_only_counts_for_repetition_if_capturable():
    a = play("e4")  # no black pawn can capture on e3
    b = Position.from_fen(a.pos.fen().replace(" e3 ", " - "))
    assert a.pos.repetition_key() == b.repetition_key()


def test_bad_fen():
    for fen in ["8/8/8/8/8/8/8/8 w - - 0 1", "4k3/8/8/8/8/8/8/4K2R b K - 0 1x", "4k3/4R3/8/8/8/8/8/4K3 w - - 0 1"]:
        with pytest.raises(ValueError):
            Game(fen)


def test_pgn():
    g = play("e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7")
    pgn = g.pgn({"White": "A", "Black": "B"})
    assert "1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0" in pgn
    assert '[Result "1-0"]' in pgn
