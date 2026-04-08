from config import logger


def score_token(token: dict) -> dict:
    """
    Score a token 0–100 based on safety signals.

    Scoring weights (total = 100):
      - Liquidity depth:        20 pts
      - Liquidity/MCap ratio:   10 pts
      - Volume activity:        10 pts
      - Buy/sell balance:       10 pts
      - Holder distribution:    15 pts  (increased — top holder concentration is critical)
      - Tax level:              10 pts
      - Contract safety:        15 pts  (NEW — GoPlus deep checks)
      - Social presence:         5 pts
      - Price momentum:          5 pts
    """
    breakdown = {}

    liquidity = token.get("liquidity", 0)
    market_cap = token.get("market_cap", 0)
    volume_24h = token.get("volume_24h", 0)
    holders = token.get("holders", 0) or token.get("holder_count", 0)
    buy_tax = token.get("buy_tax", 0)
    sell_tax = token.get("sell_tax", 0)
    social_links = token.get("social_links", {})
    price_change = token.get("price_change_24h", 0)

    # --- 1. Liquidity Depth (20 pts) ---
    if liquidity >= 200_000:
        liq_score = 20
    elif liquidity >= 100_000:
        liq_score = 16
    elif liquidity >= 50_000:
        liq_score = 12
    elif liquidity >= 25_000:
        liq_score = 8
    elif liquidity >= 10_000:
        liq_score = 4
    else:
        liq_score = 0
    breakdown["liquidity"] = liq_score

    # --- 2. Liquidity/MCap Ratio (10 pts) ---
    if market_cap > 0 and liquidity > 0:
        ratio = liquidity / market_cap
        if ratio >= 0.50:
            ratio_score = 10
        elif ratio >= 0.30:
            ratio_score = 8
        elif ratio >= 0.15:
            ratio_score = 6
        elif ratio >= 0.08:
            ratio_score = 4
        elif ratio >= 0.03:
            ratio_score = 2
        else:
            ratio_score = 0
    else:
        ratio_score = 0
    breakdown["liq_mcap_ratio"] = ratio_score

    # --- 3. Volume Activity (10 pts) ---
    if volume_24h >= 100_000:
        vol_score = 10
    elif volume_24h >= 50_000:
        vol_score = 8
    elif volume_24h >= 25_000:
        vol_score = 6
    elif volume_24h >= 10_000:
        vol_score = 4
    elif volume_24h >= 5_000:
        vol_score = 2
    else:
        vol_score = 0
    breakdown["volume"] = vol_score

    # --- 4. Buy/Sell Transaction Balance (10 pts) ---
    txns = token.get("txns_24h", {})
    buys = txns.get("buys", 0) if isinstance(txns, dict) else 0
    sells = txns.get("sells", 0) if isinstance(txns, dict) else 0
    total_txns = buys + sells
    if total_txns >= 100:
        buy_ratio = buys / total_txns
        if 0.40 <= buy_ratio <= 0.65:
            txn_score = 10
        elif 0.30 <= buy_ratio <= 0.75:
            txn_score = 7
        elif 0.20 <= buy_ratio <= 0.85:
            txn_score = 4
        else:
            txn_score = 1
    elif total_txns >= 30:
        txn_score = 4
    elif total_txns >= 10:
        txn_score = 2
    else:
        txn_score = 0
    breakdown["buy_sell_balance"] = txn_score

    # --- 5. Holder Distribution (15 pts) ---
    top_holder_pct = token.get("top_holder_percent", 0)
    holder_count = holders or token.get("holder_count", 0)

    if holder_count >= 1000:
        hcount_score = 7
    elif holder_count >= 500:
        hcount_score = 5
    elif holder_count >= 200:
        hcount_score = 4
    elif holder_count >= 100:
        hcount_score = 3
    elif holder_count >= 50:
        hcount_score = 2
    elif holder_count > 0:
        hcount_score = 1
    else:
        hcount_score = 0

    if top_holder_pct > 0:
        if top_holder_pct <= 20:
            concentration_score = 8
        elif top_holder_pct <= 35:
            concentration_score = 6
        elif top_holder_pct <= 50:
            concentration_score = 4
        elif top_holder_pct <= 70:
            concentration_score = 2
        else:
            concentration_score = 0
    else:
        concentration_score = 3

    breakdown["holders"] = hcount_score + concentration_score

    # --- 6. Tax Level (10 pts) ---
    total_tax = buy_tax + sell_tax
    if total_tax == 0 and token.get("checked", False):
        tax_score = 10
    elif total_tax == 0:
        tax_score = 4
    elif total_tax <= 3:
        tax_score = 8
    elif total_tax <= 6:
        tax_score = 5
    elif total_tax <= 10:
        tax_score = 2
    else:
        tax_score = 0
    breakdown["tax"] = tax_score

    # --- 7. Contract Safety (15 pts) ---
    safety_score = 0

    if not token.get("is_mintable", False):
        safety_score += 4

    if not token.get("is_proxy", False):
        safety_score += 3

    if not token.get("owner_change_balance", False):
        safety_score += 3

    if not token.get("can_take_back_ownership", False):
        safety_score += 2

    if token.get("lp_locked", False):
        safety_score += 3

    if not token.get("goplus_checked", False):
        safety_score = 5  # no GoPlus data — neutral score

    breakdown["contract_safety"] = safety_score

    # --- 8. Social Presence (5 pts) ---
    social_count = len(social_links) if isinstance(social_links, dict) else 0
    social_score = min(social_count, 5)
    breakdown["socials"] = social_score

    # --- 9. Price Momentum (5 pts) ---
    if 5 <= price_change <= 50:
        momentum_score = 5
    elif 0 <= price_change <= 5:
        momentum_score = 3
    elif 50 < price_change <= 100:
        momentum_score = 2
    elif price_change > 100:
        momentum_score = 0
    elif -20 <= price_change < 0:
        momentum_score = 2
    else:
        momentum_score = 0
    breakdown["momentum"] = momentum_score

    total_score = sum(breakdown.values())
    total_score = max(0, min(100, total_score))

    token["score"] = total_score
    token["score_breakdown"] = breakdown

    logger.debug(
        "Score %s: %d/100 — liq=%d ratio=%d vol=%d txn=%d hold=%d tax=%d safe=%d soc=%d mom=%d",
        token.get("symbol", "?"), total_score,
        liq_score, ratio_score, vol_score, txn_score,
        breakdown["holders"], tax_score, safety_score,
        social_score, momentum_score,
    )

    return token


def format_score_bar(score: int) -> str:
    """Return a visual score bar for Telegram messages. e.g. '🟢🟢🟢🟢🟢⚪⚪⚪⚪⚪ 50/100'"""
    filled = round(score / 10)  # 0-10 dots
    empty = 10 - filled
    if score >= 70:
        dot = "🟢"
    elif score >= 40:
        dot = "🟡"
    else:
        dot = "🔴"
    return f"{dot * filled}{'⚪' * empty} {score}/100"


def format_score_breakdown(breakdown: dict) -> str:
    """Return a compact breakdown string for logs/debug."""
    labels = {
        "liquidity": "Liq",
        "liq_mcap_ratio": "L/MC",
        "volume": "Vol",
        "buy_sell_balance": "B/S",
        "holders": "Hold",
        "tax": "Tax",
        "contract_safety": "Safe",
        "socials": "Soc",
        "momentum": "Mom",
    }
    parts = []
    for key, label in labels.items():
        val = breakdown.get(key, 0)
        parts.append(f"{label}:{val}")
    return " | ".join(parts)
