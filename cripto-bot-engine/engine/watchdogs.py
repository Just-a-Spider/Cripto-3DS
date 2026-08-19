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
