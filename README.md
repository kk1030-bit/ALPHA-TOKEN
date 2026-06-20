# ALPHA-TOKEN

Telegram bot for Binance Futures open-interest monitoring, WGL-style phase scoring, order-book accumulation checks, and hourly research reports.

The bot uses public Binance USD-M Futures endpoints and CryptoBubbles market-cap ranking. A Telegram BotFather token is required for Telegram polling and messages. No Binance API key is required for the core OI/funding/order-book data.

## Main Features

- `/report` - WGL-style top 5 research list.
- `/wgl SYMBOL` or `/thesis SYMBOL` - single-symbol research card.
- `/orderbook SYMBOL` - order-book accumulation fingerprint.
- `/oi SYMBOL` - single-symbol OI analysis.
- `/entry SYMBOL PRICE` - monitor an entered long position.
- `/entry SYMBOL short PRICE` - monitor an entered short position.
- `/positions` - current monitored positions.
- `/oi_report` - legacy OI attention report and Excel output.

## WGL Report Logic

The WGL stage scorer combines:

- Funding stage: deep negative, returning to zero, or overheated positive.
- Price and OI sync: whether price rises with new OI or diverges.
- Order book: bid/ask imbalance, ask thinning, bid stacking, and distribution risk.
- Structure: RAVE/LAB-style daily and 4H bottom or launch setup.
- OI/market cap and on-chain/DEX signals.

The output labels candidates as:

- `埋伏`
- `再確認偏強`
- `再確認`
- `不要進`

This is research automation, not financial advice.

## Required Environment Variables

Copy `.env.example` to `.env` for local use, or set these variables in your cloud host:

```text
TELEGRAM_BOT_TOKEN=your_botfather_token
ALLOWED_CHAT_IDS=optional_comma_separated_chat_ids
WATCH_MODE=dynamic
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

### Render

This repo includes `render.yaml` and a `Dockerfile`.

1. Push this repo to GitHub.
2. In Render, create a new Blueprint or Background Worker from the repo.
3. Set environment variables:
   - `TELEGRAM_BOT_TOKEN`
   - `ALLOWED_CHAT_IDS` if you want to restrict access
4. Start the worker.

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

Cloud containers may reset local sqlite/order-book history on redeploy unless you attach persistent storage. The bot will rebuild snapshots after it starts.

## Rate Limit Notes

The bot has Binance `429/418` cooldown protection. If Binance temporarily rate-limits the IP, the bot backs off instead of repeatedly hitting the API.

## Security

- Keep Telegram tokens in cloud secrets only.
- If a token was ever pasted into chat or committed accidentally, rotate it in BotFather before deploying.
- Use `ALLOWED_CHAT_IDS` in production so random users cannot control the bot.
