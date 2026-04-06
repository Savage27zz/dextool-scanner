import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def _env(key: str, default=None, cast=None, required=False):
    val = os.getenv(key, default)
    if required and val is None:
        raise EnvironmentError(f"Missing required env variable: {key}")
    if val is not None and cast is not None:
        try:
            val = cast(val)
        except (ValueError, TypeError) as exc:
            raise EnvironmentError(f"Invalid value for {key}: {exc}") from exc
    return val


TELEGRAM_BOT_TOKEN: str = _env("TELEGRAM_BOT_TOKEN", required=True)
TELEGRAM_CHAT_ID: int = _env("TELEGRAM_CHAT_ID", cast=int, required=True)
PRIVATE_KEY: str = _env("PRIVATE_KEY", required=True)

RPC_URL_SOL: str = _env("RPC_URL_SOL", default="https://api.mainnet-beta.solana.com")
RPC_URL_ETH: str = _env("RPC_URL_ETH", default="")
RPC_URL_BSC: str = _env("RPC_URL_BSC", default="")

DEXTOOLS_API_KEY: str = _env("DEXTOOLS_API_KEY", required=True)
DEXTOOLS_PLAN: str = _env("DEXTOOLS_PLAN", default="trial")

CHAIN: str = _env("CHAIN", default="SOL")
BUY_PERCENT: int = _env("BUY_PERCENT", default="50", cast=int)
TAKE_PROFIT: int = _env("TAKE_PROFIT", default="20", cast=int)
STOP_LOSS: int = _env("STOP_LOSS", default="-50", cast=int)
TRAILING_STOP: int = _env("TRAILING_STOP", default="0", cast=int)
SELL_PERCENT: int = _env("SELL_PERCENT", default="100", cast=int)
SLIPPAGE: int = _env("SLIPPAGE", default="15", cast=int)

MIN_LIQUIDITY: int = _env("MIN_LIQUIDITY", default="5000", cast=int)
MAX_MCAP: int = _env("MAX_MCAP", default="500000", cast=int)
MIN_MCAP: int = _env("MIN_MCAP", default="10000", cast=int)

SCAN_INTERVAL: int = _env("SCAN_INTERVAL", default="60", cast=int)
MONITOR_INTERVAL: int = _env("MONITOR_INTERVAL", default="30", cast=int)

MIN_SCORE: int = _env("MIN_SCORE", default="40", cast=int)
MAX_POSITIONS: int = _env("MAX_POSITIONS", default="5", cast=int)
DRY_RUN: bool = _env("DRY_RUN", default="false", cast=lambda v: str(v).lower() in ("true", "1", "yes"))

DEXTOOLS_BASE_URL = f"https://public-api.dextools.io/{DEXTOOLS_PLAN}/v2"

CHAIN_MAP = {
    "SOL": "solana",
    "ETH": "ether",
    "BSC": "bsc",
}

DS_CHAIN_MAP = {
    "SOL": "solana",
    "ETH": "ethereum",
    "BSC": "bsc",
}

NATIVE_SYMBOL = {
    "SOL": "SOL",
    "ETH": "ETH",
    "BSC": "BNB",
}

EXPLORER_TX = {
    "SOL": "https://solscan.io/tx/{}",
    "ETH": "https://etherscan.io/tx/{}",
    "BSC": "https://bscscan.com/tx/{}",
}


def _build_logger() -> logging.Logger:
    log = logging.getLogger("dextool_scanner")
    log.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = RotatingFileHandler(
        BASE_DIR / "trading.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    log.addHandler(fh)
    log.addHandler(ch)
    return log


logger = _build_logger()


def validate_config():
    errors = []
    if STOP_LOSS >= 0:
        errors.append(f"STOP_LOSS must be negative (got {STOP_LOSS})")
    if not (1 <= SELL_PERCENT <= 100):
        errors.append(f"SELL_PERCENT must be 1-100 (got {SELL_PERCENT})")
    if TAKE_PROFIT <= 0:
        errors.append(f"TAKE_PROFIT must be > 0 (got {TAKE_PROFIT})")
    if TRAILING_STOP < 0:
        errors.append(f"TRAILING_STOP must be >= 0 (got {TRAILING_STOP})")
    if MAX_POSITIONS < 1:
        errors.append(f"MAX_POSITIONS must be >= 1 (got {MAX_POSITIONS})")
    if not (1 <= BUY_PERCENT <= 100):
        errors.append(f"BUY_PERCENT must be 1-100 (got {BUY_PERCENT})")
    if not (0 <= MIN_SCORE <= 100):
        errors.append(f"MIN_SCORE must be 0-100 (got {MIN_SCORE})")
    if SCAN_INTERVAL <= 0:
        errors.append(f"SCAN_INTERVAL must be > 0 (got {SCAN_INTERVAL})")
    if MONITOR_INTERVAL <= 0:
        errors.append(f"MONITOR_INTERVAL must be > 0 (got {MONITOR_INTERVAL})")
    if MIN_MCAP >= MAX_MCAP:
        errors.append(f"MIN_MCAP ({MIN_MCAP}) must be < MAX_MCAP ({MAX_MCAP})")
    if errors:
        for e in errors:
            logger.error("Config error: %s", e)
        raise EnvironmentError("Invalid configuration:\n  " + "\n  ".join(errors))
    logger.debug("Config validation passed")


validate_config()
