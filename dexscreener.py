import asyncio
from datetime import datetime, timezone

import aiohttp

import db
from config import (
    CHAIN,
    DS_CHAIN_MAP,
    MIN_LIQUIDITY,
    MIN_MCAP,
    MAX_MCAP,
    logger,
)
from honeypot import check_honeypot

DEXSCREENER_BASE = "https://api.dexscreener.com"

_MAX_RETRIES = 3
_BACKOFF_BASE = 2


async def _ds_get(session: aiohttp.ClientSession, path: str, params: dict | None = None) -> dict | list | None:
    url = f"{DEXSCREENER_BASE}{path}"
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.debug("DexScreener API OK %s", path)
                    return data
                if resp.status == 429:
                    wait = _BACKOFF_BASE ** attempt
                    logger.warning("DexScreener rate-limited on %s – retry in %ds", path, wait)
                    await asyncio.sleep(wait)
                    continue
                body = await resp.text()
                logger.error("DexScreener %d on %s: %s", resp.status, path, body[:300])
                return None
        except asyncio.TimeoutError:
            logger.warning("DexScreener timeout on %s (attempt %d)", path, attempt)
            await asyncio.sleep(_BACKOFF_BASE ** attempt)
        except Exception as exc:
            logger.error("DexScreener request error on %s: %s", path, exc)
            return None
    return None


async def _fetch_latest_profiles(session: aiohttp.ClientSession) -> list[dict]:
    data = await _ds_get(session, "/token-profiles/latest/v1")
    if not data or not isinstance(data, list):
        return []
    return data


async def _fetch_latest_boosts(session: aiohttp.ClientSession) -> list[dict]:
    data = await _ds_get(session, "/token-boosts/latest/v1")
    if not data or not isinstance(data, list):
        return []
    return data


async def _fetch_token_pairs(session: aiohttp.ClientSession, chain_id: str, token_address: str) -> list[dict]:
    data = await _ds_get(session, f"/tokens/v1/{chain_id}/{token_address}")
    if not data:
        return []
    if isinstance(data, dict):
        return data.get("pairs", [])
    if isinstance(data, list):
        return data
    return []


def _safe_float(val, default=0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


async def _enrich_from_dexscreener(
    session: aiohttp.ClientSession,
    chain_id: str,
    token_address: str,
    chain: str,
) -> dict | None:
    pairs = await _fetch_token_pairs(session, chain_id, token_address)
    if not pairs:
        return None

    best_pair = max(pairs, key=lambda p: _safe_float((p.get("liquidity") or {}).get("usd")))

    base = best_pair.get("baseToken", {})
    name = base.get("name", "Unknown")
    symbol = base.get("symbol", "???")
    address = base.get("address", token_address)

    liq = best_pair.get("liquidity") or {}
    liquidity = _safe_float(liq.get("usd"))
    market_cap = _safe_float(best_pair.get("marketCap") or best_pair.get("fdv"))
    price_usd = _safe_float(best_pair.get("priceUsd"))
    price_native = _safe_float(best_pair.get("priceNative"))

    vol = best_pair.get("volume") or {}
    volume_24h = _safe_float(vol.get("h24"))

    pc = best_pair.get("priceChange") or {}
    price_change_24h = _safe_float(pc.get("h24"))

    txns = best_pair.get("txns") or {}
    txns_24h = txns.get("h24", {})
    total_txns = _safe_int(txns_24h.get("buys")) + _safe_int(txns_24h.get("sells"))

    created_at = best_pair.get("pairCreatedAt")
    if created_at:
        try:
            ct = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - ct).total_seconds() / 3600
            if age_hours > 24:
                logger.debug("DexScreener skip %s – age %.1fh > 24h", symbol, age_hours)
                return None
        except Exception:
            pass

    if market_cap > 0 and (market_cap < MIN_MCAP or market_cap > MAX_MCAP):
        logger.debug("DexScreener skip %s – mcap $%.0f outside range", symbol, market_cap)
        return None
    if liquidity < MIN_LIQUIDITY:
        logger.debug("DexScreener skip %s – liquidity $%.0f < min", symbol, liquidity)
        return None
    if total_txns == 0:
        logger.debug("DexScreener skip %s – 0 transactions", symbol)
        return None

    already = await db.is_token_already_bought(address, chain)
    if already:
        logger.debug("DexScreener skip %s – already bought", symbol)
        return None

    hp_result = await check_honeypot(session, chain, address)
    if hp_result["is_honeypot"]:
        logger.info(
            "DexScreener skip %s – honeypot (buy=%.1f%% sell=%.1f%%)",
            symbol, hp_result["buy_tax"], hp_result["sell_tax"],
        )
        return None

    info = best_pair.get("info") or {}
    social_links = {}
    for social in (info.get("socials") or []):
        stype = social.get("type", "")
        surl = social.get("url", "")
        if stype and surl:
            social_links[stype] = surl
    for website in (info.get("websites") or []):
        if website.get("url"):
            social_links["website"] = website["url"]
            break

    pair_address = best_pair.get("pairAddress", "")
    dexscreener_url = best_pair.get("url", f"https://dexscreener.com/{chain_id}/{pair_address}")

    dextools_chain = {"SOL": "solana", "ETH": "ether", "BSC": "bsc"}.get(chain.upper(), "solana")
    dextools_url = f"https://www.dextools.io/app/en/{dextools_chain}/pair-explorer/{pair_address}"

    return {
        "name": name,
        "symbol": symbol,
        "contract_address": address,
        "chain": chain.upper(),
        "chain_id": dextools_chain,
        "market_cap": market_cap,
        "liquidity": liquidity,
        "price_usd": price_usd,
        "price_native": price_native,
        "volume_24h": volume_24h,
        "price_change_24h": price_change_24h,
        "holders": 0,
        "buy_tax": hp_result["buy_tax"] if hp_result["checked"] else 0.0,
        "sell_tax": hp_result["sell_tax"] if hp_result["checked"] else 0.0,
        "dextools_url": dextools_url,
        "dex_pair_url": dexscreener_url,
        "deployer_wallet": "",
        "social_links": social_links,
        "pair_address": pair_address,
        "source": "dexscreener",
    }


