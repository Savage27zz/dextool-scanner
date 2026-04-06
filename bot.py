import asyncio
import signal
import sys

import aiohttp
from telegram.ext import Application, CommandHandler

import db
from config import (
    BUY_PERCENT,
    CHAIN,
    MAX_MCAP,
    MIN_LIQUIDITY,
    MIN_MCAP,
    MONITOR_INTERVAL,
    NATIVE_SYMBOL,
    PRIVATE_KEY,
    SCAN_INTERVAL,
    SLIPPAGE,
    TAKE_PROFIT,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    logger,
)
from monitor import ProfitMonitor, _format_duration
from notifier import Notifier
from scanner import scan_for_new_tokens
from trader import create_trader, generate_solana_wallet

notifier: Notifier | None = None
scanner_task: asyncio.Task | None = None

user_sessions: dict[int, dict] = {}


def _is_admin(update) -> bool:
    return update.effective_user.id == TELEGRAM_CHAT_ID


async def _is_authorized(update) -> bool:
    if _is_admin(update):
        return True
    return await db.is_user_allowed(update.effective_user.id)


async def _reject_unauthorized(update) -> bool:
    if await _is_authorized(update):
        return False
    uid = update.effective_user.id
    uname = update.effective_user.username or update.effective_user.first_name or ""
    await update.message.reply_html(
        f"🔒 <b>Access Denied</b>\n\n"
        f"Your user ID: <code>{uid}</code>\n"
        f"Ask the bot admin to run:\n"
        f"<code>/adduser {uid}</code>"
    )
    logger.warning("Unauthorized access attempt from user %d (%s)", uid, uname)
    return True


async def _get_session(user_id: int) -> dict | None:
    if user_id in user_sessions:
        return user_sessions[user_id]

    if user_id == TELEGRAM_CHAT_ID:
        pk = PRIVATE_KEY
    else:
        wallet_info = await db.get_user_wallet(user_id)
        if not wallet_info:
            return None
        pk = wallet_info["private_key"]

    t = create_trader(CHAIN, private_key=pk)
    m = ProfitMonitor(t, notifier, user_id)
    session = {"trader": t, "monitor": m, "monitor_task": None, "active": False}
    user_sessions[user_id] = session
    return session


def _any_active() -> bool:
    return any(s["active"] for s in user_sessions.values())


async def _ensure_scanner():
    global scanner_task
    if scanner_task is None or scanner_task.done():
        scanner_task = asyncio.create_task(scanner_loop())


async def _maybe_stop_scanner():
    global scanner_task
    if not _any_active() and scanner_task and not scanner_task.done():
        scanner_task.cancel()
        scanner_task = None
        logger.info("Shared scanner stopped — no active users")


async def scanner_loop():
    native = NATIVE_SYMBOL.get(CHAIN.upper(), "SOL")
    logger.info("Shared scanner started (chain=%s, interval=%ds)", CHAIN, SCAN_INTERVAL)

    while True:
        active = {uid: s for uid, s in user_sessions.items() if s["active"]}
        if not active:
            logger.info("No active users — scanner sleeping")
            await asyncio.sleep(SCAN_INTERVAL)
            continue

        try:
            async with aiohttp.ClientSession() as session:
                tokens = await scan_for_new_tokens(session, CHAIN)

                for token in tokens:
                    await db.save_detected_token(token)

                    for uid, user_sess in active.items():
                        try:
                            already = await db.is_token_already_bought(
                                token["contract_address"], token.get("chain", CHAIN).upper(), uid
                            )
                            if already:
                                continue

                            trader = user_sess["trader"]
                            if CHAIN.upper() == "SOL":
                                buy_amount = await trader.get_buy_amount()
                            else:
                                buy_amount = await trader.get_buy_amount(CHAIN)

                            if buy_amount <= 0:
                                logger.warning("Insufficient balance for user %d to buy %s", uid, token["symbol"])
                                continue

                            await notifier.notify_new_token(token, buy_amount, native)

                            if CHAIN.upper() == "SOL":
                                result = await trader.buy_token(token["contract_address"], buy_amount)
                            else:
                                result = await trader.buy_token(token["contract_address"], CHAIN, buy_amount)

                            if result is None:
                                logger.error("Buy failed for %s (user %d)", token["symbol"], uid)
                                await notifier.notify_error(f"Buy failed for {token['symbol']} (user {uid})")
                                continue

                            position = {
                                "user_id": uid,
                                "token_address": token["contract_address"],
                                "token_symbol": token["symbol"],
                                "chain": CHAIN.upper(),
                                "entry_price": result["entry_price"],
                                "tokens_received": result["tokens_received"],
                                "buy_amount_native": result["amount_spent"],
                                "buy_tx_hash": result["tx_hash"],
                                "pair_address": token.get("pair_address", ""),
                            }
                            await db.save_open_position(position)

                            await notifier.notify_buy_executed(
                                symbol=token["symbol"],
                                tokens_received=result["tokens_received"],
                                entry_price=result["entry_price"],
                                tx_hash=result["tx_hash"],
                                chain=CHAIN.upper(),
                            )

                        except Exception as exc:
                            logger.error("Error buying %s for user %d: %s", token.get("symbol"), uid, exc)

        except Exception as exc:
            logger.error("Scanner error: %s", exc)

        await asyncio.sleep(SCAN_INTERVAL)


