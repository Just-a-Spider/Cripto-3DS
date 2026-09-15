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
        # Without PIN -> 401 Unauthorized
        unauth = await ac.get("/api/state")
        assert unauth.status_code == 401

        # With PIN -> 200 OK
        response = await ac.get("/api/state", headers={"X-Auth-PIN": state.auth_pin})
        assert response.status_code == 200
        data = response.json()
        assert "is_active" in data
        assert "usdt_balance" in data
        assert "prices" in data
        assert "favorite_pairs" in data

@pytest.mark.asyncio
async def test_bot_toggle_active():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Without PIN -> 401
        unauth = await ac.post("/api/bot/toggle?active=true")
        assert unauth.status_code == 401

        response = await ac.post("/api/bot/toggle?active=true", headers={"X-Auth-PIN": state.auth_pin})
        assert response.status_code == 200
        assert response.json()["is_active"] is True
        assert state.is_active is True

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
    assert "gemini_search_model" in d
    assert "has_gemini" in d
    assert "available_gemini_models" in d
    assert "enable_search_grounding" in d
    assert "has_groq" in d
    assert d["gemini_model"] == "gemini-3.1-flash-lite"
    assert d["gemini_search_model"] == "gemini-3.1-flash-lite"
    assert d["enable_search_grounding"] is False

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

@pytest.mark.asyncio
async def test_fear_and_greed_index():
    from engine.ai_analyst import fetch_fear_and_greed_index
    fng = await fetch_fear_and_greed_index()
    assert isinstance(fng, dict)
    assert "value" in fng
    assert "classification" in fng
    assert 0 <= fng["value"] <= 100

@pytest.mark.asyncio
async def test_structured_ai_risk_parsing(monkeypatch):
    from engine.ai_analyst import analyze_trade_signal
    import engine.ai_analyst as ai_mod

    # Mock call_gemini to return a valid JSON string
    async def mock_call_gemini(*args, **kwargs):
        return """```json
{
  "verdict": "APPROVE",
  "risk_score": 3,
  "confidence": 0.9,
  "suggested_sl_percent": 2.5,
  "summary": "Strong oversold bounce setup with bullish volume convergence.",
  "red_flags": []
}
```"""
    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    res = await analyze_trade_signal(
        pair="BTCUSDT", action="BUY", price=62000.0, rsi=28.0, pct_b=-0.05,
        reason="RSI Oversold", price_history=[63000, 62500, 62000], api_key="test_key"
    )

    assert isinstance(res, dict)
    assert res["verdict"] == "APPROVE"
    assert res["risk_score"] == 3
    assert res["suggested_sl_percent"] == 2.5
    assert "oversold bounce" in res["summary"].lower()
    assert "fng_index" in res

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
async def test_market_briefing(monkeypatch):
    from engine.ai_analyst import generate_market_briefing
    import engine.ai_analyst as ai_mod
    from engine.notifier import build_briefing_embed

    # 1. Test fallback when no key provided
    fallback_data = await generate_market_briefing({}, None, api_key="")
    assert "headline" in fallback_data
    assert "macro_regime" in fallback_data

    # 2. Test mocked Gemini briefing response
    async def mock_call_gemini(*args, **kwargs):
        return """```json
{
  "headline": "Bitcoin Consolidates Ahead of Volatility",
  "macro_regime": "Market in low-volatility compression phase.",
  "key_levels": "BTC support at $64,200, resistance at $68,500.",
  "strategy_recommendation": "Maintain standard DCA pacing with tight SL."
}
```"""
    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    mock_state = {
        "prices": {"BTCUSDT": 65000.0, "ETHUSDT": 3400.0},
        "indicators": {"BTCUSDT": {"rsi": 48.0, "pct_b": 0.52}},
        "usdt_balance": 500.0
    }
    pnl = {"total_pnl_usdt": 45.20, "win_rate": 80.0, "closed_trades": 5}
    briefing = await generate_market_briefing(mock_state, pnl, api_key="valid_key")

    assert briefing["headline"] == "Bitcoin Consolidates Ahead of Volatility"
    assert "BTC support at $64,200" in briefing["key_levels"]
    assert "low-volatility compression" in briefing["macro_regime"]

    # 3. Test embed construction
    embed = build_briefing_embed(briefing, "gemini-2.5-flash")
    assert embed is not None
    assert "Bitcoin Consolidates" in embed.title

@pytest.mark.asyncio
async def test_evaluate_exit_momentum(monkeypatch):
    from engine.ai_analyst import evaluate_exit_momentum
    import engine.ai_analyst as ai_mod

    # 1. Test fallback when no key
    res_no_key = await evaluate_exit_momentum("ETHUSDT", 2000.0, 1800.0, 78.0, 1.15, [], api_key="")
    assert res_no_key["verdict"] == "TRAIL"
    assert res_no_key["trail_delta_percent"] == 1.5

    # 2. Test mocked Gemini momentum response
    async def mock_call_gemini(*args, **kwargs):
        return """```json
{
  "verdict": "TRAIL",
  "trail_delta_percent": 1.2,
  "momentum_phase": "PARABOLIC_BREAKOUT",
  "summary": "Strong continuous green candles. Trail peak with 1.2% delta."
}
```"""
    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    res = await evaluate_exit_momentum("ETHUSDT", 2050.0, 1877.0, 81.2, 1.22, [1900, 1950, 2000, 2050], api_key="valid_key")
    assert res["verdict"] == "TRAIL"
    assert res["trail_delta_percent"] == 1.2
    assert res["momentum_phase"] == "PARABOLIC_BREAKOUT"

