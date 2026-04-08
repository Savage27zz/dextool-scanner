import asyncio
import time

from solana.rpc.types import TxOpts
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction
from solders.message import Message

import db
from config import (
    OPERATOR_FEE_PCT,
    OPERATOR_FEE_ENABLED,
    TELEGRAM_CHAT_ID,
    logger,
)
from trader import _get_shared_client, create_user_trader


async def collect_fee(
    user_id: int,
    token_symbol: str,
    profit_native: float,
    admin_public_key: str,
) -> dict | None:
    if not OPERATOR_FEE_ENABLED:
        return None

    if profit_native <= 0:
        return None

    if user_id == TELEGRAM_CHAT_ID:
        return None

    fee_amount = profit_native * (OPERATOR_FEE_PCT / 100)

    if fee_amount < 0.001:
        return None

    fee_id = await db.record_fee(
        user_id=user_id,
        token_symbol=token_symbol,
        trade_profit=profit_native,
        fee_amount=fee_amount,
        fee_pct=OPERATOR_FEE_PCT,
        status="pending",
    )

    try:
        user_trader = await create_user_trader(user_id)
        if user_trader is None:
            logger.error("Fee collection failed: no wallet for user %d", user_id)
            await db.update_fee_status(fee_id, "failed")
            return None

        lamports = int(fee_amount * 1e9)
        client = _get_shared_client()
        admin_pubkey = Pubkey.from_string(admin_public_key)

        recent = await client.get_latest_blockhash()
        blockhash = recent.value.blockhash

        ix = transfer(TransferParams(
            from_pubkey=user_trader.keypair.pubkey(),
            to_pubkey=admin_pubkey,
            lamports=lamports,
        ))
        msg = Message.new_with_blockhash([ix], user_trader.keypair.pubkey(), blockhash)
        tx = Transaction.new_unsigned(msg)
        tx.sign([user_trader.keypair], blockhash)

        resp = await client.send_raw_transaction(
            bytes(tx),
            opts=TxOpts(skip_preflight=True, max_retries=3),
        )
        sig = str(resp.value)

        try:
            fee_sig = Signature.from_string(sig)
            start = time.time()
            confirmed = False
            while time.time() - start < 15:
                status_resp = await client.get_signature_statuses([fee_sig])
                statuses = status_resp.value
                if statuses and statuses[0] is not None:
                    if statuses[0].err is None:
                        confirmed = True
                        break
                    else:
                        logger.warning("Fee tx failed on-chain: %s", sig)
                        await db.update_fee_status(fee_id, "failed", sig)
                        return {
                            "fee_id": fee_id,
                            "fee_amount": fee_amount,
                            "fee_pct": OPERATOR_FEE_PCT,
                            "tx_hash": sig,
                            "error": "tx failed on-chain",
                        }
                await asyncio.sleep(2)

            if confirmed:
                await db.update_fee_status(fee_id, "collected", sig)
            else:
                await db.update_fee_status(fee_id, "submitted", sig)
                logger.warning("Fee tx submitted but unconfirmed after 15s: %s", sig)
        except Exception as confirm_exc:
            logger.warning("Fee confirmation check failed: %s", confirm_exc)
            await db.update_fee_status(fee_id, "submitted", sig)

        logger.info(
            "Fee collected: %.6f SOL from user %d for %s trade (%.1f%% of %.6f profit), tx=%s",
            fee_amount, user_id, token_symbol, OPERATOR_FEE_PCT, profit_native, sig,
        )

        return {
            "fee_id": fee_id,
            "fee_amount": fee_amount,
            "fee_pct": OPERATOR_FEE_PCT,
            "tx_hash": sig,
            "profit_native": profit_native,
        }

    except Exception as exc:
        logger.error("Fee transfer failed for user %d: %s", user_id, exc)
        await db.update_fee_status(fee_id, "failed")
        return {
            "fee_id": fee_id,
            "fee_amount": fee_amount,
            "fee_pct": OPERATOR_FEE_PCT,
            "tx_hash": "",
            "error": str(exc),
        }