async def cmd_help(update, context):
    is_auth = await _is_authorized(update)
    admin = _is_admin(update)

    lines = [
        "🤖 <b>DexTool Scanner Bot</b>\n",
        "Scans DexTools for new low-cap tokens on Solana, auto-buys qualifying tokens, and takes profit automatically.",
        "Each user gets their own wallet and trades independently.\n",
    ]

    if is_auth:
        lines.append("<b>Trading:</b>")
        lines.append("/start — Start scanning &amp; auto-trading")
        lines.append("/stop — Pause your trading")
        lines.append("/wallet — Show your wallet address &amp; balance")
        lines.append("/status — Open positions with live ROI")
        lines.append("/balance — Wallet balance")
        lines.append("/history — Last 10 completed trades")
        lines.append("/config — Current bot settings")
        if admin:
            lines.append("\n<b>Admin:</b>")
            lines.append("/adduser &lt;user_id&gt; — Approve a user (generates wallet)")
            lines.append("/removeuser &lt;user_id&gt; — Revoke access")
            lines.append("/users — List authorized users")
    else:
        uid = update.effective_user.id
        lines.append(f"Your user ID: <code>{uid}</code>")
        lines.append(f"Ask the admin to run: <code>/adduser {uid}</code>")

    await update.message.reply_html("\n".join(lines))


async def cmd_start(update, context):
    if await _reject_unauthorized(update):
        return

    uid = update.effective_user.id
    session = await _get_session(uid)
    if not session:
        await update.message.reply_text("No wallet found. Ask admin to /adduser you.")
        return

    if session["active"]:
        await update.message.reply_text("Already running.")
        return

    session["active"] = True
    session["monitor_task"] = asyncio.create_task(session["monitor"].start())
    await _ensure_scanner()

    native = NATIVE_SYMBOL.get(CHAIN.upper(), "SOL")
    balance = await session["trader"].get_balance()

    msg = (
        "🚀 <b>Trading Started</b>\n\n"
        f"Chain: {CHAIN}\n"
        f"Wallet: <code>{session['trader'].wallet}</code>\n"
        f"Balance: {balance:.4f} {native}\n"
        f"Buy: {BUY_PERCENT}% | TP: {TAKE_PROFIT}% | Slippage: {SLIPPAGE}%\n"
        f"Scan every {SCAN_INTERVAL}s | Monitor every {MONITOR_INTERVAL}s"
    )
    await update.message.reply_html(msg)
    logger.info("Trading started by user %d", uid)


