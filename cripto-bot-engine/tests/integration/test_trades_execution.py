import pytest
from httpx import ASGITransport, AsyncClient

from engine.db import init_db
from main import app, state

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_simulate_trade_signal():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/trade/simulate", headers={"X-Auth-PIN": state.auth_pin})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert state.pending_trade is not None
        assert state.pending_trade["action"] == "BUY"
        assert state.pending_trade["timeout_sec"] == 600

@pytest.mark.asyncio
async def test_trade_approval_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create trade signal
        await ac.post("/api/trade/simulate", headers={"X-Auth-PIN": state.auth_pin})
        assert state.pending_trade is not None

        # Approve trade
        response = await ac.post("/api/trade/decide?approved=true", headers={"X-Auth-PIN": state.auth_pin})
        assert response.status_code == 200
        assert response.json()["status"] == "approved"
        assert state.pending_trade is None

@pytest.mark.asyncio
async def test_trade_rejection_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create trade signal
        await ac.post("/api/trade/simulate", headers={"X-Auth-PIN": state.auth_pin})
        assert state.pending_trade is not None

        # Reject trade
        response = await ac.post("/api/trade/decide?approved=false", headers={"X-Auth-PIN": state.auth_pin})
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert state.pending_trade is None

def test_format_and_validate_order():
    from engine.state import state
    from engine.trades import format_and_validate_order

    # Mock exchange filter for BTCUSDT
    state.exchange_filters["BTCUSDT"] = {
        "minQty": 0.00001,
        "maxQty": 100.0,
        "stepSize": 0.00001,
        "minNotional": 5.0,
        "tickSize": 0.01
    }

    # BUY valid: $50 USDT at $60,000 price
    valid, qty, usdt, reason = format_and_validate_order("BTCUSDT", "BUY", 50.0, 60000.0)
    assert valid is True
    assert qty == 0.00083
    assert usdt >= 5.0
    assert reason == "OK"

    # BUY below minNotional ($3.00 < $5.00 minNotional)
    valid, qty, usdt, reason = format_and_validate_order("BTCUSDT", "BUY", 3.0, 60000.0)
    assert valid is False
    assert "below Binance minNotional" in reason

    # BUY $5.10 USDT at $69,270.01 price (stepSize 0.00001 would floor to 0.00007 = $4.85, but should ceil to 0.00008 = $5.54)
    state.usdt_balance = 100.0
    valid, qty, usdt, reason = format_and_validate_order("BTCUSDT", "BUY", 5.10, 69270.01)
    assert valid is True
    assert qty == 0.00008
    assert usdt >= 5.0
    assert reason == "OK"

    # SELL valid: 0.00083 BTC at $60,000
    valid, qty, usdt, reason = format_and_validate_order("BTCUSDT", "SELL", 0.0, 60000.0, raw_qty=0.000832)
    assert valid is True
    assert qty == 0.00083 # truncated to stepSize 0.00001
    assert usdt >= 5.0
    assert reason == "OK"

    # SELL dust (< $5.00)
    valid, qty, usdt, reason = format_and_validate_order("BTCUSDT", "SELL", 0.0, 60000.0, raw_qty=0.00002)
    assert valid is False
    assert "Dust" in reason

@pytest.mark.asyncio
async def test_trade_history_and_pnl():
    from engine.db import get_pnl_summary, get_trade_history, log_trade

    await init_db()

    # Log a simulated BUY of 0.01 BTC at $60,000 ($600 USDT)
    await log_trade("BTCUSDT", "BUY", 600.0, 60000.0, "EXECUTED", "ORD_BUY_1", is_testnet=True)

    # Log a simulated profitable SELL of 0.01 BTC at $66,000 ($660 USDT -> +$60.00 profit / +10%)
    await log_trade(
        "BTCUSDT", "SELL", 660.0, 66000.0, "EXECUTED", "ORD_SELL_1",
        is_testnet=True, realized_pnl_usdt=60.0, realized_pnl_percent=10.0
    )

    # Log a simulated loss SELL of 0.01 BTC at $57,000 ($570 USDT -> -$30.00 loss / -5%)
    await log_trade(
        "BTCUSDT", "SELL", 570.0, 57000.0, "EXECUTED", "ORD_SELL_2",
        is_testnet=True, realized_pnl_usdt=-30.0, realized_pnl_percent=-5.0
    )

    history = await get_trade_history(limit=10, is_testnet=True)
    assert len(history) >= 3
    assert history[0]["pair"] == "BTCUSDT"

    summary = await get_pnl_summary(is_testnet=True)
    assert "total_pnl_usdt" in summary
    assert "win_rate" in summary
    assert summary["wins"] >= 1
    assert summary["losses"] >= 1

