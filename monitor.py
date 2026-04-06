import asyncio
from datetime import datetime, timezone

import aiohttp

import db
from config import CHAIN, MONITOR_INTERVAL, NATIVE_SYMBOL, STOP_LOSS, TAKE_PROFIT, TRAILING_DROP, TRAILING_ENABLED, ANTIRUG_ENABLED, ANTIRUG_MIN_LIQ, ANTIRUG_LIQ_DROP_PCT, logger
from dexscreener import get_token_liquidity


class ProfitMonitor:
    def __init__(self, trader, notifier):
        self.trader = trader
        self.notifier = notifier
        self.running = False

    async def start(self):
        self.running = True
        logger.info(
            "ProfitMonitor started (interval=%ds, TP=%d%%, SL=%d%%, anti-rug=%s)",
            MONITOR_INTERVAL, TAKE_PROFIT, STOP_LOSS, "ON" if ANTIRUG_ENABLED else "OFF",
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

    async def _execute_sell_and_close(self, pos: dict, roi: float, reason: str) -> dict | None:
        token_address = pos["token_address"]
        chain = pos["chain"]
        symbol = pos["token_symbol"]

        if chain.upper() == "SOL":
            ui_balance, decimals = await self.trader.get_token_balance(token_address)
            tokens_raw = int(ui_balance * (10**decimals)) if decimals > 0 else int(ui_balance * 1e9)
            if tokens_raw <= 0:
                logger.warning("Zero balance for %s – skipping %s sell", symbol, reason)
                return None
            sell_result = await self.trader.sell_token(token_address, tokens_raw, decimals)
        else:
            ui_balance, decimals = await self.trader.get_token_balance(token_address, chain)
            tokens_raw = int(ui_balance * (10**decimals))
            if tokens_raw <= 0:
                logger.warning("Zero balance for %s – skipping %s sell", symbol, reason)
                return None
            sell_result = await self.trader.sell_token(token_address, chain, tokens_raw, decimals)

        if sell_result is None:
            logger.error("%s sell failed for %s", reason, symbol)
            await self.notifier.notify_error(f"{reason} sell failed for {symbol} ({token_address})")
            return None

        opened_at = pos.get("opened_at", "")
        duration_seconds = 0
        if opened_at:
            try:
                if isinstance(opened_at, str):
                    ot = datetime.fromisoformat(opened_at).replace(tzinfo=timezone.utc)
                else:
                    ot = opened_at
                duration_seconds = int((datetime.now(timezone.utc) - ot).total_seconds())
            except Exception:
                pass

        exit_data = {
            "exit_price": sell_result["exit_price"],
            "sell_amount_native": sell_result["native_received"],
            "profit_usd": None,
            "roi_percent": roi,
            "sell_tx_hash": sell_result["tx_hash"],
            "duration_seconds": duration_seconds,
        }

        await db.close_position(token_address, chain, exit_data)
        sell_result["duration_seconds"] = duration_seconds
        return sell_result

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
                # ── Anti-rug check (runs first, before price/TP/SL) ──
                if ANTIRUG_ENABLED:
                    rug_detected = False
                    rug_reason = ""
                    try:
                        async with aiohttp.ClientSession() as session:
                            current_liq = await get_token_liquidity(session, chain, token_address)

                        entry_liq = pos.get("entry_liquidity", 0) or 0

                        if current_liq > 0 and current_liq < ANTIRUG_MIN_LIQ:
                            rug_detected = True
                            rug_reason = f"Liquidity ${current_liq:,.0f} below ${ANTIRUG_MIN_LIQ:,} floor"
                        elif entry_liq > 0 and current_liq > 0:
                            drop_pct = ((entry_liq - current_liq) / entry_liq) * 100
                            if drop_pct >= ANTIRUG_LIQ_DROP_PCT:
                                rug_detected = True
                                rug_reason = f"Liquidity dropped {drop_pct:.0f}% (${entry_liq:,.0f} → ${current_liq:,.0f})"

                    except Exception as exc:
                        logger.error("Anti-rug check failed for %s: %s", symbol, exc)

                    if rug_detected:
                        logger.warning("RUG DETECTED for %s: %s", symbol, rug_reason)
                        try:
                            current_price = await self._get_current_price(token_address, chain)
                            roi = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
                        except Exception:
                            roi = -100

                        sell_result = await self._execute_sell_and_close(pos, roi, "Anti-rug")
                        if sell_result:
                            native = NATIVE_SYMBOL.get(chain.upper(), "SOL")
                            duration_str = _format_duration(sell_result["duration_seconds"])
                            loss_native = sell_result["native_received"] - pos["buy_amount_native"]
                            await self.notifier.notify_rug_pull(
                                symbol=symbol,
                                entry_price=entry_price,
                                exit_price=sell_result["exit_price"],
                                roi=round(roi, 2),
                                loss_native=loss_native,
                                duration=duration_str,
                                tx_hash=sell_result["tx_hash"],
                                chain=chain,
                                reason=rug_reason,
                            )
                        continue

                # ── Price / TP / SL logic ──
                current_price = await self._get_current_price(token_address, chain)
                if current_price <= 0:
                    logger.debug("Could not get price for %s", symbol)
                    continue

                roi = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
                logger.debug("%s ROI: %.2f%% (entry=%.10f, current=%.10f)", symbol, roi, entry_price, current_price)

                sold = False

                if TRAILING_ENABLED:
                    trailing_activated = bool(pos.get("trailing_activated", 0))
                    peak_price = pos.get("peak_price", 0) or 0

                    if roi >= TAKE_PROFIT and not trailing_activated:
                        trailing_activated = True
                        peak_price = current_price
                        await db.update_peak_price(token_address, chain, peak_price, True)
                        logger.info(
                            "Trailing activated for %s – ROI %.2f%%, peak=%.10f",
                            symbol, roi, peak_price,
                        )
                        native = NATIVE_SYMBOL.get(chain.upper(), "SOL")
                        await self.notifier.send_message(
                            f"📈 <b>Trailing TP activated</b> for {symbol}\n"
                            f"ROI: {roi:+.2f}% | Peak: {peak_price:.10f} {native}\n"
                            f"Will sell on {TRAILING_DROP}% drop from peak."
                        )

                    elif trailing_activated:
                        if current_price > peak_price:
                            peak_price = current_price
                            await db.update_peak_price(token_address, chain, peak_price, True)
                            logger.debug("New peak for %s: %.10f", symbol, peak_price)
                        else:
                            drop_from_peak = ((peak_price - current_price) / peak_price) * 100 if peak_price > 0 else 0
                            if drop_from_peak >= TRAILING_DROP:
                                logger.info(
                                    "Trailing sell for %s – dropped %.2f%% from peak %.10f",
                                    symbol, drop_from_peak, peak_price,
                                )
                                sell_result = await self._execute_sell_and_close(pos, roi, "Trailing-TP")
                                if sell_result:
                                    sold = True
                                    native = NATIVE_SYMBOL.get(chain.upper(), "SOL")
                                    duration_str = _format_duration(sell_result["duration_seconds"])
                                    profit_native = sell_result["native_received"] - pos["buy_amount_native"]
                                    await self.notifier.notify_take_profit(
                                        symbol=symbol,
                                        entry_price=entry_price,
                                        exit_price=sell_result["exit_price"],
                                        roi=round(roi, 2),
                                        profit_usd=profit_native,
                                        duration=duration_str,
                                        tx_hash=sell_result["tx_hash"],
                                        chain=chain,
                                    )

                else:
                    if roi >= TAKE_PROFIT:
                        logger.info("TP hit for %s – ROI %.2f%% >= %d%%", symbol, roi, TAKE_PROFIT)
                        sell_result = await self._execute_sell_and_close(pos, roi, "Take-profit")
                        if sell_result:
                            sold = True
                            native = NATIVE_SYMBOL.get(chain.upper(), "SOL")
                            duration_str = _format_duration(sell_result["duration_seconds"])
                            profit_native = sell_result["native_received"] - pos["buy_amount_native"]
                            await self.notifier.notify_take_profit(
                                symbol=symbol,
                                entry_price=entry_price,
                                exit_price=sell_result["exit_price"],
                                roi=round(roi, 2),
                                profit_usd=profit_native,
                                duration=duration_str,
                                tx_hash=sell_result["tx_hash"],
                                chain=chain,
                            )

                if not sold and STOP_LOSS < 0 and roi <= STOP_LOSS:
                    logger.info("SL hit for %s – ROI %.2f%% <= %d%%", symbol, roi, STOP_LOSS)
                    sell_result = await self._execute_sell_and_close(pos, roi, "Stop-loss")
                    if sell_result:
                        native = NATIVE_SYMBOL.get(chain.upper(), "SOL")
                        duration_str = _format_duration(sell_result["duration_seconds"])
                        loss_native = sell_result["native_received"] - pos["buy_amount_native"]
                        await self.notifier.notify_stop_loss(
                            symbol=symbol,
                            entry_price=entry_price,
                            exit_price=sell_result["exit_price"],
                            roi=round(roi, 2),
                            loss_native=loss_native,
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
            except Exception:
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
