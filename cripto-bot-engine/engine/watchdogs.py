import asyncio
from engine.logger import logger
from engine.state import state
from engine.ws_manager import broadcast_state
from engine.db import log_trade
from engine.trades import refresh_cost_bases

async def trade_timeout_watchdog():
    while True:
        await asyncio.sleep(1)
        if state.pending_trade:
            state.pending_trade["timeout_sec"] -= 1
            if state.pending_trade["timeout_sec"] <= 0:
                logger.info(f"Pending trade {state.pending_trade['id']} EXPIRED (10m timeout). Auto-cancelling.")
                await log_trade(state.pending_trade['pair'], state.pending_trade['action'], state.pending_trade['amount_usdt'], state.pending_trade.get('price', 0.0), "EXPIRED_TIMEOUT", is_testnet=state.testnet)
                state.pending_trade = None
                await broadcast_state()

async def cost_basis_watchdog():
    while True:
        await refresh_cost_bases()
        await asyncio.sleep(60)

import time
from engine.ai_analyst import scan_market_opportunities
from engine.risk_manager import risk_manager
from engine.db import load_config_item

_last_scout_time = 0.0
_scout_cooldowns = {}

async def ai_opportunity_scout_watchdog():
    global _last_scout_time
    logger.info("Started AI Opportunity Scout Watchdog.")
    while True:
        try:
            if getattr(state, "ai_scout_enabled", True) and state.is_active and state.gemini_api_key and not state.pending_trade:
                interval_hours = max(0.25, float(getattr(state, "ai_scout_interval_hours", 2.0)))
                interval_sec = interval_hours * 3600.0
                now = time.time()

                if (now - _last_scout_time) >= interval_sec:
                    _last_scout_time = now
                    logger.info(f"AI Opportunity Scout running scheduled market scan (interval: {interval_hours}h)...")

                    market_ctx = state.to_dict()
                    result = await scan_market_opportunities(market_ctx, state.gemini_api_key, model=state.gemini_model)
                    opps = result.get("top_opportunities", [])
                    min_conf = float(getattr(state, "ai_scout_min_confidence", 0.85))

                    evaluated_count = len(opps)
                    trade_staged = False

                    for opp in opps:
                        pair = opp.get("pair", "")
                        stype = opp.get("setup_type", "")
                        conf = float(opp.get("confidence", 0.0))
                        analysis = opp.get("analysis", "")

                        if not pair or conf < min_conf:
                            logger.info(f"AI Scout: Skipping {pair} ({int(conf*100)}% Conf) - Below {int(min_conf*100)}% threshold.")
                            continue

                        last_sig_time = _scout_cooldowns.get(pair, 0.0)
                        if (now - last_sig_time) < (state.signal_cooldown_hours * 3600):
                            logger.info(f"AI Scout: Skipping {pair} - In cooldown ({state.signal_cooldown_hours}h).")
                            continue

                        curr_price = state.prices.get(pair, 0.0)
                        if curr_price <= 0 and state.binance_client:
                            try:
                                ticker = await state.binance_client.get_symbol_ticker(symbol=pair)
                                curr_price = float(ticker.get("price", 0.0))
                            except Exception:
                                pass

                        if curr_price <= 0:
                            logger.info(f"AI Scout: Skipping {pair} - Live price unavailable.")
                            continue

                        action = "SELL" if "PROFIT" in stype.upper() or "EXIT" in stype.upper() else "BUY"

                        if action == "BUY":
                            max_buy = risk_manager.get_max_allowed_buy(state.usdt_balance)
                            if max_buy < 5.0:
                                logger.info(f"AI Scout: Skipping BUY {pair} - Insufficient USDT balance (${state.usdt_balance:.2f} < $5 min).")
                                continue
                            amount_usdt = max_buy
                            amount_asset = amount_usdt / curr_price
                        else:
                            asset = pair.replace("USDT", "")
                            qty = state.portfolio_balances.get(asset, 0.0)
                            asset_val = qty * curr_price
                            if asset_val < 5.0:
                                logger.info(f"AI Scout: Skipping SELL {pair} - Insufficient {asset} holding (${asset_val:.2f} < $5.00 Binance min).")
                                continue
                            amount_asset = qty * getattr(state, "partial_tp_ratio", 0.5) if getattr(state, "partial_tp_enabled", True) else qty
                            amount_usdt = amount_asset * curr_price
                            if amount_usdt < 5.0:
                                amount_asset = qty
                                amount_usdt = qty * curr_price

                        _scout_cooldowns[pair] = now
                        state.pending_trade = {
                            "id": int(now),
                            "action": action,
                            "pair": pair,
                            "amount_usdt": amount_usdt,
                            "amount_asset": amount_asset,
                            "price": curr_price,
                            "reason": f"AI Scout Setup: {stype} ({int(conf*100)}% Conf) - {analysis[:120]}",
                            "created_at": now,
                            "timeout_sec": 600,
                            "is_ai_scout": True
                        }

                        logger.info(f"AI Opportunity Scout generated proposed trade: {action} {pair} (Conf: {conf})")
                        await broadcast_state()

                        from engine.notifier import send_discord_notification
                        cfg = await load_config_item("risk_config") or {}
                        subject = f"Crypto Bot Alert: AI Scout {action} {pair}"
                        body = f"AI Opportunity Scout detected a high-probability {stype} setup ({int(conf*100)}% confidence).\nReason: {analysis}"
                        asyncio.create_task(send_discord_notification(subject, body, cfg, trade=state.pending_trade))
                        trade_staged = True
                        break

                    if not trade_staged:
                        logger.info(f"AI Scout: Completed scan across {evaluated_count} setups. No eligible trades to stage (market overbought / insufficient asset balances).")
        except Exception as e:
            logger.error(f"Error in AI Opportunity Scout watchdog: {e}")

        await asyncio.sleep(15)
