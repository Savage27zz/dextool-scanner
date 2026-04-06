import asyncio
from datetime import datetime, timezone

import aiohttp

import db
from config import (
    CHAIN,
    DRY_RUN,
    MONITOR_INTERVAL,
    NATIVE_SYMBOL,
    SELL_PERCENT,
    STOP_LOSS,
    TAKE_PROFIT,
    TRAILING_STOP,
    logger,
)


class ProfitMonitor:
    def __init__(self, trader, notifier):
        self.trader = trader
        self.notifier = notifier
        self.running = False

    async def start(self):
        self.running = True
        logger.info(
            "ProfitMonitor started (interval=%ds, TP=%d%%, SL=%d%%, TS=%d%%, sell=%d%%%s)",
            MONITOR_INTERVAL, TAKE_PROFIT, STOP_LOSS, TRAILING_STOP, SELL_PERCENT,
            ", DRY_RUN" if DRY_RUN else "",
        )
        while self.running:
            try:
                await self.check_positions()
            except Exception as exc:
                logger.error("Monitor error: %s", exc)
            await asyncio.sleep(MONITOR_INTERVAL)

    async def stop(self):
        self.running = False
        logger.info("ProfitMonitor stopped")

    async def _get_current_price(self, token_address: str, chain: str) -> float:
        if chain.upper() == "SOL":
            return await self.trader.get_token_price_via_jupiter(token_address)
        return await self.trader.get_token_price_onchain(token_address, chain)

    async def check_positions(self):
        positions = await db.get_open_positions()
        if not positions:
            return

        logger.debug("Checking %d open positions", len(positions))

        for pos in positions:
            token_address = pos["token_address"]
            chain = pos["chain"]
            entry_price = pos["entry_price"]
            symbol = pos["token_symbol"]

            try:
                current_price = await self._get_current_price(token_address, chain)
                if current_price <= 0:
                    logger.debug("Could not get price for %s", symbol)
                    continue

                roi = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
                logger.debug("%s ROI: %.2f%% (entry=%.10f, current=%.10f)", symbol, roi, entry_price, current_price)

                peak_price = pos.get("peak_price", 0) or entry_price
                if current_price > peak_price:
                    peak_price = current_price
                    await db.update_peak_price(token_address, chain, peak_price)

                trigger = None
                drop_from_peak = 0.0

                if roi <= STOP_LOSS:
                    trigger = "sl"
                    logger.info("SL target hit for %s \u2013 ROI %.2f%% <= %d%%", symbol, roi, STOP_LOSS)
                elif TRAILING_STOP > 0 and roi >= TAKE_PROFIT:
                    drop_from_peak = ((peak_price - current_price) / peak_price) * 100 if peak_price > 0 else 0
                    if drop_from_peak >= TRAILING_STOP:
                        trigger = "ts"
                        logger.info(
                            "Trailing stop for %s \u2013 dropped %.2f%% from peak (threshold %d%%)",
                            symbol, drop_from_peak, TRAILING_STOP,
                        )
                    else:
                        logger.debug(
                            "%s above TP but trailing: peak=%.10f, drop=%.2f%% < %d%%",
                            symbol, peak_price, drop_from_peak, TRAILING_STOP,
                        )
                elif roi >= TAKE_PROFIT:
                    trigger = "tp"
                    logger.info("TP target hit for %s \u2013 ROI %.2f%% >= %d%%", symbol, roi, TAKE_PROFIT)

                if not trigger:
                    continue

                sell_pct = 100 if trigger == "sl" else SELL_PERCENT
                is_full_sell = sell_pct >= 100

                if DRY_RUN:
                    logger.info(
                        "[DRY_RUN] Would sell %d%% of %s (trigger=%s, ROI=%.2f%%)",
                        sell_pct, symbol, trigger, roi,
                    )
                    continue

                if chain.upper() == "SOL":
                    ui_balance, decimals = await self.trader.get_token_balance(token_address)
                    sell_ui = ui_balance * (sell_pct / 100)
                    tokens_raw = int(sell_ui * (10**decimals)) if decimals > 0 else int(sell_ui * 1e9)
                    if tokens_raw <= 0:
                        logger.warning("Zero balance for %s \u2013 skipping sell", symbol)
                        continue
                    sell_result = await self.trader.sell_token(token_address, tokens_raw, decimals)
                else:
                    ui_balance, decimals = await self.trader.get_token_balance(token_address, chain)
                    sell_ui = ui_balance * (sell_pct / 100)
                    tokens_raw = int(sell_ui * (10**decimals))
                    if tokens_raw <= 0:
                        logger.warning("Zero balance for %s \u2013 skipping sell", symbol)
                        continue
                    sell_result = await self.trader.sell_token(token_address, chain, tokens_raw, decimals)

                if sell_result is None:
                    logger.error("Sell failed for %s", symbol)
                    await self.notifier.notify_error(f"Sell failed for {symbol} ({token_address})")
                    continue

                opened_at = pos.get("opened_at", "")
                duration_seconds = 0
                if opened_at:
                    try:
                        if isinstance(opened_at, str):
                            ot = datetime.fromisoformat(opened_at).replace(tzinfo=timezone.utc)
                        else:
                            ot = opened_at
                        duration_seconds = int((datetime.now(timezone.utc) - ot).total_seconds())
                    except Exception as exc:
                        logger.debug("Duration parse error for %s: %s", symbol, exc)

                if is_full_sell:
                    exit_data = {
                        "exit_price": sell_result["exit_price"],
                        "sell_amount_native": sell_result["native_received"],
                        "profit_usd": None,
                        "roi_percent": roi,
                        "sell_tx_hash": sell_result["tx_hash"],
                        "duration_seconds": duration_seconds,
                    }
                    await db.close_position(token_address, chain, exit_data)
                else:
                    await db.reduce_position(token_address, chain, sell_pct / 100)

                duration_str = _format_duration(duration_seconds)
                native = NATIVE_SYMBOL.get(chain.upper(), "SOL")
                pnl_native = sell_result["native_received"] - (pos["buy_amount_native"] * sell_pct / 100)
                sell_note = "" if is_full_sell else f" ({sell_pct}% partial)"

                if trigger == "tp":
                    await self.notifier.notify_take_profit(
                        symbol=symbol + sell_note,
                        entry_price=entry_price,
                        exit_price=sell_result["exit_price"],
                        roi=round(roi, 2),
                        profit_usd=pnl_native,
                        duration=duration_str,
                        tx_hash=sell_result["tx_hash"],
                        chain=chain,
                    )
                elif trigger == "ts":
                    await self.notifier.notify_trailing_stop(
                        symbol=symbol + sell_note,
                        entry_price=entry_price,
                        peak_price=peak_price,
                        exit_price=sell_result["exit_price"],
                        roi=round(roi, 2),
                        drop_pct=round(drop_from_peak, 2),
                        pnl_native=pnl_native,
                        duration=duration_str,
                        tx_hash=sell_result["tx_hash"],
                        chain=chain,
                    )
                else:
                    await self.notifier.notify_stop_loss(
                        symbol=symbol,
                        entry_price=entry_price,
                        exit_price=sell_result["exit_price"],
                        roi=round(roi, 2),
                        loss_native=abs(pnl_native),
                        duration=duration_str,
                        tx_hash=sell_result["tx_hash"],
                        chain=chain,
                    )

            except Exception as exc:
                logger.error("Error checking position %s: %s", symbol, exc)

    async def get_positions_with_roi(self) -> list[dict]:
        positions = await db.get_open_positions()
        enriched = []
        for pos in positions:
            token_address = pos["token_address"]
            chain = pos["chain"]
            entry_price = pos["entry_price"]
            try:
                current_price = await self._get_current_price(token_address, chain)
                roi = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            except Exception as exc:
                logger.debug("Price fetch error for %s: %s", pos["token_symbol"], exc)
                current_price = 0
                roi = 0
            enriched.append({
                **pos,
                "current_price": current_price,
                "roi": round(roi, 2),
            })
        return enriched


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
