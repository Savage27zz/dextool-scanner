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
    token_address TEXT NOT NULL,
    token_symbol TEXT NOT NULL,
    chain TEXT NOT NULL,
    entry_price REAL NOT NULL,
    tokens_received REAL NOT NULL,
    buy_amount_native REAL NOT NULL,
    buy_tx_hash TEXT NOT NULL,
    pair_address TEXT,
    peak_price REAL DEFAULT 0,
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(token_address, chain)
);
"""

_CREATE_COMPLETED_TRADES = """
CREATE TABLE IF NOT EXISTS completed_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_PRICE_ALERTS = """
CREATE TABLE IF NOT EXISTS price_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    token_symbol TEXT NOT NULL,
    chain TEXT NOT NULL,
    target_price REAL NOT NULL,
    direction TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    triggered INTEGER DEFAULT 0
);
"""


async def _conn() -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db():
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(_CREATE_DETECTED_TOKENS)
        await db.execute(_CREATE_OPEN_POSITIONS)
        await db.execute(_CREATE_COMPLETED_TRADES)
        await db.execute(_CREATE_ALLOWED_USERS)
        await db.execute(_CREATE_PRICE_ALERTS)
        await db.commit()
        try:
            await db.execute("ALTER TABLE open_positions ADD COLUMN peak_price REAL DEFAULT 0")
            await db.commit()
        except Exception:
            pass
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
            (token_address, token_symbol, chain, entry_price, tokens_received,
             buy_amount_native, buy_tx_hash, pair_address, peak_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        position["token_address"],
        position["token_symbol"],
        position["chain"],
        position["entry_price"],
        position["tokens_received"],
        position["buy_amount_native"],
        position["buy_tx_hash"],
        position.get("pair_address"),
        position["entry_price"],
    )
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(sql, params)
        await db.commit()
    logger.info("Saved open position for %s on %s", position["token_symbol"], position["chain"])


async def get_open_positions() -> list[dict]:
    sql = "SELECT * FROM open_positions ORDER BY opened_at DESC"
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql)
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def close_position(token_address: str, chain: str, exit_data: dict):
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM open_positions WHERE token_address = ? AND chain = ?",
            (token_address, chain),
        )
        row = await cursor.fetchone()
        if row is None:
            logger.warning("close_position: no open position for %s on %s", token_address, chain)
            return
        pos = dict(row)

        opened_at = pos["opened_at"]
        duration = exit_data.get("duration_seconds", 0)

        insert_sql = """
            INSERT INTO completed_trades
                (token_address, token_symbol, chain, entry_price, exit_price,
                 tokens_amount, buy_amount_native, sell_amount_native, profit_usd,
                 roi_percent, buy_tx_hash, sell_tx_hash, opened_at, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
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
            "DELETE FROM open_positions WHERE token_address = ? AND chain = ?",
            (token_address, chain),
        )
        await db.commit()
    logger.info(
        "Closed position %s on %s | ROI %.2f%%",
        pos["token_symbol"],
        chain,
        exit_data["roi_percent"],
    )


async def get_trade_history(limit: int = 10) -> list[dict]:
    sql = "SELECT * FROM completed_trades ORDER BY closed_at DESC LIMIT ?"
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, (limit,))
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def add_allowed_user(user_id: int, username: str = ""):
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO allowed_users (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        await conn.commit()
    logger.info("Added allowed user %d (%s)", user_id, username)


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
        cursor = await conn.execute("SELECT * FROM allowed_users ORDER BY added_at")
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def is_user_allowed(user_id: int) -> bool:
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM allowed_users WHERE user_id = ? LIMIT 1", (user_id,)
        )
        return await cursor.fetchone() is not None


async def update_peak_price(token_address: str, chain: str, peak_price: float):
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        await conn.execute(
            "UPDATE open_positions SET peak_price = ? WHERE token_address = ? AND chain = ?",
            (peak_price, token_address, chain),
        )
        await conn.commit()


async def get_open_position(token_address: str, chain: str) -> dict | None:
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM open_positions WHERE token_address = ? AND chain = ? LIMIT 1",
            (token_address, chain),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def update_position_dca(
    token_address: str,
    chain: str,
    additional_tokens: float,
    additional_native: float,
    new_tx_hash: str,
):
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM open_positions WHERE token_address = ? AND chain = ?",
            (token_address, chain),
        )
        row = await cursor.fetchone()
        if row is None:
            return
        pos = dict(row)
        new_tokens = pos["tokens_received"] + additional_tokens
        new_native = pos["buy_amount_native"] + additional_native
        new_entry = new_native / new_tokens if new_tokens > 0 else pos["entry_price"]
        await conn.execute(
            "UPDATE open_positions SET tokens_received = ?, buy_amount_native = ?, "
            "entry_price = ?, buy_tx_hash = ?, peak_price = ? "
            "WHERE token_address = ? AND chain = ?",
            (new_tokens, new_native, new_entry, new_tx_hash,
             max(pos.get("peak_price", 0) or 0, new_entry), token_address, chain),
        )
        await conn.commit()
    logger.info(
        "DCA update %s on %s: +%.4f tokens, +%.4f native, new entry=%.10f",
        token_address, chain, additional_tokens, additional_native, new_entry,
    )


async def reduce_position(token_address: str, chain: str, fraction_sold: float):
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM open_positions WHERE token_address = ? AND chain = ?",
            (token_address, chain),
        )
        row = await cursor.fetchone()
        if row is None:
            return
        pos = dict(row)
        remaining = 1.0 - fraction_sold
        new_tokens = pos["tokens_received"] * remaining
        new_native = pos["buy_amount_native"] * remaining
        await conn.execute(
            "UPDATE open_positions SET tokens_received = ?, buy_amount_native = ? "
            "WHERE token_address = ? AND chain = ?",
            (new_tokens, new_native, token_address, chain),
        )
        await conn.commit()
    logger.info(
        "Reduced position %s on %s by %.0f%% — %.4f tokens remaining",
        token_address, chain, fraction_sold * 100, new_tokens,
    )


async def save_price_alert(token_address: str, token_symbol: str, chain: str, target_price: float, direction: str):
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        await conn.execute(
            "INSERT INTO price_alerts (token_address, token_symbol, chain, target_price, direction) VALUES (?, ?, ?, ?, ?)",
            (token_address, token_symbol, chain, target_price, direction),
        )
        await conn.commit()
    logger.info("Saved price alert for %s: %s %.10f", token_symbol, direction, target_price)


async def get_active_alerts() -> list[dict]:
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("SELECT * FROM price_alerts WHERE triggered = 0 ORDER BY created_at")
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def trigger_alert(alert_id: int):
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        await conn.execute("UPDATE price_alerts SET triggered = 1 WHERE id = ?", (alert_id,))
        await conn.commit()


async def delete_alert(alert_id: int) -> bool:
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        cursor = await conn.execute("DELETE FROM price_alerts WHERE id = ?", (alert_id,))
        await conn.commit()
        return cursor.rowcount > 0


async def is_token_already_bought(contract_address: str, chain: str) -> bool:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cur1 = await db.execute(
            "SELECT 1 FROM open_positions WHERE token_address = ? AND chain = ? LIMIT 1",
            (contract_address, chain),
        )
        if await cur1.fetchone():
            return True
        cur2 = await db.execute(
            "SELECT 1 FROM completed_trades WHERE token_address = ? AND chain = ? LIMIT 1",
            (contract_address, chain),
        )
        if await cur2.fetchone():
            return True
    return False
