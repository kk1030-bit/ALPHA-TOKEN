from __future__ import annotations

from types import SimpleNamespace
import time
import unittest

from wgl_v3 import (
    STRUCTURE_MODEL_VERSION,
    component_scores,
    migrate_structure_screen,
    scan_structure_universe,
    screen_structure_klines,
)


def candle(day: int, close: float, *, volume: float = 1_000.0, closed: bool = True) -> list[object]:
    open_time = day * 86_400_000
    close_time = int(time.time() * 1000) - 1 if closed else int(time.time() * 1000) + 86_400_000
    return [
        open_time,
        str(close),
        str(close * 1.02),
        str(close * 0.98),
        str(close),
        "0",
        close_time,
        str(volume),
    ]


class StructureScreenTests(unittest.TestCase):
    def test_long_drawdown_and_compressed_base_scores_high(self) -> None:
        rows = []
        for day in range(60):
            rows.append(candle(day, 10.0 - day * 0.14, volume=5_000.0))
        for day in range(60, 120):
            close = 1.05 + ((day % 5) - 2) * 0.005
            rows.append(candle(day, close, volume=600.0))
        result = screen_structure_klines("BASEUSDT", rows)
        self.assertTrue(result["eligible"])
        self.assertGreaterEqual(result["score"], 65)
        self.assertGreaterEqual(result["drawdown_from_high_pct"], 80)

    def test_uptrend_at_range_high_is_not_a_bottom(self) -> None:
        rows = [candle(day, 1.0 + day * 0.08, volume=1_000.0) for day in range(100)]
        result = screen_structure_klines("HIGHUSDT", rows)
        self.assertFalse(result["eligible"])
        self.assertLess(result["score"], 45)

    def test_incomplete_daily_candle_is_ignored(self) -> None:
        rows = [candle(day, 2.0, volume=1_000.0) for day in range(30)]
        baseline = screen_structure_klines("CLOSEDUSDT", rows)
        rows.append(candle(31, 20.0, volume=100_000.0, closed=False))
        with_open_candle = screen_structure_klines("CLOSEDUSDT", rows)
        self.assertEqual(baseline["score"], with_open_candle["score"])
        self.assertEqual(baseline["data_points"], with_open_candle["data_points"])

    def test_strong_long_base_accepts_near_compression(self) -> None:
        migrated = migrate_structure_screen(
            {
                "model_version": "v3.1",
                "score": 84,
                "data_points": 179,
                "drawdown_from_high_pct": 89.68,
                "range_position_pct": 1.0,
                "recent_low_extension_pct": 9.59,
                "recent_range_width_pct": 326.59,
                "range_14d_pct": 37.7,
                "base_days": 30,
                "compression_ratio": 1.04,
                "volume_ratio_7d": 0.97,
                "prior_test_pump_pct": 40.0,
                "price_1d_pct": 6.58,
                "price_7d_pct": -1.03,
            }
        )
        self.assertIsNotNone(migrated)
        self.assertTrue(migrated["eligible"])

    def test_controlled_test_day_does_not_kill_long_base(self) -> None:
        migrated = migrate_structure_screen(
            {
                "model_version": "v3.1",
                "score": 66,
                "data_points": 179,
                "drawdown_from_high_pct": 66.29,
                "range_position_pct": 8.73,
                "recent_low_extension_pct": 23.15,
                "recent_range_width_pct": 28.06,
                "base_days": 30,
                "compression_ratio": 0.82,
                "volume_ratio_7d": 0.69,
                "prior_test_pump_pct": 12.72,
                "price_1d_pct": 18.59,
                "price_7d_pct": 16.42,
            }
        )
        self.assertIsNotNone(migrated)
        self.assertTrue(migrated["eligible"])
        self.assertGreaterEqual(migrated["score"], 80)

    def test_ambiguous_high_score_cache_refreshes_before_known_good_cache(self) -> None:
        candidates = []
        cache = {}
        for index in range(5):
            symbol = f"GOOD{index}USDT"
            candidates.append({"row": SimpleNamespace(symbol=symbol, oi_value_usd=1), "metrics": {}})
            cache[symbol] = {
                "cached_at": time.time(),
                "screen": {
                    "model_version": STRUCTURE_MODEL_VERSION,
                    "score": 100,
                    "eligible": True,
                    "data_points": 179,
                    "migrated_from_previous_model": True,
                },
            }
        candidates.append({"row": SimpleNamespace(symbol="GUNUSDT", oi_value_usd=1), "metrics": {}})
        cache["GUNUSDT"] = {
            "cached_at": time.time(),
            "screen": {
                "model_version": STRUCTURE_MODEL_VERSION,
                "score": 84,
                "eligible": False,
                "data_points": 179,
                "migrated_from_previous_model": True,
            },
        }
        fetched = []

        def fetch(symbol: str, **_: object) -> list[list[object]]:
            fetched.append(symbol)
            return [candle(day, 1.0) for day in range(30)]

        scan_structure_universe(candidates, fetch, cache, max_refresh=1, workers=1)
        self.assertEqual(fetched, ["GUNUSDT"])


