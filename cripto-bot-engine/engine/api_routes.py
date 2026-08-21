import asyncio
import time
import json
import aiohttp
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

def verify_pin(request: Request, x_auth_pin: str = Header(None)):
    if request.client.host == "127.0.0.1":
        return
    if x_auth_pin != state.auth_pin:
        logger.warning(f"Invalid PIN received: {x_auth_pin!r} (expected {state.auth_pin!r}) from {request.client.host}")
        raise HTTPException(status_code=401, detail="Invalid PIN")

@router.get("/", response_class=HTMLResponse)
async def get_index():
    return HTMLResponse("<h1>Engine is running.</h1>")

@router.get("/web", response_class=HTMLResponse)
async def get_web():
    return FileResponse("web_companion.html")

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, pin: str = None):
    await ws_manager.connect(websocket)
    if websocket.client.host != "127.0.0.1":
        if pin != state.auth_pin:
            await websocket.close(code=1008)
            ws_manager.disconnect(websocket)
            return
    try:
        await websocket.send_text(json.dumps(state.to_dict())) # Wait, json is needed.
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
    cfg_dict = cfg.dict()

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

    if cfg.gemini_api_key:
        state.gemini_api_key = cfg.gemini_api_key.strip()
        cfg_dict["gemini_api_key"] = state.gemini_api_key
    else:
        state.gemini_api_key = saved_cfg.get("gemini_api_key", state.gemini_api_key)
        cfg_dict["gemini_api_key"] = state.gemini_api_key

    state.gemini_model = cfg.gemini_model.strip() if cfg.gemini_model else "gemini-3.1-flash-lite"
    cfg_dict["gemini_model"] = state.gemini_model

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
async def get_trades():
    from engine.db import get_trade_history, get_pnl_summary
    history = await get_trade_history(limit=50, is_testnet=state.testnet)
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
            title="✅ Cripto-3DS Discord Bot Connected",
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
    models = await fetch_available_gemini_models(state.gemini_api_key)
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

@router.post("/api/trade/decide", dependencies=[Depends(verify_pin)])
async def api_decide_trade(approved: bool, override_usdt: float = None):
    return await decide_trade(approved, override_usdt)

@router.post("/api/trade/simulate", dependencies=[Depends(verify_pin)])
async def simulate_trade():
    curr_price = state.prices.get("BTCUSDT", 0.0) or 64000.0
    state.pending_trade = {
        "id": int(time.time()),
        "action": "BUY",
        "pair": "BTCUSDT",
        "amount_usdt": risk_manager.max_trade_usdt,
        "price": curr_price,
        "reason": "Simulated Test Buy",
        "created_at": time.time(),
        "timeout_sec": 600
    }
    logger.info("Simulated trade decision queued.")
    
    from engine.notifier import send_discord_notification
    cfg = await load_config_item("risk_config") or {}
    subject = f"Crypto Bot Alert: BUY BTCUSDT (Simulated)"
    body = f"A new BUY signal for BTCUSDT requires your approval.\nPrice: {curr_price}\nReason: Simulated Test Buy"
    asyncio.create_task(send_discord_notification(subject, body, cfg, trade=state.pending_trade))
    
    await broadcast_state()
    return {"status": "ok", "pending_trade": state.pending_trade}

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

@router.post("/api/test/run", dependencies=[Depends(verify_pin)])
async def api_run_test_suite():
    import asyncio, time
    start = time.time()
    proc = await asyncio.create_subprocess_exec(
        ".venv/bin/python3", "-m", "pytest", "tests/test_engine.py", "-k", "not test_api_run_test_suite", "-v",
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
