import json
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bot


class TelegramMenuTests(unittest.TestCase):
    def test_menu_contains_only_core_commands(self) -> None:
        commands = [item["command"] for item in bot.telegram_menu_commands()]
        self.assertEqual(
            commands,
            [
                "report",
                "wgl",
                "entry",
                "positions",
                "exit",
                "orderbook",
                "onchain",
                "strategy_report",
                "alerts",
                "status",
                "help",
            ],
        )
        self.assertEqual(len(commands), len(set(commands)))

    def test_help_hides_legacy_and_diagnostic_commands(self) -> None:
        text = bot.help_text()
        for command in (
            "/oi_report",
            "/scan",
            "/universe",
            "/strategy_settings",
            "/onchain_report",
            "/thesis",
        ):
            self.assertNotIn(command, text)
        self.assertIn("/report", text)
        self.assertIn("/alerts on", text)
        self.assertIn("/status", text)

    def test_alerts_command_controls_subscription(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "subscribers.json"
            with patch.object(bot, "SUBSCRIBERS_PATH", path):
                enabled = bot.handle_text("/alerts on", 123)
                self.assertIn("已開啟", enabled)
                self.assertEqual(bot.load_subscribers(), {123})

                status = bot.handle_text("/alerts", 123)
                self.assertIn("已開啟", status)

                disabled = bot.handle_text("/alerts off", 123)
                self.assertIn("已關閉", disabled)
                self.assertEqual(bot.load_subscribers(), set())

    def test_status_uses_cached_data_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = root / "latest_report.json"
            subscribers_path = root / "subscribers.json"
            positions_path = root / "positions.json"
            strategy_path = root / "strategy_positions.json"
            report_path.write_text(
                json.dumps(
                    {
                        "generated_at": time.time() - 60,
                        "generated_local": "2026-07-10 15:00",
                        "universe_size": 782,
                        "symbols": ["AAAUSDT", "BBBUSDT"],
                    }
                ),
                encoding="utf-8",
            )
            subscribers_path.write_text(json.dumps({"chat_ids": [123]}), encoding="utf-8")
            positions_path.write_text(
                json.dumps(
                    {
                        "positions": [
                            {
                                "chat_id": 123,
                                "symbol": "AAAUSDT",
                                "entry_price": 1.0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            strategy_path.write_text(
                json.dumps({"positions": [{"symbol": "BBBUSDT"}]}),
                encoding="utf-8",
            )

            with ExitStack() as stack:
                stack.enter_context(patch.object(bot, "WGL_LATEST_REPORT_PATH", report_path))
                stack.enter_context(patch.object(bot, "SUBSCRIBERS_PATH", subscribers_path))
                stack.enter_context(patch.object(bot, "POSITIONS_PATH", positions_path))
                stack.enter_context(patch.object(bot, "STRATEGY_POSITIONS_PATH", strategy_path))
                text = bot.format_system_status(123)

            self.assertIn("通知：開", text)
            self.assertIn("市場：782 合約", text)
            self.assertIn("AAAUSDT、BBBUSDT", text)
            self.assertIn("手動盯盤 1", text)
            self.assertIn("模擬單 1", text)


class OiTrendSignalTests(unittest.TestCase):
    def test_bottom_oi_trend_is_actionable_confirmation(self) -> None:
        result = bot.classify_oi_trend_signal(
            contracts_1h_pct=5.4,
            price_1h_pct=6.1,
            oi_value_usd=2_000_000,
            funding_rate_pct=0.005,
            structure={"score": 84, "base_days": 30, "recent_low_extension_pct": 10},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["lane"], "bottom")
        self.assertEqual(result["signal_type"], "底部點火")

    def test_extended_structure_uses_momentum_lane_only(self) -> None:
        result = bot.classify_oi_trend_signal(
            contracts_1h_pct=9.1,
            price_1h_pct=12.5,
            oi_value_usd=5_000_000,
            funding_rate_pct=0.01,
            structure={"score": 11, "base_days": 23, "recent_low_extension_pct": 440},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["lane"], "momentum")
        self.assertIn("禁止直接追價", result["action"])

    def test_crowded_positive_funding_blocks_trend_alert(self) -> None:
        result = bot.classify_oi_trend_signal(
            contracts_1h_pct=12.0,
            price_1h_pct=5.0,
            oi_value_usd=5_000_000,
            funding_rate_pct=0.12,
            structure={"score": 90, "base_days": 30, "recent_low_extension_pct": 10},
        )
        self.assertIsNone(result)

    def test_momentum_quota_survives_structure_ranking(self) -> None:
        candidates = []
        for index in range(20):
            candidates.append(
                {
                    "row": SimpleNamespace(symbol=f"BASE{index}USDT"),
                    "prefilter_score": 90 - index * 0.1,
                    "momentum_rank_score": 0,
                    "live_momentum_score": 0,
                    "selection_lane": "structure",
                    "structure_screen": {"score": 95},
                }
            )
        candidates.append(
            {
                "row": SimpleNamespace(symbol="FASTUSDT"),
                "prefilter_score": 10,
                "momentum_rank_score": 100,
                "live_momentum_score": 80,
                "selection_lane": "momentum",
                "structure_screen": {"score": 10},
            }
        )
        selected = bot.select_deep_candidates(
            candidates,
            {"FASTUSDT"},
            limit=10,
            momentum_quota=2,
        )
        self.assertIn("FASTUSDT", {item["row"].symbol for item in selected})


if __name__ == "__main__":
    unittest.main()
