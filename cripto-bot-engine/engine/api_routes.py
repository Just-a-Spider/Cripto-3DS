import os
import asyncio
import time
import json
import secrets
import aiohttp
from typing import Any, Dict, Optional, Union
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Header, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from engine.logger import logger, recent_logs
from engine.state import state, ConfigModel, get_cipher
from engine.ws_manager import ws_manager, broadcast_state
from engine.risk_manager import risk_manager
from engine.db import get_trade_history, save_config_item, load_config_item
from engine.shared import save_strategy_state
from engine.trades import decide_trade
from engine.binance_client import restart_binance_websocket

router = APIRouter()

def dump_pydantic_model(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump") and callable(getattr(model, "model_dump")):
        return model.model_dump()
    if hasattr(model, "dict") and callable(getattr(model, "dict")):
        return model.dict()
    return vars(model)

def verify_pin(request: Request, x_auth_pin: str = Header(None)):
    if not state.auth_pin:
        return
    if not x_auth_pin or not secrets.compare_digest(str(x_auth_pin).strip(), str(state.auth_pin).strip()):
        client_host = request.client.host if request.client else "unknown"
        logger.warning(f"Unauthorized API access attempt blocked from {client_host}")
        raise HTTPException(status_code=401, detail="Invalid PIN")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_COMPANION_PATH = os.path.join(BASE_DIR, "web_companion.html")

@router.get("/", response_class=HTMLResponse)
async def get_index():
    return HTMLResponse("<h1>Engine is running.</h1>")

@router.get("/web", response_class=HTMLResponse)
async def get_web():
    path = WEB_COMPANION_PATH if os.path.exists(WEB_COMPANION_PATH) else "web_companion.html"
    response = FileResponse(path)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, pin: str = None):
    if state.auth_pin:
        if not pin or not secrets.compare_digest(str(pin).strip(), str(state.auth_pin).strip()):
            await websocket.close(code=1008)
            return
    await ws_manager.connect(websocket)
    try:
        await websocket.send_text(json.dumps(state.to_dict()))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

@router.get("/api/state", dependencies=[Depends(verify_pin)])
async def get_state():
    return JSONResponse(state.to_dict())

@router.get("/api/history", dependencies=[Depends(verify_pin)])
async def get_history():
    history = await get_trade_history(30, state.testnet)
    return JSONResponse({"history": history})

