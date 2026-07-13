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

`1000XECUSDT` long, sourced from the first actionable hourly trade-plan report.

| Field | Value |
| --- | ---: |
| Signal time | 2026-07-13 12:34:07 Asia/Taipei |
| Entry | 0.00596962 |
| Entry zone | 0.0059522843 - 0.0059782879 |
| TP1 | 0.0062830251 |
| TP2 | 0.0064919618 |
| SL | 0.0057606833 |
| Confidence | 100/100 |
| TP1 time | 2026-07-13 14:06:59 Asia/Taipei |
| TP2 time | 2026-07-13 14:07:59 Asia/Taipei |
| Weighted strategy return | +7.00% |
| Leveraged return | +70.00% |
| Gross PnL | +350.00 USDT |

Canonical runtime events are stored in `data/strategy_events.jsonl` under trade ID
`PLAN-1000XECUSDT-1783917247`. Runtime data is excluded from Git and must live on
persistent storage in production.
