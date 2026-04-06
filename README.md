# DexTool Scanner — Multi-User Solana Trading Bot

Automated Telegram trading bot that scans DexTools for newly launched low-cap tokens on **Solana** (with optional ETH/BSC support). Each approved user gets a personal auto-generated Solana wallet — the bot trades independently for each user using their own funds.

## Features

- **Multi-user wallets** — Each user gets a BIP-39 seed-phrase wallet on `/adduser`. Users fund their own wallets and trade independently.
- **Multi-chain support** — Solana (primary), Ethereum, BSC
- **DexTools API v2 integration** — scans hot pools and new token listings
- **Jupiter V6 swaps** — best-route execution on Solana via Jupiter aggregator
- **Uniswap V2 / PancakeSwap V2** — DEX routing for EVM chains
- **Honeypot protection** — dual-check via DexTools audit API + GoPlus Security blocks honeypot tokens before buying
- **Configurable filters** — market cap range, minimum liquidity, honeypot detection
- **Auto take-profit & stop-loss** — monitors positions and sells at target ROI
- **Trailing take-profit** — locks in gains by tracking peak price and selling on configurable drop
- **Telegram interface** — real-time notifications and bot commands
- **SQLite persistence** — tracks detected tokens, open positions, and completed trades per user
- **Encrypted wallet storage** — private keys and seed phrases encrypted at rest with Fernet
- **Wallet export** — users can export seed phrase / private key to import into Phantom or Solflare
- **Broadcast alerts** — "NEW LOWCAP DETECTED" alerts sent to all chats (DMs + groups)
- **Privacy controls** — admin sees all users; regular users see only their own data
- **Rotating log files** — `trading.log` with automatic rotation (5 MB × 3 backups)
- **Graceful shutdown** — handles SIGINT/SIGTERM cleanly
- **Whale tracking** — monitors configurable whale wallets for large buys on watched tokens (Solana only)
- **Anti-rug protection** — monitors liquidity of open positions; auto-sells immediately if liquidity drops below a floor or by a configurable percentage from entry
- **Operator fee system** — configurable percentage fee on profitable trades, automatically transferred to admin wallet with full audit trail

## Prerequisites

