import os
import asyncio
import time
from engine.logger import logger
from engine.state import state
from engine.risk_manager import risk_manager
from engine.db import log_trade, get_average_buy_price
from engine.ws_manager import broadcast_state

async def fetch_binance_cost_basis(client, pair: str, current_qty: float) -> float:
    try:
        trades = await client.get_my_trades(symbol=pair)
        if not trades:
            return 0.0
            
        trades.reverse() # Newest first
        accumulated_qty = 0.0
        accumulated_cost = 0.0
        
        for t in trades:
            if t.get('isBuyer'):
                qty = float(t.get('qty', 0))
                price = float(t.get('price', 0))
                
                # Only average the buys that make up our current holdings
                needed = current_qty - accumulated_qty
                if needed <= 0:
                    break
                    
                use_qty = min(qty, needed)
                accumulated_qty += use_qty
                accumulated_cost += use_qty * price
                
        if accumulated_qty > 0:
            return accumulated_cost / accumulated_qty
    except Exception as e:
        logger.error(f"Error fetching Binance trades for {pair}: {e}")
    return 0.0

async def sync_binance_balances():
    if state.binance_client:
        try:
            account = await state.binance_client.get_account()
            new_portfolio = {}
            for asset in account.get("balances", []):
                free_val = float(asset.get("free", 0.0))
                locked_val = float(asset.get("locked", 0.0))
                total_val = free_val + locked_val
                if total_val > 0:
                    new_portfolio[asset["asset"]] = total_val
                if asset["asset"] == "USDT":
                    state.usdt_balance = total_val
            state.portfolio_balances = new_portfolio
            logger.info(f"Synced live Binance account balances: USDT=${state.usdt_balance:.2f}")
        except Exception as e:
            logger.warning(f"Could not sync Binance account balances: {e}")

async def refresh_cost_bases():
    for asset, qty in state.portfolio_balances.items():
        if qty > 0 and asset != "USDT":
            pair = asset + "USDT"
            
            binance_avg = 0.0
            if state.binance_client and state.is_active:
                binance_avg = await fetch_binance_cost_basis(state.binance_client, pair, qty)
                
            if binance_avg > 0:
                state.cost_bases[pair] = binance_avg
            else:
                state.cost_bases[pair] = await get_average_buy_price(pair, state.testnet)

import math
from typing import Dict, Any, Tuple

def get_symbol_filter(pair: str) -> Dict[str, float]:
    return state.exchange_filters.get(pair, {
        "minQty": 0.00001,
        "maxQty": 999999.0,
        "stepSize": 0.0001,
        "minNotional": 5.0,
        "tickSize": 0.01
    })

def format_and_validate_order(pair: str, action: str, amount_usdt: float, price: float, raw_qty: float = 0.0) -> Tuple[bool, float, float, str]:
    filters = get_symbol_filter(pair)
    step = filters.get("stepSize", 0.0001)
    min_notional = filters.get("minNotional", 5.0)
    min_qty = filters.get("minQty", 0.00001)

    if price <= 0:
        return False, 0.0, 0.0, f"Invalid price {price}"

    if action == "BUY":
        raw_calc_qty = amount_usdt / price
        if step >= 1.0:
            qty = float(math.floor(raw_calc_qty / step) * step)
        else:
            precision = max(0, -int(math.floor(math.log10(step)))) if step > 0 else 4
            qty = math.floor(raw_calc_qty / step) * step
            qty = round(qty, precision)

        effective_usdt = round(qty * price, 2)
        if effective_usdt < min_notional:
            return False, qty, effective_usdt, f"Order ${effective_usdt:.2f} is below Binance minNotional (${min_notional:.2f})"
        if qty < min_qty:
            return False, qty, effective_usdt, f"Quantity {qty} is below minQty ({min_qty})"
        return True, qty, effective_usdt, "OK"
    else: # SELL
        if raw_qty <= 0:
            return False, 0.0, 0.0, "No asset balance to sell"

        if step >= 1.0:
            qty = float(math.floor(raw_qty / step) * step)
        else:
            precision = max(0, -int(math.floor(math.log10(step)))) if step > 0 else 4
            qty = math.floor(raw_qty / step) * step
            qty = round(qty, precision)

        effective_usdt = round(qty * price, 2)
        if effective_usdt < min_notional:
            return False, qty, effective_usdt, f"Sell value ${effective_usdt:.2f} is below minNotional (${min_notional:.2f}) - Dust"
        if qty < min_qty:
            return False, qty, effective_usdt, f"Quantity {qty} is below minQty ({min_qty})"
        return True, qty, effective_usdt, "OK"

