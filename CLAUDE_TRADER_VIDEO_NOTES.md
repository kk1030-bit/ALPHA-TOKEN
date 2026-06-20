# Claude Trader Video Notes

Source: https://www.youtube.com/watch?v=RetsRS5u-8Q

Video title: `I Let Claude Trade For A Month and Made $102k (With Proof)`

## 核心方法

這支影片的重點不是讓 AI 直接亂下單，而是把交易流程拆成兩層：

1. 研究層
   - AI 作為 analyst，先定義策略條件、風險上限、候選名單與交易論證。
   - 這一層偏非決定性，適合做 qualitative review。

2. 監控層
   - 交易後用固定規則和 dashboard 每天監控。
   - 這一層偏決定性，負責價格、風險、新聞、技術指標、出場提醒。

## 可移植到目前 crypto bot 的部分

1. 先有策略約束，再找標的
   - 不能先看到某個幣動了才硬套理由。
   - 我們的約束是：日線長底部、4H 回收、OI/市值合理、funding 不熱、鏈上不偏空。

2. 每個候選都要有交易論證卡
   - 為什麼是它？
   - 多頭證據是什麼？
   - 反方證據是什麼？
   - 觸發條件是什麼？
   - 失效條件是什麼？

3. 分數不能只看單一維度
   - 影片用 catalyst、IV、相關性、風險/回報等維度打分。
   - 我們改成：日線結構、4H 結構、OI/市值、funding、鏈上/DEX、是否已過熱。

4. 監控比開倉更重要
   - 開倉後不應該只等 TP/SL。
   - 要持續看：鏈上轉入 CEX、OI 是否轉弱、funding 是否過熱、4H 結構是否破壞。

5. 避免短期限賭完美 timing
   - 影片偏好讓 thesis 有時間發展。
   - 我們對應到 crypto：不要追 15m 爆量，優先找日線/4H 還在主升前的圖。

## Bot 改進方向

1. 新增 `/thesis SYMBOL`
   - 輸出交易論證卡，而不是只有分數。

2. 每小時雷達只列候選，但進場前要看 thesis
   - 每小時報告負責篩選。
   - `/thesis` 負責確認是否值得研究/埋伏。

3. 策略開倉前增加 thesis gate
   - 日線 + 4H + 鏈上都通過，才允許開單。
   - 鏈上偏空或結構破壞，只列觀察，不進場。

4. 之後可加新聞/敘事資料源
   - 對 crypto 來說，類似影片中的 catalyst。
   - 可先接 X/Telegram/官方公告，但要防止雜訊。

