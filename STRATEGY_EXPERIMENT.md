# Trade Plan Strategy Experiment

Start date: 2026-07-13

Primary model: `trade_plan_v1`

Paper assumptions:

- Margin per trade: `500 USDT`
- Leverage: `10x`
- TP1: close 50%, then move the remaining stop to entry
- TP2: close the remaining 50%
- Results are gross of fees, funding, and slippage
- Legacy daily-bottom positions remain monitored but do not enter this model's statistics

## Baseline Trade

`1000XECUSDT` long, sourced from the actionable realtime OI trend plan sent at 21:29.

| Field | Value |
| --- | ---: |
| Signal time | 2026-07-13 21:29:15 Asia/Taipei |
| Entry | 0.00653200 |
| Entry zone | 0.0064895393 - 0.0065532304 |
| TP1 | 0.0072610000 |
| TP2 | 0.0074157864 |
| SL | 0.0061784854 |
| Confidence | 96/100 |
| Status | Open |

Canonical runtime events are stored in `data/strategy_events.jsonl` under trade ID
`PLAN-1000XECUSDT-1783949355`. The source event is retained in
`data/spikes/20260713.jsonl`. Runtime data is excluded from Git and must live on
persistent storage in production.