@pytest.mark.asyncio
async def test_clear_trade_history():
    from httpx import ASGITransport, AsyncClient

    from engine.db import clear_trade_history, get_trade_history, log_trade
    from engine.state import state
    from main import app

    # Insert test data: 1 EXECUTED, 1 REJECTED, 1 TIMEOUT
    await log_trade("SOLUSDT", "BUY", 15.0, 150.0, "EXECUTED", "ORD_EX", is_testnet=True)
    await log_trade("SOLUSDT", "BUY", 15.0, 150.0, "REJECTED", "ORD_REJ", is_testnet=True)
    await log_trade("SOLUSDT", "BUY", 15.0, 150.0, "TIMEOUT", "ORD_TO", is_testnet=True)

    # Clean only unexecuted/rejected trades
    deleted = await clear_trade_history(only_unexecuted=True, is_testnet=True)
    assert deleted >= 2

    # Verify EXECUTED trade still exists
    history = await get_trade_history(limit=50, is_testnet=True)
    statuses = [t["status"] for t in history if t["pair"] == "SOLUSDT"]
    assert "EXECUTED" in statuses
    assert "REJECTED" not in statuses
    assert "TIMEOUT" not in statuses

    # Test DELETE endpoint
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete("/api/trades/clear?only_rejected=true", headers={"X-Auth-PIN": state.auth_pin})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_manual_sell_execution():
    from engine.state import state
    from engine.trades import execute_manual_sell

    state.auth_pin = "1234"
    state.portfolio_balances["XRP"] = 10.0
    state.prices["XRPUSDT"] = 1.05
    state.cost_bases["XRPUSDT"] = 1.00

    # 1. Test invalid PIN
    res_bad_pin = await execute_manual_sell("XRP", 100.0, "9999")
    assert res_bad_pin["status"] == "error"
    assert "Invalid PIN" in res_bad_pin["message"]

    # 2. Test sell value below $5.00 MIN_NOTIONAL (selling 20% of 10 XRP = 2 XRP = $2.10)
    res_dust = await execute_manual_sell("XRP", 20.0, "1234")
    assert res_dust["status"] == "error"
    assert "below minNotional" in res_dust["message"].lower() or "dust" in res_dust["message"].lower()

    # 3. Test successful sell (selling 100% of 10 XRP = $10.50)
    res_ok = await execute_manual_sell("XRP", 100.0, "1234")
    assert res_ok["status"] == "success"
    assert res_ok["sold_qty"] == 10.0
    assert res_ok["amount_usdt"] == 10.50
    assert res_ok["realized_pnl_usdt"] == 0.50 # (1.05 - 1.00) * 10
    assert res_ok["realized_pnl_percent"] == 5.0 # +5.0%
    assert state.portfolio_balances.get("XRP", 0.0) == 0.0

@pytest.mark.asyncio
async def test_manual_buy_execution():
    from engine.state import state
    from engine.trades import execute_manual_buy

    state.prices["SOLUSDT"] = 150.0
    state.usdt_balance = 100.0
    state.portfolio_balances["SOL"] = 0.0

    # 1. Invalid PIN -> Error
    res_err = await execute_manual_buy("SOL", 10.0, "wrong_pin")
    assert res_err["status"] == "error"

    # 2. Valid execution
    res = await execute_manual_buy("SOL", 15.0, state.auth_pin)
    assert res["status"] == "success"
    assert res["pair"] == "SOLUSDT"
    assert res["bought_usdt"] == 15.0
    assert state.portfolio_balances["SOL"] > 0

@pytest.mark.asyncio
async def test_multi_pending_trades_state():
    from engine.state import state
    state.clear_pending_trades()
    assert state.pending_trade is None
    assert len(state.pending_trades) == 0

    t1 = {"id": 101, "pair": "BTCUSDT", "action": "BUY", "price": 64000.0, "amount_usdt": 10.0}
    t2 = {"id": 102, "pair": "ETHUSDT", "action": "BUY", "price": 3400.0, "amount_usdt": 10.0}

    state.add_pending_trade(t1)
    state.add_pending_trade(t2)

    assert len(state.pending_trades) == 2
    assert state.get_pending_trade(101) == t1
    assert state.get_pending_trade(102) == t2
    assert state.pending_trade is not None # First pending trade

    # State dict includes both
    d = state.to_dict()
    assert len(d["pending_trades"]) == 2
    assert d["pending_trade"] is not None

    # Removal
    removed = state.remove_pending_trade(101)
    assert removed == t1
    assert len(state.pending_trades) == 1
    assert state.get_pending_trade(101) is None
    assert state.get_pending_trade(102) == t2

    # Setting pending_trade = None clears all
    state.pending_trade = None
    assert len(state.pending_trades) == 0

