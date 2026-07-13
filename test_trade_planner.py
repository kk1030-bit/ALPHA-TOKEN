from types import SimpleNamespace
import unittest

from trade_planner import build_trade_plan, calculate_4h_market_context


def liquid_components(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "structure_score": 80,
        "capital_score": 60,
        "trigger_score": 70,
        "quality_score": 85,
        "risk_score": 10,
        "liquidity_score": 90,
        "liquidity_ready": True,
        "liquidity_blocked": False,
        "spot_taker_imbalance": 0.2,
        "basis_pct": 0.0,
        "short_squeeze": False,
    }
    values.update(overrides)
    return values


def market_context(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "four_h_close": 101.0,
        "four_h_atr": 4.0,
        "four_h_atr_pct": 4.0,
        "four_h_ema20": 100.0,
        "four_h_ema50": 98.0,
        "four_h_swing_high_20": 130.0,
        "four_h_swing_low_20": 75.0,
        "four_h_range_position_60_pct": 45.0,
        "price_1h_pct": 2.0,
        "oi_1h_pct": 5.0,
        "price_6h_pct": 4.0,
        "price_24h_pct": 8.0,
        "range_24h_position_pct": 45.0,
        "strong_pullback": True,
    }
    values.update(overrides)
    return values


class TradePlannerTests(unittest.TestCase):
    def test_calculates_atr_ema_and_range_from_4h_klines(self) -> None:
        klines = []
        for index in range(70):
            close = 80.0 + index * 0.25
            klines.append([index, close - 0.2, close + 1.0, close - 1.0, close])

        context = calculate_4h_market_context(klines)

        self.assertIsNotNone(context["four_h_atr"])
        self.assertIsNotNone(context["four_h_ema20"])
        self.assertIsNotNone(context["four_h_ema50"])
        self.assertGreater(context["four_h_range_position_60_pct"], 80)

    def test_strong_bottom_setup_builds_long_tp_and_sl(self) -> None:
        plan = build_trade_plan(
            row=SimpleNamespace(mark_price=100.0, funding_rate_pct=0.005),
            metrics={},
            structure={},
            wgl=market_context(),
            book=None,
            components=liquid_components(),
        )

        self.assertEqual(plan["trade_decision"], "做多")
        self.assertLess(plan["stop_loss"], plan["entry_mid"])
        self.assertLess(plan["entry_mid"], plan["take_profit_1"])
        self.assertLess(plan["take_profit_1"], plan["take_profit_2"])
        self.assertGreaterEqual(plan["risk_reward_1"], 1.5)

    def test_distribution_setup_builds_short_tp_and_sl(self) -> None:
        book = SimpleNamespace(
            verdict="偏弱/派發",
            avg_imbalance_50=-0.4,
            ask_depth_change_pct=30.0,
        )
        plan = build_trade_plan(
            row=SimpleNamespace(mark_price=100.0, funding_rate_pct=0.12),
            metrics={},
            structure={},
            wgl=market_context(
                four_h_close=98.0,
                four_h_ema20=100.0,
                four_h_ema50=102.0,
                four_h_swing_low_20=75.0,
                four_h_range_position_60_pct=80.0,
                range_24h_position_pct=80.0,
                price_1h_pct=-2.0,
                oi_1h_pct=5.0,
                price_6h_pct=12.0,
                price_24h_pct=20.0,
                strong_pullback=False,
            ),
            book=book,
            components=liquid_components(spot_taker_imbalance=-0.3, basis_pct=0.3),
        )

        self.assertEqual(plan["trade_decision"], "做空")
        self.assertLess(plan["take_profit_2"], plan["take_profit_1"])
        self.assertLess(plan["take_profit_1"], plan["entry_mid"])
        self.assertLess(plan["entry_mid"], plan["stop_loss"])

    def test_low_trigger_setup_is_not_a_trade(self) -> None:
        plan = build_trade_plan(
            row=SimpleNamespace(mark_price=100.0, funding_rate_pct=0.005),
            metrics={},
            structure={},
            wgl=market_context(price_1h_pct=0.1, oi_1h_pct=0.2, strong_pullback=False),
            book=None,
            components=liquid_components(trigger_score=20, capital_score=20),
        )

        self.assertEqual(plan["trade_decision"], "不交易")
        self.assertIsNone(plan["take_profit_1"])

    def test_single_short_confirmation_is_not_enough(self) -> None:
        plan = build_trade_plan(
            row=SimpleNamespace(mark_price=100.0, funding_rate_pct=0.12),
            metrics={},
            structure={},
            wgl=market_context(
                four_h_close=98.0,
                four_h_ema20=100.0,
                four_h_ema50=102.0,
                four_h_range_position_60_pct=80.0,
                range_24h_position_pct=80.0,
                price_1h_pct=-2.0,
                oi_1h_pct=5.0,
                price_24h_pct=20.0,
                strong_pullback=False,
            ),
            book=None,
            components=liquid_components(spot_taker_imbalance=0.0, basis_pct=0.0),
        )

        self.assertEqual(plan["trade_decision"], "不交易")

    def test_nearby_resistance_blocks_sub_1_5r_long(self) -> None:
        plan = build_trade_plan(
            row=SimpleNamespace(mark_price=100.0, funding_rate_pct=0.005),
            metrics={},
            structure={},
            wgl=market_context(four_h_swing_high_20=104.0),
            book=None,
            components=liquid_components(),
        )

        self.assertEqual(plan["trade_decision"], "不交易")
        self.assertIn("未達 1.5R", plan["plan_reason"])


if __name__ == "__main__":
    unittest.main()