async def cmd_stop(update, context):
    if await _reject_unauthorized(update):
        return

    uid = update.effective_user.id
    session = user_sessions.get(uid)
    if not session or not session["active"]:
        await update.message.reply_text("Not running.")
        return

    session["active"] = False
    await session["monitor"].stop()
    if session["monitor_task"] and not session["monitor_task"].done():
        session["monitor_task"].cancel()
    session["monitor_task"] = None

    await _maybe_stop_scanner()

    await update.message.reply_html("🛑 <b>Trading Stopped</b>\nYour scanning and trading paused.")
    logger.info("Trading stopped by user %d", uid)


async def cmd_wallet(update, context):
    if await _reject_unauthorized(update):
        return

    uid = update.effective_user.id
    session = await _get_session(uid)
    if not session:
        await update.message.reply_text("No wallet found. Ask admin to /adduser you.")
        return

    native = NATIVE_SYMBOL.get(CHAIN.upper(), "SOL")
    balance = await session["trader"].get_balance()
    addr = session["trader"].wallet

    msg = (
        "👛 <b>Your Wallet</b>\n\n"
        f"Address:\n<code>{addr}</code>\n\n"
        f"Balance: {balance:.6f} {native}\n\n"
        f"Send {native} to the address above to fund your trading wallet."
    )
    await update.message.reply_html(msg)


async def cmd_status(update, context):
    if await _reject_unauthorized(update):
        return

    uid = update.effective_user.id
    session = await _get_session(uid)
    if not session:
        await update.message.reply_text("No wallet found.")
        return

    positions = await session["monitor"].get_positions_with_roi()

    if not positions:
        await update.message.reply_text("No open positions.")
        return

    native = NATIVE_SYMBOL.get(CHAIN.upper(), "SOL")
    lines = ["📊 <b>Open Positions</b>\n"]
    for p in positions:
        roi = p.get("roi", 0)
        arrow = "🟢" if roi >= 0 else "🔴"
        lines.append(
            f"{arrow} <b>{p['token_symbol']}</b> | ROI: {roi:+.2f}%\n"
            f"   Entry: {p['entry_price']:.10f} {native}\n"
            f"   Current: {p.get('current_price', 0):.10f} {native}\n"
            f"   Amount: {p['tokens_received']:.4f} | Spent: {p['buy_amount_native']:.4f} {native}\n"
        )

    await update.message.reply_html("\n".join(lines))


async def cmd_balance(update, context):
    if await _reject_unauthorized(update):
        return

    uid = update.effective_user.id
    session = await _get_session(uid)
    if not session:
        await update.message.reply_text("No wallet found.")
        return

    native = NATIVE_SYMBOL.get(CHAIN.upper(), "SOL")
    balance = await session["trader"].get_balance()
    await update.message.reply_html(f"💰 <b>Wallet Balance</b>\n{balance:.6f} {native} ({CHAIN})")


async def cmd_history(update, context):
    if await _reject_unauthorized(update):
        return

    uid = update.effective_user.id
    trades = await db.get_trade_history(uid, limit=10)

    if not trades:
        await update.message.reply_text("No completed trades.")
        return

    native = NATIVE_SYMBOL.get(CHAIN.upper(), "SOL")
    lines = ["📜 <b>Trade History</b> (last 10)\n"]
    for t in trades:
        roi = t.get("roi_percent", 0)
        arrow = "🟢" if roi >= 0 else "🔴"
        dur = _format_duration(t.get("duration_seconds", 0))
        lines.append(
            f"{arrow} <b>{t['token_symbol']}</b> | ROI: {roi:+.2f}%\n"
            f"   Buy: {t['buy_amount_native']:.4f} → Sell: {t['sell_amount_native']:.4f} {native}\n"
            f"   Duration: {dur}\n"
        )

    await update.message.reply_html("\n".join(lines))


async def cmd_config(update, context):
    if await _reject_unauthorized(update):
        return

    msg = (
        "⚙️ <b>Configuration</b>\n\n"
        f"Chain: {CHAIN}\n"
        f"Buy Percent: {BUY_PERCENT}%\n"
        f"Take Profit: {TAKE_PROFIT}%\n"
        f"Slippage: {SLIPPAGE}%\n"
        f"Min Liquidity: ${MIN_LIQUIDITY:,}\n"
        f"Market Cap Range: ${MIN_MCAP:,} – ${MAX_MCAP:,}\n"
        f"Scan Interval: {SCAN_INTERVAL}s\n"
        f"Monitor Interval: {MONITOR_INTERVAL}s"
    )
    await update.message.reply_html(msg)


