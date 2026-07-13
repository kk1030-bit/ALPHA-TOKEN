# ALPHA-TOKEN

Telegram bot for Binance Futures open-interest monitoring, WGL-style phase scoring, order-book accumulation checks, and hourly research reports.

The bot scans every Binance USD-M USDT contract. CryptoBubbles market cap and rank are optional reference fields only: they never filter, score, or order candidates. A Telegram BotFather token is required; no Binance API key is required for public market data.

## Main Features

Deferred design work is recorded in [NIGHT_MODE_PLAN.md](NIGHT_MODE_PLAN.md). The overnight shadow/testnet/live rollout is parked and is not enabled.

- `/report` - latest cached full-universe TOP 5 report; it never starts a blocking rescan or changes statistics.
- `/wgl SYMBOL` or `/thesis SYMBOL` - single-symbol research card.
- `/orderbook SYMBOL` - order-book accumulation fingerprint.
- `/onchain SYMBOL` - verified-address on-chain and DEX evidence.
- `/entry SYMBOL PRICE` - monitor an entered long position.
- `/entry SYMBOL short PRICE` - monitor an entered short position.
- `/positions` - current monitored positions.
- `/exit SYMBOL` - stop monitoring a manually closed position.
- `/strategy_report` - paper positions and TP/SL performance.
- `/alerts on|off` - hourly report and real-time alert switch.
- `/status` - cached report age, next scan, universe coverage, and monitor status.

The Telegram menu exposes only these core workflows. Legacy and diagnostic commands such as `/oi_report`, `/scan`, `/universe`, and detailed settings remain callable for backward compatibility but are hidden from the menu.

Automatic notifications use compact PNG cards by default (`TELEGRAM_CARD_MODE=1`). The hourly TOP 5 is combined into one ranking card; transition, OI spike, position, strategy, and daily-summary events use one action card each. Full text remains in persisted JSON/report files, and Telegram falls back to text if card rendering or `sendPhoto` fails. `/report`, `/strategy_report`, and `/positions` also return cards; detailed research commands remain text-first.

## WGL Report Format

The hourly scanner first evaluates closed daily candles across the full universe, then runs deeper OI, funding, spot-flow, order-book, and verified on-chain checks. Deep-analysis capacity is split between long-bottom structures and a reserved live-momentum lane, so strong OI/price expansion cannot be removed by the bottom-structure ranking alone. Every result is reduced to `做多`, `做空`, or `不交易`. The card separates:

- `方向 / 信心 / 多分 / 空分`
- `進場區 / TP1 / TP2 / SL / R:R`
- `結構 / 資金 / 觸發 / 資料完整度 / 風險`
- `理由 / 倉位管理 / 失效條件`
- `首次推送 / 首訊方向 / 最新推送`
- `推送價格 / 當前幣價 / 推送後漲跌`
- `市值條件：無`; market cap is displayed as non-scoring reference data when available

An actionable plan requires direction agreement, complete liquidity data, valid 4-hour ATR and structure, and at least `TRADE_PLAN_MIN_RISK_REWARD` room to the nearest 4-hour support or resistance. TP1 defaults to at least `1.5R`; TP2 targets `2.5R`. After TP1, the plan takes half profit and moves the remaining stop to the average entry. Any candidate that cannot produce all prices is `不交易`. Repeated symbols remain eligible and are shown through history counters.

The real-time scanner still detects 180-second OI spikes and 1-hour ignition events, but raw detections are persisted rather than pushed directly. Telegram receives an alert only after the same trade planner produces a complete `做多` or `做空` setup with entry, TP1, TP2, and SL.

Long plans require a qualified structure plus synchronized price/OI or a strong pullback reclaim. Short plans require price down with OI up, an elevated price location, a 4-hour loss of EMA20, and at least two independent confirmations from hot funding, spot selling, positive basis, or order-book distribution.

Strong negative funding is treated asymmetrically: when price and OI rise together it is short-squeeze evidence, while crowded positive funding remains a long-risk block.

The bot saves every full-universe structure result and rejection reason, not only TOP 5. It also stores state transitions and signal outcomes. At `23:59` local time it evaluates TP/SL-first, MFE, and MAE from Binance 1-minute candles; a candle that touches TP and SL in the same minute is explicitly marked ambiguous.

Permanent first-push statistics start at `WGL_STATS_START_DATE` (`2026-07-09` by default). Repeated symbols can appear on later scans and on later dates; each appearance is retained.

On-chain scores count only when the DEX token address matches a verified provider contract. Symbol-only DexScreener matches are shown as reference and contribute zero points. Order-book scoring combines persistent depth with executed taker-buy/taker-sell flow to reduce spoof-wall false positives.

