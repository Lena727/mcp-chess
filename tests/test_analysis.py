from mcp_chess.analysis import best_san, classify, win_chance
from mcp_chess.engine import START_FEN


def test_win_chance_bounds():
    assert win_chance({"cp": 0}) == 0
    assert 0.9 < win_chance({"cp": 1000}) < 1
    assert win_chance({"mate": 3}) == 1 and win_chance({"mate": -2}) == -1
    assert win_chance({"mate": 0, "winner": "b"}) == -1


def test_classify_from_movers_point_of_view():
    assert classify({"cp": 0}, {"cp": -400}, "w") == "blunder"
    assert classify({"cp": 0}, {"cp": -400}, "b") == ""  # good for black
    assert classify({"cp": 0}, {"cp": 120}, "b") == "mistake"
    assert classify({"cp": 50}, {"cp": -10}, "w") == "inaccuracy"
    assert classify({"cp": 900}, {"cp": 700}, "w") == ""  # still winning


def test_best_san():
    assert best_san(START_FEN, "g1f3") == "Nf3"
    assert best_san(START_FEN, None) is None