async def cmd_adduser(update, context):
    if not _is_admin(update):
        await update.message.reply_text("Admin only.")
        return

    if not context.args:
        await update.message.reply_html("Usage: <code>/adduser &lt;user_id&gt;</code>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID. Must be a number.")
        return

    existing = await db.get_user_wallet(target_id)
    if existing:
        await update.message.reply_html(
            f"User <code>{target_id}</code> already exists.\n"
            f"Wallet: <code>{existing['wallet_address']}</code>"
        )
        return

    wallet_address, private_key = generate_solana_wallet()
    username = context.args[1] if len(context.args) > 1 else ""
    await db.add_allowed_user(target_id, username, wallet_address, private_key)

    await update.message.reply_html(
        f"✅ <b>User Added</b>\n\n"
        f"User ID: <code>{target_id}</code>\n"
        f"Wallet: <code>{wallet_address}</code>\n\n"
        f"They can now message this bot and use /wallet to see their deposit address.\n"
        f"They need to send SOL to their wallet before trading."
    )


async def cmd_removeuser(update, context):
    if not _is_admin(update):
        await update.message.reply_text("Admin only.")
        return

    if not context.args:
        await update.message.reply_html("Usage: <code>/removeuser &lt;user_id&gt;</code>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID. Must be a number.")
        return

    session = user_sessions.pop(target_id, None)
    if session:
        session["active"] = False
        await session["monitor"].stop()
        await session["trader"].close()

    removed = await db.remove_allowed_user(target_id)
    if removed:
        await _maybe_stop_scanner()
        await update.message.reply_html(f"🚫 User <code>{target_id}</code> removed and wallet deleted.")
    else:
        await update.message.reply_html(f"User <code>{target_id}</code> was not in the list.")


async def cmd_users(update, context):
    if not _is_admin(update):
        await update.message.reply_text("Admin only.")
        return

    users = await db.get_allowed_users()
    if not users:
        await update.message.reply_text("No authorized users (besides admin).")
        return

    lines = ["👥 <b>Authorized Users</b>\n"]
    for u in users:
        name = u.get("username") or "—"
        uid = u["user_id"]
        addr = u.get("wallet_address", "?")
        status = "🟢 active" if uid in user_sessions and user_sessions[uid]["active"] else "⚪ idle"
        lines.append(
            f"• <code>{uid}</code> ({name}) {status}\n"
            f"  Wallet: <code>{addr[:8]}…{addr[-6:]}</code>"
        )

    await update.message.reply_html("\n".join(lines))


async def post_init(application):
    global notifier

    await db.init_db()

    notifier = Notifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    admin_session = await _get_session(TELEGRAM_CHAT_ID)
    native = NATIVE_SYMBOL.get(CHAIN.upper(), "SOL")
    balance = await admin_session["trader"].get_balance()

    logger.info("Bot initialised – chain=%s, admin balance=%.6f %s", CHAIN, balance, native)
    await notifier.send_message(
        f"🤖 <b>DexTool Scanner Online</b>\n"
        f"Chain: {CHAIN} | Admin balance: {balance:.4f} {native}\n"
        f"Send /start to begin trading."
    )


async def shutdown(application):
    for uid, session in user_sessions.items():
        session["active"] = False
        await session["monitor"].stop()
        await session["trader"].close()
    user_sessions.clear()
    logger.info("Shutdown complete")


def main():
    logger.info("Starting DexTool Scanner Bot …")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(shutdown)
        .build()
    )

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("config", cmd_config))
    app.add_handler(CommandHandler("adduser", cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("users", cmd_users))

    def _handle_signal(signum, frame):
        logger.info("Received signal %s – shutting down", signum)
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Polling for Telegram updates …")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
