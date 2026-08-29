import asyncio
import datetime
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
                # Volatility-adjusted interval: range-bound markets scan more frequently,
                # high-volatility markets scan less frequently to reduce API load
                _rsi_history = getattr(state.rsi_strategy, 'price_histories', {})
                _recent_rsi_ranges = []
                for _pair in state.favorite_pairs:
                    _hist = _rsi_history.get(_pair, [])
                    if len(_hist) > 10:
                        _recent_rsi_ranges.append(max(_hist[-10:]) - min(_hist[-10:]))
                _avg_rsi_range = sum(_recent_rsi_ranges) / len(_recent_rsi_ranges) if _recent_rsi_ranges else 20

                if _avg_rsi_range <= 15:        # Range-bound
                    _vol_factor = 0.8           # Narrow interval to 1.6h
                elif _avg_rsi_range <= 30:     # Moderate
                    _vol_factor = 1.0           # Standard interval 2h
                else:                          # High volatility
                    _vol_factor = 1.3           # Widen interval to 2.6h

                interval_hours = max(0.25, float(getattr(state, "ai_scout_interval_hours", 2.0)) * _vol_factor)
                interval_sec = interval_hours * 3600.0
                now = time.time()

                if (now - _last_scout_time) >= interval_sec:
                    _last_scout_time = now
                    logger.info(f"AI Opportunity Scout running scheduled market scan (interval: {interval_hours}h)...")

                    market_ctx = state.to_dict()
                    
                    # Extract market regime from indicators for AI context
                    _indicators = market_ctx.get("indicators", {})
                    _rsi_vals = []
                    for _pair, _ind in _indicators.items():
                        _rsi = _ind.get("rsi", 50)
                        if isinstance(_rsi, (int, float)):
                            _rsi_vals.append(_rsi)
                    _avg_rsi = sum(_rsi_vals) / len(_rsi_vals) if _rsi_vals else 50
                    if _avg_rsi >= 55:
                        _market_regime = "BULLISH_GREED"
                    elif _avg_rsi <= 45:
                        _market_regime = "BEARISH_FEAR"
                    else:
                        _market_regime = "NEUTRAL"
                    
                    result = await scan_market_opportunities(market_ctx, state.gemini_api_key, model=state.gemini_model, market_regime=_market_regime)
                    opps = result.get("top_opportunities", [])
                    _min_conf_base = float(getattr(state, "ai_scout_min_confidence", 0.85))

                    # Time-of-day confidence factor
                    now = time.time()
                    hour = datetime.datetime.utcfromtimestamp(now).hour
                    if 0 <= hour < 6:
                        _time_factor = 0.95   # Asian session — quieter, lower threshold
                    elif 6 <= hour < 18:
                        _time_factor = 1.0    # Mixed sessions — standard threshold
                    else:
                        _time_factor = 0.9    # US session — higher volatility, lower threshold
                    min_conf = _min_conf_base * _time_factor

                    # Confidence decay for stale analyses
                    _age_factor = max(0.0, 1.0 - (now - _last_scout_time) / (interval_hours * 3600.0 + 60))
                    adjusted_min_conf = min_conf * _age_factor

                    evaluated_count = len(opps)
                    trade_staged = False

                    for opp in opps:
                        pair = opp.get("pair", "")
                        stype = opp.get("setup_type", "")
                        conf = float(opp.get("confidence", 0.0))
                        analysis = opp.get("analysis", "")

                        if not pair or conf < adjusted_min_conf:
                            logger.info(f"AI Scout: Skipping {pair} ({int(conf*100)}% Conf) - Below {int(adjusted_min_conf*100)}% threshold (age factor: {_age_factor:.2f}).")
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

                        if not risk_manager.require_human_approval:
                            logger.info(f"AI Scout: Auto-executing trade (approval not required): {action} {pair}")
                            from engine.trades import decide_trade
                            result = await decide_trade(approved=True)
                            logger.info(f"AI Scout auto-execution result: {result.get('status')}")

                            from engine.notifier import send_discord_notification
                            cfg = await load_config_item("risk_config") or {}
                            subject = f"Crypto Bot Alert: AI Scout {action} {pair} Auto-Executed"
                            body = f"AI Opportunity Scout trade automatically executed ({int(conf*100)}% confidence).\nReason: {analysis}\nStatus: {result.get('status')}"
                            asyncio.create_task(send_discord_notification(subject, body, cfg))
                        else:
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
