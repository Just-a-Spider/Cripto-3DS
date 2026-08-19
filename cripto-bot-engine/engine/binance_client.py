import asyncio
import time
from binance import AsyncClient, BinanceSocketManager
from engine.logger import logger
from engine.state import state
from engine.ws_manager import broadcast_state
from engine.risk_manager import risk_manager
from engine.shared import save_strategy_state
from engine.db import load_config_item

async def listen_user_data(bm):
    try:
        async with bm.user_socket() as stream:
            while True:
                res = await stream.recv()
                if res and res.get('e') == 'outboundAccountPosition':
                    for bal in res.get('B', []):
                        amt = float(bal['f'])
                        if amt > 0 or bal['a'] in state.portfolio_balances:
                            state.portfolio_balances[bal['a']] = amt
                        if bal['a'] == 'USDT':
                            state.usdt_balance = amt
                    logger.info("WS Updated Portfolio Balances")
                    await broadcast_state()
    except Exception as e:
        logger.error(f"User socket error: {e}")

async def listen_market_data(bm):
    try:
        streams = [f"{pair.lower()}@ticker" for pair in state.favorite_pairs]
        if not streams: return
        async with bm.multiplex_socket(streams) as stream:
            while True:
                res = await stream.recv()
                if res and 'data' in res:
                    data = res['data']
                    symbol = data.get('s')
                    close_price = float(data.get('c', 0.0))
                    if symbol in state.favorite_pairs:
                        state.prices[symbol] = close_price
                        
                        if state.is_active and not state.pending_trade:
                            tpsl_sig = state.tpsl_strategy.evaluate_tpsl(state.prices, state.portfolio_balances, state.cost_bases, state.signal_cooldown_hours)
                            
                            sig = tpsl_sig
                            max_buy = 0.0
                            if not sig:
                                max_buy = risk_manager.get_max_allowed_buy(state.usdt_balance)
                                can_buy = max_buy >= 5.0
                                
                                dca_sig = None
                                if can_buy:
                                    dca_sig = state.dca_strategy.evaluate(state.prices, state.usdt_balance, state.favorite_pairs, state.signal_cooldown_hours)
                                    
                                rsi_sig = state.rsi_strategy.evaluate(
                                    state.prices, state.usdt_balance, state.favorite_pairs, 
                                    state.portfolio_balances, state.cost_bases, state.signal_cooldown_hours, can_buy=can_buy
                                )
                                sig = dca_sig or rsi_sig
                                
                            if sig:
                                amount_usdt = max_buy if sig['action'] == 'BUY' else risk_manager.max_trade_usdt
                                amount_asset = sig.get("amount_asset", 0.0)
                                if sig['action'] == 'SELL':
                                    amount_usdt = amount_asset * sig['price']
                                    
                                state.pending_trade = {
                                    "id": int(time.time()),
                                    "action": sig["action"],
                                    "pair": sig["pair"],
                                    "amount_usdt": amount_usdt,
                                    "amount_asset": amount_asset,
                                    "price": sig["price"],
                                    "reason": sig.get("reason", sig.get("strategy")),
                                    "created_at": time.time(),
                                    "timeout_sec": 600
                                }
                                logger.info(f"Strategy signal generated: {sig}")
                                await save_strategy_state()

                                from engine.notifier import send_discord_notification
                                cfg = await load_config_item("risk_config") or {}
                                subject = f"Crypto Bot Alert: {sig['action']} {sig['pair']}"
                                body = f"A new {sig['action']} signal for {sig['pair']} requires your approval.\nPrice: {sig['price']}\nReason: {sig.get('reason', sig.get('strategy'))}"
                                asyncio.create_task(send_discord_notification(subject, body, cfg, trade=state.pending_trade))

                        await broadcast_state()
    except Exception as e:
        logger.error(f"Market socket error: {e}")

async def start_binance_websocket():
    if not state.api_key:
        logger.warning("No Binance API Key set. Waiting for user configuration...")
        return

    try:
        client = await AsyncClient.create(api_key=state.api_key, api_secret=state.secret_key, testnet=state.testnet)
        state.binance_client = client
        logger.info(f"Connected to Binance AsyncClient (Testnet={state.testnet})")
        
        try:
            account = await client.get_account()
            for asset in account.get("balances", []):
                free_val = float(asset["free"])
                if free_val > 0:
                    state.portfolio_balances[asset["asset"]] = free_val
                if asset["asset"] == "USDT":
                    state.usdt_balance = free_val
            logger.info(f"Initial API USDT Balance: ${state.usdt_balance:.2f}")
            
            if state.favorite_pairs:
                interval_map = {1: '1m', 3: '3m', 5: '5m', 15: '15m', 30: '30m', 60: '1h', 120: '2h', 240: '4h', 1440: '1d'}
                kline_interval = interval_map.get(state.rsi_strategy.timeframe_minutes, '1h')
                
                for pair in state.favorite_pairs:
                    klines = await client.get_klines(symbol=pair, interval=kline_interval, limit=state.rsi_strategy.history_length)
                    history = [float(k[4]) for k in klines]
                    state.rsi_strategy.price_histories[pair] = history
                    state.rsi_strategy.last_sample_times[pair] = time.time()
            # Preload Binance exchange filters (minNotional, stepSize, etc.)
            try:
                ex_info = await client.get_exchange_info()
                for s in ex_info.get("symbols", []):
                    sym = s.get("symbol")
                    filters = {}
                    for f in s.get("filters", []):
                        ft = f.get("filterType")
                        if ft == "LOT_SIZE":
                            filters["minQty"] = float(f.get("minQty", 0.0))
                            filters["maxQty"] = float(f.get("maxQty", 0.0))
                            filters["stepSize"] = float(f.get("stepSize", 0.0001))
                        elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
                            filters["minNotional"] = float(f.get("minNotional", f.get("notional", 5.0)))
                        elif ft == "PRICE_FILTER":
                            filters["tickSize"] = float(f.get("tickSize", 0.01))
                    state.exchange_filters[sym] = filters
                logger.info(f"Cached exchange filters for {len(state.exchange_filters)} symbols.")
            except Exception as ef_err:
                logger.warning(f"Could not preload exchange filters: {ef_err}")

            await broadcast_state()
        except Exception as e:
            logger.warning(f"Could not fetch account balance or RSI history initially: {e}")

        bm = BinanceSocketManager(client)
        t1 = asyncio.create_task(listen_user_data(bm))
        t2 = asyncio.create_task(listen_market_data(bm))
        state.ws_tasks = [t1, t2]
    except Exception as e:
        logger.error(f"Failed to start Binance WebSockets: {e}")

async def restart_binance_websocket():
    for task in state.ws_tasks:
        task.cancel()
    state.ws_tasks = []
    if state.binance_client:
        await state.binance_client.close_connection()
        state.binance_client = None
    await start_binance_websocket()
