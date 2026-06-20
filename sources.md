# Sources Checked

- Binance Open Interest docs: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest
- Binance Open Interest Statistics docs: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest-Statistics
- Telegram Bot API docs: https://core.telegram.org/bots/api
- CryptoBubbles dynamic market-cap data: https://cryptobubbles.net/backend/data/bubbles1000.usd.json
- Arkham Intel API guide: https://arkm.com/api/docs
- Arkham API machine-readable index: https://arkm.com/llms.txt
- Arkham transfers endpoint: https://arkm.com/llms/get-transfers.md
- Arkham token top-flow endpoint: https://arkm.com/llms/get-token-top_flow-id.md
- Arkham user alerts endpoint: https://arkm.com/llms/post-user-alerts.md
- Etherscan ERC20 transfer endpoint: https://docs.etherscan.io/api-reference/endpoint/tokentx
- Etherscan event logs endpoint: https://docs.etherscan.io/api-reference/endpoint/getlogs
- DexScreener API reference: https://docs.dexscreener.com/api/reference
- Bitquery V2 API docs: https://docs.bitquery.io/

Notes:

- Binance current OI and OI history endpoints are public market data endpoints and do not require a Binance API key.
- Telegram requires a BotFather token. This is a secret credential and must be created by the bot owner.
- Dynamic monitoring uses CryptoBubbles rank `>= 101`, then keeps symbols that have a matching Binance Futures USDT market.
- Arkham is the preferred source for exchange/deposit labels, including CEX inflow/outflow checks.
- Etherscan-style explorers are the raw fallback for ERC20 transfers and contract event logs.
- DexScreener is used to verify whether on-chain transfers are supported by DEX liquidity and market activity.
- Bitquery is the cross-chain streaming fallback when a live multi-chain transfer/trade feed is needed.
