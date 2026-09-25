import os

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from engine.db import init_db
from main import app, state


@pytest.fixture(autouse=True)
async def reset_state(tmp_path, monkeypatch):
    """
    Enterprise-grade test isolation fixture.
    1. Isolates SQLite storage to a temporary database file per test.
    2. Initializes clean database schema.
    3. Resets all in-memory BotState variables to safe baselines.
    """
    test_db = str(tmp_path / "test_bot_data.db")
    import engine.db
    import engine.history_analyzer

    monkeypatch.setattr(engine.db, "DB_PATH", test_db)
    monkeypatch.setattr(engine.history_analyzer, "DB_PATH", test_db)

    await init_db()

    state.is_active = False
    state.pending_trade = None
    state.pending_trades.clear()
    state.portfolio_balances.clear()
    state.cost_bases.clear()
    state.usdt_balance = 1000.0
    state.auth_pin = "1234"
    state.favorite_pairs = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
    state.prices = {"BTCUSDT": 0.0, "ETHUSDT": 0.0, "BNBUSDT": 0.0, "SOLUSDT": 0.0}
    state.ai_api_key = ""
    state.gemini_api_key = ""
    state.groq_api_key = ""
    state.ai_fallback_api_key = ""
    state.gemini_model = "gemini-3.1-flash-lite"
    state.gemini_search_model = "gemini-3.1-flash-lite"
    state.enable_search_grounding = False
    state.binance_client = None
    state.dca_strategy.enabled = False
    state.dca_strategy.interval_sec = 3600

    from engine.risk_manager import risk_manager

    risk_manager.max_trade_usdt = 50.0
    risk_manager.max_daily_spend_usdt = 200.0
    risk_manager.min_usdt_reserve = 20.0
    risk_manager.require_human_approval = True
    risk_manager.daily_spent_usdt = 0.0

    yield test_db


@pytest.fixture
async def async_client():
    """Provides a configured async HTTP client connected to the FastAPI app."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
def auth_headers():
    """Provides valid authorization headers for protected API endpoints."""
    return {"X-Auth-PIN": state.auth_pin}