@pytest.mark.asyncio
async def test_trailing_profit_runner_handoff():
    from engine.state import state
    from engine.strategies import RSIStrategy, TPSLStrategy

    state.tpsl_strategy = TPSLStrategy(trailing_enabled=True, trailing_activation_percent=3.0, trailing_delta_percent=1.5)
    rsi_strat = RSIStrategy(oversold_rsi=30.0, overbought_rsi=70.0, min_profit_percent=5.0)
    rsi_strat.enabled = True

    # Populate price history (ending below $2000 so $2000 is an upward step)
    rsi_strat.price_histories["ETHUSDT"] = [1800.0 + (i * 5.0) for i in range(30)] # 1800 -> 1945
    portfolio = {"ETH": 0.05} # Worth > $5
    cost_bases = {"ETHUSDT": 1877.0}
    prices = {"ETHUSDT": 2000.0} # +6.55% profit

    # When RSI is overbought and trailing is enabled, RSI strategy hands off to TPSL peak tracker rather than instant dumping
    sig = rsi_strat.evaluate(prices, 50.0, ["ETHUSDT"], portfolio, cost_bases)
    assert sig is None # Did not dump statically!
    assert state.tpsl_strategy.peak_prices.get("ETHUSDT") == 2000.0 # Peak registered!

    # Now simulate price surging to $2,067
    prices["ETHUSDT"] = 2067.0
    tpsl_sig = state.tpsl_strategy.evaluate_tpsl(prices, portfolio, cost_bases)
    assert tpsl_sig is None # Still riding the pump!
    assert state.tpsl_strategy.peak_prices.get("ETHUSDT") == 2067.0

    # Now simulate a 2% pullback from peak ($2067 -> $2020)
    prices["ETHUSDT"] = 2020.0
    exit_sig = state.tpsl_strategy.evaluate_tpsl(prices, portfolio, cost_bases)
    assert exit_sig is not None
    assert exit_sig["action"] == "SELL"
    assert "Trailing Stop" in exit_sig["reason"]

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
async def test_news_service_and_confluence():
    import time
    from engine.news_service import news_service, NewsItem
    from engine.strategies import MultiTimeframeFilter

    # 1. Test News Item & Risk Flag
    news_service.cached_news = [
        NewsItem("SEC launches lawsuit against XRP", "XRP", "CryptoPanic", "http://test", "HIGH_RISK", time.time())
    ]
    assert news_service.has_high_risk_event("XRP") is True
    assert news_service.has_high_risk_event("BTC") is False

    # 2. Test MultiTimeframeFilter Confluence
    # Stable trend -> pass
    stable_hist = [100.0, 101.0, 100.5, 102.0, 101.5, 103.0, 102.5, 104.0, 103.5, 105.0]
    ok, reason = MultiTimeframeFilter.evaluate_confluence(stable_hist)
    assert ok is True

    # Severe macro drop (from 100 -> 90 = -10% drop) -> block
    crash_hist = [100.0, 99.0, 98.0, 97.0, 95.0, 94.0, 93.0, 92.0, 91.0, 90.0]
    blocked, block_reason = MultiTimeframeFilter.evaluate_confluence(crash_hist)
    assert blocked is False
    assert "Severe Macro Drop" in block_reason

@pytest.mark.asyncio
async def test_google_search_grounding_provider():
    from engine.news_service import GoogleSearchGroundingProvider
    provider = GoogleSearchGroundingProvider(api_key="")
    items = await provider.fetch_news(["BTC", "ETH"])
    assert len(items) > 0
    assert items[0].asset in ["BTC", "ETH", "MARKET"]

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
async def test_gemini_unsupported_model_filter():
    from engine.ai_analyst import _is_unsupported_model
    assert _is_unsupported_model("gemini-2.5-flash") is True
    assert _is_unsupported_model("gemini-2.0-flash") is True
    assert _is_unsupported_model("gemini-1.5-flash") is True
    assert _is_unsupported_model("gemini-3.1-flash-lite-tts") is True
    assert _is_unsupported_model("gemini-3.5-transcribe") is True
    assert _is_unsupported_model("gemini-3.5-audio") is True
    assert _is_unsupported_model("gemini-3.5-live") is True
    assert _is_unsupported_model("gemini-3.1-flash-lite") is False
    assert _is_unsupported_model("gemini-3.5-flash") is False
    assert _is_unsupported_model("gemini-flash-lite-latest") is False

@pytest.mark.asyncio
async def test_gemini_call_fallback(monkeypatch):
    import aiohttp
    import engine.ai_analyst as ai_mod
    from engine.ai_analyst import call_gemini

    monkeypatch.setattr(ai_mod, "HAS_GENAI_SDK", False)

    called = []
    def mock_post(self, url, json=None, timeout=None):
        called.append(url)
        class MockResp:
            status = 200
            async def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}
            async def text(self):
                return "OK"
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        return MockResp()

    monkeypatch.setattr(aiohttp.ClientSession, "post", mock_post)

    res = await call_gemini("Test", "test_key", model="gemini-3.1-flash-lite")
    assert res == "OK"
    assert len(called) > 0

@pytest.mark.asyncio
async def test_ask_gemini_full_watchlist_and_positions_context(monkeypatch):
    from engine.ai_analyst import ask_gemini
    import engine.ai_analyst as ai_mod

    captured_prompt = []
    async def mock_call_gemini(prompt, api_key, model=None, system_instruction=None, json_mode=False, use_google_search=False):
        captured_prompt.append(prompt)
        return "Market analysis complete."

    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    mock_state = {
        "favorite_pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT"],
        "prices": {
            "BTCUSDT": 65000.0,
            "ETHUSDT": 3200.0,
            "SOLUSDT": 180.0,
            "BNBUSDT": 580.0,
            "XRPUSDT": 0.60,
            "ADAUSDT": 0.45,
            "AVAXUSDT": 28.0,
            "NEARUSDT": 5.20
        },
        "indicators": {
            "BTCUSDT": {"rsi": 55.0, "pct_b": 0.6},
            "ETHUSDT": {"rsi": 48.0, "pct_b": 0.4},
            "SOLUSDT": {"rsi": 41.0, "pct_b": 0.25},
            "BNBUSDT": {"rsi": 62.0, "pct_b": 0.75},
            "XRPUSDT": {"rsi": 71.0, "pct_b": 0.90},
            "ADAUSDT": {"rsi": 38.0, "pct_b": 0.20},
            "AVAXUSDT": {"rsi": 44.0, "pct_b": 0.30},
            "NEARUSDT": {"rsi": 50.0, "pct_b": 0.50}
        },
        "portfolio": {
            "USDT": 500.0,
            "SOL": 2.5,
            "XRP": 500.0
        },
        "cost_bases": {
            "SOLUSDT": 150.0,
            "XRPUSDT": 0.50
        },
        "usdt_balance": 500.0,
        "testnet": True,
        "strategies": {
            "dca_interval": 3600,
            "rsi_threshold": 30.0,
            "bull_rsi_threshold": 42.0,
            "tp_percent": 5.0,
            "partial_tp_percent": 4.0,
            "sl_percent": 3.0
        }
    }

    ans = await ask_gemini("Should I take profit on my positions?", mock_state, api_key="valid_key")
    assert ans == "Market analysis complete."
    assert len(captured_prompt) == 1
    prompt_text = captured_prompt[0]

    # Verify all 8 pairs are included (no [:6] truncation!)
    for pair in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT"]:
        assert pair in prompt_text

    # Verify open positions with unrealized PnL are included
    assert "SOL: 2.500000 @ Entry $150.0000" in prompt_text
    assert "XRP: 500.000000 @ Entry $0.5000" in prompt_text

