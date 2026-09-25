import json
import logging
import os
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger("CriptoBotEngine.DB")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "bot_data.db")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Enable Write-Ahead Logging (WAL) and optimized sync for 24/7 flash stability & zero lock contention
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute("PRAGMA synchronous = NORMAL;")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                pair TEXT NOT NULL,
                action TEXT NOT NULL,
                amount_usdt REAL NOT NULL,
                price REAL NOT NULL,
                status TEXT NOT NULL,
                binance_order_id TEXT,
                is_testnet INTEGER DEFAULT 1,
                realized_pnl_usdt REAL DEFAULT 0.0,
                realized_pnl_percent REAL DEFAULT 0.0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS news_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset TEXT NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                url TEXT NOT NULL,
                sentiment_tag TEXT NOT NULL,
                published_at REAL NOT NULL
            )
        """)
        await db.commit()

        # Database indexes for speed and zero-contention filtering
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trade_history_spend ON trade_history (is_testnet, action, status, timestamp);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trade_history_pair ON trade_history (pair, is_testnet, status);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trade_history_order ON trade_history (binance_order_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trade_history_ts ON trade_history (is_testnet, timestamp DESC);")
        await db.commit()

        # Schema auto-migration if upgrading existing db
        try:
            await db.execute("ALTER TABLE trade_history ADD COLUMN realized_pnl_usdt REAL DEFAULT 0.0")
            await db.execute("ALTER TABLE trade_history ADD COLUMN realized_pnl_percent REAL DEFAULT 0.0")
            await db.commit()
        except Exception:
            pass # Columns already exist

        await deduplicate_trade_history()
        logger.info("SQLite database initialized successfully.")

async def save_config_item(key: str, value: Any):
    async with aiosqlite.connect(DB_PATH) as db:
        val_str = json.dumps(value)
        await db.execute(
            "INSERT INTO bot_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?",
            (key, val_str, val_str)
        )
        await db.commit()

async def load_config_item(key: str, default: Any = None) -> Any:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM bot_config WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row[0])
            return default

async def log_trade(
    pair: str,
    action: str,
    amount_usdt: float,
    price: float,
    status: str,
    order_id: str = "",
    is_testnet: bool = True,
    realized_pnl_usdt: float = 0.0,
    realized_pnl_percent: float = 0.0,
    timestamp: float | None = None
):
    import time
    ts = timestamp if timestamp is not None else time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO trade_history
               (timestamp, pair, action, amount_usdt, price, status, binance_order_id, is_testnet, realized_pnl_usdt, realized_pnl_percent)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, pair, action, amount_usdt, price, status, order_id, int(is_testnet), realized_pnl_usdt, realized_pnl_percent)
        )
        await db.commit()

async def get_trade_history(limit: int = 100, is_testnet: bool = True) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trade_history WHERE is_testnet = ? ORDER BY timestamp DESC, id DESC LIMIT ?", (int(is_testnet), limit)) as cursor:
            rows = await cursor.fetchall()
            trades = []
            for r in rows:
                d = dict(r)
                if "order_id" not in d and "binance_order_id" in d:
                    d["order_id"] = d["binance_order_id"]
                trades.append(d)
            return trades

async def get_pnl_summary(is_testnet: bool = True) -> dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT
                COUNT(*) as total_trades,
                COALESCE(SUM(CASE WHEN action = 'SELL' AND status = 'EXECUTED' THEN realized_pnl_usdt ELSE 0.0 END), 0.0) as total_pnl_usdt,
                COALESCE(SUM(CASE WHEN action = 'SELL' AND status = 'EXECUTED' AND realized_pnl_usdt > 0 THEN 1 ELSE 0 END), 0) as wins,
                COALESCE(SUM(CASE WHEN action = 'SELL' AND status = 'EXECUTED' AND realized_pnl_usdt < 0 THEN 1 ELSE 0 END), 0) as losses,
                COALESCE(SUM(CASE WHEN action = 'SELL' AND status = 'EXECUTED' THEN 1 ELSE 0 END), 0) as closed_trades
            FROM trade_history
            WHERE is_testnet = ?
        """, (int(is_testnet),)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return {"total_pnl_usdt": 0.0, "win_rate": 0.0, "total_trades": 0, "wins": 0, "losses": 0, "closed_trades": 0}

            total_trades, total_pnl, wins, losses, closed_trades = row
            win_rate = round((wins / closed_trades * 100), 1) if closed_trades > 0 else 0.0
            return {
                "total_pnl_usdt": round(total_pnl, 2),
                "win_rate": win_rate,
                "total_trades": total_trades,
                "wins": wins,
                "losses": losses,
                "closed_trades": closed_trades
            }

