from config import logger


def score_token(token: dict) -> dict:
    """
    Score a token 0–100 based on safety signals.
    Returns the token dict with added 'score' (int) and 'score_breakdown' (dict) keys.

    Scoring weights (total = 100):
      - Liquidity depth:        25 pts
      - Liquidity/MCap ratio:   15 pts
      - Volume activity:        15 pts
      - Buy/sell balance:       10 pts
      - Holder count:           10 pts
      - Tax level:              10 pts
      - Social presence:         5 pts
      - Token age:               5 pts
      - Price momentum:          5 pts
    """
    breakdown = {}

    liquidity = token.get("liquidity", 0)
    market_cap = token.get("market_cap", 0)
    volume_24h = token.get("volume_24h", 0)
    holders = token.get("holders", 0)
    buy_tax = token.get("buy_tax", 0)
    sell_tax = token.get("sell_tax", 0)
    social_links = token.get("social_links", {})
    price_change = token.get("price_change_24h", 0)
    source = token.get("source", "dextools")

    # --- 1. Liquidity Depth (25 pts) ---
    # $5k=5, $10k=10, $25k=15, $50k=20, $100k+=25
    if liquidity >= 100_000:
        liq_score = 25
    elif liquidity >= 50_000:
        liq_score = 20
    elif liquidity >= 25_000:
        liq_score = 15
    elif liquidity >= 10_000:
        liq_score = 10
    elif liquidity >= 5_000:
        liq_score = 5
    else:
        liq_score = 0
    breakdown["liquidity"] = liq_score

    # --- 2. Liquidity/MCap Ratio (15 pts) ---
    # Higher ratio = more liquid relative to valuation = safer
    # ratio >= 0.5 = 15, >= 0.3 = 12, >= 0.15 = 9, >= 0.08 = 6, >= 0.03 = 3, else 0
    if market_cap > 0 and liquidity > 0:
        ratio = liquidity / market_cap
        if ratio >= 0.50:
            ratio_score = 15
        elif ratio >= 0.30:
            ratio_score = 12
        elif ratio >= 0.15:
            ratio_score = 9
        elif ratio >= 0.08:
            ratio_score = 6
        elif ratio >= 0.03:
            ratio_score = 3
        else:
            ratio_score = 0
    else:
        ratio_score = 0
    breakdown["liq_mcap_ratio"] = ratio_score

    # --- 3. Volume Activity (15 pts) ---
    # $1k=3, $5k=6, $10k=9, $25k=12, $50k+=15
    if volume_24h >= 50_000:
        vol_score = 15
    elif volume_24h >= 25_000:
        vol_score = 12
    elif volume_24h >= 10_000:
        vol_score = 9
    elif volume_24h >= 5_000:
        vol_score = 6
    elif volume_24h >= 1_000:
        vol_score = 3
    else:
        vol_score = 0
    breakdown["volume"] = vol_score

    # --- 4. Buy/Sell Transaction Balance (10 pts) ---
    # Only available from DexScreener (txns field). For DexTools tokens, give 5/10 (neutral).
    # A healthy token has buys > sells but not overwhelmingly so.
    # Ratio of buys/(buys+sells): 0.4-0.7 = healthy
    txns = token.get("txns_24h", {})
    buys = txns.get("buys", 0) if isinstance(txns, dict) else 0
    sells = txns.get("sells", 0) if isinstance(txns, dict) else 0
    total_txns = buys + sells
    if total_txns > 0:
        buy_ratio = buys / total_txns
        if 0.40 <= buy_ratio <= 0.70:
            txn_score = 10  # healthy balance
        elif 0.30 <= buy_ratio <= 0.80:
            txn_score = 7
        elif 0.20 <= buy_ratio <= 0.90:
            txn_score = 4
        else:
            txn_score = 1  # extreme imbalance (all buys = pump, all sells = dump)
    else:
        txn_score = 5  # neutral if no txn data
    breakdown["buy_sell_balance"] = txn_score

    # --- 5. Holder Count (10 pts) ---
    # 0 (unknown/dexscreener) = 3 (neutral), 1-50 = 2, 50-200 = 5, 200-500 = 7, 500+ = 10
    if holders == 0 and source == "dexscreener":
        holder_score = 3  # unknown — neutral, don't penalize
    elif holders >= 500:
        holder_score = 10
    elif holders >= 200:
        holder_score = 7
    elif holders >= 50:
        holder_score = 5
    elif holders >= 1:
        holder_score = 2
    else:
        holder_score = 0
    breakdown["holders"] = holder_score

    # --- 6. Tax Level (10 pts) ---
    # Combined buy+sell tax: 0% = 10, 1-5% = 8, 5-10% = 5, 10-20% = 2, 20%+ = 0
    # Unknown (dexscreener source, both 0) = 5 (neutral)
    total_tax = buy_tax + sell_tax
    if source == "dexscreener" and total_tax == 0:
        tax_score = 5  # unknown — neutral
    elif total_tax == 0:
        tax_score = 10
    elif total_tax <= 5:
        tax_score = 8
    elif total_tax <= 10:
        tax_score = 5
    elif total_tax <= 20:
        tax_score = 2
    else:
        tax_score = 0
    breakdown["tax"] = tax_score

    # --- 7. Social Presence (5 pts) ---
    # Each social link = 1pt (max 5): website, twitter, telegram, discord, other
    social_count = len(social_links) if isinstance(social_links, dict) else 0
    social_score = min(social_count, 5)
    breakdown["socials"] = social_score

    # --- 8. Token Age (5 pts) ---
    # Very new (< 1h) = 1 (risky), 1-6h = 3, 6-12h = 5, 12-24h = 4
    age_hours = token.get("age_hours")
    if age_hours is not None:
        if age_hours < 1:
            age_score = 1
        elif age_hours < 6:
            age_score = 3
        elif age_hours < 12:
            age_score = 5
        else:
            age_score = 4
    else:
        age_score = 3
    breakdown["age"] = age_score

    # --- 9. Price Momentum (5 pts) ---
    # Slight positive = good (5-50% up = 5), flat = ok (3),
    # extreme pump (>100%) = risky (1), negative = warning (1)
    if 5 <= price_change <= 50:
        momentum_score = 5
    elif 0 <= price_change <= 5:
        momentum_score = 3
    elif 50 < price_change <= 100:
        momentum_score = 3
    elif price_change > 100:
        momentum_score = 1  # extreme pump, likely to dump
    elif -20 <= price_change < 0:
        momentum_score = 2
    else:
        momentum_score = 1  # heavy dump
    breakdown["momentum"] = momentum_score

    total_score = sum(breakdown.values())
    # Clamp to 0-100
    total_score = max(0, min(100, total_score))

    token["score"] = total_score
    token["score_breakdown"] = breakdown

    logger.debug(
        "Score %s: %d/100 — liq=%d ratio=%d vol=%d txn=%d hold=%d tax=%d soc=%d age=%d mom=%d",
        token.get("symbol", "?"), total_score,
        liq_score, ratio_score, vol_score, txn_score, holder_score,
        tax_score, social_score, age_score, momentum_score,
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
        "socials": "Soc",
        "age": "Age",
        "momentum": "Mom",
    }
    parts = []
    for key, label in labels.items():
        val = breakdown.get(key, 0)
        parts.append(f"{label}:{val}")
    return " | ".join(parts)