Execution liquidity is a hard trade gate. The default profile models a `5,000 USDT` notional position and requires at least `$5M` 24-hour quote turnover, `$100K` recent 1-hour turnover when available, at least `1.5x` the reference order size on both sides within `0.5%`, spread no wider than `0.20%`, and estimated buy/sell slippage no worse than `0.30%`. A failed or incomplete gate becomes `不交易`. Real-time OI spike and ignition alerts also require the complete trade plan, not only the turnover floor.

The planner publishes research levels only; it does not place live exchange orders. Existing manually registered position monitoring remains separate.

This is research automation, not financial advice.

## Required Environment Variables

Copy `.env.example` to `.env` for local use, or set these variables in your cloud host:

```text
TELEGRAM_BOT_TOKEN=your_botfather_token
ALLOWED_CHAT_IDS=optional_comma_separated_chat_ids
WATCH_MODE=dynamic
REPORT_INTERVAL_SECONDS=3600
OI_SPIKE_BATCH_SIZE=220
MOMENTUM_DEEP_QUOTA=15
OI_TREND_WINDOW_SECONDS=3600
OI_TREND_MIN_CONTRACTS_PCT=5
OI_MOMENTUM_MIN_CONTRACTS_PCT=8
STRATEGY_SCAN_INTERVAL_SECONDS=300
ORDERBOOK_WATCH_CANDIDATES=10
STRUCTURE_REFRESH_BATCH_SIZE=60
STRUCTURE_CACHE_SECONDS=21600
LIQUIDITY_REFERENCE_NOTIONAL_USD=5000
LIQUIDITY_MIN_QUOTE_VOLUME_24H_USD=5000000
LIQUIDITY_MIN_QUOTE_VOLUME_1H_USD=100000
LIQUIDITY_MIN_DEPTH_MULTIPLE=1.5
LIQUIDITY_MAX_SPREAD_PCT=0.20
LIQUIDITY_MAX_SLIPPAGE_PCT=0.30
```

Important: never commit `.env`. It contains your Telegram token.

## Local Run

```powershell
cd E:\oi_phase
python -m pip install -r requirements.txt
python bot.py
```

Local test commands:

```powershell
python bot.py --once BTC
python bot.py --report
python bot.py --universe
```

## Cloud Run

GitHub only stores the code. To keep the Telegram bot running while your computer is off, deploy the repo as a background worker on a host such as Render, Railway, Fly.io, or a VPS.

For this bot, persistent storage matters because `data/` contains WGL symbol statistics, daily summaries, position tracking, and the order-book SQLite file. A cloud container without persistent storage can still run, but those files can disappear after redeploys or restarts.

### Render

This repo includes `render.yaml` and a `Dockerfile`. The blueprint runs a Docker background worker and mounts a 1 GB persistent disk at `/app/data`.

1. Push this repo to GitHub.
2. In Render, create a new Blueprint or Background Worker from the repo.
3. Set environment variables:
   - `TELEGRAM_BOT_TOKEN`
   - `ALLOWED_CHAT_IDS` if you want to restrict access
   - `WATCH_MODE=dynamic`
4. Start the worker.

Recommended Render setup:

- Service type: Background Worker
- Plan: Starter or higher
- Disk: 1 GB, mount path `/app/data`
- Auto deploy: enabled from GitHub

Start command when not using Docker:

```bash
python bot.py
```

### Railway

Use the included `Dockerfile` or `Procfile`.

Set:

```text
TELEGRAM_BOT_TOKEN=your_botfather_token
WATCH_MODE=dynamic
```

Then deploy as a worker process.

## Data Files

Runtime files are intentionally ignored by git:

- `.env`
- `data/`
- `logs/`
- `*.sqlite`
- generated Excel reports
- daily WGL duplicate-tracking files
- permanent WGL symbol stats and daily WGL summaries
- full-universe scan events, structure cache, state transitions, and path-aware signal outcomes

Cloud containers may reset local sqlite/order-book history on redeploy unless you attach persistent storage. The bot will rebuild snapshots after it starts.

## Rate Limit Notes

The bot has Binance `429/418` cooldown protection. OI spike monitoring rotates through bounded batches while still covering the universe inside the 180-second window. Daily structures are cached and refreshed incrementally, so removing the market-cap filter does not turn into thousands of requests every hour.

## Security

- Keep Telegram tokens in cloud secrets only.
- If a token was ever pasted into chat or committed accidentally, rotate it in BotFather before deploying.
- Use `ALLOWED_CHAT_IDS` in production so random users cannot control the bot.
