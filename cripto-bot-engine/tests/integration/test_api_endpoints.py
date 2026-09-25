import pytest
from httpx import ASGITransport, AsyncClient

from engine.db import init_db
from main import app, state

pytestmark = pytest.mark.integration


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
async def test_get_trades_endpoint():
    from httpx import ASGITransport, AsyncClient

    from engine.state import state
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trades", headers={"X-Auth-PIN": state.auth_pin})
        assert resp.status_code == 200
        data = resp.json()
        assert "trades" in data
        assert "summary" in data
        assert "total_pnl_usdt" in data["summary"]


def test_auth_pin_redacted_from_state_to_dict():
    state.auth_pin = "sensitive_security_pin_9876"
    dump = state.to_dict()
    assert "auth_pin" not in dump
    assert "pin" not in dump
    assert dump.get("has_pin") is True


def test_setup_env_script(tmp_path):
    import os
    import stat

    from setup_env import parse_args, setup_environment

    test_env = tmp_path / ".env.test"
    args = parse_args(
        [
            "--env-path",
            str(test_env),
            "--discord-id",
            "123456789012345678,987654321098765432",
            "--pin",
            "4321",
            "--binance-key",
            "test_key",
            "--binance-secret",
            "test_sec",
            "--testnet",
            "true",
            "--discord-token",
            "bot_tok_123",
            "--discord-channel",
            "ch_123",
            "--gemini-key",
            "gem_key_123",
            "--gemini-model",
            "gemini-3.1-flash-lite",
            "--non-interactive",
        ]
    )

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
        "ai_scout_min_confidence": 0.85,
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
    import shutil
    import subprocess

    if shutil.which("node"):
        p = subprocess.run(
            ["node", "-c", "static/js/app.js", "static/js/trading.js", "static/js/settings.js"],
            capture_output=True,
            text=True,
        )
        assert p.returncode == 0, f"JS syntax check failed: {p.stderr}"

    # 5. Check 404 for missing static asset
    res_404 = client.get("/static/css/nonexistent.css")
    assert res_404.status_code == 404


@pytest.mark.asyncio
async def test_api_config_ai_and_groq_persistence():
    from fastapi.testclient import TestClient

    from engine.db import load_config_item
    from engine.state import state
    from main import app

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
        "groq_model": "llama-3.3-70b-versatile",
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


def test_api_deduplicate_trades():
    from fastapi.testclient import TestClient

    from engine.state import state
    from main import app

    client = TestClient(app)
    pin = state.auth_pin or "1234"
    res = client.post("/api/trades/deduplicate", headers={"X-Auth-PIN": pin})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "pruned_duplicates" in data


@pytest.mark.asyncio
async def test_api_decide_all_and_by_id_endpoints():
    from engine.risk_manager import risk_manager
    from engine.state import state

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
        assert t2["id"] != t1["id"]

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
async def test_api_trades_analysis_and_reconcile_endpoints():
    from httpx import ASGITransport, AsyncClient

    from main import app, state

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # GET /api/trades/analysis
        res = await ac.get("/api/trades/analysis", headers={"X-Auth-PIN": state.auth_pin})
        assert res.status_code == 200
        data = res.json()
        assert "global" in data
        assert "assets" in data

        # POST /api/trades/reconcile
        rec_res = await ac.post("/api/trades/reconcile", headers={"X-Auth-PIN": state.auth_pin})
        assert rec_res.status_code == 200
        assert rec_res.json()["status"] == "ok"
        assert "analysis" in rec_res.json()

        # GET /api/trades includes asset_performance
        tr_res = await ac.get("/api/trades", headers={"X-Auth-PIN": state.auth_pin})
        assert tr_res.status_code == 200
        assert "asset_performance" in tr_res.json()


@pytest.mark.asyncio
async def test_ai_routes_endpoints():
    from fastapi.testclient import TestClient

    from engine.state import state
    from main import app

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
        json={
            "provider": "custom",
            "model": "test-model",
            "api_key": "bad_key",
            "base_url": "http://127.0.0.1:9999/v1",
        },
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

    from engine.db import load_config_item
    from engine.state import state
    from main import app

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
        "ai_fallback_api_key": "gsk-secret67890",
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


@pytest.mark.asyncio
async def test_dca_configuration_persistence_and_toggle(async_client):
    from engine.db import load_config_item
    from engine.state import state

    pin = state.auth_pin or "1234"

    # 1. Verify dca_enabled in state.to_dict()["strategies"]
    d = state.to_dict()
    assert "dca_enabled" in d["strategies"]
    assert "dca_interval" in d["strategies"]

    # 2. Test dedicated toggle endpoint /api/strategy/dca/toggle
    resp = await async_client.post("/api/strategy/dca/toggle?enabled=true", headers={"X-Auth-PIN": pin})
    assert resp.status_code == 200
    assert resp.json()["dca_enabled"] is True
    assert state.dca_strategy.enabled is True

    # Check persistence in DB
    saved = await load_config_item("risk_config")
    assert saved.get("dca_enabled") is True

    # Toggle with no param (flips to False)
    resp = await async_client.post("/api/strategy/dca/toggle", headers={"X-Auth-PIN": pin})
    assert resp.status_code == 200
    assert resp.json()["dca_enabled"] is False
    assert state.dca_strategy.enabled is False

    # Check persistence flipped to False
    saved = await load_config_item("risk_config")
    assert saved.get("dca_enabled") is False

    # 3. Test saving via /api/config payload with dca_enabled=True and dca_interval=1800
    cfg_payload = {
        "max_trade_usdt": 50.0,
        "max_daily_spend_usdt": 200.0,
        "min_usdt_reserve": 20.0,
        "require_human_approval": True,
        "auth_pin": pin,
        "dca_enabled": True,
        "dca_interval": 1800,
        "favorite_pairs": "BTCUSDT,ETHUSDT",
    }
    resp = await async_client.post("/api/config", headers={"X-Auth-PIN": pin}, json=cfg_payload)
    assert resp.status_code == 200
    assert state.dca_strategy.enabled is True
    assert state.dca_strategy.interval_sec == 1800

    saved = await load_config_item("risk_config")
    assert saved.get("dca_enabled") is True
    assert saved.get("dca_interval") == 1800

    # Reset state to default False
    await async_client.post("/api/strategy/dca/toggle?enabled=false", headers={"X-Auth-PIN": pin})
