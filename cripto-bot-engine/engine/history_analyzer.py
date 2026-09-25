import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

import aiosqlite

from engine.db import DB_PATH

logger = logging.getLogger("CriptoBotEngine.HistoryAnalyzer")

async def analyze_and_reconcile_history(is_testnet: bool = True, update_db: bool = True) -> dict[str, Any]:
    """
    Chronologically analyzes all historical executed BUY and SELL trades from SQLite,
    reconciles exact realized profit/loss using FIFO inventory accounting,
    backfills missing or zero realized PnL in SQLite rows, and computes
    comprehensive asset-by-asset win/loss performance scorecards and loss streaks.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, pair, action, amount_usdt, price, status, binance_order_id,
                      realized_pnl_usdt, realized_pnl_percent, timestamp
               FROM trade_history
               WHERE is_testnet = ? AND status = 'EXECUTED'
               ORDER BY timestamp ASC, id ASC""",
            (int(is_testnet),)
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        empty_res = {
            "is_testnet": is_testnet,
            "global": {
                "total_trades": 0,
                "total_closed_trades": 0,
                "total_wins": 0,
                "total_losses": 0,
                "total_breakeven": 0,
                "overall_win_rate": 0.0,
                "net_realized_pnl_usdt": 0.0,
                "portfolio_profit_factor": 0.0,
                "best_performing_asset": "NONE",
                "worst_performing_asset": "NONE",
                "total_volume_usdt": 0.0
            },
            "assets": {}
        }
        return empty_res

    # FIFO Inventory queues: pair -> list of [remaining_qty, price, trade_id]
    inventory: dict[str, list[list[Any]]] = defaultdict(list)
    # Asset performance metrics
    asset_metrics: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "pair": "",
        "asset": "",
        "total_buys": 0,
        "total_sells": 0,
        "total_buy_usdt": 0.0,
        "total_sell_usdt": 0.0,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "win_rate": 0.0,
        "net_realized_pnl_usdt": 0.0,
        "gross_profit_usdt": 0.0,
        "gross_loss_usdt": 0.0,
        "profit_factor": 0.0,
        "avg_win_usdt": 0.0,
        "avg_loss_usdt": 0.0,
        "largest_win_usdt": 0.0,
        "largest_loss_usdt": 0.0,
        "current_position_qty": 0.0,
        "current_cost_basis": 0.0,
        "consecutive_losses": 0,
        "performance_status": "NEUTRAL"
    })

    db_updates = []
    total_volume = 0.0

    for r in rows:
        tid = r["id"]
        pair = str(r["pair"]).strip().upper()
        action = str(r["action"]).strip().upper()
        amount_usdt = float(r["amount_usdt"] or 0.0)
        price = float(r["price"] or 0.0)
        stored_pnl = float(r["realized_pnl_usdt"] or 0.0)
        stored_pct = float(r["realized_pnl_percent"] or 0.0)

        total_volume += amount_usdt
        asset_name = pair.replace("USDT", "")
        metrics = asset_metrics[pair]
        metrics["pair"] = pair
        metrics["asset"] = asset_name

        if price <= 0.0:
            logger.debug(f"Skipping trade {tid} with non-positive price: {price}")
            continue

        trade_qty = amount_usdt / price

        if action == "BUY":
            metrics["total_buys"] += 1
            metrics["total_buy_usdt"] = round(metrics["total_buy_usdt"] + amount_usdt, 2)
            inventory[pair].append([trade_qty, price, tid])

        elif action == "SELL":
            metrics["total_sells"] += 1
            metrics["total_sell_usdt"] = round(metrics["total_sell_usdt"] + amount_usdt, 2)

            needed_qty = trade_qty
            cost_of_sold = 0.0

            # Match against prior BUY inventory FIFO
            while needed_qty > 1e-8 and inventory[pair]:
                first = inventory[pair][0]
                avail_qty = first[0]
                buy_price = first[1]
                used_qty = min(avail_qty, needed_qty)
                cost_of_sold += used_qty * buy_price
                first[0] -= used_qty
                needed_qty -= used_qty
                if first[0] <= 1e-8:
                    inventory[pair].pop(0)

            sold_matched_qty = trade_qty - needed_qty

            if sold_matched_qty > 1e-8 and cost_of_sold > 0:
                calc_pnl = round((sold_matched_qty * price) - cost_of_sold, 4)
                calc_pct = round((calc_pnl / cost_of_sold) * 100.0, 2)
            elif stored_pnl != 0.0:
                # If sells preceded recorded buys (e.g. manual sell of pre-existing external balance),
                # fallback to existing recorded PnL if present
                calc_pnl = stored_pnl
                calc_pct = stored_pct
            else:
                calc_pnl = 0.0
                calc_pct = 0.0

            # Update asset accounting metrics
            metrics["closed_trades"] += 1
            metrics["net_realized_pnl_usdt"] = round(metrics["net_realized_pnl_usdt"] + calc_pnl, 2)

            if calc_pnl > 0.005:
                metrics["wins"] += 1
                metrics["gross_profit_usdt"] = round(metrics["gross_profit_usdt"] + calc_pnl, 2)
                metrics["largest_win_usdt"] = max(metrics["largest_win_usdt"], round(calc_pnl, 2))
                metrics["consecutive_losses"] = 0
            elif calc_pnl < -0.005:
                metrics["losses"] += 1
                loss_abs = abs(calc_pnl)
                metrics["gross_loss_usdt"] = round(metrics["gross_loss_usdt"] + loss_abs, 2)
                metrics["largest_loss_usdt"] = max(metrics["largest_loss_usdt"], round(loss_abs, 2))
                metrics["consecutive_losses"] += 1
            else:
                metrics["breakeven"] += 1

            # Check if database needs updating
            if update_db and (abs(stored_pnl - calc_pnl) > 0.01 or abs(stored_pct - calc_pct) > 0.1):
                db_updates.append((round(calc_pnl, 2), calc_pct, tid))

    # Apply database updates for reconciled PnL
    if update_db and db_updates:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.executemany(
                "UPDATE trade_history SET realized_pnl_usdt = ?, realized_pnl_percent = ? WHERE id = ?",
                db_updates
            )
            await db.commit()
        logger.info(f"Reconciled and backfilled PnL across {len(db_updates)} historical SELL trades in SQLite.")

    # Calculate remaining inventory and final asset summaries
    for pair, metrics in asset_metrics.items():
        rem_items = inventory.get(pair, [])
        rem_qty = sum(item[0] for item in rem_items)
        rem_cost = sum(item[0] * item[1] for item in rem_items)
        avg_cost = (rem_cost / rem_qty) if rem_qty > 0.0 else 0.0

        metrics["current_position_qty"] = round(rem_qty, 6)
        metrics["current_cost_basis"] = round(avg_cost, 4)

        closed = metrics["closed_trades"]
        if closed > 0:
            metrics["win_rate"] = round((metrics["wins"] / closed) * 100.0, 1)

        if metrics["gross_loss_usdt"] > 0:
            metrics["profit_factor"] = round(metrics["gross_profit_usdt"] / metrics["gross_loss_usdt"], 2)
        elif metrics["gross_profit_usdt"] > 0:
            metrics["profit_factor"] = 99.9  # Zero losses with profits
        else:
            metrics["profit_factor"] = 0.0

        if metrics["wins"] > 0:
            metrics["avg_win_usdt"] = round(metrics["gross_profit_usdt"] / metrics["wins"], 2)
        if metrics["losses"] > 0:
            metrics["avg_loss_usdt"] = round(metrics["gross_loss_usdt"] / metrics["losses"], 2)

        # Classify status
        net_pnl = metrics["net_realized_pnl_usdt"]
        if metrics["consecutive_losses"] >= 3:
            metrics["performance_status"] = "DRAWDOWN"
        elif net_pnl > 20.0 and metrics["win_rate"] >= 70.0:
            metrics["performance_status"] = "HIGH_PROFIT"
        elif net_pnl > 0.0:
            metrics["performance_status"] = "PROFITABLE"
        elif net_pnl < -5.0 or (closed >= 3 and metrics["win_rate"] < 40.0):
            metrics["performance_status"] = "UNDERPERFORMING"
        elif rem_qty > 0.0 and closed == 0:
            metrics["performance_status"] = "ACCUMULATING"
        else:
            metrics["performance_status"] = "NEUTRAL"

    # Global portfolio calculation
    total_trades = len(rows)
    total_closed = sum(m["closed_trades"] for m in asset_metrics.values())
    total_wins = sum(m["wins"] for m in asset_metrics.values())
    total_losses = sum(m["losses"] for m in asset_metrics.values())
    total_breakeven = sum(m["breakeven"] for m in asset_metrics.values())
    net_pnl = round(sum(m["net_realized_pnl_usdt"] for m in asset_metrics.values()), 2)
    overall_win_rate = round((total_wins / total_closed * 100.0), 1) if total_closed > 0 else 0.0

    total_gross_profit = sum(m["gross_profit_usdt"] for m in asset_metrics.values())
    total_gross_loss = sum(m["gross_loss_usdt"] for m in asset_metrics.values())
    portfolio_pf = round(total_gross_profit / total_gross_loss, 2) if total_gross_loss > 0 else (99.9 if total_gross_profit > 0 else 0.0)

    # Identify best and worst assets
    best_asset = "NONE"
    worst_asset = "NONE"
    if asset_metrics:
        sorted_by_pnl = sorted(asset_metrics.values(), key=lambda x: x["net_realized_pnl_usdt"], reverse=True)
        if sorted_by_pnl and sorted_by_pnl[0]["net_realized_pnl_usdt"] > 0:
            best_asset = f"{sorted_by_pnl[0]['asset']} (+${sorted_by_pnl[0]['net_realized_pnl_usdt']:.2f})"
        if sorted_by_pnl and sorted_by_pnl[-1]["net_realized_pnl_usdt"] < 0:
            worst_asset = f"{sorted_by_pnl[-1]['asset']} (${sorted_by_pnl[-1]['net_realized_pnl_usdt']:.2f})"

    result = {
        "is_testnet": is_testnet,
        "global": {
            "total_trades": total_trades,
            "total_closed_trades": total_closed,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "total_breakeven": total_breakeven,
            "overall_win_rate": overall_win_rate,
            "net_realized_pnl_usdt": net_pnl,
            "portfolio_profit_factor": portfolio_pf,
            "best_performing_asset": best_asset,
            "worst_performing_asset": worst_asset,
            "total_volume_usdt": round(total_volume, 2)
        },
        "assets": dict(asset_metrics)
    }

    return result
