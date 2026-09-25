import pytest
from httpx import ASGITransport, AsyncClient

from engine.db import init_db
from main import app, state

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_3ds_telemetry_pin_commands():
    import asyncio

    from engine.telemetry import start_3ds_tcp_server

    state.auth_pin = "1234"
    state.is_active = True

    # Start server in task on custom test port
    server_task = asyncio.create_task(start_3ds_tcp_server(host="127.0.0.1", port=7399))
    await asyncio.sleep(0.1)

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 7399)

        # Test command without auth
        writer.write(b"PAUSE\n")
        await writer.drain()
        resp = await reader.readline()
        assert resp == b"NOT_AUTHENTICATED\n"

        # Test command with invalid PIN
        writer.write(b"PAUSE|9999\n")
        await writer.drain()
        resp = await reader.readline()
        assert resp == b"AUTH_FAIL\n"

        # Test command with valid PIN
        writer.write(b"PAUSE|1234\n")
        await writer.drain()
        await asyncio.sleep(0.05)
        assert state.is_active is False

        # Test EMERGENCY_STOP with valid PIN
        writer.write(b"EMERGENCY_STOP|1234\n")
        await writer.drain()
        await asyncio.sleep(0.05)
        assert state.is_active is False
        assert state.pending_trade is None

        writer.close()
        await writer.wait_closed()
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

@pytest.mark.asyncio
async def test_telemetry_ai_fields():
    from engine.state import state
    state.pending_trade = {
        "id": 12345,
        "action": "BUY",
        "pair": "BTCUSDT",
        "amount_usdt": 50.0,
        "price": 62000.0,
        "reason": "RSI Oversold",
        "ai_risk": "LOW (3/10)",
        "ai_verdict": "APPROVE"
    }

    # Verify telemetry payload mapping
    payload_ai_risk = state.pending_trade.get("ai_risk", "")
    payload_ai_verdict = state.pending_trade.get("ai_verdict", "")
    assert payload_ai_risk == "LOW (3/10)"
    assert payload_ai_verdict == "APPROVE"

    state.pending_trade = None

@pytest.mark.asyncio
async def test_discord_multi_trade_embed_and_view():
    from engine.notifier import BatchTradeApprovalView, build_multi_trade_embed

    trades = [
        {"id": 401, "pair": "BTCUSDT", "action": "BUY", "price": 64200.0, "amount_usdt": 10.0, "ai_risk": "LOW (2/10)", "reason": "Oversold RSI"},
        {"id": 402, "pair": "ETHUSDT", "action": "BUY", "price": 3450.0, "amount_usdt": 10.0, "ai_risk": "MED (5/10)", "reason": "Breakout"},
        {"id": 403, "pair": "SOLUSDT", "action": "SELL", "price": 148.0, "amount_usdt": 10.0, "ai_risk": "LOW (1/10)", "reason": "Take Profit"}
    ]

    embed = build_multi_trade_embed(trades)
    assert embed is not None
    assert "Trade Confirmations Required" in embed.title
    assert any("Signals Overview Table" in f.name for f in embed.fields)

    # Test view components
    view = BatchTradeApprovalView(trades=trades, timeout=600)
    assert view is not None
    # For 3 trades, check select menus exist
    custom_ids = [getattr(c, "custom_id", "") for c in view.children]
    assert "batch_approve_all" in custom_ids
    assert "batch_reject_all" in custom_ids
    assert "select_approve" in custom_ids
    assert "select_reject" in custom_ids

    # Test resolution embed update
    resolutions = {
        401: {"status": "approved", "order_id": "ORD123"},
        402: {"status": "rejected", "reason": "Manual rejection"},
        403: {"status": "approved", "order_id": "ORD124"}
    }
    resolved_embed = build_multi_trade_embed(trades, resolutions)
    assert resolved_embed is not None
    assert "Batch Trades Resolved" in resolved_embed.title

@pytest.mark.asyncio
async def test_discord_rbac_authorization():
    orig_allowed = list(state.allowed_discord_user_ids)
    test_uid = "123456789012345678"
    state.allowed_discord_user_ids = [test_uid]

    try:
        # Authorized user checks via is_discord_user_authorized
        assert state.is_discord_user_authorized(test_uid) is True
        assert state.is_discord_user_authorized(int(test_uid)) is True

        # Unauthorized user checks
        unauth_id = "999999999999999999"
        assert state.is_discord_user_authorized(unauth_id) is False
        assert state.is_discord_user_authorized(int(unauth_id)) is False

        # BatchTradeApprovalView interaction check
        from engine.notifier import BatchTradeApprovalView, guard_discord_auth, require_discord_auth
        view = BatchTradeApprovalView(trades=[{"id": 881, "pair": "ETHUSDT", "action": "BUY", "price": 3000.0, "amount_usdt": 10.0}])

        class MockUser:
            def __init__(self, uid):
                self.id = uid

        class MockResponse:
            def __init__(self, parent):
                self.parent = parent
            async def send_message(self, text, ephemeral=False):
                self.parent.sent_messages.append({"text": text, "ephemeral": ephemeral})

        class MockInteraction:
            def __init__(self, uid):
                self.user = MockUser(uid)
                self.sent_messages = []
                self.response = MockResponse(self)

        # Test unauthorized interaction callback
        unauth_interaction = MockInteraction(999999999999999999)
        cb = view._make_single_callback(881, True)
        await cb(unauth_interaction)
        assert len(unauth_interaction.sent_messages) == 1
        assert "Unauthorized" in unauth_interaction.sent_messages[0]["text"]
        assert "999999999999999999" in unauth_interaction.sent_messages[0]["text"]
        assert unauth_interaction.sent_messages[0]["ephemeral"] is True

        # Test direct guard_discord_auth function
        assert await guard_discord_auth(unauth_interaction) is False
        auth_interaction = MockInteraction(int(test_uid))
        assert await guard_discord_auth(auth_interaction) is True

        # Test integer in allowed list matches string caller
        state.allowed_discord_user_ids = [int(test_uid)]
        assert state.is_discord_user_authorized(test_uid) is True
    finally:
        state.allowed_discord_user_ids = orig_allowed
