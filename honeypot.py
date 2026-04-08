"""Honeypot detection via DexTools audit API with GoPlus Security fallback."""

import aiohttp

from config import CHAIN_MAP, DEXTOOLS_API_KEY, DEXTOOLS_BASE_URL, logger

_DT_HEADERS = {
    "X-API-Key": DEXTOOLS_API_KEY,
    "accept": "application/json",
}

_GOPLUS_CHAIN_IDS = {
    "SOL": "solana",
    "ETH": "1",
    "BSC": "56",
}

_HIGH_TAX_THRESHOLD = 50.0

_RESULT_UNKNOWN = {
    "is_honeypot": False,
    "buy_tax": 0.0,
    "sell_tax": 0.0,
    "checked": False,
    "is_open_source": False,
    "is_mintable": False,
    "is_proxy": False,
    "can_take_back_ownership": False,
    "owner_change_balance": False,
    "creator_percent": 0.0,
    "owner_percent": 0.0,
    "top_holder_percent": 0.0,
    "holder_count": 0,
    "lp_locked": False,
    "lp_holder_count": 0,
    "goplus_checked": False,
}


def _safe_float(val, default=0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


async def _check_dextools(session: aiohttp.ClientSession, chain: str, address: str) -> dict:
    chain_id = CHAIN_MAP.get(chain.upper(), "solana")
    url = f"{DEXTOOLS_BASE_URL}/token/{chain_id}/{address}/audit"
    try:
        async with session.get(
            url, headers=_DT_HEADERS, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status != 200:
                return dict(_RESULT_UNKNOWN)
            data = await resp.json()
    except Exception as exc:
        logger.debug("DexTools audit error for %s: %s", address, exc)
        return dict(_RESULT_UNKNOWN)

    audit = data.get("data", data)
    if not audit or not isinstance(audit, dict):
        return dict(_RESULT_UNKNOWN)

    is_hp = bool(audit.get("isHoneypot", False))
    buy_tax = _safe_float(audit.get("buyTax"))
    sell_tax = _safe_float(audit.get("sellTax"))

    if sell_tax > _HIGH_TAX_THRESHOLD:
        is_hp = True

    return {
        "is_honeypot": is_hp, "buy_tax": buy_tax, "sell_tax": sell_tax, "checked": True,
        "is_open_source": False, "is_mintable": False, "is_proxy": False,
        "can_take_back_ownership": False, "owner_change_balance": False,
        "creator_percent": 0.0, "owner_percent": 0.0, "top_holder_percent": 0.0,
        "holder_count": 0, "lp_locked": False, "lp_holder_count": 0,
        "goplus_checked": False,
    }


async def _check_goplus(session: aiohttp.ClientSession, chain: str, address: str) -> dict:
    gp_chain = _GOPLUS_CHAIN_IDS.get(chain.upper())
    if not gp_chain:
        return dict(_RESULT_UNKNOWN)

    if gp_chain == "solana":
        url = (
            "https://api.gopluslabs.com/api/v1/solana/token_security"
            f"?contract_addresses={address}"
        )
    else:
        url = (
            f"https://api.gopluslabs.com/api/v1/token_security/{gp_chain}"
            f"?contract_addresses={address}"
        )

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return dict(_RESULT_UNKNOWN)
            data = await resp.json()
    except Exception as exc:
        logger.debug("GoPlus check error for %s: %s", address, exc)
        return dict(_RESULT_UNKNOWN)

    result = data.get("result", {})
    token_data = result.get(address.lower()) or result.get(address) or {}
    if not token_data:
        return dict(_RESULT_UNKNOWN)

    is_hp = str(token_data.get("is_honeypot", "0")) == "1"
    buy_tax = _safe_float(token_data.get("buy_tax")) * 100
    sell_tax = _safe_float(token_data.get("sell_tax")) * 100

    if str(token_data.get("cannot_sell_all", "0")) == "1":
        is_hp = True
    if sell_tax > _HIGH_TAX_THRESHOLD:
        is_hp = True

    is_open_source = str(token_data.get("is_open_source", "0")) == "1"
    is_mintable = str(token_data.get("is_mintable", "0")) == "1"
    is_proxy = str(token_data.get("is_proxy", "0")) == "1"
    can_take_back_ownership = str(token_data.get("can_take_back_ownership", "0")) == "1"
    owner_change_balance = str(token_data.get("owner_change_balance", "0")) == "1"

    creator_percent = _safe_float(token_data.get("creator_percent")) * 100
    owner_percent = _safe_float(token_data.get("owner_percent")) * 100

    top_holder_percent = 0.0
    holders_list = token_data.get("holders", [])
    if isinstance(holders_list, list) and holders_list:
        for h in holders_list[:10]:
            if isinstance(h, dict):
                pct = _safe_float(h.get("percent")) * 100
                is_locked = str(h.get("is_locked", "0")) == "1"
                is_contract = str(h.get("is_contract", "0")) == "1"
                if not (is_locked and is_contract):
                    top_holder_percent += pct

    holder_count = int(_safe_float(token_data.get("holder_count", 0)))

    lp_holder_count = int(_safe_float(token_data.get("lp_holder_count", 0)))
    lp_total_supply = _safe_float(token_data.get("lp_total_supply", 0))

    lp_locked = False
    lp_holders = token_data.get("lp_holders", [])
    if isinstance(lp_holders, list):
        for lph in lp_holders:
            if isinstance(lph, dict) and str(lph.get("is_locked", "0")) == "1":
                locked_pct = _safe_float(lph.get("percent")) * 100
                if locked_pct > 50:
                    lp_locked = True
                    break

    if can_take_back_ownership and owner_change_balance:
        is_hp = True
    if creator_percent > 80:
        is_hp = True

    return {
        "is_honeypot": is_hp,
        "buy_tax": buy_tax,
        "sell_tax": sell_tax,
        "checked": True,
        "is_open_source": is_open_source,
        "is_mintable": is_mintable,
        "is_proxy": is_proxy,
        "can_take_back_ownership": can_take_back_ownership,
        "owner_change_balance": owner_change_balance,
        "creator_percent": creator_percent,
        "owner_percent": owner_percent,
        "top_holder_percent": top_holder_percent,
        "holder_count": holder_count,
        "lp_locked": lp_locked,
        "lp_holder_count": lp_holder_count,
        "goplus_checked": True,
    }


async def check_honeypot(session: aiohttp.ClientSession, chain: str, address: str) -> dict:
    """
    Check if a token is a honeypot.  DexTools audit first, GoPlus fallback.

    Returns dict:
        is_honeypot (bool) — True if the token is a honeypot or has >50% sell tax
        buy_tax     (float) — buy tax percentage (0–100)
        sell_tax    (float) — sell tax percentage (0–100)
        checked     (bool)  — True if at least one API returned data
    """
    dt_result = None
    if DEXTOOLS_API_KEY:
        dt_result = await _check_dextools(session, chain, address)

    gp_result = await _check_goplus(session, chain, address)

    if dt_result and dt_result["checked"]:
        merged = dict(gp_result) if gp_result["checked"] else dict(_RESULT_UNKNOWN)
        merged["is_honeypot"] = dt_result["is_honeypot"] or merged.get("is_honeypot", False)
        merged["buy_tax"] = dt_result["buy_tax"]
        merged["sell_tax"] = dt_result["sell_tax"]
        merged["checked"] = True
        if merged["is_honeypot"]:
            logger.warning("HONEYPOT: %s on %s — buy=%.1f%% sell=%.1f%%", address, chain, merged["buy_tax"], merged["sell_tax"])
        return merged

    if gp_result["checked"]:
        if gp_result["is_honeypot"]:
            logger.warning("HONEYPOT (GoPlus): %s on %s — buy=%.1f%% sell=%.1f%%", address, chain, gp_result["buy_tax"], gp_result["sell_tax"])
        return gp_result

    logger.debug("Honeypot check inconclusive for %s on %s — no audit data", address, chain)
    return dict(_RESULT_UNKNOWN)