async def get_average_buy_price(pair: str, is_testnet: bool) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT amount_usdt, price FROM trade_history WHERE pair = ? AND action = 'BUY' AND status = 'EXECUTED' AND is_testnet = ? ORDER BY timestamp DESC, id DESC LIMIT 20", (pair, int(is_testnet))) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return 0.0
            total_cost = sum([r[0] for r in rows])
            total_qty = sum([r[0] / r[1] for r in rows])
            if total_qty == 0:
                return 0.0
            return total_cost / total_qty

async def clear_trade_history(only_unexecuted: bool = True, is_testnet: bool | None = None) -> int:
    """
    Purges unwanted trade records from SQLite.
    If only_unexecuted is True, only deletes REJECTED and TIMEOUT entries, preserving real executed PnL trades.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        query = "DELETE FROM trade_history WHERE 1=1"
        params = []
        if only_unexecuted:
            query += " AND status != 'EXECUTED'"
        if is_testnet is not None:
            query += " AND is_testnet = ?"
            params.append(int(is_testnet))

        cursor = await db.execute(query, tuple(params))
        await db.commit()
        return cursor.rowcount

async def save_news_cache(news_items: list[dict[str, Any]]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM news_cache")
        for item in news_items:
            await db.execute(
                "INSERT INTO news_cache (asset, title, source, url, sentiment_tag, published_at) VALUES (?, ?, ?, ?, ?, ?)",
                (item.get("asset", "MARKET"), item.get("title", ""), item.get("source", ""), item.get("url", ""), item.get("sentiment_tag", "NEUTRAL"), item.get("published_at", 0.0))
            )
        await db.commit()

async def get_cached_news(limit: int = 10) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM news_cache ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_rolling_daily_spend(is_testnet: bool = True, window_seconds: float = 86400.0) -> float:
    import time
    since_ts = time.time() - window_seconds
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT COALESCE(SUM(amount_usdt), 0.0)
               FROM trade_history
               WHERE action = 'BUY' AND status = 'EXECUTED' AND is_testnet = ? AND timestamp >= ?""",
            (int(is_testnet), since_ts)
        ) as cursor:
            row = await cursor.fetchone()
            return float(row[0]) if row else 0.0

async def order_exists(binance_order_id: str, is_testnet: bool = True) -> bool:
    if not binance_order_id:
        return False
    ref = str(binance_order_id).strip()
    if ref in ("SIMULATED_ORDER", "SIMULATED", "None", ""):
        return False
    base_oid = ref.split("_")[0] if "_" in ref else ref
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT 1 FROM trade_history
               WHERE is_testnet = ?
               AND (
                   binance_order_id = ?
                   OR binance_order_id = ?
                   OR binance_order_id LIKE ?
               ) LIMIT 1""",
            (int(is_testnet), ref, base_oid, f"{base_oid}_%")
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None

async def deduplicate_trade_history(is_testnet: bool | None = None) -> int:
    """
    Scans and purges redundant or duplicate trade records:
    1. Duplicate identical binance_order_ids.
    2. Overlapping live OID records when detailed OID_TID synced fills exist.
    """
    total_deleted = 0
    testnet_clause = f"AND is_testnet = {int(is_testnet)}" if is_testnet is not None else ""

    async with aiosqlite.connect(DB_PATH) as db:
        # Step 1: Exact duplicate binance_order_id
        cursor = await db.execute(f"""
            DELETE FROM trade_history
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM trade_history
                WHERE binance_order_id IS NOT NULL
                  AND binance_order_id != ''
                  AND binance_order_id NOT IN ('SIMULATED_ORDER', 'SIMULATED', 'None')
                  {testnet_clause}
                GROUP BY is_testnet, binance_order_id
            )
            AND binance_order_id IS NOT NULL
            AND binance_order_id != ''
            AND binance_order_id NOT IN ('SIMULATED_ORDER', 'SIMULATED', 'None')
            {testnet_clause}
        """)
        total_deleted += cursor.rowcount

        # Step 2: Delete bare OID orders if detailed OID_TID exists
        cursor = await db.execute(f"""
            DELETE FROM trade_history
            WHERE binance_order_id IS NOT NULL
            AND binance_order_id != ''
            AND binance_order_id NOT IN ('SIMULATED_ORDER', 'SIMULATED', 'None')
            {testnet_clause}
            AND EXISTS (
                SELECT 1 FROM trade_history th2
                WHERE th2.is_testnet = trade_history.is_testnet
                AND th2.binance_order_id LIKE (trade_history.binance_order_id || '_%')
            )
        """)
        total_deleted += cursor.rowcount

        await db.commit()

    if total_deleted > 0:
        logger.info(f"Deduplicated trade history: pruned {total_deleted} redundant trade records.")
    return total_deleted