class ComponentScoreTests(unittest.TestCase):
    def test_pullback_requires_structure_trigger_and_data(self) -> None:
        row = SimpleNamespace(funding_rate_pct=0.01)
        book = SimpleNamespace(
            snapshot_count=20,
            score=65,
            verdict="吸籌觀察",
            avg_imbalance_50=0.2,
        )
        result = component_scores(
            row=row,
            metrics={"contracts_1h_pct": 4.0, "price_1h_pct": 3.0, "spot_taker_imbalance": 0.2},
            structure={"score": 78, "eligible": True, "data_points": 120},
            wgl={
                "strong_pullback": True,
                "price_1h_pct": 3.0,
                "oi_1h_pct": 4.0,
                "price_6h_pct": 6.0,
                "oi_6h_pct": 7.0,
                "volume_ratio": 1.8,
                "risks": [],
            },
            book=book,
            bottom=None,
            launch=None,
            onchain=None,
            orderbook_min_snapshots=12,
        )
        self.assertEqual(result["signal_state"], "回踩進場")
        self.assertGreaterEqual(result["quality_score"], 50)

    def test_hot_funding_invalidates_setup(self) -> None:
        row = SimpleNamespace(funding_rate_pct=0.15)
        result = component_scores(
            row=row,
            metrics={"contracts_1h_pct": 5.0, "price_1h_pct": 4.0},
            structure={"score": 80, "eligible": True, "data_points": 120},
            wgl={"strong_pullback": True, "oi_1h_pct": 5.0, "price_1h_pct": 4.0, "risks": []},
            book=None,
            bottom=None,
            launch=None,
            onchain=None,
            orderbook_min_snapshots=12,
        )
        self.assertEqual(result["signal_state"], "失效/派發")

    def test_failed_structure_gate_cannot_become_ready_from_raw_score(self) -> None:
        row = SimpleNamespace(funding_rate_pct=0.01)
        result = component_scores(
            row=row,
            metrics={"contracts_1h_pct": 1.0, "price_1h_pct": 0.2},
            structure={"score": 85, "eligible": False, "data_points": 120},
            wgl={"oi_1h_pct": 1.0, "price_1h_pct": 0.2, "risks": []},
            book=None,
            bottom=None,
            launch=None,
            onchain=None,
            orderbook_min_snapshots=12,
        )
        self.assertEqual(result["structure_score"], 40)
        self.assertEqual(result["signal_state"], "結構未成熟")

    def test_negative_funding_with_price_and_oi_rising_is_short_squeeze(self) -> None:
        row = SimpleNamespace(funding_rate_pct=-0.60)
        result = component_scores(
            row=row,
            metrics={"contracts_1h_pct": 8.0, "price_1h_pct": 3.0},
            structure={"score": 84, "eligible": True, "data_points": 120},
            wgl={"oi_1h_pct": 8.0, "price_1h_pct": 3.0, "risks": []},
            book=None,
            bottom=None,
            launch=None,
            onchain=None,
            orderbook_min_snapshots=12,
        )
        self.assertTrue(result["short_squeeze"])
        self.assertEqual(result["signal_state"], "點火確認")
        self.assertLess(result["risk_score"], 45)


if __name__ == "__main__":
    unittest.main()