@router.post("/api/config", dependencies=[Depends(verify_pin)])
async def update_config(cfg: ConfigModel):
    risk_manager.max_trade_usdt = cfg.max_trade_usdt
    risk_manager.max_daily_spend_usdt = cfg.max_daily_spend_usdt
    risk_manager.min_usdt_reserve = cfg.min_usdt_reserve
    risk_manager.require_human_approval = cfg.require_human_approval
    state.auth_pin = cfg.auth_pin
    state.testnet = cfg.testnet
    
    if cfg.favorite_pairs:
        state.favorite_pairs = [p.strip() for p in cfg.favorite_pairs.split(",") if p.strip()]
        
    state.dca_strategy.interval_sec = cfg.dca_interval
    state.rsi_strategy.oversold_rsi = cfg.rsi_threshold
    state.tpsl_strategy.tp_percent = cfg.tp_percent
    state.tpsl_strategy.sl_percent = cfg.sl_percent
    state.tpsl_strategy.trailing_enabled = cfg.trailing_enabled
    state.tpsl_strategy.trailing_activation_percent = cfg.trailing_activation_percent
    state.tpsl_strategy.trailing_delta_percent = cfg.trailing_delta_percent
    state.tpsl_strategy.partial_tp_enabled = getattr(cfg, "partial_tp_enabled", True)
    state.tpsl_strategy.partial_tp_percent = getattr(cfg, "partial_tp_percent", 4.0)
    state.tpsl_strategy.partial_tp_ratio = getattr(cfg, "partial_tp_ratio", 0.5)
    state.rsi_strategy.bull_regime_dip_enabled = getattr(cfg, "bull_regime_dip_enabled", True)
    state.rsi_strategy.bull_rsi_threshold = getattr(cfg, "bull_rsi_threshold", 42.0)
    state.rsi_strategy.timeframe_minutes = cfg.rsi_timeframe_minutes
    state.rsi_strategy.history_length = cfg.rsi_history_length
    state.signal_cooldown_hours = cfg.signal_cooldown_hours
    state.discord_webhook_url = cfg.discord_webhook_url
    saved_cfg = await load_config_item("risk_config") or {}
    cfg_dict = dump_pydantic_model(cfg)

    raw_input_ids = getattr(cfg, "allowed_discord_user_ids", None)
    if raw_input_ids == "CLEAR" or raw_input_ids == []:
        state.allowed_discord_user_ids = []
    elif isinstance(raw_input_ids, list) and raw_input_ids:
        state.allowed_discord_user_ids = [str(x).strip() for x in raw_input_ids if str(x).strip()]
    elif isinstance(raw_input_ids, str) and raw_input_ids.strip():
        state.allowed_discord_user_ids = [x.strip() for x in raw_input_ids.split(",") if x.strip()]
    else:
        fallback_ids = saved_cfg.get("allowed_discord_user_ids")
        if fallback_ids is None:
            fallback_ids = state.allowed_discord_user_ids
        if isinstance(fallback_ids, list):
            state.allowed_discord_user_ids = [str(x).strip() for x in fallback_ids if str(x).strip()]
        elif isinstance(fallback_ids, str) and fallback_ids.strip():
            state.allowed_discord_user_ids = [x.strip() for x in fallback_ids.split(",") if x.strip()]
    cfg_dict["allowed_discord_user_ids"] = state.allowed_discord_user_ids

    if cfg.api_key and cfg.secret_key:
        cipher = get_cipher(state.auth_pin)
        cfg_dict["api_key"] = cipher.encrypt(cfg.api_key.encode()).decode()
        cfg_dict["secret_key"] = cipher.encrypt(cfg.secret_key.encode()).decode()
        state.api_key = cfg.api_key
        state.secret_key = cfg.secret_key
    else:
        cfg_dict["api_key"] = saved_cfg.get("api_key", "")
        cfg_dict["secret_key"] = saved_cfg.get("secret_key", "")

    if cfg.discord_bot_token:
        state.discord_bot_token = cfg.discord_bot_token.strip()
        cfg_dict["discord_bot_token"] = state.discord_bot_token
    else:
        state.discord_bot_token = saved_cfg.get("discord_bot_token", state.discord_bot_token)
        cfg_dict["discord_bot_token"] = state.discord_bot_token

    if cfg.discord_channel_id:
        state.discord_channel_id = cfg.discord_channel_id.strip()
        cfg_dict["discord_channel_id"] = state.discord_channel_id
    else:
        state.discord_channel_id = saved_cfg.get("discord_channel_id", state.discord_channel_id)
        cfg_dict["discord_channel_id"] = state.discord_channel_id

    cipher = get_cipher(state.auth_pin)

    # Universal AI Provider Configuration
    if getattr(cfg, "ai_provider", None):
        state.ai_provider = cfg.ai_provider.strip().lower()
    cfg_dict["ai_provider"] = state.ai_provider

    if getattr(cfg, "ai_model", None):
        state.ai_model = cfg.ai_model.strip()
    cfg_dict["ai_model"] = state.ai_model

    if getattr(cfg, "ai_base_url", None) is not None:
        state.ai_base_url = cfg.ai_base_url.strip()
    cfg_dict["ai_base_url"] = state.ai_base_url

    if getattr(cfg, "ai_fallback_provider", None):
        state.ai_fallback_provider = cfg.ai_fallback_provider.strip().lower()
    cfg_dict["ai_fallback_provider"] = state.ai_fallback_provider

    if getattr(cfg, "ai_fallback_model", None):
        state.ai_fallback_model = cfg.ai_fallback_model.strip()
    cfg_dict["ai_fallback_model"] = state.ai_fallback_model

    if getattr(cfg, "ai_api_key", None):
        state.ai_api_key = cfg.ai_api_key.strip()
        cfg_dict["ai_api_key"] = cipher.encrypt(state.ai_api_key.encode()).decode()
    elif "ai_api_key" in saved_cfg:
        cfg_dict["ai_api_key"] = saved_cfg["ai_api_key"]

    if getattr(cfg, "ai_fallback_api_key", None):
        state.ai_fallback_api_key = cfg.ai_fallback_api_key.strip()
        cfg_dict["ai_fallback_api_key"] = cipher.encrypt(state.ai_fallback_api_key.encode()).decode()
    elif "ai_fallback_api_key" in saved_cfg:
        cfg_dict["ai_fallback_api_key"] = saved_cfg["ai_fallback_api_key"]

    # Legacy variables
    if cfg.gemini_api_key:
        state.gemini_api_key = cfg.gemini_api_key.strip()
        cfg_dict["gemini_api_key"] = state.gemini_api_key
        if state.ai_provider == "google" and not getattr(cfg, "ai_api_key", None):
            state.ai_api_key = state.gemini_api_key
    else:
        state.gemini_api_key = saved_cfg.get("gemini_api_key", state.gemini_api_key)
        cfg_dict["gemini_api_key"] = state.gemini_api_key

    state.gemini_model = cfg.gemini_model.strip() if cfg.gemini_model else "gemini-3.1-flash"
    cfg_dict["gemini_model"] = state.gemini_model

    if getattr(cfg, "gemini_search_model", None):
        state.gemini_search_model = cfg.gemini_search_model.strip()
    cfg_dict["gemini_search_model"] = state.gemini_search_model

    state.enable_search_grounding = bool(getattr(cfg, "enable_search_grounding", False))
    cfg_dict["enable_search_grounding"] = state.enable_search_grounding

    if getattr(cfg, "groq_api_key", None):
        state.groq_api_key = cfg.groq_api_key.strip()
        cfg_dict["groq_api_key"] = state.groq_api_key
        if state.ai_fallback_provider == "groq" and not getattr(cfg, "ai_fallback_api_key", None):
            state.ai_fallback_api_key = state.groq_api_key
    else:
        state.groq_api_key = saved_cfg.get("groq_api_key", state.groq_api_key)
        cfg_dict["groq_api_key"] = state.groq_api_key

    state.groq_model = cfg.groq_model.strip() if getattr(cfg, "groq_model", None) else "llama-3.3-70b-versatile"
    cfg_dict["groq_model"] = state.groq_model

    state.ai_scout_enabled = getattr(cfg, "ai_scout_enabled", True)
    state.ai_scout_interval_hours = float(getattr(cfg, "ai_scout_interval_hours", 2.0))
    state.ai_scout_min_confidence = float(getattr(cfg, "ai_scout_min_confidence", 0.85))
    cfg_dict["ai_scout_enabled"] = state.ai_scout_enabled
    cfg_dict["ai_scout_interval_hours"] = state.ai_scout_interval_hours
    cfg_dict["ai_scout_min_confidence"] = state.ai_scout_min_confidence

    await save_config_item("risk_config", cfg_dict)
    logger.info(f"Updated engine config (Keys encrypted using Auth PIN).")
    
    if cfg.api_key or cfg.favorite_pairs:
        logger.info("Restarting Binance connections due to config change...")
        asyncio.create_task(restart_binance_websocket())

    if state.discord_bot_token and state.discord_channel_id:
        from engine.notifier import discord_bot_service
        asyncio.create_task(discord_bot_service.start(state.discord_bot_token, state.discord_channel_id))

    await broadcast_state()
    return {"status": "ok"}

