import pytest
from httpx import ASGITransport, AsyncClient

from engine.db import init_db
from main import app, state

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_history_analyzer_fifo_reconciliation():
    from engine.db import log_trade
    from engine.history_analyzer import analyze_and_reconcile_history

    await init_db()

    # Create distinct test pair
    tpair = "TESTCOINUSDT"
    now = 1700000000.0

    import aiosqlite

    from engine.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM trade_history WHERE pair = ?", (tpair,))
        await db.commit()

    # 1. Buy 10 units at $10.0 ($100 USDT)
    await log_trade(tpair, "BUY", 100.0, 10.0, "EXECUTED", "ORD_T1", is_testnet=True, timestamp=now)
    # 2. Buy 10 units at $20.0 ($200 USDT)
    await log_trade(tpair, "BUY", 200.0, 20.0, "EXECUTED", "ORD_T2", is_testnet=True, timestamp=now + 10)
    # 3. Sell 5 units at $15.0 ($75 USDT -> matched against first buy at $10: cost=$50 -> profit = +$25.00 / +50%)
    await log_trade(tpair, "SELL", 75.0, 15.0, "EXECUTED", "ORD_T3", is_testnet=True, timestamp=now + 20, realized_pnl_usdt=0.0)
    # 4. Sell 10 units at $12.0 ($120 USDT -> matched against 5 units @ $10 ($50) + 5 units @ $20 ($100): cost=$150 -> loss = -$30.00 / -20%)
    await log_trade(tpair, "SELL", 120.0, 12.0, "EXECUTED", "ORD_T4", is_testnet=True, timestamp=now + 30, realized_pnl_usdt=0.0)

    analysis = await analyze_and_reconcile_history(is_testnet=True, update_db=True)
    assert tpair in analysis["assets"]

    m = analysis["assets"][tpair]
    assert m["total_buys"] == 2
    assert m["total_sells"] == 2
    assert m["closed_trades"] == 2
    assert m["wins"] == 1
    assert m["losses"] == 1
    assert m["win_rate"] == 50.0
    assert abs(m["net_realized_pnl_usdt"] - (-5.0)) < 0.1  # +25 - 30 = -5
    assert m["current_position_qty"] == 5.0
    assert m["current_cost_basis"] == 20.0

@pytest.mark.asyncio
async def test_history_analyzer_edge_cases():
    from engine.db import log_trade
    from engine.history_analyzer import analyze_and_reconcile_history

    epair = "EDGECOINUSDT"
    now = 1700050000.0

    import aiosqlite

    from engine.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM trade_history WHERE pair = ?", (epair,))
        await db.commit()

    # Trade with price <= 0 should be safely skipped
    await log_trade(epair, "BUY", 50.0, 0.0, "EXECUTED", "ORD_ZERO", is_testnet=True, timestamp=now)
    # Sell with no prior buy inventory
    await log_trade(epair, "SELL", 50.0, 10.0, "EXECUTED", "ORD_NOBUY", is_testnet=True, timestamp=now + 10)

    analysis = await analyze_and_reconcile_history(is_testnet=True, update_db=False)
    assert epair in analysis["assets"]
    assert analysis["assets"][epair]["total_sells"] == 1