async def decide_trade(approved: bool, override_usdt: float = None):
    if state.pending_trade:
        trade = state.pending_trade
        if approved and override_usdt is not None and override_usdt > 0:
            trade['amount_usdt'] = override_usdt
            if trade['price'] > 0:
                trade['amount_asset'] = override_usdt / trade['price']
        state.pending_trade = None
        if approved:
            logger.info(f"Trade APPROVED: {trade['action']} {trade['pair']}")
            
            # 1. Risk Manager validation
            valid, reason = risk_manager.validate_trade(trade['action'], trade['amount_usdt'], state.usdt_balance)
            if not valid:
                logger.warning(f"Trade blocked by RiskManager: {reason}")
                await log_trade(trade['pair'], trade['action'], trade['amount_usdt'], trade['price'], f"BLOCKED: {reason}", is_testnet=state.testnet)
                await broadcast_state()
                return {"status": "blocked", "reason": reason}

            # 2. Exchange Filter & Lot Size validation
            raw_qty = trade.get('amount_asset', 0.0)
            ex_valid, adj_qty, adj_usdt, ex_reason = format_and_validate_order(
                trade['pair'], trade['action'], trade['amount_usdt'], trade['price'], raw_qty
            )
            if not ex_valid:
                logger.warning(f"Trade blocked by Exchange Filter: {ex_reason}")
                await log_trade(trade['pair'], trade['action'], trade['amount_usdt'], trade['price'], f"BLOCKED: {ex_reason}", is_testnet=state.testnet)
                await broadcast_state()
                return {"status": "blocked", "reason": ex_reason}

            order_id = "SIMULATED_ORDER"
            if state.binance_client and state.is_active:
                try:
                    if trade['action'] == 'BUY':
                        order = await state.binance_client.create_order(
                            symbol=trade['pair'],
                            side='BUY',
                            type='MARKET',
                            quoteOrderQty=adj_usdt
                        )
                    else:
                        logger.info(f"Executing SELL order on Binance: {adj_qty} {trade['pair']}")
                        order = await state.binance_client.create_order(
                            symbol=trade['pair'],
                            side='SELL',
                            type='MARKET',
                            quantity=adj_qty
                        )
                    order_id = str(order.get('orderId', ''))
                    logger.info(f"Binance order executed: {order_id}")
                except Exception as e:
                    logger.error(f"Binance order failed: {e}")

            # Calculate realized PnL if SELL
            realized_pnl_usdt = 0.0
            realized_pnl_percent = 0.0
            if trade['action'] == 'SELL':
                cost_basis = state.cost_bases.get(trade['pair'], 0.0)
                if cost_basis > 0 and trade['price'] > 0:
                    sell_qty = adj_qty if adj_qty > 0 else (trade.get('amount_usdt', 0.0) / trade['price'])
                    realized_pnl_usdt = round((trade['price'] - cost_basis) * sell_qty, 2)
                    realized_pnl_percent = round(((trade['price'] - cost_basis) / cost_basis) * 100, 2)
                    logger.info(f"Realized PnL for {trade['pair']}: ${realized_pnl_usdt:+.2f} ({realized_pnl_percent:+.2f}%)")

            risk_manager.record_spend(trade.get('amount_usdt', 0.0))
            await log_trade(
                trade['pair'],
                trade['action'],
                trade.get('amount_usdt', 0.0),
                trade['price'],
                "EXECUTED",
                order_id,
                is_testnet=state.testnet,
                realized_pnl_usdt=realized_pnl_usdt,
                realized_pnl_percent=realized_pnl_percent
            )
            
            if state.binance_client:
                await sync_binance_balances()
            else:
                asset = trade['pair'].replace("USDT", "")
                if trade['action'] == 'BUY':
                    state.usdt_balance = max(0.0, state.usdt_balance - adj_usdt)
                    state.portfolio_balances[asset] = state.portfolio_balances.get(asset, 0.0) + adj_qty
                else:
                    state.usdt_balance += adj_usdt
                    state.portfolio_balances[asset] = max(0.0, state.portfolio_balances.get(asset, 0.0) - adj_qty)

            asyncio.create_task(refresh_cost_bases())
            await broadcast_state()
            return {
                "status": "approved",
                "trade": trade,
                "order_id": order_id,
                "realized_pnl_usdt": realized_pnl_usdt,
                "realized_pnl_percent": realized_pnl_percent
            }
        else:
            logger.info(f"Trade REJECTED: {trade['action']} {trade['pair']}")
            await log_trade(trade['pair'], trade['action'], trade['amount_usdt'], trade['price'], "REJECTED", is_testnet=state.testnet)
            await broadcast_state()
            return {"status": "rejected", "trade": trade}
    return {"status": "no_pending_trade"}


