import json
from pathlib import Path

import aiosqlite

from config import logger, BASE_DIR

DB_PATH = BASE_DIR / "trading.db"

_CREATE_DETECTED_TOKENS = """
CREATE TABLE IF NOT EXISTS detected_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    contract_address TEXT NOT NULL,
    chain TEXT NOT NULL,
    market_cap REAL,
    liquidity REAL,
    price_usd REAL,
    price_native REAL,
    volume_24h REAL,
    price_change_24h REAL,
    holders INTEGER,
    buy_tax REAL,
    sell_tax REAL,
    dextools_url TEXT,
    dex_pair_url TEXT,
    deployer_wallet TEXT,
    social_links TEXT,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(contract_address, chain)
);
"""

_CREATE_OPEN_POSITIONS = """
CREATE TABLE IF NOT EXISTS open_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_address TEXT NOT NULL,
    token_symbol TEXT NOT NULL,
    chain TEXT NOT NULL,
    entry_price REAL NOT NULL,
    tokens_received REAL NOT NULL,
    buy_amount_native REAL NOT NULL,
    buy_tx_hash TEXT NOT NULL,
    pair_address TEXT,
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(token_address, chain, user_id)
);
"""

_CREATE_COMPLETED_TRADES = """
CREATE TABLE IF NOT EXISTS completed_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_address TEXT NOT NULL,
    token_symbol TEXT NOT NULL,
    chain TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    tokens_amount REAL NOT NULL,
    buy_amount_native REAL NOT NULL,
    sell_amount_native REAL NOT NULL,
    profit_usd REAL,
    roi_percent REAL NOT NULL,
    buy_tx_hash TEXT NOT NULL,
    sell_tx_hash TEXT NOT NULL,
    opened_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duration_seconds INTEGER
);
"""

_CREATE_ALLOWED_USERS = """
CREATE TABLE IF NOT EXISTS allowed_users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    wallet_address TEXT NOT NULL,
    private_key TEXT NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db():
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(_CREATE_DETECTED_TOKENS)
        await db.execute(_CREATE_OPEN_POSITIONS)
        await db.execute(_CREATE_COMPLETED_TRADES)
        await db.execute(_CREATE_ALLOWED_USERS)
        await db.commit()
    logger.info("Database initialised at %s", DB_PATH)


async def save_detected_token(token_data: dict):
    sql = """
        INSERT OR IGNORE INTO detected_tokens
            (name, symbol, contract_address, chain, market_cap, liquidity,
             price_usd, price_native, volume_24h, price_change_24h, holders,
             buy_tax, sell_tax, dextools_url, dex_pair_url, deployer_wallet, social_links)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    social = token_data.get("social_links")
    if isinstance(social, dict):
        social = json.dumps(social)
    params = (
        token_data.get("name"),
        token_data.get("symbol"),
        token_data.get("contract_address"),
        token_data.get("chain"),
        token_data.get("market_cap"),
        token_data.get("liquidity"),
        token_data.get("price_usd"),
        token_data.get("price_native"),
        token_data.get("volume_24h"),
        token_data.get("price_change_24h"),
        token_data.get("holders"),
        token_data.get("buy_tax"),
        token_data.get("sell_tax"),
        token_data.get("dextools_url"),
        token_data.get("dex_pair_url"),
        token_data.get("deployer_wallet"),
        social,
    )
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(sql, params)
        await db.commit()
    logger.debug("Saved detected token %s (%s)", token_data.get("symbol"), token_data.get("contract_address"))


