from __future__ import annotations

import math
from typing import Any


ACTIONABLE_DECISIONS = {"做多", "做空"}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> int:
    return int(round(max(low, min(high, value))))


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    multiplier = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1 or len(highs) != len(closes) or len(lows) != len(closes):
        return None
    true_ranges: list[float] = []
    for index in range(1, len(closes)):
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    return sum(true_ranges[-period:]) / period if len(true_ranges) >= period else None


def calculate_4h_market_context(klines: list[list[Any]]) -> dict[str, float | None]:
    rows: list[tuple[float, float, float]] = []
    for row in klines:
        if len(row) < 5:
            continue
        high = _number(row[2])
        low = _number(row[3])
        close = _number(row[4])
        if high is None or low is None or close is None or high < low or close <= 0:
            continue
        rows.append((high, low, close))
    if len(rows) < 20:
        return {
            "four_h_close": None,
            "four_h_atr": None,
            "four_h_atr_pct": None,
            "four_h_ema20": None,
            "four_h_ema50": None,
            "four_h_swing_high_20": None,
            "four_h_swing_low_20": None,
            "four_h_swing_high_60": None,
            "four_h_swing_low_60": None,
            "four_h_range_position_60_pct": None,
        }

    highs = [row[0] for row in rows]
    lows = [row[1] for row in rows]
    closes = [row[2] for row in rows]
    close = closes[-1]
    atr = _atr(highs, lows, closes)
    high_20 = max(highs[-20:])
    low_20 = min(lows[-20:])
    high_60 = max(highs[-60:])
    low_60 = min(lows[-60:])
    range_position = (close - low_60) / (high_60 - low_60) * 100.0 if high_60 > low_60 else None
    return {
        "four_h_close": close,
        "four_h_atr": atr,
        "four_h_atr_pct": atr / close * 100.0 if atr is not None and close > 0 else None,
        "four_h_ema20": _ema(closes, 20),
        "four_h_ema50": _ema(closes, 50),
        "four_h_swing_high_20": high_20,
        "four_h_swing_low_20": low_20,
        "four_h_swing_high_60": high_60,
        "four_h_swing_low_60": low_60,
        "four_h_range_position_60_pct": range_position,
    }


def _empty_plan(reason: str, *, long_score: int = 0, short_score: int = 0) -> dict[str, Any]:
    return {
        "trade_side": "NONE",
        "trade_decision": "不交易",
        "plan_priority": 0,
        "plan_confidence": max(long_score, short_score),
        "entry_low": None,
        "entry_high": None,
        "entry_mid": None,
        "take_profit_1": None,
        "take_profit_2": None,
        "stop_loss": None,
        "risk_reward_1": None,
        "risk_reward_2": None,
        "stop_distance_pct": None,
        "take_profit_1_pct": None,
        "take_profit_2_pct": None,
        "plan_reason": reason,
        "plan_management": "沒有通過條件，不建立倉位",
        "plan_invalidation": reason,
        "long_score": long_score,
        "short_score": short_score,
    }


def _book_distribution(book: Any | None) -> bool:
    if not book:
        return False
    imbalance = _number(getattr(book, "avg_imbalance_50", None))
    ask_change = _number(getattr(book, "ask_depth_change_pct", None))
    return bool(
        getattr(book, "verdict", "") == "偏弱/派發"
        or (imbalance is not None and imbalance <= -0.25)
        or (ask_change is not None and ask_change >= 25)
    )


