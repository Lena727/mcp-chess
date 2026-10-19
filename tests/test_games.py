import asyncio

import pytest

from mcp_chess.games import GameError, Lobby


def make():
    lobby = Lobby()
    m = lobby.create("agent", "agent")
    w = lobby.claim(m, "w").key
    b = lobby.join(m.id).key
    return lobby, m, w, b


def test_turns_and_seats_are_enforced():
    lobby, m, w, b = make()
    with pytest.raises(GameError, match="not your turn"):
        lobby.move(b, "e5")
    with pytest.raises(GameError, match="unknown seat"):
        lobby.move("nope", "e4")
    with pytest.raises(GameError, match="illegal.*legal: "):
        lobby.move(w, "e5")
    assert lobby.move(w, "e4") == "e4"
    with pytest.raises(GameError, match="no free agent seat"):
        lobby.join(m.id)


def test_state_text_is_compact_and_hides_legal_when_waiting():
    lobby, m, w, b = make()
    s = lobby.state_text(w)
    assert "YOUR TURN" in s and "legal(20):" in s
    assert "legal" not in lobby.state_text(b)
    lobby.move(w, "e4")
    assert "last: 1.e4" in lobby.state_text(b)
    lobby.move(b, "e5")
    assert "last: 1...e5" in lobby.state_text(w)


def test_draw_offer_flow():
    lobby, m, w, b = make()
    lobby.action(w, "offer_draw")
    assert "opponent offers a draw" in lobby.state_text(b)
    with pytest.raises(GameError):
        lobby.action(w, "accept_draw")  # cannot accept own offer
    lobby.move(w, "e4")
    lobby.move(b, "e5")  # moving declines
    assert m.draw_offer is None
    lobby.action(b, "offer_draw")
    lobby.action(w, "accept_draw")
    assert (m.game.result, m.game.reason) == ("1/2-1/2", "agreement")
    assert "GAME OVER" in lobby.state_text(w)


def test_resign_and_claim():
    lobby, m, w, b = make()
    with pytest.raises(GameError, match="no draw to claim"):
        lobby.action(w, "claim_draw")
    lobby.action(b, "resign")
    assert m.game.result == "1-0" and "you won" in lobby.state_text(w)


def test_wait_turn_wakes_on_opponent_move():
    async def run():
        lobby, m, w, b = make()
        waiter = asyncio.create_task(lobby.wait_turn(b, 5))
        await asyncio.sleep(0.05)
        assert not waiter.done()
        lobby.move(w, "d4")
        assert await asyncio.wait_for(waiter, 1) is True
        assert await lobby.wait_turn(w, 0.05) is False  # times out

    asyncio.run(run())