@router.get("/api/logs", dependencies=[Depends(verify_pin)])
async def get_logs():
    return JSONResponse({"logs": list(recent_logs)})

@router.get("/api/trades", dependencies=[Depends(verify_pin)])
async def get_trades(limit: int = 100):
    from engine.db import get_trade_history, get_pnl_summary
    history = await get_trade_history(limit=limit, is_testnet=state.testnet)
    summary = await get_pnl_summary(is_testnet=state.testnet)
    return JSONResponse({
        "trades": history,
        "summary": summary
    })

@router.delete("/api/trades/clear", dependencies=[Depends(verify_pin)])
async def clear_trades(only_rejected: bool = True):
    from engine.db import clear_trade_history
    deleted = await clear_trade_history(only_unexecuted=only_rejected, is_testnet=state.testnet)
    logger.info(f"Purged {deleted} trade records (only_rejected={only_rejected}).")
    await broadcast_state()
    return JSONResponse({"status": "ok", "deleted": deleted})

@router.post("/api/trades/deduplicate", dependencies=[Depends(verify_pin)])
async def api_deduplicate_trades():
    from engine.db import deduplicate_trade_history
    deleted = await deduplicate_trade_history(is_testnet=state.testnet)
    logger.info(f"Manual trade deduplication triggered: {deleted} duplicates removed.")
    await broadcast_state()
    return JSONResponse({"status": "ok", "pruned_duplicates": deleted})

