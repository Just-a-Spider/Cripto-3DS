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
    assert d["gemini_model"] == "gemini-3.1-flash-lite"
    assert d["gemini_search_model"] == "gemini-2.5-flash"

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