@pytest.mark.asyncio
async def test_decide_trade_by_id_and_pair():
    from engine.risk_manager import risk_manager
    from engine.state import state
    from engine.trades import decide_trade

    risk_manager.max_trade_usdt = 100.0
    risk_manager.max_daily_spend_usdt = 500.0
    risk_manager.daily_spent = 0.0

    state.clear_pending_trades()
    state.usdt_balance = 1000.0
    state.prices["BTCUSDT"] = 64000.0
    state.prices["ETHUSDT"] = 3400.0
    state.prices["SOLUSDT"] = 150.0

    t1 = {"id": 201, "pair": "BTCUSDT", "action": "BUY", "price": 64000.0, "amount_usdt": 10.0, "amount_asset": 10.0/64000.0, "reason": "Test BTC"}
    t2 = {"id": 202, "pair": "ETHUSDT", "action": "BUY", "price": 3400.0, "amount_usdt": 10.0, "amount_asset": 10.0/3400.0, "reason": "Test ETH"}
    t3 = {"id": 203, "pair": "SOLUSDT", "action": "BUY", "price": 150.0, "amount_usdt": 10.0, "amount_asset": 10.0/150.0, "reason": "Test SOL"}

    state.add_pending_trade(t1)
    state.add_pending_trade(t2)
    state.add_pending_trade(t3)
    assert len(state.pending_trades) == 3

    # Approve ETH by trade_id
    res_eth = await decide_trade(approved=True, trade_id=202)
    assert res_eth["status"] == "approved"
    assert len(state.pending_trades) == 2
    assert 202 not in state.pending_trades
    assert 201 in state.pending_trades
    assert 203 in state.pending_trades

    # Reject SOL by pair
    res_sol = await decide_trade(approved=False, pair="SOLUSDT")
    assert res_sol["status"] == "rejected"
    assert len(state.pending_trades) == 1
    assert 203 not in state.pending_trades
    assert 201 in state.pending_trades # BTC still pending!

    # Approve remaining BTC
    res_btc = await decide_trade(approved=True)
    assert res_btc["status"] == "approved"
    assert len(state.pending_trades) == 0

@pytest.mark.asyncio
async def test_decide_all_trades_batch():
    from engine.risk_manager import risk_manager
    from engine.state import state
    from engine.trades import decide_all_trades

    risk_manager.max_trade_usdt = 100.0
    risk_manager.max_daily_spend_usdt = 500.0
    risk_manager.daily_spent = 0.0

    state.clear_pending_trades()
    state.usdt_balance = 1000.0
    state.prices["BTCUSDT"] = 64000.0
    state.prices["ETHUSDT"] = 3400.0

    t1 = {"id": 301, "pair": "BTCUSDT", "action": "BUY", "price": 64000.0, "amount_usdt": 10.0, "amount_asset": 10.0/64000.0, "reason": "Batch BTC"}
    t2 = {"id": 302, "pair": "ETHUSDT", "action": "BUY", "price": 3400.0, "amount_usdt": 10.0, "amount_asset": 10.0/3400.0, "reason": "Batch ETH"}

    state.add_pending_trade(t1)
    state.add_pending_trade(t2)
    assert len(state.pending_trades) == 2

    results = await decide_all_trades(approved=True)
    assert len(results) == 2
    assert all(r["status"] == "approved" for r in results)
    assert len(state.pending_trades) == 0

