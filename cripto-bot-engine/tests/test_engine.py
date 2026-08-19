import pytest
from httpx import AsyncClient, ASGITransport
from main import app, state
from engine.db import init_db

@pytest.fixture(autouse=True)
async def reset_state():
    await init_db()
    state.is_active = False
    state.pending_trade = None
    state.usdt_balance = 1000.0

@pytest.mark.asyncio
async def test_get_state_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/state")
        assert response.status_code == 200
        data = response.json()
        assert "is_active" in data
        assert "usdt_balance" in data
        assert "prices" in data
        assert "favorite_pairs" in data

@pytest.mark.asyncio
async def test_bot_toggle_active():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/bot/toggle?active=true")
        assert response.status_code == 200
        assert response.json()["is_active"] is True
        assert state.is_active is True

@pytest.mark.asyncio
async def test_simulate_trade_signal():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/trade/simulate")
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
        await ac.post("/api/trade/simulate")
        assert state.pending_trade is not None

        # Approve trade
        response = await ac.post("/api/trade/decide?approved=true")
        assert response.status_code == 200
        assert response.json()["status"] == "approved"
        assert state.pending_trade is None

@pytest.mark.asyncio
async def test_trade_rejection_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create trade signal
        await ac.post("/api/trade/simulate")
        assert state.pending_trade is not None

        # Reject trade
        response = await ac.post("/api/trade/decide?approved=false")
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert state.pending_trade is None

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

def test_wilder_rsi_calculation():
    from engine.strategies import calculate_wilder_rsi

    # Less than 15 data points returns neutral 50.0
    assert calculate_wilder_rsi([100.0, 101.0, 102.0]) == 50.0

    # 15 monotonically increasing prices -> RSI = 100.0
    up_prices = [100.0 + i for i in range(20)]
    assert calculate_wilder_rsi(up_prices) == 100.0

    # 15 monotonically decreasing prices -> RSI near 0
    down_prices = [100.0 - i for i in range(20)]
    rsi_down = calculate_wilder_rsi(down_prices)
    assert rsi_down < 5.0

def test_bollinger_bands_calculation():
    from engine.strategies import calculate_bollinger_bands

    # Constant prices -> std_dev = 0, percent_b = 0.5
    flat_prices = [50.0] * 25
    sma, upper, lower, pct_b = calculate_bollinger_bands(flat_prices, 20)
    assert sma == 50.0
    assert upper == 50.0
    assert lower == 50.0
    assert pct_b == 0.5

    # Rising prices -> current price near upper band (%b > 0.8)
    rising_prices = [100.0 + (i * 2) for i in range(30)]
    sma, upper, lower, pct_b = calculate_bollinger_bands(rising_prices, 20)
    assert sma < rising_prices[-1]
    assert pct_b > 0.8

def test_format_and_validate_order():
    from engine.trades import format_and_validate_order
    from engine.state import state

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

def test_trailing_stop_loss():
    from engine.strategies import TPSLStrategy

    tsl = TPSLStrategy(trailing_enabled=True, trailing_activation_percent=3.0, trailing_delta_percent=1.5, sl_percent=3.0)
    portfolio = {"BTC": 0.01}
    cost_bases = {"BTCUSDT": 60000.0} # Cost basis = $60,000

    # 1. Price is +1.6% ($61,000) -> Below 3% activation threshold -> No signal
    sig = tsl.evaluate_tpsl({"BTCUSDT": 61000.0}, portfolio, cost_bases)
    assert sig is None

    # 2. Price climbs to +5% ($63,000) -> Activates TSL, records peak $63,000 -> No sell signal yet (price is at peak)
    sig = tsl.evaluate_tpsl({"BTCUSDT": 63000.0}, portfolio, cost_bases)
    assert sig is None
    assert tsl.peak_prices["BTCUSDT"] == 63000.0

    # 3. Price climbs further to $65,000 -> Peak updates to $65,000 -> No sell signal yet
    sig = tsl.evaluate_tpsl({"BTCUSDT": 65000.0}, portfolio, cost_bases)
    assert sig is None
    assert tsl.peak_prices["BTCUSDT"] == 65000.0

    # 4. Small pullback to $64,500 (0.76% drop < 1.5% delta) -> No signal
    sig = tsl.evaluate_tpsl({"BTCUSDT": 64500.0}, portfolio, cost_bases)
    assert sig is None

    # 5. Full pullback to $63,800 (1.84% drop >= 1.5% delta from $65,000 peak) -> Trigger Trailing SELL!
    sig = tsl.evaluate_tpsl({"BTCUSDT": 63800.0}, portfolio, cost_bases)
    assert sig is not None
    assert sig["action"] == "SELL"
    assert sig["pair"] == "BTCUSDT"
    assert "Trailing Stop" in sig["reason"]