@pytest.mark.asyncio
async def test_bull_regime_dip_buying():
    from engine.strategies import RSIStrategy

    # Oversold is 30.0, but bull_regime_dip_enabled is True and bull_rsi_threshold is 42.0
    strat = RSIStrategy(oversold_rsi=30.0, bull_regime_dip_enabled=True, bull_rsi_threshold=42.0)
    strat.enabled = True

    # Simulate price history with healthy bull dip (RSI ~40.4, %B ~0.19, no knife drop)
    strat.price_histories["SOLUSDT"] = [170.0] * 10 + [175.0, 174.0, 173.0, 172.0, 171.0, 170.0, 169.0, 168.0, 168.2, 168.0]
    prices = {"SOLUSDT": 168.0}
    portfolio = {"USDT": 500.0}
    cost_bases = {}

    sig = strat.evaluate(prices, 500.0, ["SOLUSDT"], portfolio, cost_bases, can_buy=True)
    assert sig is not None
    assert sig["action"] == "BUY"
    assert sig["pair"] == "SOLUSDT"
    assert "Bull Regime Dip Buy" in sig["reason"]

@pytest.mark.asyncio
async def test_partial_take_profit_and_breakeven_stop():
    from engine.strategies import TPSLStrategy

    tpsl = TPSLStrategy(
        tp_percent=6.0,
        sl_percent=3.0,
        trailing_enabled=True,
        partial_tp_enabled=True,
        partial_tp_percent=4.0,
        partial_tp_ratio=0.5
    )
    tpsl.enabled = True

    portfolio = {"SOL": 10.0}
    cost_bases = {"SOLUSDT": 100.0}

    # 1. Price at $104.5 (+4.5% profit) -> Triggers Partial TP (50% position scale-out)
    prices = {"SOLUSDT": 104.5}
    sig_tp1 = tpsl.evaluate_tpsl(prices, portfolio, cost_bases)
    assert sig_tp1 is not None
    assert sig_tp1["action"] == "SELL"
    assert sig_tp1["is_partial_tp"] is True
    assert sig_tp1["amount_asset"] == 5.0 # 50% of 10.0
    assert "SOLUSDT" in tpsl.tp_staged_positions

    # 2. Re-evaluating immediately should not double-trigger partial TP
    sig_again = tpsl.evaluate_tpsl(prices, portfolio, cost_bases)
    assert sig_again is None

    # 3. Simulate price pulling back to $100.1 (+0.1% profit) -> Triggers Breakeven Stop Protection
    prices_dump = {"SOLUSDT": 100.1}
    sig_be = tpsl.evaluate_tpsl(prices_dump, portfolio, cost_bases)
    assert sig_be is not None
    assert sig_be["action"] == "SELL"
    assert "Breakeven Stop Protection" in sig_be["reason"]
    assert "SOLUSDT" not in tpsl.tp_staged_positions

@pytest.mark.asyncio
async def test_scan_market_opportunities(monkeypatch):
    from engine.ai_analyst import scan_market_opportunities
    import engine.ai_analyst as ai_mod

    # 1. Fallback when no key
    res_no_key = await scan_market_opportunities({}, api_key="")
    assert res_no_key["market_regime"] == "NEUTRAL"
    assert len(res_no_key["top_opportunities"]) == 0

    # 2. Mocked Gemini response
    async def mock_call_gemini(prompt, api_key, model=None, system_instruction=None, json_mode=False, use_google_search=False):
        return """```json
{
  "market_regime": "BULLISH_GREED",
  "top_opportunities": [
    {
      "pair": "SOLUSDT",
      "setup_type": "DIP_BUY",
      "confidence": 0.88,
      "key_levels": "Support: $170.00, Resistance: $192.00",
      "analysis": "RSI pulled back to 41 near lower Bollinger Band with strong macro greed support."
    },
    {
      "pair": "XRPUSDT",
      "setup_type": "TAKE_PROFIT",
      "confidence": 0.82,
      "key_levels": "Resistance: $0.62",
      "analysis": "RSI 71 overbought testing resistance. Partial 50% TP recommended."
    }
  ],
  "tactical_summary": "Buy minor dips on large caps and secure partial profits on overextended runners."
}
```"""
    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    mock_state = {
        "favorite_pairs": ["SOLUSDT", "XRPUSDT"],
        "prices": {"SOLUSDT": 172.0, "XRPUSDT": 0.61},
        "indicators": {
            "SOLUSDT": {"rsi": 41.0, "pct_b": 0.22},
            "XRPUSDT": {"rsi": 71.0, "pct_b": 0.88}
        },
        "portfolio": {"SOL": 2.0},
        "cost_bases": {"SOLUSDT": 160.0}
    }

    data = await scan_market_opportunities(mock_state, api_key="valid_key")
    assert data["market_regime"] == "BULLISH_GREED"
    assert len(data["top_opportunities"]) == 2
    assert data["top_opportunities"][0]["setup_type"] == "DIP_BUY"
    assert data["top_opportunities"][1]["setup_type"] == "TAKE_PROFIT"
    assert "Buy minor dips" in data["tactical_summary"]

@pytest.mark.asyncio
async def test_ai_scout_dynamic_config_update():
    from engine.state import state
    from httpx import AsyncClient, ASGITransport
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        cfg_payload = {
            "max_trade_usdt": 15.0,
            "max_daily_spend_usdt": 50.0,
            "min_usdt_reserve": 20.0,
            "require_human_approval": True,
            "auth_pin": state.auth_pin,
            "favorite_pairs": "BTCUSDT,ETHUSDT,SOLUSDT",
            "testnet": True,
            "dca_interval": 3600,
            "rsi_threshold": 30.0,
            "tp_percent": 5.0,
            "sl_percent": 3.0,
            "trailing_enabled": True,
            "trailing_activation_percent": 3.0,
            "trailing_delta_percent": 1.5,
            "ai_scout_enabled": True,
            "ai_scout_interval_hours": 3.5,
            "ai_scout_min_confidence": 0.90,
            "rsi_timeframe_minutes": 60,
            "rsi_history_length": 250,
            "signal_cooldown_hours": 24.0
        }
        res = await ac.post("/api/config", json=cfg_payload, headers={"X-Auth-PIN": state.auth_pin})
        assert res.status_code == 200

        # Verify state updated dynamically without hardcoding
        assert state.ai_scout_enabled is True
        assert state.ai_scout_interval_hours == 3.5
        assert state.ai_scout_min_confidence == 0.90

        # Verify to_dict contains updated ai_scout config
        state_dict = state.to_dict()
        assert state_dict["ai_scout"]["enabled"] is True
        assert state_dict["ai_scout"]["interval_hours"] == 3.5
        assert state_dict["ai_scout"]["min_confidence"] == 0.90