@router.post("/api/discord/test", dependencies=[Depends(verify_pin)])
async def test_discord_connection():
    import discord
    from engine.notifier import discord_bot_service, HAS_DISCORD_PY
    if not HAS_DISCORD_PY:
        return JSONResponse({"status": "error", "message": "discord.py is not installed on this machine."})

    token = state.discord_bot_token.strip().strip('"').strip("'")
    channel_id = "".join(filter(str.isdigit, str(state.discord_channel_id)))

    if not token or not channel_id:
        return JSONResponse({"status": "error", "message": "Bot Token or Channel ID is missing in settings."})

    try:
        if not discord_bot_service.client or not discord_bot_service.is_ready:
            logger.info("Initializing Discord Bot test connection...")
            if not discord_bot_service.is_connecting:
                asyncio.create_task(discord_bot_service.start(token, channel_id))
            try:
                await asyncio.wait_for(discord_bot_service.ready_event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
            if not discord_bot_service.is_ready:
                err_detail = discord_bot_service.last_error or "Gateway handshake took longer than 10s. Check token validity or network."
                return JSONResponse({"status": "error", "message": f"Connection Timed Out: {err_detail}"})

        clean_channel_id = int(channel_id)
        channel = discord_bot_service.client.get_channel(clean_channel_id)
        if not channel:
            channel = await discord_bot_service.client.fetch_channel(clean_channel_id)

        if not channel:
            return JSONResponse({"status": "error", "message": f"Channel ID {clean_channel_id} not found or Bot not invited to server."})

        embed = discord.Embed(
            title="Cripto-3DS Discord Bot Connected",
            description="Discord bot communication test successful! Interactive buttons and slash commands are active.",
            color=0x50fa7b
        )
        embed.add_field(name="Server Time", value=time.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        embed.add_field(name="Engine Status", value="ACTIVE" if state.is_active else "PAUSED", inline=True)
        await channel.send(embed=embed)
        logger.info("Discord test message successfully sent to channel.")
        return JSONResponse({"status": "ok", "message": f"Connected as {discord_bot_service.client.user}! Test message sent."})
    except Exception as e:
        logger.error(f"Discord test error: {type(e).__name__}: {e}")
        return JSONResponse({"status": "error", "message": f"{type(e).__name__}: {e}"})

@router.get("/api/gemini/models", dependencies=[Depends(verify_pin)])
async def get_gemini_models():
    from engine.ai_analyst import fetch_available_gemini_models
    key = getattr(state, "ai_api_key", "") or getattr(state, "gemini_api_key", "")
    models = await fetch_available_gemini_models(key)
    if models:
        state.available_gemini_models = models
    return JSONResponse({"models": state.available_gemini_models})

@router.get("/api/symbols", dependencies=[Depends(verify_pin)])
async def get_symbols():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.binance.com/api/v3/exchangeInfo") as resp:
                info = await resp.json()
                symbols = [s['symbol'] for s in info.get('symbols', []) if s['symbol'].endswith('USDT')]
                return JSONResponse({"symbols": sorted(symbols)})
    except Exception as e:
        logger.error(f"Error fetching symbols: {e}")
        return JSONResponse({"symbols": []})

@router.post("/api/bot/toggle", dependencies=[Depends(verify_pin)])
async def toggle_bot(active: bool):
    state.is_active = active
    logger.info(f"Bot toggled: active={active}")
    await broadcast_state()
    return {"status": "ok", "is_active": state.is_active}

from engine.trades import decide_trade, decide_all_trades

@router.post("/api/trade/decide", dependencies=[Depends(verify_pin)])
async def api_decide_trade(approved: bool, trade_id: int = None, pair: str = None, override_usdt: float = None):
    return await decide_trade(approved=approved, trade_id=trade_id, pair=pair, override_usdt=override_usdt)

@router.post("/api/trade/decide_all", dependencies=[Depends(verify_pin)])
async def api_decide_all_trades(approved: bool):
    results = await decide_all_trades(approved=approved)
    return {"status": "ok", "results": results}

@router.post("/api/trade/simulate", dependencies=[Depends(verify_pin)])
async def simulate_trade(count: int = 1):
    count = max(1, min(4, count))
    target_pairs = state.favorite_pairs[:count] or ["BTCUSDT", "ETHUSDT"][:count]
    now = time.time()
    staged = []

    for i, p in enumerate(target_pairs):
        curr_price = state.prices.get(p, 0.0) or (64000.0 if "BTC" in p else (3400.0 if "ETH" in p else 150.0))
        trade_id = int(now * 1000) + i
        t = {
            "id": trade_id,
            "action": "BUY" if i % 2 == 0 else "SELL",
            "pair": p,
            "amount_usdt": risk_manager.max_trade_usdt,
            "amount_asset": risk_manager.max_trade_usdt / curr_price,
            "price": curr_price,
            "reason": f"Simulated Test Signal ({p})",
            "created_at": now,
            "timeout_sec": 600
        }
        state.add_pending_trade(t)
        staged.append(t)

    logger.info(f"Simulated {len(staged)} trade decision(s) queued.")
    
    from engine.notifier import send_discord_notification
    cfg = await load_config_item("risk_config") or {}
    subject = f"Crypto Bot Alert: Simulated Signals ({len(staged)} Assets)"
    body = f"Simulated trade signals require approval: {', '.join(t['pair'] for t in staged)}"
    asyncio.create_task(send_discord_notification(subject, body, cfg, trades=staged))
    
    await broadcast_state()
    return {"status": "ok", "pending_trades": staged, "pending_trade": state.pending_trade}

@router.post("/api/trade/force")
async def force_evaluate_endpoint(x_auth_pin: str = Header(None)):
    if x_auth_pin != state.auth_pin:
        raise HTTPException(status_code=401, detail="Unauthorized")
    state.rsi_strategy.cooldowns.clear()
    state.dca_strategy.cooldowns.clear()
    state.tpsl_strategy.cooldowns.clear()
    await save_strategy_state()
    return {"status": "cooldowns_cleared"}

from pydantic import BaseModel

class ManualSellRequest(BaseModel):
    asset: str
    percent: float = 100.0
    pin: str

class ManualBuyRequest(BaseModel):
    asset: str
    usdt_amount: float
    pin: str

@router.post("/api/trade/manual_sell")
@router.post("/api/manual_sell")
async def api_manual_sell(req: ManualSellRequest):
    from engine.trades import execute_manual_sell
    res = await execute_manual_sell(req.asset, req.percent, req.pin)
    if res.get("status") == "error":
        return JSONResponse(res, status_code=400)
    return JSONResponse(res)

@router.post("/api/trade/manual_buy")
@router.post("/api/manual_buy")
async def api_manual_buy(req: ManualBuyRequest):
    from engine.trades import execute_manual_buy
    res = await execute_manual_buy(req.asset, req.usdt_amount, req.pin)
    if res.get("status") == "error":
        return JSONResponse(res, status_code=400)
    return JSONResponse(res)

@router.post("/api/balance/sync", dependencies=[Depends(verify_pin)])
async def api_sync_balance():
    from engine.trades import sync_binance_balances
    await sync_binance_balances()
    await broadcast_state()
    return {"status": "ok", "usdt_balance": state.usdt_balance, "portfolio": state.portfolio_balances}

@router.get("/api/news", dependencies=[Depends(verify_pin)])
async def get_news_insights():
    from engine.ai_analyst import summarize_news_insights
    data = await summarize_news_insights(state.gemini_api_key, state.gemini_model)
    return JSONResponse(data)

@router.post("/api/sync/trades_2026", dependencies=[Depends(verify_pin)])
async def api_sync_2026_trades():
    from engine.trades import sync_binance_2026_trades
    res = await sync_binance_2026_trades()
    return JSONResponse(res)

@router.post("/api/test/run", dependencies=[Depends(verify_pin)])
async def api_run_test_suite():
    import asyncio, time, sys
    start = time.time()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest", "tests/test_engine.py", "-k", "not test_api_run_test_suite", "-v",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    duration = round(time.time() - start, 2)
    output = stdout.decode() + stderr.decode()
    passed = output.count("PASSED")
    return {
        "status": "success" if proc.returncode == 0 else "error",
        "exit_code": proc.returncode,
        "passed": passed,
        "duration": duration,
        "log": output
    }