def test_state_indicators():
    from engine.state import state

    state.favorite_pairs = ["BTCUSDT", "ETHUSDT"]
    data = state.to_dict()
    assert "indicators" in data
    assert "BTCUSDT" in data["indicators"]
    assert "rsi" in data["indicators"]["BTCUSDT"]
    assert "pct_b" in data["indicators"]["BTCUSDT"]
    assert "trailing_enabled" in data["strategies"]

@pytest.mark.asyncio
async def test_trade_history_and_pnl():
    from engine.db import init_db, log_trade, get_trade_history, get_pnl_summary

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
async def test_get_trades_endpoint():
    from httpx import AsyncClient, ASGITransport
    from main import app
    from engine.state import state

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trades", headers={"X-Auth-PIN": state.auth_pin})
        assert resp.status_code == 200
        data = resp.json()
        assert "trades" in data
        assert "summary" in data
        assert "total_pnl_usdt" in data["summary"]

@pytest.mark.asyncio
async def test_gemini_analyst_fallback():
    from engine.ai_analyst import analyze_trade_signal, ask_gemini
    
    # 1. When no key provided -> returns None gracefully
    analysis = await analyze_trade_signal(
        pair="BTCUSDT", action="BUY", price=62000.0, rsi=25.0, pct_b=0.1, 
        reason="RSI Oversold", price_history=[63000.0, 62500.0, 62000.0], api_key=""
    )
    assert analysis is None

    # 2. ask_gemini fallback message
    ans = await ask_gemini("What is RSI?", {}, api_key="")
    assert "Google AI Studio API key not configured" in ans

@pytest.mark.asyncio
async def test_chart_generator():
    from engine.chart_generator import generate_candlestick_chart, calculate_rsi_series
    import io

    # Simulated klines
    fake_klines = []
    base_price = 60000.0
    for i in range(30):
        fake_klines.append({
            "time": 1700000000 + i * 3600,
            "open": base_price + i * 10,
            "high": base_price + i * 10 + 50,
            "low": base_price + i * 10 - 30,
            "close": base_price + i * 10 + 20,
            "volume": 100.0
        })

    buf = await generate_candlestick_chart("BTCUSDT", fake_klines, interval="1h")
    assert isinstance(buf, io.BytesIO)
    bytes_data = buf.getvalue()
    assert len(bytes_data) > 100 # Valid PNG image bytes
    assert bytes_data[:8] == b'\x89PNG\r\n\x1a\n' # PNG file signature

    # RSI series calculation test
    closes = [k["close"] for k in fake_klines]
    rsi = calculate_rsi_series(closes, period=14)
    assert len(rsi) == len(closes)
    assert 0.0 <= rsi[-1] <= 100.0

def test_gemini_state_and_config():
    from engine.state import state
    d = state.to_dict()
    assert "gemini_model" in d
    assert "has_gemini" in d
    assert "available_gemini_models" in d
    assert d["gemini_model"] == "gemini-2.5-flash"

@pytest.mark.asyncio
async def test_clear_trade_history():
    from engine.db import log_trade, clear_trade_history, get_trade_history
    from httpx import AsyncClient, ASGITransport
    from main import app
    from engine.state import state

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