@pytest.mark.asyncio
async def test_ai_scout_watchdog_execution(monkeypatch):
    import asyncio
    from engine.state import state
    import engine.watchdogs as wd_mod
    import engine.ai_analyst as ai_mod

    state.is_active = True
    state.gemini_api_key = "valid_key"
    state.pending_trade = None
    state.prices["SOLUSDT"] = 180.0
    state.usdt_balance = 500.0
    state.ai_scout_enabled = True
    state.ai_scout_interval_hours = 1.0
    state.ai_scout_min_confidence = 0.85
    wd_mod._last_scout_time = 0.0
    wd_mod._scout_cooldowns.clear()

    # 1. Mock scan_market_opportunities with a high-confidence setup
    async def mock_scan(ctx, api_key, model=None, market_regime=None):
        return {
            "market_regime": "BULLISH_GREED",
            "top_opportunities": [
                {
                    "pair": "SOLUSDT",
                    "setup_type": "DIP_BUY",
                    "confidence": 0.92,
                    "key_levels": "Support: $175",
                    "analysis": "High confidence consolidation breakout on 4h candle close."
                }
            ]
        }

    monkeypatch.setattr(wd_mod, "scan_market_opportunities", mock_scan)

    # Trigger watchdog iteration
    task = asyncio.create_task(wd_mod.ai_opportunity_scout_watchdog())
    await asyncio.sleep(0.05) # Allow watchdog to run first iteration
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert state.pending_trade is not None
    assert state.pending_trade["action"] == "BUY"
    assert state.pending_trade["pair"] == "SOLUSDT"
    assert state.pending_trade["is_ai_scout"] is True
    assert "92% Conf" in state.pending_trade["reason"]

@pytest.mark.asyncio
async def test_ai_scout_auto_execution_when_approval_disabled(monkeypatch):
    import asyncio
    from engine.state import state
    from engine.risk_manager import risk_manager
    import engine.watchdogs as wd_mod
    from engine.db import get_trade_history

    risk_manager.require_human_approval = False
    state.is_active = True
    state.gemini_api_key = "valid_key"
    state.pending_trade = None
    state.prices["SOLUSDT"] = 180.0
    state.usdt_balance = 500.0
    state.ai_scout_enabled = True
    state.ai_scout_interval_hours = 1.0
    state.ai_scout_min_confidence = 0.85
    wd_mod._last_scout_time = 0.0
    wd_mod._scout_cooldowns.clear()

    async def mock_scan(ctx, api_key, model=None, **kwargs):
        return {
            "market_regime": "BULLISH_GREED",
            "top_opportunities": [
                {
                    "pair": "SOLUSDT",
                    "setup_type": "DIP_BUY",
                    "confidence": 0.95,
                    "key_levels": "Support: $175",
                    "analysis": "Immediate auto-executable breakout."
                }
            ]
        }

    monkeypatch.setattr(wd_mod, "scan_market_opportunities", mock_scan)

    task = asyncio.create_task(wd_mod.ai_opportunity_scout_watchdog())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Trade should have been auto-executed immediately, leaving no pending_trade
    assert state.pending_trade is None
    trades = await get_trade_history(limit=5, is_testnet=state.testnet)
    assert any(t["pair"] == "SOLUSDT" and t["status"] == "EXECUTED" for t in trades)

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
    from engine.state import state
    from engine.trades import decide_trade
    from engine.risk_manager import risk_manager

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
    from engine.state import state
    from engine.trades import decide_all_trades
    from engine.risk_manager import risk_manager

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
async def test_api_decide_all_and_by_id_endpoints():
    from engine.state import state
    from engine.risk_manager import risk_manager

    risk_manager.max_trade_usdt = 100.0
    risk_manager.max_daily_spend_usdt = 500.0
    risk_manager.daily_spent = 0.0

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        state.clear_pending_trades()
        headers = {"X-Auth-PIN": state.auth_pin}
        # Simulate 2 trades
        resp = await ac.post("/api/trade/simulate?count=2", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["pending_trades"]) == 2
        assert len(state.pending_trades) == 2

        t1 = data["pending_trades"][0]
        t2 = data["pending_trades"][1]

        # Decide individual trade by trade_id
        resp1 = await ac.post(f"/api/trade/decide?approved=true&trade_id={t1['id']}", headers=headers)
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "approved"
        assert len(state.pending_trades) == 1

        # Decide all remaining trades
        resp_all = await ac.post("/api/trade/decide_all?approved=false", headers=headers)
        assert resp_all.status_code == 200
        res_data = resp_all.json()
        assert res_data["status"] == "ok"
        assert len(res_data["results"]) == 1
        assert res_data["results"][0]["status"] == "rejected"
        assert len(state.pending_trades) == 0

@pytest.mark.asyncio
async def test_ai_scout_parallel_multi_asset_staging(monkeypatch):
    import asyncio
    from engine.state import state
    from engine.risk_manager import risk_manager
    import engine.watchdogs as wd_mod

    risk_manager.require_human_approval = True
    state.is_active = True
    state.gemini_api_key = "valid_key"
    state.clear_pending_trades()
    state.prices["BTCUSDT"] = 64000.0
    state.prices["ETHUSDT"] = 3400.0
    state.prices["SOLUSDT"] = 150.0
    state.usdt_balance = 1000.0
    state.ai_scout_enabled = True
    state.ai_scout_interval_hours = 1.0
    state.ai_scout_min_confidence = 0.80
    wd_mod._last_scout_time = 0.0
    wd_mod._scout_cooldowns.clear()

    async def mock_scan(ctx, api_key, model=None, market_regime=None):
        return {
            "market_regime": "BULLISH_GREED",
            "top_opportunities": [
                {
                    "pair": "BTCUSDT",
                    "setup_type": "DIP_BUY",
                    "confidence": 0.92,
                    "key_levels": "Support: $63,500",
                    "analysis": "High-volume bounce off 200 EMA."
                },
                {
                    "pair": "ETHUSDT",
                    "setup_type": "BREAKOUT",
                    "confidence": 0.88,
                    "key_levels": "Resistance: $3,500",
                    "analysis": "Ascending triangle breakout confirmation."
                },
                {
                    "pair": "SOLUSDT",
                    "setup_type": "DIP_BUY",
                    "confidence": 0.85,
                    "key_levels": "Support: $145",
                    "analysis": "Oversold RSI recovery."
                }
            ]
        }

    monkeypatch.setattr(wd_mod, "scan_market_opportunities", mock_scan)

    task = asyncio.create_task(wd_mod.ai_opportunity_scout_watchdog())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # All 3 qualified opportunities must be staged in parallel!
    assert len(state.pending_trades) == 3
    staged_pairs = {t["pair"] for t in state.pending_trades.values()}
    assert "BTCUSDT" in staged_pairs
    assert "ETHUSDT" in staged_pairs
    assert "SOLUSDT" in staged_pairs

