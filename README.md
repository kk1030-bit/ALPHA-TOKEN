# ALPHA-TOKEN

Telegram bot for Binance Futures open-interest monitoring, WGL-style phase scoring, order-book accumulation checks, and hourly research reports.

The bot uses public Binance USD-M Futures endpoints and CryptoBubbles market-cap ranking. A Telegram BotFather token is required for Telegram polling and messages. No Binance API key is required for the core OI/funding/order-book data.

## Main Features

- `/report` - WGL decision list with first-alert filtering.
- `/wgl SYMBOL` or `/thesis SYMBOL` - single-symbol research card.
- `/orderbook SYMBOL` - order-book accumulation fingerprint.
- `/oi SYMBOL` - single-symbol OI analysis.
- `/entry SYMBOL PRICE` - monitor an entered long position.
- `/entry SYMBOL short PRICE` - monitor an entered short position.
- `/positions` - current monitored positions.
- `/oi_report` - legacy OI attention report and Excel output.

## WGL Report Format

The hourly WGL report now prints each top candidate as a `資金異動` card:

- `幣種 / 分數 / 品質 / 風險 / 模式`
- `妖幣欄位 / 妖幣原因`
- `首次推送 / 首訊方向 / 最新推送`
- `推送價格 / 當前幣價 / 推送後漲跌`
- `看漲情緒 / 市值 / 短線異動 / 趨勢異動`

The underlying decisions are still tracked as `可開單`, `待確認`, `觀察`, and `不要進`; repeated symbols remain eligible and are shown through the card's history counters.

Duplicate symbols are no longer filtered out of `/report`. The bot keeps permanent symbol statistics and also records each date on which a symbol appears. The first-push baseline starts at `WGL_STATS_START_DATE` (`2026-07-09` by default), so older legacy appearances are ignored. At `23:59` local time it writes and sends a daily summary for every symbol that appeared that day.

Default trade-management assumptions: TP +10% take half, SL -7%, then move stop to entry after half take-profit.

This is research automation, not financial advice.

## Required Environment Variables

Copy `.env.example` to `.env` for local use, or set these variables in your cloud host:

```text
TELEGRAM_BOT_TOKEN=your_botfather_token
ALLOWED_CHAT_IDS=optional_comma_separated_chat_ids
WATCH_MODE=dynamic
WGL_OPEN_MAX_RANK=2
WGL_OPEN_MIN_SCORE=60
WGL_IGNITION_MIN_SCORE=55
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

Cloud containers may reset local sqlite/order-book history on redeploy unless you attach persistent storage. The bot will rebuild snapshots after it starts.

## Rate Limit Notes

The bot has Binance `429/418` cooldown protection. If Binance temporarily rate-limits the IP, the bot backs off instead of repeatedly hitting the API.

## Security

- Keep Telegram tokens in cloud secrets only.
- If a token was ever pasted into chat or committed accidentally, rotate it in BotFather before deploying.
- Use `ALLOWED_CHAT_IDS` in production so random users cannot control the bot.