def build_trade_plan(
    *,
    row: Any,
    metrics: dict[str, Any] | None,
    structure: dict[str, Any] | None,
    wgl: dict[str, Any] | None,
    book: Any | None,
    components: dict[str, Any],
    min_confidence: int = 70,
    min_risk_reward: float = 1.5,
) -> dict[str, Any]:
    metrics = metrics or {}
    structure = structure or {}
    wgl = wgl or {}
    price = _number(getattr(row, "mark_price", None)) or _number(getattr(row, "price", None))
    if price is None or price <= 0:
        return _empty_plan("缺少有效價格")
    if components.get("liquidity_blocked"):
        reasons = components.get("liquidity_reasons") or []
        return _empty_plan("流動性不足：" + "、".join(str(reason) for reason in reasons[:2]))
    if not components.get("liquidity_ready"):
        return _empty_plan("流動性深度與滑價資料未完整")

    atr = _number(wgl.get("four_h_atr"))
    atr_pct = _number(wgl.get("four_h_atr_pct"))
    close_4h = _number(wgl.get("four_h_close"))
    ema20 = _number(wgl.get("four_h_ema20"))
    ema50 = _number(wgl.get("four_h_ema50"))
    if atr is None or atr <= 0 or atr_pct is None or close_4h is None or ema20 is None:
        return _empty_plan("缺少4H ATR或均線資料")

    structure_score = float(components.get("structure_score") or 0)
    capital_score = float(components.get("capital_score") or 0)
    trigger_score = float(components.get("trigger_score") or 0)
    quality_score = float(components.get("quality_score") or 0)
    risk_score = float(components.get("risk_score") or 0)
    liquidity_score = float(components.get("liquidity_score") or 0)
    funding = _number(getattr(row, "funding_rate_pct", None))
    price_1h = _number(wgl.get("price_1h_pct"))
    if price_1h is None:
        price_1h = _number(metrics.get("price_1h_pct"))
    oi_1h = _number(wgl.get("oi_1h_pct"))
    if oi_1h is None:
        oi_1h = _number(metrics.get("contracts_1h_pct"))
    price_6h = _number(wgl.get("price_6h_pct"))
    price_24h = _number(wgl.get("price_24h_pct"))
    range_24h = _number(wgl.get("range_24h_position_pct"))
    range_4h = _number(wgl.get("four_h_range_position_60_pct"))
    spot_imbalance = _number(components.get("spot_taker_imbalance"))
    basis_pct = _number(components.get("basis_pct"))
    strong_pullback = bool(wgl.get("strong_pullback"))
    short_squeeze = bool(components.get("short_squeeze"))

    price_oi_long = bool(price_1h is not None and oi_1h is not None and price_1h > 0 and oi_1h > 0)
    four_h_reclaim = close_4h >= ema20 * 0.995
    long_score = 10.0
    long_score += 20 if structure_score >= 75 else 14 if structure_score >= 60 else 5 if structure_score >= 50 else 0
    long_score += 15 if capital_score >= 50 else 10 if capital_score >= 35 else 4 if capital_score >= 25 else 0
    long_score += 25 if trigger_score >= 60 else 18 if trigger_score >= 45 else 6 if trigger_score >= 30 else 0
    long_score += 15 if price_oi_long and (price_1h or 0) >= 1 and (oi_1h or 0) >= 2 else 8 if price_oi_long else 0
    long_score += 15 if strong_pullback else 0
    long_score += 8 if short_squeeze else 0
    long_score += 7 if four_h_reclaim else 0
    long_score += 5 if ema50 is not None and ema20 >= ema50 else 0
    long_score += 5 if funding is not None and funding < 0.08 else 0
    long_score += min(5.0, liquidity_score * 0.05)
    long_score -= 25 if risk_score >= 45 else 12 if risk_score >= 30 else 0
    long_score_int = _clamp(long_score)
    long_ready = bool(
        structure_score >= 55
        and trigger_score >= 45
        and quality_score >= 55
        and risk_score < 35
        and (price_oi_long or strong_pullback)
        and four_h_reclaim
        and long_score_int >= min_confidence
    )

    bearish_build = bool(price_1h is not None and oi_1h is not None and price_1h <= -0.8 and oi_1h >= 2.0)
    funding_hot = bool(funding is not None and funding >= 0.08)
    distribution = _book_distribution(book)
    spot_selling = bool(spot_imbalance is not None and spot_imbalance <= -0.15)
    elevated_basis = bool(basis_pct is not None and basis_pct >= 0.20)
    extended_position = bool(
        (price_24h is not None and price_24h >= 12)
        or (price_6h is not None and price_6h >= 8)
        or (range_24h is not None and range_24h >= 65)
        or (range_4h is not None and range_4h >= 65)
    )
    below_ema20 = close_4h <= ema20 * 1.005
    short_score = 10.0
    short_score += 30 if bearish_build else 0
    short_score += 25 if funding is not None and funding >= 0.12 else 15 if funding_hot else 0
    short_score += 20 if distribution else 0
    short_score += 15 if spot_selling else 0
    short_score += 10 if elevated_basis else 0
    short_score += 15 if extended_position else 0
    short_score += 10 if below_ema20 else 0
    short_score += 5 if ema50 is not None and ema20 <= ema50 else 0
    short_score += min(5.0, liquidity_score * 0.05)
    short_score_int = _clamp(short_score)
    short_confirmation_count = sum(
        1 for confirmed in (distribution, spot_selling, funding_hot, elevated_basis) if confirmed
    )
    short_ready = bool(
        bearish_build
        and extended_position
        and short_confirmation_count >= 2
        and quality_score >= 55
        and below_ema20
        and short_score_int >= min_confidence
    )

    if long_ready and short_ready and abs(long_score_int - short_score_int) < 8:
        return _empty_plan("多空條件衝突，不建立倉位", long_score=long_score_int, short_score=short_score_int)
    if long_ready and (not short_ready or long_score_int > short_score_int):
        side = "LONG"
        decision = "做多"
        confidence = long_score_int
        reason = "4H回收且價格/OI同步，底部與資金條件通過"
    elif short_ready:
        side = "SHORT"
        decision = "做空"
        confidence = short_score_int
        reason = "高位轉弱且價格下跌/OI增加，空方資金確認"
    else:
        if max(long_score_int, short_score_int) < min_confidence:
            reason = f"多空優勢不足：多 {long_score_int}/空 {short_score_int}"
        elif not four_h_reclaim and long_score_int >= short_score_int:
            reason = "做多未收回4H EMA20"
        elif not extended_position and short_score_int > long_score_int:
            reason = "做空位階不足，避免在底部追空"
        elif bearish_build and short_score_int > long_score_int and short_confirmation_count < 2:
            reason = f"做空確認不足：{short_confirmation_count}/2"
        elif quality_score < 55:
            reason = f"資料品質不足：{int(quality_score)}/55"
        else:
            reason = "方向條件未同時成立"
        return _empty_plan(reason, long_score=long_score_int, short_score=short_score_int)

    stop_distance_pct = max(3.5, min(8.0, atr_pct * 1.25))
    risk_distance = price * stop_distance_pct / 100.0
    entry_buffer = min(atr * 0.15, price * 0.01)
    if side == "LONG":
        entry_low = price - entry_buffer
        entry_high = price + entry_buffer * 0.5
        stop_loss = price - risk_distance
        tp1 = price + risk_distance * min_risk_reward
        tp2 = price + risk_distance * 2.5
        nearby_level = _number(wgl.get("four_h_swing_high_20"))
        invalidation = "4H收盤跌破SL，或OI轉負／流動性失效"
    else:
        entry_low = price - entry_buffer * 0.5
        entry_high = price + entry_buffer
        stop_loss = price + risk_distance
        tp1 = price - risk_distance * min_risk_reward
        tp2 = price - risk_distance * 2.5
        nearby_level = _number(wgl.get("four_h_swing_low_20"))
        invalidation = "4H收盤站回SL，或OI轉負／流動性失效"

    if nearby_level is not None:
        room = (nearby_level - price) / risk_distance if side == "LONG" else (price - nearby_level) / risk_distance
        if 0 < room < min_risk_reward:
            return _empty_plan(
                f"最近4H{'阻力' if side == 'LONG' else '支撐'}僅 {room:.2f}R，未達 {min_risk_reward:.1f}R",
                long_score=long_score_int,
                short_score=short_score_int,
            )
        if min_risk_reward <= room < 2.5:
            tp1 = nearby_level

    rr1 = abs(tp1 - price) / risk_distance
    rr2 = abs(tp2 - price) / risk_distance
    return {
        "trade_side": side,
        "trade_decision": decision,
        "plan_priority": 2,
        "plan_confidence": confidence,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "entry_mid": price,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "stop_loss": stop_loss,
        "risk_reward_1": rr1,
        "risk_reward_2": rr2,
        "stop_distance_pct": stop_distance_pct,
        "take_profit_1_pct": abs(tp1 - price) / price * 100.0,
        "take_profit_2_pct": abs(tp2 - price) / price * 100.0,
        "plan_reason": reason,
        "plan_management": "TP1停利一半，剩餘止損移到進場均價；TP2出清剩餘",
        "plan_invalidation": invalidation,
        "long_score": long_score_int,
        "short_score": short_score_int,
    }
