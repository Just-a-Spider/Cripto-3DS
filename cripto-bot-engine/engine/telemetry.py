import asyncio
import json
from engine.logger import logger
from engine.state import state
from engine.ws_manager import broadcast_state
from engine.trades import decide_trade
from engine.shared import save_strategy_state

async def start_3ds_tcp_server(host: str = '0.0.0.0', port: int = 7343):
    async def handle_3ds_client(reader, writer):
        addr = writer.get_extra_info('peername')
        logger.info(f"3DS Client connected from {addr}")
        client_auth = {"authenticated": False}

        async def command_reader():
            while True:
                line = await reader.readline()
                if not line:
                    break
                
                cmd = line.decode('utf-8', errors='ignore').strip()
                if not cmd:
                    continue
                
                logger.info(f"3DS Command received: {cmd}")

                if cmd.startswith("AUTH "):
                    pin = cmd.split(" ")[1] if len(cmd.split(" ")) > 1 else ""
                    if pin == state.auth_pin:
                        client_auth["authenticated"] = True
                        writer.write(b"AUTH_OK\n")
                        logger.info("3DS Client authentication successful.")
                    else:
                        writer.write(b"AUTH_FAIL\n")
                        logger.warning(f"3DS Client authentication failed (invalid PIN: {pin!r})")
                    await writer.drain()
                    continue

                parts = cmd.split("|")
                action = parts[0]
                param = None
                provided_pin = None

                if len(parts) == 2:
                    if parts[1] == state.auth_pin:
                        provided_pin = parts[1]
                    else:
                        if action in ["EMERGENCY_STOP", "PAUSE", "TOGGLE_DCA", "FORCE_EVALUATE", "APPROVE", "REJECT"]:
                            logger.warning(f"Invalid PIN provided for action {action}: {parts[1]!r}")
                            writer.write(b"AUTH_FAIL\n")
                            await writer.drain()
                            continue
                        else:
                            param = parts[1]
                elif len(parts) >= 3:
                    param = parts[1]
                    provided_pin = parts[2]
                    if provided_pin != state.auth_pin:
                        logger.warning(f"Invalid PIN for action {action}|{param}: {provided_pin!r}")
                        writer.write(b"AUTH_FAIL\n")
                        await writer.drain()
                        continue

                # Enforce authentication either via session or per-action PIN payload
                if provided_pin == state.auth_pin:
                    client_auth["authenticated"] = True

                if not client_auth["authenticated"]:
                    writer.write(b"NOT_AUTHENTICATED\n")
                    await writer.drain()
                    continue

                # Execute action immediately with zero latency
                if action == "EMERGENCY_STOP":
                    state.is_active = False
                    state.pending_trade = None
                    logger.info("3DS Action executed: EMERGENCY_STOP -> Bot halted, pending trades cleared.")
                    await broadcast_state()

                elif action == "PAUSE":
                    state.is_active = not state.is_active
                    logger.info(f"3DS Action executed: PAUSE -> is_active={state.is_active}")
                    await broadcast_state()

                elif action == "TOGGLE_DCA":
                    state.dca_strategy.enabled = not state.dca_strategy.enabled
                    logger.info(f"3DS Action executed: TOGGLE_DCA -> dca_enabled={state.dca_strategy.enabled}")
                    await broadcast_state()

                elif action == "FORCE_EVALUATE":
                    state.rsi_strategy.cooldowns.clear()
                    state.dca_strategy.cooldowns.clear()
                    state.tpsl_strategy.cooldowns.clear()
                    await save_strategy_state()
                    logger.info("3DS Action executed: FORCE_EVALUATE -> Strategy cooldowns cleared.")
                    await broadcast_state()

                elif action == "SET_OVERRIDE" and param:
                    try:
                        override_amount = float(param)
                        if state.pending_trade and override_amount > 0:
                            state.pending_trade['amount_usdt'] = override_amount
                            if state.pending_trade['price'] > 0:
                                state.pending_trade['amount_asset'] = override_amount / state.pending_trade['price']
                            logger.info(f"3DS Action executed: SET_OVERRIDE -> ${override_amount:.2f} USDT")
                            await broadcast_state()
                    except ValueError:
                        pass

                elif action == "APPROVE":
                    res = await decide_trade(True)
                    logger.info(f"3DS Action executed: APPROVE -> {res}")

                elif action == "REJECT":
                    res = await decide_trade(False)
                    logger.info(f"3DS Action executed: REJECT -> {res}")

                elif action == "CLEAR_BUY" and param:
                    pair = param
                    if pair in state.rsi_strategy.cooldowns:
                        del state.rsi_strategy.cooldowns[pair]
                    if pair in state.dca_strategy.cooldowns:
                        del state.dca_strategy.cooldowns[pair]
                    await save_strategy_state()
                    logger.info(f"3DS Action executed: CLEAR_BUY for {pair}")

                elif action == "CLEAR_SELL" and param:
                    pair = param
                    if pair in state.tpsl_strategy.cooldowns:
                        del state.tpsl_strategy.cooldowns[pair]
                    await save_strategy_state()
                    logger.info(f"3DS Action executed: CLEAR_SELL for {pair}")

        async def telemetry_streamer():
            while True:
                if not client_auth["authenticated"]:
                    await asyncio.sleep(0.1)
                    continue

                if state.favorite_pairs:
                    state.current_pair_idx = (state.current_pair_idx + 1) % len(state.favorite_pairs)
                    curr_pair = state.favorite_pairs[state.current_pair_idx]
                else:
                    curr_pair = "BTCUSDT"

                curr_price = state.prices.get(curr_pair, 0.0)

                total_val = state.usdt_balance
                for asset, amt in state.portfolio_balances.items():
                    if amt > 0 and asset != "USDT":
                        p = state.prices.get(asset + "USDT", 0.0)
                        total_val += amt * p

                fav_assets_list = []
                for p in state.favorite_pairs:
                    asset = p.replace("USDT", "")
                    bal = state.portfolio_balances.get(asset, 0.0)
                    price = state.prices.get(p, 0.0)
                    rsi = state.rsi_strategy.calculate_rsi(p)
                    fav_assets_list.append(f"{asset}:{bal:.4f}:{price:.2f}:{rsi:.1f}")
                top_assets_str = ",".join(fav_assets_list)
                
                live_rsi = state.rsi_strategy.calculate_rsi(curr_pair)
                
                payload = {
                    "status": "ACTIVE" if state.is_active else "PAUSED",
                    "pair": curr_pair,
                    "price": round(curr_price, 2),
                    "usdt": round(total_val, 2),
                    "rsi": round(live_rsi, 1),
                    "has_trade": state.pending_trade is not None,
                    "dca_enabled": state.dca_strategy.enabled,
                    "trade_action": "",
                    "trade_pair": "",
                    "trade_reason": "",
                    "trade_price": 0.0,
                    "trade_amount_usdt": 0.0,
                    "ai_risk": "",
                    "ai_verdict": "",
                    "top_assets": top_assets_str
                }
                
                if state.pending_trade:
                    payload["trade_action"] = state.pending_trade.get("action", "")
                    payload["trade_pair"] = state.pending_trade.get("pair", "")
                    reason = state.pending_trade.get("reason", "")
                    if "Dollar Cost Averaging" in reason: reason = "DCA"
                    elif "Take Profit" in reason: reason = "TP"
                    elif "Stop Loss" in reason: reason = "SL"
                    payload["trade_reason"] = reason[:10]
                    payload["trade_price"] = round(state.pending_trade.get("price", 0.0), 2)
                    payload["trade_amount_usdt"] = round(state.pending_trade.get("amount_usdt", 0.0), 2)
                    payload["ai_risk"] = state.pending_trade.get("ai_risk", "")
                    payload["ai_verdict"] = state.pending_trade.get("ai_verdict", "")

                msg = json.dumps(payload) + "\n"
                writer.write(msg.encode('utf-8'))
                await writer.drain()
                await asyncio.sleep(2)

        reader_task = asyncio.create_task(command_reader())
        streamer_task = asyncio.create_task(telemetry_streamer())

        try:
            done, pending = await asyncio.wait(
                [reader_task, streamer_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
        except Exception as e:
            logger.info(f"3DS Client session exception: {e}")
        finally:
            reader_task.cancel()
            streamer_task.cancel()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            logger.info(f"3DS Client connection closed for {addr}")

    server = await asyncio.start_server(handle_3ds_client, host, port)
    logger.info(f"3DS Telemetry TCP Server listening on {host}:{port}...")
    async with server:
        await server.serve_forever()