async def execute_manual_sell(asset: str, percent: float, pin: str) -> Dict[str, Any]:
    asset = asset.upper().strip()
    if asset == "USDT":
        return {"status": "error", "message": "Cannot sell USDT for USDT."}

    if pin != state.auth_pin:
        return {"status": "error", "message": "Invalid PIN code."}

    total_qty = state.portfolio_balances.get(asset, 0.0)
    if total_qty <= 0:
        return {"status": "error", "message": f"No {asset} balance available in portfolio."}

    percent = max(1.0, min(100.0, float(percent)))
    raw_qty = total_qty * (percent / 100.0)

    pair = f"{asset}USDT"
    curr_price = state.prices.get(pair, 0.0)
    if curr_price <= 0 and state.binance_client:
        try:
            ticker = await state.binance_client.get_symbol_ticker(symbol=pair)
            curr_price = float(ticker.get("price", 0.0))
        except Exception as e:
            logger.warning(f"Could not fetch ticker for {pair}: {e}")

    if curr_price <= 0:
        return {"status": "error", "message": f"Unable to determine current price for {pair}."}

    # Validate against exchange filters & minNotional
    ex_valid, adj_qty, adj_usdt, ex_reason = format_and_validate_order(
        pair, "SELL", raw_qty * curr_price, curr_price, raw_qty
    )
    if not ex_valid:
        return {"status": "error", "message": ex_reason}

    order_id = "SIMULATED_MANUAL_SELL"
    if state.binance_client and state.is_active:
        try:
            logger.info(f"Executing MANUAL SELL on Binance: {adj_qty} {pair}")
            order = await state.binance_client.create_order(
                symbol=pair,
                side='SELL',
                type='MARKET',
                quantity=adj_qty
            )
            order_id = str(order.get('orderId', ''))
            logger.info(f"Manual Binance sell executed: {order_id}")
        except Exception as e:
            logger.error(f"Manual Binance sell failed: {e}")
            return {"status": "error", "message": f"Binance error: {e}"}

    # Calculate realized PnL
    cost_basis = state.cost_bases.get(pair, 0.0)
    realized_pnl_usdt = 0.0
    realized_pnl_percent = 0.0
    if cost_basis > 0 and curr_price > 0:
        realized_pnl_usdt = round((curr_price - cost_basis) * adj_qty, 2)
        realized_pnl_percent = round(((curr_price - cost_basis) / cost_basis) * 100, 2)
        logger.info(f"Manual sell PnL for {pair}: ${realized_pnl_usdt:+.2f} ({realized_pnl_percent:+.2f}%)")

    await log_trade(
        pair,
        "SELL",
        adj_usdt,
        curr_price,
        "EXECUTED",
        order_id,
        is_testnet=state.testnet,
        realized_pnl_usdt=realized_pnl_usdt,
        realized_pnl_percent=realized_pnl_percent
    )

    if state.binance_client:
        await sync_binance_balances()
    else:
        # Update local portfolio state in simulated mode
        state.portfolio_balances[asset] = max(0.0, total_qty - adj_qty)
        state.usdt_balance += adj_usdt

    asyncio.create_task(refresh_cost_bases())
    await broadcast_state()

    return {
        "status": "success",
        "pair": pair,
        "sold_qty": adj_qty,
        "amount_usdt": adj_usdt,
        "price": curr_price,
        "order_id": order_id,
        "realized_pnl_usdt": realized_pnl_usdt,
        "realized_pnl_percent": realized_pnl_percent
    }
