import pytest
from httpx import ASGITransport, AsyncClient

from engine.db import init_db
from main import app, state

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_ai_scout_dynamic_config_update():
    from engine.state import state

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

    import engine.ai_analyst as ai_mod
    import engine.watchdogs as wd_mod
    from engine.state import state

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

    import engine.watchdogs as wd_mod
    from engine.db import get_trade_history
    from engine.risk_manager import risk_manager
    from engine.state import state

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
async def test_ai_scout_parallel_multi_asset_staging(monkeypatch):
    import asyncio

    import engine.watchdogs as wd_mod
    from engine.risk_manager import risk_manager
    from engine.state import state

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
