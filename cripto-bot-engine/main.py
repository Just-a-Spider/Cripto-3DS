import os
import asyncio
import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from engine.db import init_db
from engine.logger import logger
from engine.state import state, get_cipher
from engine.risk_manager import risk_manager
from engine.db import load_config_item

from engine.api_routes import router as api_router
from engine.ai_routes import router as ai_router
from engine.telemetry import start_3ds_tcp_server
from engine.watchdogs import trade_timeout_watchdog, cost_basis_watchdog
from engine.binance_client import start_binance_websocket

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    
    saved_cfg = await load_config_item("risk_config") or {}
    
    # Risk Configuration
    risk_manager.max_trade_usdt = float(saved_cfg.get("max_trade_usdt", os.getenv("MAX_TRADE_USDT", risk_manager.max_trade_usdt)))
    risk_manager.max_daily_spend_usdt = float(saved_cfg.get("max_daily_spend_usdt", os.getenv("MAX_DAILY_SPEND_USDT", risk_manager.max_daily_spend_usdt)))
    risk_manager.min_usdt_reserve = float(saved_cfg.get("min_usdt_reserve", os.getenv("MIN_USDT_RESERVE", risk_manager.min_usdt_reserve)))
    
    raw_approval = saved_cfg.get("require_human_approval", os.getenv("REQUIRE_HUMAN_APPROVAL", str(risk_manager.require_human_approval)))
    risk_manager.require_human_approval = str(raw_approval).lower() in ("true", "1", "yes")

    state.auth_pin = str(saved_cfg.get("auth_pin", os.getenv("AUTH_PIN", "1234")))

    raw_testnet = saved_cfg.get("testnet", os.getenv("BINANCE_TESTNET", "true"))
    state.testnet = str(raw_testnet).lower() in ("true", "1", "yes")
    
    fav_pairs = saved_cfg.get("favorite_pairs", os.getenv("FAVORITE_PAIRS", "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT"))
    if isinstance(fav_pairs, list):
        state.favorite_pairs = fav_pairs
    elif isinstance(fav_pairs, str) and fav_pairs.strip():
        state.favorite_pairs = [p.strip() for p in fav_pairs.split(",") if p.strip()]
    
    state.dca_strategy.interval_sec = int(saved_cfg.get("dca_interval", os.getenv("DCA_INTERVAL", 3600)))
    state.rsi_strategy.oversold_rsi = float(saved_cfg.get("rsi_threshold", os.getenv("RSI_THRESHOLD", 30.0)))
    state.tpsl_strategy.tp_percent = float(saved_cfg.get("tp_percent", os.getenv("TP_PERCENT", 5.0)))
    state.tpsl_strategy.sl_percent = float(saved_cfg.get("sl_percent", os.getenv("SL_PERCENT", 3.0)))

    raw_trailing = saved_cfg.get("trailing_enabled", os.getenv("TRAILING_ENABLED", "true"))
    state.tpsl_strategy.trailing_enabled = str(raw_trailing).lower() in ("true", "1", "yes")
    state.tpsl_strategy.trailing_activation_percent = float(saved_cfg.get("trailing_activation_percent", os.getenv("TRAILING_ACTIVATION_PERCENT", 3.0)))
    state.tpsl_strategy.trailing_delta_percent = float(saved_cfg.get("trailing_delta_percent", os.getenv("TRAILING_DELTA_PERCENT", 1.5)))

    raw_partial_tp = saved_cfg.get("partial_tp_enabled", os.getenv("PARTIAL_TP_ENABLED", "true"))
    state.tpsl_strategy.partial_tp_enabled = str(raw_partial_tp).lower() in ("true", "1", "yes")
    state.tpsl_strategy.partial_tp_percent = float(saved_cfg.get("partial_tp_percent", os.getenv("PARTIAL_TP_PERCENT", 4.0)))
    state.tpsl_strategy.partial_tp_ratio = float(saved_cfg.get("partial_tp_ratio", os.getenv("PARTIAL_TP_RATIO", 0.5)))

    raw_bull_dip = saved_cfg.get("bull_regime_dip_enabled", os.getenv("BULL_REGIME_DIP_ENABLED", "true"))
    state.rsi_strategy.bull_regime_dip_enabled = str(raw_bull_dip).lower() in ("true", "1", "yes")
    state.rsi_strategy.bull_rsi_threshold = float(saved_cfg.get("bull_rsi_threshold", os.getenv("BULL_RSI_THRESHOLD", 42.0)))

    state.rsi_strategy.timeframe_minutes = int(saved_cfg.get("rsi_timeframe_minutes", os.getenv("RSI_TIMEFRAME_MINUTES", 60)))
    state.rsi_strategy.history_length = int(saved_cfg.get("rsi_history_length", os.getenv("RSI_HISTORY_LENGTH", 250)))
    state.signal_cooldown_hours = float(saved_cfg.get("signal_cooldown_hours", os.getenv("SIGNAL_COOLDOWN_HOURS", 24.0)))

    state.discord_webhook_url = saved_cfg.get("discord_webhook_url", os.getenv("DISCORD_WEBHOOK_URL", ""))
    state.discord_bot_token = saved_cfg.get("discord_bot_token", os.getenv("DISCORD_BOT_TOKEN", ""))
    state.discord_channel_id = saved_cfg.get("discord_channel_id", os.getenv("DISCORD_CHANNEL_ID", ""))
    
    raw_discord_ids = saved_cfg.get("allowed_discord_user_ids")
    if not raw_discord_ids:
        raw_discord_ids = os.getenv("ALLOWED_DISCORD_USER_IDS") or os.getenv("DISCORD_USER_ID", "")

    if isinstance(raw_discord_ids, list):
        state.allowed_discord_user_ids = [str(x).strip() for x in raw_discord_ids if str(x).strip()]
    elif isinstance(raw_discord_ids, str) and raw_discord_ids.strip():
        state.allowed_discord_user_ids = [x.strip() for x in raw_discord_ids.split(",") if x.strip()]
    else:
        state.allowed_discord_user_ids = []

    if state.allowed_discord_user_ids:
        logger.info(f"Loaded {len(state.allowed_discord_user_ids)} authorized Discord operator ID(s).")
    else:
        logger.warning("No authorized Discord user IDs configured. Discord bot commands will be locked until an ID is set in .env or Web Companion.")

    state.ai_provider = saved_cfg.get("ai_provider", os.getenv("AI_PROVIDER", "google"))
    state.ai_model = saved_cfg.get("ai_model", os.getenv("AI_MODEL", "gemini-3.1-flash"))
    state.ai_base_url = saved_cfg.get("ai_base_url", os.getenv("AI_BASE_URL", ""))
    state.ai_fallback_provider = saved_cfg.get("ai_fallback_provider", os.getenv("AI_FALLBACK_PROVIDER", "groq"))
    state.ai_fallback_model = saved_cfg.get("ai_fallback_model", os.getenv("AI_FALLBACK_MODEL", "llama-3.3-70b-versatile"))

    state.gemini_api_key = saved_cfg.get("gemini_api_key", os.getenv("GEMINI_API_KEY", ""))
    state.gemini_model = saved_cfg.get("gemini_model", os.getenv("GEMINI_MODEL", "gemini-3.1-flash"))
    state.gemini_search_model = saved_cfg.get("gemini_search_model", os.getenv("GEMINI_SEARCH_MODEL", "gemini-3.1-flash-lite"))
    
    raw_grounding = saved_cfg.get("enable_search_grounding", os.getenv("ENABLE_SEARCH_GROUNDING", "false"))
    state.enable_search_grounding = str(raw_grounding).lower() in ("true", "1", "yes")

    state.groq_api_key = saved_cfg.get("groq_api_key", os.getenv("GROQ_API_KEY", ""))
    state.groq_model = saved_cfg.get("groq_model", os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))

    # Decrypt AI Provider keys if encrypted
    enc_ai_key = saved_cfg.get("ai_api_key", "")
    if enc_ai_key:
        try:
            cipher = get_cipher(state.auth_pin)
            state.ai_api_key = cipher.decrypt(enc_ai_key.encode()).decode()
        except Exception:
            state.ai_api_key = os.getenv("AI_API_KEY", "")
    else:
        state.ai_api_key = os.getenv("AI_API_KEY", "")

    enc_fb_key = saved_cfg.get("ai_fallback_api_key", "")
    if enc_fb_key:
        try:
            cipher = get_cipher(state.auth_pin)
            state.ai_fallback_api_key = cipher.decrypt(enc_fb_key.encode()).decode()
        except Exception:
            state.ai_fallback_api_key = os.getenv("AI_FALLBACK_API_KEY", "")
    else:
        state.ai_fallback_api_key = os.getenv("AI_FALLBACK_API_KEY", "")

    if not state.ai_api_key and state.ai_provider == "google" and state.gemini_api_key:
        state.ai_api_key = state.gemini_api_key
    if not state.ai_fallback_api_key and state.ai_fallback_provider == "groq" and state.groq_api_key:
        state.ai_fallback_api_key = state.groq_api_key

    raw_ai_scout = saved_cfg.get("ai_scout_enabled", os.getenv("AI_SCOUT_ENABLED", "true"))
    state.ai_scout_enabled = str(raw_ai_scout).lower() in ("true", "1", "yes")
    state.ai_scout_interval_hours = float(saved_cfg.get("ai_scout_interval_hours", os.getenv("AI_SCOUT_INTERVAL_HOURS", 2.0)))
    state.ai_scout_min_confidence = float(saved_cfg.get("ai_scout_min_confidence", os.getenv("AI_SCOUT_MIN_CONFIDENCE", 0.85)))
        
    enc_api = saved_cfg.get("api_key", "")
    enc_sec = saved_cfg.get("secret_key", "")
    if enc_api and enc_sec:
        try:
            cipher = get_cipher(state.auth_pin)
            state.api_key = cipher.decrypt(enc_api.encode()).decode()
            state.secret_key = cipher.decrypt(enc_sec.encode()).decode()
        except Exception:
            try:
                from engine.state import get_legacy_cipher
                legacy = get_legacy_cipher(state.auth_pin)
                state.api_key = legacy.decrypt(enc_api.encode()).decode()
                state.secret_key = legacy.decrypt(enc_sec.encode()).decode()
                # Re-encrypt with modern PBKDF2HMAC
                saved_cfg["api_key"] = cipher.encrypt(state.api_key.encode()).decode() # type: ignore
                saved_cfg["secret_key"] = cipher.encrypt(state.secret_key.encode()).decode() # type: ignore
                from engine.db import save_config_item
                await save_config_item("risk_config", saved_cfg)
                logger.info("Migrated API keys to PBKDF2HMAC encryption.")
            except Exception:
                logger.error("Failed to decrypt API keys (Invalid PIN?)")
    
    if saved_cfg:
        logger.info(f"Loaded config from DB.")

    await risk_manager.refresh_daily_spend(state.testnet)
        
    state_data = await load_config_item("strategy_state")
    if state_data:
        state.dca_strategy.cooldowns = state_data.get("dca_cooldowns", {})
        state.dca_strategy.last_trade_time = state_data.get("dca_last_trade", 0.0)
        state.rsi_strategy.cooldowns = state_data.get("rsi_cooldowns", {})
        state.tpsl_strategy.cooldowns = state_data.get("tpsl_cooldowns", {})
        logger.info("Loaded strategy cooldown state from DB.")
    
    if not state.api_key:
        state.api_key = os.getenv("BINANCE_API_KEY", "")
        state.secret_key = os.getenv("BINANCE_SECRET_KEY", "")

    if state.discord_bot_token and state.discord_channel_id:
        from engine.notifier import discord_bot_service
        asyncio.create_task(discord_bot_service.start(state.discord_bot_token, state.discord_channel_id))

    if state.gemini_api_key:
        async def init_gemini_models():
            from engine.ai_analyst import fetch_available_gemini_models
            models = await fetch_available_gemini_models(state.gemini_api_key)
            if models:
                state.available_gemini_models = models
                logger.info(f"Discovered {len(models)} active Google Gemini models on boot.")
        asyncio.create_task(init_gemini_models())

    from engine.watchdogs import trade_timeout_watchdog, cost_basis_watchdog, ai_opportunity_scout_watchdog
    asyncio.create_task(start_3ds_tcp_server())
    asyncio.create_task(trade_timeout_watchdog())
    asyncio.create_task(cost_basis_watchdog())
    asyncio.create_task(ai_opportunity_scout_watchdog())
    asyncio.create_task(start_binance_websocket())

    yield
    logger.info("Engine services shutting down cleanly.")

app = FastAPI(title="Cripto-3DS Bot Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|100\.\d+\.\d+\.\d+|moto-e20)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(api_router)
app.include_router(ai_router)

if __name__ == "__main__":
    if os.environ.get("HEADLESS", "false").lower() == "true":
        logger.info("Running in headless mode...")
        uvicorn.run(app, host="0.0.0.0", port=7344)
    else:
        import threading
        from ui import run_gui
        
        t = threading.Thread(target=uvicorn.run, args=(app,), kwargs={"host": "0.0.0.0", "port": 7344}, daemon=True)
        t.start()
        
        run_gui()