@pytest.mark.asyncio
async def test_binance_order_failure_aborts_without_executed_status():
    from engine.db import get_trade_history
    from engine.risk_manager import risk_manager
    from engine.trades import decide_trade

    state.is_active = True
    state.testnet = False
    state.usdt_balance = 500.0
    initial_daily_spent = risk_manager.daily_spent

    class MockFailingBinanceClient:
        async def create_order(self, **kwargs):
            raise RuntimeError("Binance API: Insufficient margin balance")

    state.binance_client = MockFailingBinanceClient()
    state.clear_pending_trades()

    trade_payload = {
        "id": 9991,
        "pair": "BTCUSDT",
        "action": "BUY",
        "amount_usdt": 50.0,
        "amount_asset": 50.0 / 65000.0,
        "price": 65000.0,
        "reason": "Test Failure Handling"
    }
    state.add_pending_trade(trade_payload)

    # Attempt to approve trade
    result = await decide_trade(approved=True, trade_id=9991)

    assert result["status"] == "error"
    assert "Binance API: Insufficient margin balance" in result["message"]
    assert state.usdt_balance == 500.0
    assert risk_manager.daily_spent == initial_daily_spent
    assert 9991 not in state.pending_trades

    # Check database: status should be FAILED, NOT EXECUTED
    history = await get_trade_history(limit=5, is_testnet=False)
    assert any("FAILED" in t["status"] for t in history if t["pair"] == "BTCUSDT")
    assert not any(t["status"] == "EXECUTED" and t["amount_usdt"] == 50.0 for t in history)

    state.testnet = True
    state.binance_client = None

@pytest.mark.asyncio
async def test_rolling_24h_spend_calculation():
    import time

    from engine.db import get_rolling_daily_spend, log_trade
    from engine.risk_manager import risk_manager

    now = time.time()
    # 1. BUY 2 hours ago (within 24h): $40
    await log_trade("BTCUSDT", "BUY", 40.0, 60000.0, "EXECUTED", "ORD_RECENT_BUY", is_testnet=True, timestamp=now - 7200)
    # 2. BUY 26 hours ago (older than 24h): $100 -> must NOT count
    await log_trade("BTCUSDT", "BUY", 100.0, 60000.0, "EXECUTED", "ORD_OLD_BUY", is_testnet=True, timestamp=now - (26 * 3600))
    # 3. SELL 1 hour ago (within 24h): $80 -> must NOT count towards spend
    await log_trade("BTCUSDT", "SELL", 80.0, 65000.0, "EXECUTED", "ORD_RECENT_SELL", is_testnet=True, timestamp=now - 3600)
    # 4. FAILED BUY 1 hour ago: $50 -> must NOT count
    await log_trade("BTCUSDT", "BUY", 50.0, 60000.0, "FAILED: Test", "ORD_FAILED_BUY", is_testnet=True, timestamp=now - 3600)

    spend_24h = await get_rolling_daily_spend(is_testnet=True)
    assert spend_24h >= 40.0

    await risk_manager.refresh_daily_spend(is_testnet=True)
    assert risk_manager.daily_spent == spend_24h

    # Verify record_spend only increments for BUY
    prev_spent = risk_manager.daily_spent
    risk_manager.record_spend(25.0, action="SELL")
    assert risk_manager.daily_spent == prev_spent

    risk_manager.record_spend(25.0, action="BUY")
    assert risk_manager.daily_spent == prev_spent + 25.0

@pytest.mark.asyncio
async def test_sync_binance_2026_trades_deduplication():
    import time

    from engine.db import get_trade_history
    from engine.trades import sync_binance_2026_trades

    uid = int(time.time() * 1000)
    oid1, tid1 = uid, uid + 1
    oid2, tid2 = uid + 2, uid + 3
    ref1 = f"{oid1}_{tid1}"

    class MockBinanceClient2026:
        async def get_my_trades(self, symbol, startTime, limit=500):
            if symbol == "SOLUSDT":
                return [
                    {
                        "id": tid1,
                        "orderId": oid1,
                        "isBuyer": True,
                        "price": "180.0",
                        "qty": "0.1",
                        "quoteQty": "18.0",
                        "time": 1767225600000
                    },
                    {
                        "id": tid2,
                        "orderId": oid2,
                        "isBuyer": False,
                        "price": "195.0",
                        "qty": "0.1",
                        "quoteQty": "19.5",
                        "time": 1767312000000
                    }
                ]
            return []

    mock_client = MockBinanceClient2026()

    # First sync: 2 trades imported
    res1 = await sync_binance_2026_trades(client=mock_client, pairs=["SOLUSDT"])
    assert res1["status"] == "ok"
    assert res1["imported"] == 2

    # Second sync: should be deduplicated (0 new imports)
    res2 = await sync_binance_2026_trades(client=mock_client, pairs=["SOLUSDT"])
    assert res2["status"] == "ok"
    assert res2["imported"] == 0

    history = await get_trade_history(limit=1000, is_testnet=state.testnet)
    sol_trades = [t for t in history if t["pair"] == "SOLUSDT" and ref1 in str(t.get("order_id", ""))]
    assert len(sol_trades) == 1
    assert sol_trades[0]["action"] == "BUY"
    assert sol_trades[0]["status"] == "EXECUTED"