- Python 3.11+
- A funded Solana wallet for the admin (base58 private key)
- Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Solana RPC endpoint (public, or Helius/QuickNode for better reliability)
- *(Optional)* DexTools API key ([developer.dextools.io](https://developer.dextools.io)) — paid plans only; bot works without it using DexScreener's free API

> **Required:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `PRIVATE_KEY`, `ENCRYPTION_KEY`
> **Optional:** `DEXTOOLS_API_KEY` (paid — bot works without it using DexScreener's free API)

## Installation

```bash
git clone https://github.com/Savage27zz/dextool-scanner.git
cd dextool-scanner
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your keys and configuration
```

### Generate an encryption key

The bot encrypts all user wallet private keys at rest. Generate an encryption key once:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the output into `ENCRYPTION_KEY=` in your `.env` file. **Back this key up** — without it, stored wallets cannot be decrypted.

## Configuration

Edit `.env` with your values:

| Variable | Description | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from BotFather | *required* |
| `TELEGRAM_CHAT_ID` | Your Telegram user ID (admin) | *required* |
| `PRIVATE_KEY` | Base58 Solana private key (admin wallet) | *required* |
| `ENCRYPTION_KEY` | Fernet key for encrypting user wallets | *required* |
| `RPC_URL_SOL` | Solana RPC endpoint | `https://api.mainnet-beta.solana.com` |
| `RPC_URL_ETH` | Ethereum RPC (optional) | — |
| `RPC_URL_BSC` | BSC RPC (optional) | — |
| `DEXTOOLS_API_KEY` | DexTools API key (optional — enables richer data) | — |
| `DEXTOOLS_PLAN` | DexTools plan tier (`trial`, `standard`, etc.) | `trial` |
| `CHAIN` | Active chain: `SOL`, `ETH`, or `BSC` | `SOL` |
| `BUY_PERCENT` | % of wallet balance to use per trade | `50` |
| `TAKE_PROFIT` | ROI % target to trigger sell | `20` |
| `STOP_LOSS` | ROI % to trigger stop-loss sell (negative, e.g. -30) | `-30` |
| `TRAILING_ENABLED` | Enable trailing take-profit mode | `true` |
| `TRAILING_DROP` | % drop from peak price to trigger trailing sell | `10` |
| `SLIPPAGE` | Slippage tolerance % | `15` |
| `MIN_LIQUIDITY` | Minimum pool liquidity in USD | `5000` |
| `MIN_MCAP` | Minimum market cap in USD | `10000` |
| `MAX_MCAP` | Maximum market cap in USD | `500000` |
| `SCAN_INTERVAL` | Seconds between DexTools scans | `60` |
| `MONITOR_INTERVAL` | Seconds between position checks | `30` |
| `MIN_SCORE` | Minimum safety score (0-100) for auto-buy | `40` |
| `WHALE_TRACKING_ENABLED` | Enable whale/smart-money wallet tracking (Solana only) | `true` |
| `WHALE_CHECK_INTERVAL` | Seconds between whale wallet checks | `45` |
| `WHALE_MIN_SOL` | Minimum SOL spent by whale to trigger alert | `1.0` |
| `ANTIRUG_ENABLED` | Enable anti-rug liquidity protection | `true` |
| `ANTIRUG_MIN_LIQ` | Emergency sell if liquidity drops below this USD amount | `1000` |
| `ANTIRUG_LIQ_DROP_PCT` | Emergency sell if liquidity drops by this % from entry | `70` |
| `OPERATOR_FEE_ENABLED` | Enable operator fee on profitable trades | `true` |
| `OPERATOR_FEE_PCT` | Percentage of profit taken as operator fee | `5` |

## Docker (recommended)

The easiest way to run the bot — no Python install needed.

1. Install [Docker Desktop](https://docker.com/products/docker-desktop/)
2. Clone the repo and configure:
   ```bash
   git clone https://github.com/Savage27zz/dextool-scanner.git
   cd dextool-scanner
   cp .env.example .env
   # Edit .env with your keys (including ENCRYPTION_KEY)
   ```
3. Start the bot:
   ```bash
   docker compose up -d
   ```
4. View logs:
   ```bash
   docker compose logs -f
   ```
5. Stop the bot:
   ```bash
   docker compose down
   ```

Data (database + logs) is persisted in the `data/` directory.

## Usage

```bash
python bot.py
```

The bot will connect to Telegram and send a startup message. Use commands to control it.

## Multi-User Setup

1. **Admin starts the bot** — the admin's existing wallet (from `PRIVATE_KEY`) is automatically registered.
2. **Admin adds users** — `/adduser <user_id>` generates a BIP-39 wallet for the new user and grants bot access.
3. **User funds wallet** — the user sends SOL to the wallet address shown in `/wallet`.
4. **User can export** — `/export` (in DM) shows seed phrase and private key for import into Phantom/Solflare.
5. **Auto-trading** — when the scanner detects a qualifying token, it buys for ALL users who have auto-trade enabled and sufficient balance.
6. **Monitor & sell** — the profit monitor checks all users' positions and sells using each user's own wallet.
7. **Withdraw** — users can withdraw SOL with `/withdraw <amount> <address>`.

### Privacy Model

- **Admin** sees all users' wallets, balances, positions, and trade history.
- **Regular users** see only their own wallet, balance, positions, and history.
- **Admin's wallet** is hidden from other users.
- **Buy/sell notifications** are sent only to the individual user (plus an admin summary).
- **Token detection alerts** are broadcast to all chats (DMs + groups).

## Bot Commands

**Anyone** (including unapproved users):
| Command | Description |
|---|---|
| `/help` | Show bot info and available commands |

**Authorized users** (admin + approved users):
| Command | Description |
|---|---|
| `/wallet` | Your wallet address, balance, and auto-trade status |
| `/status` | Your open positions with live ROI |
| `/balance` | Your wallet SOL balance |
| `/history` | Your last 10 completed trades |
| `/portfolio` | Your full portfolio overview with PnL |
| `/config` | Display current configuration |
| `/buy <address> [amount]` | Manually buy a token with your wallet |
| `/sell <address> [percent]` | Manually sell a token from your wallet |
| `/autotrade on\|off` | Toggle auto-trading for your wallet |
| `/withdraw <amount> <address>` | Withdraw SOL to an external address |
| `/export` | Export wallet credentials (DM only) |

**Admin only** (`TELEGRAM_CHAT_ID` owner):
| Command | Description |
|---|---|
| `/start` | Start scanning and auto-trading for all users |
| `/stop` | Stop scanning (bot stays responsive) |
| `/adduser <id>` | Grant access and generate wallet for a user |
| `/removeuser <id>` | Revoke access and delete user's wallet |
| `/users` | List all users with wallet info and balances |
| `/chats` | List all active bot chats |
| `/status all` | Show all users' open positions |
| `/portfolio all` | Show all users' portfolio overview |
| `/addwhale <address> [label]` | Track a whale/smart-money wallet |
| `/removewhale <address>` | Stop tracking a whale wallet |
| `/whales` | List tracked whale wallets & recent events |
| `/fees` | Show operator fee revenue stats |

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Telegram    │◄───►│    bot.py     │────►│  notifier.py │
│   (Users)     │     │  (commands)   │     │  (messages)  │
└──────────────┘     └──────┬───────┘     └──────────────┘
                            │
                   ┌────────┼────────┐
                   ▼        │        ▼
            ┌────────────┐  │  ┌─────────────┐
            │ scanner.py  │  │  │ monitor.py   │
            │ (DexTools   │  │  │ (per-user    │
            │  API scan)  │  │  │  profit      │
            └──────┬─────┘  │  │  tracking)   │
                   │        │  └──────┬──────┘
                   ▼        │         │
            ┌──────────┐    │  ┌──────┴──────────────────┐
            │  whale_   │    │  │      trader.py           │
            │ tracker   │◄───┘  │  ┌──────────┐           │
            │ (wallet   │       │  │ Solana    │ per-user  │
            │  monitor) │       │  │ Trader    │ keypairs  │
            └──────────┘       │  │ (Jupiter) │           │
                               │  └──────────┘           │
                               └──────────┬──────────────┘
                                          │
                               ┌──────────┴──────────┐
                               │       db.py          │
                               │  (SQLite + wallets   │
                               │   + fee_ledger)      │
                               └──────────┬──────────┘
                                          │
                               ┌──────────┴──────────┐
                               │   crypto_utils.py    │
                               │  (Fernet encryption) │
                               └─────────────────────┘

config.py ─── loaded by all modules (env vars + logger)
```

**Scan → Filter → Buy (per user) → Monitor → Sell** loop:

1. `scanner.py` queries DexTools API every 60s for hot pools and new tokens
2. Filters by market cap, liquidity, age, and honeypot status
3. "NEW LOWCAP DETECTED" alert broadcast to all chats
4. For each user with auto-trade enabled and sufficient balance, `trader.py` executes a buy via Jupiter
5. `monitor.py` checks all users' positions every 30s using Jupiter Price API
6. Anti-rug check runs first each cycle — if liquidity drops, triggers an emergency sell per user
7. When ROI hits the take-profit target, executes a sell per user and logs the trade

## File Overview

| File | Purpose |
|---|---|
| `config.py` | Loads `.env`, validates config, sets up rotating logger |
| `crypto_utils.py` | Fernet encryption/decryption for wallet private keys and seed phrases |
| `db.py` | SQLite async layer — detected tokens, positions, trades, user wallets, bot chats |
| `scanner.py` | DexTools API v2 — scan, enrich, filter new tokens |
| `trader.py` | `SolanaTrader` (Jupiter V6) with per-user keypair support + `EVMTrader` (web3.py) |
| `monitor.py` | Background per-user profit-monitoring loop |
| `notifier.py` | Telegram message formatting, per-user DMs, and broadcast alerts |
| `bot.py` | Entry point — Telegram bot commands, scanner/monitor orchestration, wallet generation |
| `fee_collector.py` | Operator fee calculation and SOL transfer logic |
| `whale_tracker.py` | Background whale wallet tracker — monitors large DEX buys |

## Disclaimer

**This software is for educational purposes only.** Trading cryptocurrencies involves substantial risk of loss. This bot trades real funds automatically. Use at your own risk. The authors are not responsible for any financial losses. Never trade with funds you cannot afford to lose. Always test with small amounts first.
