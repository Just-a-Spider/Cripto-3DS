import os
import asyncio
import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from engine.db import init_db
from engine.logger import logger
from engine.state import state, get_cipher
from engine.risk_manager import risk_manager
from engine.db import load_config_item

from engine.api_routes import router as api_router
from engine.telemetry import start_3ds_tcp_server
from engine.watchdogs import trade_timeout_watchdog, cost_basis_watchdog
from engine.binance_client import start_binance_websocket

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    
    saved_cfg = await load_config_item("risk_config")
    if saved_cfg:
        risk_manager.max_trade_usdt = saved_cfg.get("max_trade_usdt", risk_manager.max_trade_usdt)
        risk_manager.max_daily_spend_usdt = saved_cfg.get("max_daily_spend_usdt", risk_manager.max_daily_spend_usdt)
        risk_manager.min_usdt_reserve = saved_cfg.get("min_usdt_reserve", risk_manager.min_usdt_reserve)
        risk_manager.require_human_approval = saved_cfg.get("require_human_approval", risk_manager.require_human_approval)
        state.auth_pin = saved_cfg.get("auth_pin", "1234")
        state.testnet = saved_cfg.get("testnet", True)
        
        if "favorite_pairs" in saved_cfg:
            state.favorite_pairs = saved_cfg["favorite_pairs"].split(",")
        
        state.dca_strategy.interval_sec = saved_cfg.get("dca_interval", 3600)
        state.rsi_strategy.oversold_rsi = saved_cfg.get("rsi_threshold", 30.0)
        state.tpsl_strategy.tp_percent = saved_cfg.get("tp_percent", 5.0)
        state.tpsl_strategy.sl_percent = saved_cfg.get("sl_percent", 3.0)
        state.tpsl_strategy.trailing_enabled = saved_cfg.get("trailing_enabled", True)
        state.tpsl_strategy.trailing_activation_percent = saved_cfg.get("trailing_activation_percent", 3.0)
        state.tpsl_strategy.trailing_delta_percent = saved_cfg.get("trailing_delta_percent", 1.5)
        state.rsi_strategy.timeframe_minutes = saved_cfg.get("rsi_timeframe_minutes", 60)
        state.rsi_strategy.history_length = saved_cfg.get("rsi_history_length", 250)
        state.signal_cooldown_hours = saved_cfg.get("signal_cooldown_hours", 24.0)
        state.discord_webhook_url = saved_cfg.get("discord_webhook_url", "")
        state.discord_bot_token = saved_cfg.get("discord_bot_token", os.getenv("DISCORD_BOT_TOKEN", ""))
        state.discord_channel_id = saved_cfg.get("discord_channel_id", os.getenv("DISCORD_CHANNEL_ID", ""))
        state.gemini_api_key = saved_cfg.get("gemini_api_key", os.getenv("GEMINI_API_KEY", ""))
        state.gemini_model = saved_cfg.get("gemini_model", os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"))
        state.gemini_search_model = saved_cfg.get("gemini_search_model", os.getenv("GEMINI_SEARCH_MODEL", "gemini-3.5-flash"))
            
        enc_api = saved_cfg.get("api_key", "")
        enc_sec = saved_cfg.get("secret_key", "")
        if enc_api and enc_sec:
            try:
                cipher = get_cipher(state.auth_pin)
                state.api_key = cipher.decrypt(enc_api.encode()).decode()
                state.secret_key = cipher.decrypt(enc_sec.encode()).decode()
            except Exception as e:
                logger.error("Failed to decrypt API keys (Invalid PIN?)")
        
        logger.info(f"Loaded config from DB.")
        
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

    asyncio.create_task(start_3ds_tcp_server())
    asyncio.create_task(trade_timeout_watchdog())
    asyncio.create_task(cost_basis_watchdog())
    asyncio.create_task(start_binance_websocket())

    yield
    logger.info("Engine services shutting down cleanly.")

app = FastAPI(title="Cripto-3DS Bot Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

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
