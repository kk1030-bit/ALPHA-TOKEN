# On-chain Signal Playbook

目標：在日線長底部策略開單前，加入鏈上異動判斷，避免只靠 OI 和 K 線。這份規則把鏈上事件轉成偏多、偏空、警戒三種訊號。

## 主要資料源

1. Arkham
   - 用途：地址標籤、實體歸屬、交易所入金/出金、巨鯨/基金/做市商流向。
   - 關鍵 API：
     - `GET /transfers`
     - `GET /token/top_flow/{id}`
     - `GET /intelligence/search`
     - `POST /user/alerts`
   - 優先原因：它支援 entity / deposit selector，例如 `to=deposit:binance` 這種格式，適合抓交易所入金。

2. Etherscan / BscScan / BaseScan 類 API
   - 用途：原始 ERC20 `Transfer` 紀錄、合約事件、代幣持有人變化。
   - 關鍵 API：
     - `tokentx`
     - `getLogs`
   - 角色：當 Arkham 沒標籤或沒有該鏈資料時，用 explorer API 做底層驗證。

3. DexScreener
   - 用途：DEX pair、流動性、成交量、買賣 txns、價格變化。
   - 關鍵 API：
     - `/token-pairs/v1/{chainId}/{tokenAddress}`
     - `/tokens/v1/{chainId}/{tokenAddresses}`
   - 角色：判斷鏈上買盤是真的進場，還是只有轉帳但沒有市場承接。

4. Bitquery
   - 用途：多鏈 GraphQL / WebSocket 串流，追 DEX trades、transfers、holders。
   - 角色：如果之後要做跨鏈即時監控，用它補 Arkham rate limit 或缺鏈問題。

## 人工調查流程

每個標的先確認四件事：

1. Token address
   - 確認 Binance futures 標的對應哪條鏈、哪個合約。
   - 同名幣非常多，不能只靠 symbol。

2. 最近 24h / 7d 大額流向
   - 追 token top flow。
   - 看最大流入、最大流出、交易所、做市商、基金、未知巨鯨。

3. 交易所方向
   - 大額轉入 CEX：偏空，特別是 Bitget、Binance、OKX、Bybit。
   - 大額從 CEX 提出到冷錢包或新錢包：偏多或籌碼轉移，需要看後續有沒有再進 DEX。
   - 交易所之間互轉：警戒，不直接判多空。

4. DEX 市場承接
   - 轉帳後如果 DEX 買量、流動性、價格同步上升，偏多。
   - 轉帳到 CEX 後價格橫盤但 OI 上升，容易是準備砸或對沖，偏空。
   - DEX 流動性被撤，偏空。

## 自動分數

起始分數 `0`。

偏多加分：

- CEX 出金到非交易所錢包，且金額大於流通市值 0.3%：`+2`
- 主要巨鯨 7d 淨增持：`+2`
- DEX 流動性 24h 增加，且價格未大漲：`+2`
- 大額 DEX 買入，且不是同一錢包自買自賣：`+2`
- Token 進入多個新大戶錢包，集中度沒有惡化：`+1`

偏空扣分：

- 大額轉入 Bitget：`-4`
- 大額轉入任一 CEX：`-3`
- 團隊/基金/解鎖錢包轉出：`-4`
- DEX 流動性撤出：`-3`
- 巨鯨 7d 淨減持：`-2`
- CEX 入金後 OI 上升、價格沒有漲：`-2`
- 同一筆資金多次拆單入金交易所：`-2`

判斷：

- `>= +4`：鏈上偏多，可加強日線埋伏信號。
- `+1 ~ +3`：偏多但不夠，列觀察。
- `0`：鏈上中性，不加分。
- `-1 ~ -3`：警戒，策略降權。
- `<= -4`：鏈上偏空，不開單；如果已有倉位，列為出場警報。

## 接到策略 bot 的規則

1. 開倉前必須跑一次 on-chain check。
2. 如果日線策略通過，但鏈上分數 `<= -4`，不開單。
3. 如果鏈上分數 `-1 ~ -3`，只發觀察，不自動開單。
4. 如果鏈上分數 `>= +4`，日線策略分數加權。
5. 已持倉期間，如果出現大額轉入 Bitget 或其他 CEX，立刻通知，不等 3 小時報告。

## 優先整合順序

1. 先做人工調查模板和 TG 指令 `/onchain SYMBOL`。
2. 接 DexScreener，補 token address、DEX 流動性與成交量。
3. 接 Arkham API，如果有 key，就用 entity/deposit label 判斷 CEX 入出金。
4. 沒有 Arkham key 時，先用手工維護的 CEX 地址表 + explorer API 當備援。
5. 最後把 on-chain score 接進日線策略開倉與持倉出場警報。

## Bitget 特別規則

大額轉入 Bitget 預設偏空，因為它通常代表可交易籌碼進入交易所。除非同時出現以下條件，否則不當成利多：

- 轉入金額很小，低於流通市值 0.1%。
- 同時間更大額從其他 CEX 提出。
- DEX 買盤明顯承接，價格站上日線底部區間上緣。
- 已知做市商錢包例行補庫存，且沒有後續賣壓。

