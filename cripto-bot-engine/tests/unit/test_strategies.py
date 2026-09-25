import pytest
from httpx import ASGITransport, AsyncClient

from engine.db import init_db
from main import app, state

pytestmark = pytest.mark.unit


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


def test_trailing_stop_loss():
    from engine.strategies import TPSLStrategy

    tsl = TPSLStrategy(
        trailing_enabled=True, trailing_activation_percent=3.0, trailing_delta_percent=1.5, sl_percent=3.0
    )
    portfolio = {"BTC": 0.01}
    cost_bases = {"BTCUSDT": 60000.0}  # Cost basis = $60,000

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


@pytest.mark.asyncio
async def test_bull_regime_dip_buying():
    from engine.strategies import RSIStrategy

    # Oversold is 30.0, but bull_regime_dip_enabled is True and bull_rsi_threshold is 42.0
    strat = RSIStrategy(oversold_rsi=30.0, bull_regime_dip_enabled=True, bull_rsi_threshold=42.0)
    strat.enabled = True

    # Simulate price history with healthy bull dip (RSI ~40.4, %B ~0.19, no knife drop)
    strat.price_histories["SOLUSDT"] = [170.0] * 10 + [
        175.0,
        174.0,
        173.0,
        172.0,
        171.0,
        170.0,
        169.0,
        168.0,
        168.2,
        168.0,
    ]
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
        partial_tp_ratio=0.5,
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
    assert sig_tp1["amount_asset"] == 5.0  # 50% of 10.0
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
async def test_trailing_profit_runner_handoff():
    from engine.state import state
    from engine.strategies import RSIStrategy, TPSLStrategy

    state.tpsl_strategy = TPSLStrategy(
        trailing_enabled=True, trailing_activation_percent=3.0, trailing_delta_percent=1.5
    )
    rsi_strat = RSIStrategy(oversold_rsi=30.0, overbought_rsi=70.0, min_profit_percent=5.0)
    rsi_strat.enabled = True

    # Populate price history (ending below $2000 so $2000 is an upward step)
    rsi_strat.price_histories["ETHUSDT"] = [1800.0 + (i * 5.0) for i in range(30)]  # 1800 -> 1945
    portfolio = {"ETH": 0.05}  # Worth > $5
    cost_bases = {"ETHUSDT": 1877.0}
    prices = {"ETHUSDT": 2000.0}  # +6.55% profit

    # When RSI is overbought and trailing is enabled, RSI strategy hands off to TPSL peak tracker rather than instant dumping
    sig = rsi_strat.evaluate(prices, 50.0, ["ETHUSDT"], portfolio, cost_bases)
    assert sig is None  # Did not dump statically!
    assert state.tpsl_strategy.peak_prices.get("ETHUSDT") == 2000.0  # Peak registered!

    # Now simulate price surging to $2,067
    prices["ETHUSDT"] = 2067.0
    tpsl_sig = state.tpsl_strategy.evaluate_tpsl(prices, portfolio, cost_bases)
    assert tpsl_sig is None  # Still riding the pump!
    assert state.tpsl_strategy.peak_prices.get("ETHUSDT") == 2067.0

    # Now simulate a 2% pullback from peak ($2067 -> $2020)
    prices["ETHUSDT"] = 2020.0
    exit_sig = state.tpsl_strategy.evaluate_tpsl(prices, portfolio, cost_bases)
    assert exit_sig is not None
    assert exit_sig["action"] == "SELL"
    assert "Trailing Stop" in exit_sig["reason"]


def test_dca_strategy_evaluation():
    import time

    from engine.strategies import DCAStrategy

    dca = DCAStrategy(interval_sec=3600)
    prices = {"BTCUSDT": 60000.0, "ETHUSDT": 3000.0}
    pairs = ["BTCUSDT", "ETHUSDT"]

    # 1. Disabled -> returns None
    dca.enabled = False
    assert dca.evaluate(prices, 500.0, pairs) is None

    # 2. Enabled but interval not passed -> returns None
    dca.enabled = True
    dca.last_trade_time = time.time()
    assert dca.evaluate(prices, 500.0, pairs) is None

    # 3. Enabled and interval passed -> returns BUY signal
    dca.last_trade_time = time.time() - 3601
    sig1 = dca.evaluate(prices, 500.0, pairs)
    assert sig1 is not None
    assert sig1["action"] == "BUY"
    assert sig1["pair"] in pairs

    # 4. Immediate second evaluation is within interval -> returns None
    assert dca.evaluate(prices, 500.0, pairs) is None

    # 5. Cooldown prevents signal
    dca.last_trade_time = time.time() - 3601
    next_pair = pairs[(dca.current_index + 1) % len(pairs)]
    dca.cooldowns[next_pair] = time.time()
    assert dca.evaluate(prices, 500.0, pairs, cooldown_hours=1.0) is None