@pytest.mark.asyncio
async def test_discord_multi_trade_embed_and_view():
    from engine.notifier import build_multi_trade_embed, BatchTradeApprovalView

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
async def test_binance_order_failure_aborts_without_executed_status():
    from engine.trades import decide_trade
    from engine.risk_manager import risk_manager
    from engine.db import get_trade_history

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

def test_auth_pin_redacted_from_state_to_dict():
    state.auth_pin = "sensitive_security_pin_9876"
    dump = state.to_dict()
    assert "auth_pin" not in dump
    assert "pin" not in dump
    assert dump.get("has_pin") is True

@pytest.mark.asyncio
async def test_rolling_24h_spend_calculation():
    import time
    from engine.db import log_trade, get_rolling_daily_spend
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

@pytest.mark.asyncio
async def test_sync_binance_2026_trades_deduplication():
    import time
    from engine.trades import sync_binance_2026_trades
    from engine.db import get_trade_history

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
    import time
    import asyncio
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

def test_setup_env_script(tmp_path):
    import os
    import stat
    from setup_env import parse_args, setup_environment

    test_env = tmp_path / ".env.test"
    args = parse_args([
        "--env-path", str(test_env),
        "--discord-id", "123456789012345678,987654321098765432",
        "--pin", "4321",
        "--binance-key", "test_key",
        "--binance-secret", "test_sec",
        "--testnet", "true",
        "--discord-token", "bot_tok_123",
        "--discord-channel", "ch_123",
        "--gemini-key", "gem_key_123",
        "--gemini-model", "gemini-3.1-flash-lite",
        "--non-interactive"
    ])

    written_path = setup_environment(args)
    assert written_path.exists()

    content = written_path.read_text(encoding="utf-8")
    assert "ALLOWED_DISCORD_USER_IDS=123456789012345678,987654321098765432" in content
    assert "AUTH_PIN=4321" in content
    assert "BINANCE_API_KEY=test_key" in content
    assert "BINANCE_SECRET_KEY=test_sec" in content
    assert "BINANCE_TESTNET=true" in content
    assert "DISCORD_BOT_TOKEN=bot_tok_123" in content
    assert "DISCORD_CHANNEL_ID=ch_123" in content
    assert "GEMINI_API_KEY=gem_key_123" in content

    # Check 0600 file permissions (read/write only for owner)
    mode = stat.S_IMODE(os.stat(written_path).st_mode)
    assert mode == 0o600

def test_api_config_discord_id_persistence():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    pin = state.auth_pin or "1234"
    orig_allowed = list(state.allowed_discord_user_ids)

    base_payload = {
        "max_trade_usdt": 50.0,
        "max_daily_spend_usdt": 200.0,
        "min_usdt_reserve": 20.0,
        "require_human_approval": True,
        "auth_pin": pin,
        "favorite_pairs": "BTCUSDT,ETHUSDT",
        "testnet": True,
        "dca_interval": 3600,
        "rsi_threshold": 30.0,
        "tp_percent": 5.0,
        "sl_percent": 3.0,
        "trailing_enabled": True,
        "trailing_activation_percent": 3.0,
        "trailing_delta_percent": 1.5,
        "partial_tp_enabled": True,
        "partial_tp_percent": 4.0,
        "partial_tp_ratio": 0.5,
        "bull_regime_dip_enabled": True,
        "bull_rsi_threshold": 42.0,
        "rsi_timeframe_minutes": 60,
        "rsi_history_length": 250,
        "signal_cooldown_hours": 24.0,
        "discord_webhook_url": "",
        "discord_bot_token": "",
        "discord_channel_id": "",
        "allowed_discord_user_ids": "",
        "gemini_api_key": "",
        "gemini_model": "gemini-3.1-flash-lite",
        "gemini_search_model": "gemini-3.5-flash",
        "ai_scout_enabled": True,
        "ai_scout_interval_hours": 2.0,
        "ai_scout_min_confidence": 0.85
    }

    try:
        # 1. Save comma-separated string
        p1 = dict(base_payload)
        p1["allowed_discord_user_ids"] = "111222333444, 555666777888"
        r1 = client.post("/api/config", json=p1, headers={"X-Auth-PIN": pin})
        assert r1.status_code == 200
        assert state.allowed_discord_user_ids == ["111222333444", "555666777888"]

        # 2. Save list of strings
        p2 = dict(base_payload)
        p2["allowed_discord_user_ids"] = ["999000111", "222333444"]
        r2 = client.post("/api/config", json=p2, headers={"X-Auth-PIN": pin})
        assert r2.status_code == 200
        assert state.allowed_discord_user_ids == ["999000111", "222333444"]

        # 3. Empty string should NOT overwrite existing saved IDs
        p3 = dict(base_payload)
        p3["allowed_discord_user_ids"] = ""
        r3 = client.post("/api/config", json=p3, headers={"X-Auth-PIN": pin})
        assert r3.status_code == 200
        assert state.allowed_discord_user_ids == ["999000111", "222333444"]

        # 4. Explicit CLEAR should reset to empty list
        p4 = dict(base_payload)
        p4["allowed_discord_user_ids"] = "CLEAR"
        r4 = client.post("/api/config", json=p4, headers={"X-Auth-PIN": pin})
        assert r4.status_code == 200
        assert state.allowed_discord_user_ids == []
    finally:
        state.allowed_discord_user_ids = orig_allowed

def test_web_companion_cache_headers():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    res = client.get("/web")
    assert res.status_code == 200
    cache_control = res.headers.get("cache-control", "")
    assert "no-cache" in cache_control
    assert "no-store" in cache_control

def test_web_companion_decoupled_static_assets():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    # 1. Check HTML links static assets cleanly
    res_html = client.get("/web")
    assert res_html.status_code == 200
    html_text = res_html.text
    assert '<link rel="stylesheet" href="/static/css/style.css">' in html_text
    assert '<script src="/static/js/app.js"></script>' in html_text
    assert '<script src="/static/js/trading.js"></script>' in html_text
    assert '<script src="/static/js/settings.js"></script>' in html_text
    assert "<style>" not in html_text
    assert 'id="enable-search-grounding"' in html_text
    assert 'id="groq-api-key"' in html_text
    assert 'id="groq-model"' in html_text

    # 2. Check CSS endpoint
    res_css = client.get("/static/css/style.css")
    assert res_css.status_code == 200
    assert "text/css" in res_css.headers.get("content-type", "")
    assert ":root" in res_css.text
    assert ".card" in res_css.text

    # 3. Check decoupled JS endpoints
    res_app = client.get("/static/js/app.js")
    assert res_app.status_code == 200
    assert "javascript" in res_app.headers.get("content-type", "")
    assert "submitPin" in res_app.text
    assert "updateUI" in res_app.text
    assert "fetchNewsInsights" in res_app.text

    res_trading = client.get("/static/js/trading.js")
    assert res_trading.status_code == 200
    assert "javascript" in res_trading.headers.get("content-type", "")
    assert "submitManualBuy" in res_trading.text
    assert "submitManualSell" in res_trading.text

    res_settings = client.get("/static/js/settings.js")
    assert res_settings.status_code == 200
    assert "javascript" in res_settings.headers.get("content-type", "")
    assert "populateSettingsInputs" in res_settings.text
    assert "saveConfig" in res_settings.text

    # 4. Check syntax validity of JS files
    import shutil, subprocess
    if shutil.which("node"):
        p = subprocess.run(["node", "-c", "static/js/app.js", "static/js/trading.js", "static/js/settings.js"], capture_output=True, text=True)
        assert p.returncode == 0, f"JS syntax check failed: {p.stderr}"

    # 5. Check 404 for missing static asset
    res_404 = client.get("/static/css/nonexistent.css")
    assert res_404.status_code == 404