async def get_token_liquidity(session: aiohttp.ClientSession, chain: str, token_address: str) -> float:
    """Fetch current USD liquidity for a token from DexScreener. Returns 0.0 on failure."""
    chain_id = DS_CHAIN_MAP.get(chain.upper(), "solana")
    pairs = await _fetch_token_pairs(session, chain_id, token_address)
    if not pairs:
        return 0.0
    best = max(pairs, key=lambda p: _safe_float((p.get("liquidity") or {}).get("usd")))
    return _safe_float((best.get("liquidity") or {}).get("usd"))


async def scan_dexscreener(session: aiohttp.ClientSession, chain: str | None = None) -> list[dict]:
    """
    Scan DexScreener for new tokens. Uses two discovery methods:
    1. Latest token profiles — tokens that recently set up their profile
    2. Latest boosted tokens — tokens getting community attention/promotion

    Then enriches each with full pair data and applies the same filters
    as the DexTools scanner.
    """
    chain = (chain or CHAIN).upper()
    chain_id = DS_CHAIN_MAP.get(chain, "solana")
    logger.info("DexScreener scan starting for %s (%s)", chain, chain_id)

    seen: set[str] = set()
    candidates: list[str] = []

    profiles = await _fetch_latest_profiles(session)
    for p in profiles:
        if p.get("chainId") == chain_id:
            addr = p.get("tokenAddress")
            if addr and addr.lower() not in seen:
                seen.add(addr.lower())
                candidates.append(addr)

    boosts = await _fetch_latest_boosts(session)
    for b in boosts:
        if b.get("chainId") == chain_id:
            addr = b.get("tokenAddress")
            if addr and addr.lower() not in seen:
                seen.add(addr.lower())
                candidates.append(addr)

    logger.info("DexScreener found %d candidates to evaluate", len(candidates))

    qualifying: list[dict] = []
    batch_size = 5
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        tasks = [_enrich_from_dexscreener(session, chain_id, addr, chain) for addr in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error("DexScreener enrichment error: %s", result)
                continue
            if result is not None:
                qualifying.append(result)
        if i + batch_size < len(candidates):
            await asyncio.sleep(0.5)

    logger.info("DexScreener scan complete – %d qualifying tokens", len(qualifying))
    return qualifying
