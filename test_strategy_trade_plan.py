import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot


def plan_position(*, side: str = "LONG") -> dict[str, object]:
    if side == "SHORT":
        tp1, tp2, stop = 95.0, 90.0, 104.0
    else:
        tp1, tp2, stop = 105.25, 108.75, 96.5
    return {
        "id": f"PLAN-TEST-{side}",
        "strategy_model": "trade_plan_v1",
        "symbol": "TESTUSDT",
        "side": side,
        "entry_ts": 1.0,
        "entry_price": 100.0,
        "entry_low": 99.5,
        "entry_high": 100.5,
        "entry_phase": "交易計畫",
        "entry_reason": "test",
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "initial_stop_loss": stop,
        "stop_loss": stop,
        "margin_usd": 500.0,
        "leverage": 10.0,
        "remaining_fraction": 1.0,
        "tp1_hit": False,
        "realized_return_pct": 0.0,
    }


def candle(open_ms: int, *, high: float, low: float, close: float = 100.0) -> list[object]:
    return [open_ms, 100.0, high, low, close, 0.0, open_ms + 59_999]


class TradePlanPositionTests(unittest.TestCase):
    def test_latest_report_persists_machine_readable_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "latest.json"
            candidate = {
                "symbol": "TESTUSDT",
                "report_rank": 1,
                "signal_state": "點火確認",
                "score": 90,
                "trade_decision": "做多",
                "trade_side": "LONG",
                "plan_confidence": 88,
                "entry_low": 99.5,
                "entry_high": 100.5,
                "entry_mid": 100.0,
                "take_profit_1": 105.0,
                "take_profit_2": 108.0,
                "stop_loss": 96.0,
            }
            with patch.object(bot, "WGL_LATEST_REPORT_PATH", path):
                bot.save_latest_wgl_report("report", [candidate], 700)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["items"][0]["trade_decision"], "做多")
        self.assertEqual(payload["items"][0]["entry_mid"], 100.0)

    def test_long_tp1_then_tp2_uses_half_position_weighting(self) -> None:
        position = plan_position()
        events, closed = bot.evaluate_trade_plan_candles(
            position,
            [
                candle(60_000, high=105.3, low=100.1, close=105.2),
                candle(120_000, high=108.8, low=104.0, close=108.7),
            ],
        )

        self.assertTrue(closed)
        self.assertEqual([event["action"] for event in events], ["PLAN_TP1", "PLAN_TP2"])
        self.assertAlmostEqual(events[-1]["pnl_pct"], 7.0)
        self.assertAlmostEqual(events[-1]["leveraged_pnl_pct"], 70.0)
        self.assertAlmostEqual(events[-1]["pnl_usd"], 350.0)
        self.assertEqual(position["stop_loss"], 100.0)

    def test_short_tp1_then_tp2_is_directionally_correct(self) -> None:
        position = plan_position(side="SHORT")
        events, closed = bot.evaluate_trade_plan_candles(
            position,
            [
                candle(60_000, high=99.0, low=94.9, close=95.0),
                candle(120_000, high=94.0, low=89.9, close=90.0),
            ],
        )

        self.assertTrue(closed)
        self.assertEqual(events[-1]["action"], "PLAN_TP2")
        self.assertAlmostEqual(events[-1]["pnl_pct"], 7.5)
        self.assertAlmostEqual(events[-1]["pnl_usd"], 375.0)

    def test_initial_stop_records_full_position_loss(self) -> None:
        position = plan_position()
        events, closed = bot.evaluate_trade_plan_candles(
            position,
            [candle(60_000, high=101.0, low=96.4, close=97.0)],
        )

        self.assertTrue(closed)
        self.assertEqual(events[-1]["action"], "PLAN_SL")
        self.assertAlmostEqual(events[-1]["pnl_pct"], -3.5)
        self.assertAlmostEqual(events[-1]["pnl_usd"], -175.0)

    def test_same_minute_tp1_and_sl_is_counted_as_sl(self) -> None:
        position = plan_position()
        events, closed = bot.evaluate_trade_plan_candles(
            position,
            [candle(60_000, high=106.0, low=96.0, close=102.0)],
        )

        self.assertTrue(closed)
        self.assertEqual([event["action"] for event in events], ["PLAN_SL"])
        self.assertIn("保守原則", events[0]["note"])

    def test_latest_report_opens_once_at_published_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "latest.json"
            path.write_text(
                json.dumps(
                    {
                        "generated_at": 1_000.0,
                        "generated_local": "2026-07-13 12:34",
                        "items": [
                            {
                                "symbol": "TESTUSDT",
                                "trade_decision": "做多",
                                "trade_side": "LONG",
                                "plan_confidence": 90,
                                "entry_low": 99.5,
                                "entry_high": 100.5,
                                "entry_mid": 100.0,
                                "take_profit_1": 105.0,
                                "take_profit_2": 108.0,
                                "stop_loss": 96.0,
                                "risk_reward_1": 1.5,
                                "risk_reward_2": 2.5,
                                "plan_reason": "test",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.object(bot, "WGL_LATEST_REPORT_PATH", path):
                positions, events, alerts = bot.latest_trade_plan_entries([], [], now=1_100.0)
                again, repeated_events, _ = bot.latest_trade_plan_entries(positions, events, now=1_100.0)

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["entry_price"], 100.0)
        self.assertEqual(events[0]["action"], "PLAN_OPEN")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(len(again), 1)
        self.assertEqual(repeated_events, [])


if __name__ == "__main__":
    unittest.main()
