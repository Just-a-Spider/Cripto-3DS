import aiosqlite
import json
import logging
import os
from typing import Dict, Any, List, Optional

logger = logging.getLogger("CriptoBotEngine.DB")
DB_PATH = "bot_data.db"

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
        await db.commit()

        # Schema auto-migration if upgrading existing db
        try:
            await db.execute("ALTER TABLE trade_history ADD COLUMN realized_pnl_usdt REAL DEFAULT 0.0")
            await db.execute("ALTER TABLE trade_history ADD COLUMN realized_pnl_percent REAL DEFAULT 0.0")
            await db.commit()
        except Exception:
            pass # Columns already exist

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
    realized_pnl_percent: float = 0.0
):
    import time
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO trade_history 
               (timestamp, pair, action, amount_usdt, price, status, binance_order_id, is_testnet, realized_pnl_usdt, realized_pnl_percent) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), pair, action, amount_usdt, price, status, order_id, int(is_testnet), realized_pnl_usdt, realized_pnl_percent)
        )
        await db.commit()

async def get_trade_history(limit: int = 50, is_testnet: bool = True) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trade_history WHERE is_testnet = ? ORDER BY id DESC LIMIT ?", (int(is_testnet), limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_pnl_summary(is_testnet: bool = True) -> Dict[str, Any]:
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
        async with db.execute("SELECT amount_usdt, price FROM trade_history WHERE pair = ? AND action = 'BUY' AND status = 'EXECUTED' AND is_testnet = ? ORDER BY id DESC LIMIT 20", (pair, int(is_testnet))) as cursor:
            rows = await cursor.fetchall()
            if not rows: return 0.0
            total_cost = sum([r[0] for r in rows])
            total_qty = sum([r[0] / r[1] for r in rows])
            if total_qty == 0: return 0.0
            return total_cost / total_qty

async def clear_trade_history(only_unexecuted: bool = True, is_testnet: Optional[bool] = None) -> int:
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