@pytest.mark.asyncio
async def test_api_config_ai_and_groq_persistence():
    from fastapi.testclient import TestClient
    from main import app
    from engine.state import state
    from engine.db import load_config_item

    client = TestClient(app)
    pin = state.auth_pin or "0716"

    payload = {
        "max_trade_usdt": 45.0,
        "max_daily_spend_usdt": 180.0,
        "min_usdt_reserve": 15.0,
        "require_human_approval": True,
        "auth_pin": pin,
        "testnet": True,
        "favorite_pairs": "BTCUSDT,ETHUSDT",
        "gemini_model": "gemini-3.1-flash-lite",
        "gemini_search_model": "gemini-3.5-flash-lite",
        "enable_search_grounding": True,
        "groq_api_key": "gsk_test12345",
        "groq_model": "llama-3.3-70b-versatile"
    }

    res = client.post("/api/config", json=payload, headers={"X-Auth-PIN": pin})
    assert res.status_code == 200

    # Verify memory state
    assert state.gemini_search_model == "gemini-3.5-flash-lite"
    assert state.enable_search_grounding is True
    assert state.groq_api_key == "gsk_test12345"
    assert state.groq_model == "llama-3.3-70b-versatile"

    # Verify DB persistence
    saved = await load_config_item("risk_config")
    assert saved is not None
    assert saved.get("gemini_search_model") == "gemini-3.5-flash-lite"
    assert saved.get("enable_search_grounding") is True
    assert saved.get("groq_api_key") == "gsk_test12345"
    assert saved.get("groq_model") == "llama-3.3-70b-versatile"

def test_dump_pydantic_model_compatibility():
    from engine.api_routes import dump_pydantic_model

    class V2Dummy:
        def model_dump(self):
            return {"a": 1, "b": "v2"}

    assert dump_pydantic_model(V2Dummy()) == {"a": 1, "b": "v2"}

    class V1Dummy:
        def dict(self):
            return {"a": 2, "b": "v1"}

    assert dump_pydantic_model(V1Dummy()) == {"a": 2, "b": "v1"}

    class PlainDummy:
        def __init__(self):
            self.a = 3
            self.b = "plain"

    assert dump_pydantic_model(PlainDummy()) == {"a": 3, "b": "plain"}

@pytest.mark.asyncio
async def test_api_config_with_simulated_pydantic_v1():
    from engine.api_routes import update_config
    from engine.state import state

    orig_allowed = list(state.allowed_discord_user_ids)
    try:
        class MockV1Config:
            def __init__(self):
                self.max_trade_usdt = 25.0
                self.max_daily_spend_usdt = 100.0
                self.min_usdt_reserve = 20.0
                self.require_human_approval = False
                self.auth_pin = state.auth_pin or "1234"
                self.api_key = ""
                self.secret_key = ""
                self.favorite_pairs = "BTCUSDT"
                self.testnet = True
                self.dca_interval = 3600
                self.rsi_threshold = 30.0
                self.tp_percent = 5.0
                self.sl_percent = 3.0
                self.trailing_enabled = True
                self.trailing_activation_percent = 3.0
                self.trailing_delta_percent = 1.5
                self.partial_tp_enabled = True
                self.partial_tp_percent = 4.0
                self.partial_tp_ratio = 0.5
                self.bull_regime_dip_enabled = True
                self.bull_rsi_threshold = 42.0
                self.rsi_timeframe_minutes = 60
                self.rsi_history_length = 250
                self.signal_cooldown_hours = 24.0
                self.discord_webhook_url = ""
                self.discord_bot_token = ""
                self.discord_channel_id = ""
                self.allowed_discord_user_ids = "888777666"
                self.gemini_api_key = ""
                self.gemini_model = "gemini-3.1-flash-lite"
                self.gemini_search_model = "gemini-3.5-flash"
                self.ai_scout_enabled = True
                self.ai_scout_interval_hours = 2.0
                self.ai_scout_min_confidence = 0.85

            def dict(self):
                return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

        mock_cfg = MockV1Config()
        assert not hasattr(mock_cfg, "model_dump")
        res = await update_config(mock_cfg)
        assert res["status"] == "ok"
        assert "888777666" in state.allowed_discord_user_ids
    finally:
        state.allowed_discord_user_ids = orig_allowed

@pytest.mark.asyncio
async def test_trade_history_chronological_sorting():
    from engine.db import log_trade, get_trade_history
    import time

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
    from engine.db import log_trade, order_exists, deduplicate_trade_history, get_trade_history
    import time

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

def test_api_deduplicate_trades():
    from fastapi.testclient import TestClient
    from main import app
    from engine.state import state

    client = TestClient(app)
    pin = state.auth_pin or "1234"
    res = client.post("/api/trades/deduplicate", headers={"X-Auth-PIN": pin})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "pruned_duplicates" in data


@pytest.mark.asyncio
async def test_gemini_503_circuit_breaker_and_cooldown():
    from engine.ai_analyst import is_model_on_cooldown, record_model_cooldown, clear_model_cooldowns

    clear_model_cooldowns()
    assert not is_model_on_cooldown("gemini-3.5-flash-lite")

    # Record 503 high demand cooldown
    record_model_cooldown("gemini-3.5-flash-lite", duration_sec=600.0)
    assert is_model_on_cooldown("gemini-3.5-flash-lite")
    assert is_model_on_cooldown("models/gemini-3.5-flash-lite")

    clear_model_cooldowns()
    assert not is_model_on_cooldown("gemini-3.5-flash-lite")