async def save_open_position(position: dict):
    sql = """
        INSERT INTO open_positions
            (user_id, token_address, token_symbol, chain, entry_price, tokens_received,
             buy_amount_native, buy_tx_hash, pair_address)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        position["user_id"],
        position["token_address"],
        position["token_symbol"],
        position["chain"],
        position["entry_price"],
        position["tokens_received"],
        position["buy_amount_native"],
        position["buy_tx_hash"],
        position.get("pair_address"),
    )
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(sql, params)
        await db.commit()
    logger.info("Saved open position for %s on %s (user %d)", position["token_symbol"], position["chain"], position["user_id"])


async def get_open_positions(user_id: int) -> list[dict]:
    sql = "SELECT * FROM open_positions WHERE user_id = ? ORDER BY opened_at DESC"
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, (user_id,))
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def close_position(token_address: str, chain: str, user_id: int, exit_data: dict):
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM open_positions WHERE token_address = ? AND chain = ? AND user_id = ?",
            (token_address, chain, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            logger.warning("close_position: no open position for %s on %s (user %d)", token_address, chain, user_id)
            return
        pos = dict(row)

        opened_at = pos["opened_at"]
        duration = exit_data.get("duration_seconds", 0)

        insert_sql = """
            INSERT INTO completed_trades
                (user_id, token_address, token_symbol, chain, entry_price, exit_price,
                 tokens_amount, buy_amount_native, sell_amount_native, profit_usd,
                 roi_percent, buy_tx_hash, sell_tx_hash, opened_at, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            user_id,
            pos["token_address"],
            pos["token_symbol"],
            pos["chain"],
            pos["entry_price"],
            exit_data["exit_price"],
            pos["tokens_received"],
            pos["buy_amount_native"],
            exit_data["sell_amount_native"],
            exit_data.get("profit_usd"),
            exit_data["roi_percent"],
            pos["buy_tx_hash"],
            exit_data["sell_tx_hash"],
            opened_at,
            duration,
        )
        await db.execute(insert_sql, params)
        await db.execute(
            "DELETE FROM open_positions WHERE token_address = ? AND chain = ? AND user_id = ?",
            (token_address, chain, user_id),
        )
        await db.commit()
    logger.info(
        "Closed position %s on %s | ROI %.2f%% (user %d)",
        pos["token_symbol"], chain, exit_data["roi_percent"], user_id,
    )


async def get_trade_history(user_id: int, limit: int = 10) -> list[dict]:
    sql = "SELECT * FROM completed_trades WHERE user_id = ? ORDER BY closed_at DESC LIMIT ?"
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, (user_id, limit))
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def is_token_already_bought(contract_address: str, chain: str, user_id: int) -> bool:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cur1 = await db.execute(
            "SELECT 1 FROM open_positions WHERE token_address = ? AND chain = ? AND user_id = ? LIMIT 1",
            (contract_address, chain, user_id),
        )
        if await cur1.fetchone():
            return True
        cur2 = await db.execute(
            "SELECT 1 FROM completed_trades WHERE token_address = ? AND chain = ? AND user_id = ? LIMIT 1",
            (contract_address, chain, user_id),
        )
        if await cur2.fetchone():
            return True
    return False


async def add_allowed_user(user_id: int, username: str, wallet_address: str, private_key: str):
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO allowed_users (user_id, username, wallet_address, private_key) VALUES (?, ?, ?, ?)",
            (user_id, username, wallet_address, private_key),
        )
        await conn.commit()
    logger.info("Added allowed user %d (%s) wallet %s", user_id, username, wallet_address)


async def remove_allowed_user(user_id: int) -> bool:
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        cursor = await conn.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))
        await conn.commit()
        removed = cursor.rowcount > 0
    if removed:
        logger.info("Removed allowed user %d", user_id)
    return removed


async def get_allowed_users() -> list[dict]:
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("SELECT user_id, username, wallet_address, added_at FROM allowed_users ORDER BY added_at")
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def is_user_allowed(user_id: int) -> bool:
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM allowed_users WHERE user_id = ? LIMIT 1", (user_id,)
        )
        return await cursor.fetchone() is not None


async def get_user_wallet(user_id: int) -> dict | None:
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT wallet_address, private_key FROM allowed_users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None