@pytest.mark.asyncio
async def test_trade_timeout_watchdog_clock():
    import asyncio
    import time

    from engine.watchdogs import trade_timeout_watchdog

    state.clear_pending_trades()
    now = time.time()
    # Expired trade: created 700s ago with 600s timeout
    state.add_pending_trade({
        "id": 9995,
        "pair": "AVAXUSDT",
        "action": "BUY",
        "price": 30.0,
        "amount_usdt": 10.0,
        "timeout_sec": 600,
        "created_at": now - 700
    })
    # Active trade: created 50s ago with 600s timeout
    state.add_pending_trade({
        "id": 9996,
        "pair": "DOTUSDT",
        "action": "BUY",
        "price": 5.0,
        "amount_usdt": 10.0,
        "timeout_sec": 600,
        "created_at": now - 50
    })

    assert len(state.pending_trades) == 2

    task = asyncio.create_task(trade_timeout_watchdog())
    await asyncio.sleep(1.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert 9995 not in state.pending_trades
    assert 9996 in state.pending_trades
    assert state.pending_trades[9996]["timeout_sec"] < 600

@pytest.mark.asyncio
async def test_trade_history_chronological_sorting():
    import time

    from engine.db import get_trade_history, log_trade

    uid = int(time.time() * 1000)
    o1, o2, o3 = f"ORD_SORT_1_{uid}", f"ORD_SORT_2_{uid}", f"ORD_SORT_3_{uid}"
    base_time = time.time()
    await log_trade("BTCUSDT", "BUY", 100.0, 60000.0, "EXECUTED", o1, is_testnet=True, timestamp=base_time - 100)
    await log_trade("SOLUSDT", "BUY", 50.0, 150.0, "EXECUTED", o2, is_testnet=True, timestamp=base_time - 1000)
    await log_trade("ETHUSDT", "BUY", 75.0, 3000.0, "EXECUTED", o3, is_testnet=True, timestamp=base_time)

    history = await get_trade_history(limit=1000, is_testnet=True)
    sort_orders = [t["order_id"] for t in history if t["order_id"] in (o1, o2, o3)]
    assert sort_orders == [o3, o1, o2]

@pytest.mark.asyncio
async def test_order_exists_and_deduplicate_trade_history():
    import time

    from engine.db import deduplicate_trade_history, get_trade_history, log_trade, order_exists

    uid = int(time.time() * 1000)
    oid = f"TEST_DEDUP_{uid}"
    tid1 = f"{oid}_1"

    await log_trade("BTCUSDT", "BUY", 20.0, 60000.0, "EXECUTED", oid, is_testnet=True)
    assert await order_exists(oid, is_testnet=True) is True
    assert await order_exists(tid1, is_testnet=True) is True

    await log_trade("BTCUSDT", "BUY", 20.0, 60000.0, "EXECUTED", tid1, is_testnet=True)
    await log_trade("BTCUSDT", "BUY", 20.0, 60000.0, "EXECUTED", tid1, is_testnet=True)

    pruned = await deduplicate_trade_history(is_testnet=True)
    assert pruned >= 2

    history = await get_trade_history(limit=50, is_testnet=True)
    matching = [t for t in history if oid in str(t.get("order_id", ""))]
    assert len(matching) == 1
    assert matching[0]["order_id"] == tid1

@pytest.mark.asyncio
async def test_trade_signal_projection_integration():
    from engine.ai_analyst import fallback_trade_signal_analysis
    from main import state

    # Test fallback_trade_signal_analysis produces projection
    analysis = await fallback_trade_signal_analysis(
        pair="SOLUSDT",
        action="BUY",
        price=150.0,
        rsi=28.0,
        pct_b=0.15,
        reason="Oversold Dip",
        extra_context={"amount_usdt": 75.0}
    )
    assert "projection" in analysis
    assert analysis["projection"]["action"] == "BUY"
    assert analysis["projection"]["amount_usdt"] == 75.0

    # Test state.add_pending_trade automatically attaches deterministic projection
    t = {
        "pair": "SOLUSDT",
        "action": "SELL",
        "price": 180.0,
        "amount_asset": 0.5,
        "amount_usdt": 90.0,
        "reason": "TP hit"
    }
    state.cost_bases["SOLUSDT"] = 140.0
    tid = state.add_pending_trade(t)
    pending = state.get_pending_trade(tid)
    assert "projection" in pending
    assert pending["projection"]["outcome_type"] == "PROFIT"
    assert pending["projection"]["projected_pnl_usdt"] == 20.0  # (180 - 140) * 0.5