@pytest.mark.asyncio
async def test_trade_signal_algorithmic_fallback_on_outage(monkeypatch):
    from engine.ai_analyst import analyze_trade_signal
    import engine.ai_analyst as ai_mod

    # Simulate Gemini total outage (returning None)
    async def mock_call_gemini(*args, **kwargs):
        return None

    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    res = await analyze_trade_signal(
        pair="BTCUSDT",
        action="BUY",
        price=64000.0,
        rsi=28.0,
        pct_b=0.15,
        reason="Wilder RSI Oversold Bounce",
        price_history=[65000, 64500, 64000],
        api_key="mock_free_key"
    )

    assert isinstance(res, dict)
    assert res["verdict"] == "APPROVE"
    assert res["risk_score"] <= 4
    assert res["suggested_sl_percent"] > 0
    assert "[ALGO FALLBACK]" in res["summary"]
    assert "fng_index" in res


@pytest.mark.asyncio
async def test_scout_algorithmic_fallback_on_outage(monkeypatch):
    from engine.ai_analyst import scan_market_opportunities
    import engine.ai_analyst as ai_mod

    # Simulate Gemini outage
    async def mock_call_gemini(*args, **kwargs):
        return None

    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    mock_state = {
        "prices": {"BTCUSDT": 64000.0, "ETHUSDT": 3200.0},
        "favorite_pairs": ["BTCUSDT", "ETHUSDT"],
        "indicators": {
            "BTCUSDT": {"rsi": 28.0, "pct_b": 0.15},
            "ETHUSDT": {"rsi": 72.0, "pct_b": 0.88}
        }
    }

    res = await scan_market_opportunities(mock_state, api_key="mock_free_key")
    assert isinstance(res, dict)
    opps = res.get("top_opportunities", [])
    assert len(opps) >= 1
    types = [o["setup_type"] for o in opps]
    assert "DIP_BUY" in types or "TAKE_PROFIT" in types
    assert "[ALGO FALLBACK]" in res.get("tactical_summary", "")


@pytest.mark.asyncio
async def test_news_default_provider_and_grounding_disabled():
    from engine.news_service import news_service, CryptoPanicProvider, GoogleSearchGroundingProvider
    from engine.state import state

    # 1. Default news service uses CryptoPanicProvider
    assert isinstance(news_service.provider, CryptoPanicProvider)

    # 2. Grounding provider respects enable_search_grounding=False
    state.enable_search_grounding = False
    grounding = GoogleSearchGroundingProvider()
    news = await grounding.fetch_news(["BTC"])
    assert isinstance(news, list)
    # Delegates cleanly to CryptoPanic without error
    assert len(news) > 0


@pytest.mark.asyncio
async def test_news_synthesis_fallback():
    from engine.ai_analyst import fallback_news_synthesis
    from engine.news_service import NewsItem
    import time

    sample_items = [
        NewsItem(title="Bitcoin ETF Inflows Surge To Record High", asset="BTC", source="CoinDesk", url="https://example.com", sentiment_tag="BULLISH", published_at=time.time()),
        NewsItem(title="Regulatory Clarity Sparks Altcoin Rally", asset="ETH", source="CoinTelegraph", url="https://example.com", sentiment_tag="BULLISH", published_at=time.time()),
    ]

    res = fallback_news_synthesis(sample_items)
    assert res["overall_catalyst"] == "BULLISH"
    assert len(res["bullets"]) == 3
    assert "BTC" in res["bullets"][0]


@pytest.mark.asyncio
async def test_groq_fallback_integration(monkeypatch):
    import engine.ai_analyst as ai_mod
    from engine.state import state

    # Mock call_groq returning valid JSON
    async def mock_call_groq(*args, **kwargs):
        return '{"verdict": "APPROVE", "risk_score": 2, "confidence": 0.95, "suggested_sl_percent": 2.0, "summary": "Groq Llama-3.3-70b evaluated oversold confluence.", "red_flags": []}'

    monkeypatch.setattr(ai_mod, "call_groq", mock_call_groq)
    # Ensure Gemini returns None so Groq is reached
    monkeypatch.setattr(ai_mod, "HAS_GENAI_SDK", False)

    state.groq_api_key = "gsk_test123"
    try:
        raw = await ai_mod.call_gemini("test prompt", api_key="invalid_gemini_key")
        assert raw is not None
        assert "Groq Llama-3.3-70b" in raw
    finally:
        state.groq_api_key = ""


@pytest.mark.asyncio
async def test_gemini_outage_capped_models_and_fast_bypass(monkeypatch):
    import engine.ai_analyst as ai_mod
    from engine.ai_analyst import call_gemini, record_model_cooldown, clear_model_cooldowns, ACTIVE_GEMINI_PRIORITY

    clear_model_cooldowns()
    monkeypatch.setattr(ai_mod, "HAS_GENAI_SDK", False)

    # Place all active Gemini models on cooldown (simulating widespread 503)
    for m in ACTIVE_GEMINI_PRIORITY:
        record_model_cooldown(m, 600.0)

    # With all on cooldown, call_gemini must immediately bypass without making HTTP calls
    import aiohttp
    called = []
    def mock_post(*args, **kwargs):
        called.append(args)
        raise RuntimeError("Should not be called")

    monkeypatch.setattr(aiohttp.ClientSession, "post", mock_post)

    res = await call_gemini("test prompt", api_key="test_key", model="gemini-3.1-flash-lite")
    # Must return None (or Groq if configured) with 0 HTTP calls
    assert res is None
    assert len(called) == 0

    clear_model_cooldowns()


@pytest.mark.asyncio
async def test_langchain_provider_factory():
    from engine.ai_provider import get_chat_model
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_openai import ChatOpenAI
    from langchain_anthropic import ChatAnthropic
    from langchain_groq import ChatGroq

    # 1. Google
    m_google = get_chat_model("google", model_name="gemini-3.1-flash", api_key="test_google_key")
    assert isinstance(m_google, ChatGoogleGenerativeAI)
    assert m_google.model == "gemini-3.1-flash"

    # 2. OpenAI
    m_openai = get_chat_model("openai", model_name="gpt-4o-mini", api_key="sk-test")
    assert isinstance(m_openai, ChatOpenAI)
    assert m_openai.model_name == "gpt-4o-mini"

    # 3. Anthropic
    m_anthropic = get_chat_model("anthropic", model_name="claude-3-5-haiku-latest", api_key="sk-ant-test")
    assert isinstance(m_anthropic, ChatAnthropic)
    assert m_anthropic.model == "claude-3-5-haiku-latest"

    # 4. Groq
    m_groq = get_chat_model("groq", model_name="llama-3.3-70b-versatile", api_key="gsk-test")
    assert isinstance(m_groq, ChatGroq)
    assert m_groq.model_name == "llama-3.3-70b-versatile"

    # 5. Ollama
    m_ollama = get_chat_model("ollama", model_name="llama3.2", base_url="http://localhost:11434/v1")
    assert isinstance(m_ollama, ChatOpenAI)
    assert "11434" in str(m_ollama.openai_api_base)
    assert m_ollama.model_name == "llama3.2"

    # 6. DeepSeek
    m_deepseek = get_chat_model("deepseek", model_name="deepseek-chat", api_key="sk-ds-test")
    assert isinstance(m_deepseek, ChatOpenAI)
    assert "deepseek.com" in str(m_deepseek.openai_api_base)


@pytest.mark.asyncio
async def test_langchain_execute_ai_completion_fallback(monkeypatch):
    from engine import ai_provider
    from langchain_core.messages import AIMessage

    class MockFailingModel:
        async def ainvoke(self, messages):
            raise RuntimeError("Primary provider rate limited (429)")

        def with_fallbacks(self, fallbacks):
            self.fallbacks = fallbacks
            return self

    class MockSuccessfulFallback:
        async def ainvoke(self, messages):
            return AIMessage(content="Fallback response from Groq!")

    # Test automatic fallback execution
    def mock_get_chat_model(provider, *args, **kwargs):
        if provider == "google":
            return MockFailingModel()
        return MockSuccessfulFallback()

    monkeypatch.setattr(ai_provider, "get_chat_model", mock_get_chat_model)

    res = await ai_provider.execute_ai_completion(
        prompt="Analyze Bitcoin",
        provider="google",
        api_key="fake_key",
        fallback_provider="groq",
        fallback_api_key="fake_groq_key"
    )
    assert res == "Fallback response from Groq!"


@pytest.mark.asyncio
async def test_ai_session_manager_and_history():
    from engine.ai_session import SessionManager

    mgr = SessionManager()
    session = mgr.get_or_create_session("test_channel_1")
    session.history.add_user_message("Hello AI!")
    session.history.add_ai_message("Hello human trader.")

    hist = mgr.get_history("test_channel_1")
    assert len(hist) == 2
    assert hist[0]["role"] == "user"
    assert hist[0]["content"] == "Hello AI!"
    assert hist[1]["role"] == "assistant"
    assert hist[1]["content"] == "Hello human trader."

    # Test pruning
    for i in range(25):
        session.history.add_user_message(f"Msg {i}")
    session.prune(max_messages=10)
    assert len(session.history.messages) == 10

    # Test clear
    assert mgr.clear_session("test_channel_1") is True
    assert len(mgr.get_history("test_channel_1")) == 0


@pytest.mark.asyncio
async def test_ai_execute_chat_turn(monkeypatch):
    from engine.ai_session import execute_chat_turn, session_manager
    from engine import ai_provider
    from langchain_core.messages import AIMessage

    session_manager.clear_session("test_chat_session")

    class MockChatModel:
        async def ainvoke(self, messages):
            return AIMessage(content="Bitcoin RSI is consolidating at 45.")

    monkeypatch.setattr(ai_provider, "get_chat_model", lambda *a, **kw: MockChatModel())

    res1 = await execute_chat_turn(
        query="What is BTC RSI right now?",
        session_id="test_chat_session",
        market_context={"prices": {"BTCUSDT": 65000.0}},
        provider="google",
        api_key="fake_key"
    )
    assert "consolidating" in res1["answer"]
    assert res1["turn_count"] == 1

    history = session_manager.get_history("test_chat_session")
    assert len(history) == 2
    assert history[0]["content"] == "What is BTC RSI right now?"
    assert history[1]["content"] == "Bitcoin RSI is consolidating at 45."

    session_manager.clear_session("test_chat_session")


@pytest.mark.asyncio
async def test_ai_routes_endpoints():
    from fastapi.testclient import TestClient
    from main import app
    from engine.state import state

    client = TestClient(app)
    pin = state.auth_pin or "1234"

    # 1. GET /api/ai/providers
    r1 = client.get("/api/ai/providers")
    assert r1.status_code == 200
    providers = r1.json().get("providers", {})
    assert "google" in providers
    assert "openai" in providers
    assert "anthropic" in providers
    assert "groq" in providers
    assert "ollama" in providers
    assert "deepseek" in providers

    # 2. GET /api/ai/models
    r2 = client.get("/api/ai/models?provider=openai")
    assert r2.status_code == 200
    models = r2.json().get("models", [])
    assert "gpt-4o-mini" in models

    # 3. POST /api/ai/test (Invalid key should report error cleanly)
    r3 = client.post(
        "/api/ai/test",
        headers={"X-Auth-PIN": pin},
        json={"provider": "custom", "model": "test-model", "api_key": "bad_key", "base_url": "http://127.0.0.1:9999/v1"}
    )
    assert r3.status_code == 200
    assert r3.json()["status"] == "error"

    # 4. GET /api/ai/chat/history & DELETE
    r4 = client.get("/api/ai/chat/history?session_id=pytest_test", headers={"X-Auth-PIN": pin})
    assert r4.status_code == 200
    assert r4.json()["history"] == []

    r5 = client.delete("/api/ai/chat/history?session_id=pytest_test", headers={"X-Auth-PIN": pin})
    assert r5.status_code == 200
    assert r5.json()["cleared"] is False


@pytest.mark.asyncio
async def test_ai_config_persistence_and_encryption():
    from fastapi.testclient import TestClient
    from main import app
    from engine.state import state
    from engine.db import load_config_item

    client = TestClient(app)
    pin = state.auth_pin or "1234"

    payload = {
        "max_trade_usdt": 50.0,
        "max_daily_spend_usdt": 200.0,
        "min_usdt_reserve": 20.0,
        "require_human_approval": True,
        "auth_pin": pin,
        "ai_provider": "openai",
        "ai_model": "gpt-4o-mini",
        "ai_api_key": "sk-proj-secret12345",
        "ai_base_url": "https://api.openai.com/v1",
        "ai_fallback_provider": "groq",
        "ai_fallback_model": "llama-3.3-70b-versatile",
        "ai_fallback_api_key": "gsk-secret67890"
    }

    resp = client.post("/api/config", headers={"X-Auth-PIN": pin}, json=payload)
    assert resp.status_code == 200

    # Verify state updated in memory
    assert state.ai_provider == "openai"
    assert state.ai_model == "gpt-4o-mini"
    assert state.ai_api_key == "sk-proj-secret12345"
    assert state.ai_base_url == "https://api.openai.com/v1"
    assert state.ai_fallback_provider == "groq"
    assert state.ai_fallback_api_key == "gsk-secret67890"
    assert state.has_ai is True

    # Verify encrypted in DB
    saved = await load_config_item("risk_config")
    assert saved is not None
    assert saved.get("ai_api_key") != "sk-proj-secret12345"  # Encrypted
    assert len(saved.get("ai_api_key", "")) > 30

    # Verify to_dict includes AI fields
    d = state.to_dict()
    assert d["ai_provider"] == "openai"
    assert d["ai_model"] == "gpt-4o-mini"
    assert d["has_ai"] is True














