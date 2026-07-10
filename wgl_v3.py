from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import math
import statistics
import time
from typing import Any, Callable


TRIGGER_STATES = {"點火確認", "回踩進場"}
STRUCTURE_MODEL_VERSION = "v3.2"
STATE_PRIORITY = {
    "失效/派發": -1,
    "結構未成熟": 0,
    "底部觀察": 1,
    "資金預備": 2,
    "點火確認": 3,
    "回踩進場": 4,
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old * 100.0


def _median(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None and math.isfinite(value)]
    return statistics.median(clean) if clean else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> int:
    return int(round(max(low, min(high, value))))


def migrate_structure_screen(screen: dict[str, Any]) -> dict[str, Any] | None:
    """Upgrade an older successful feature snapshot without another API call."""
    if not isinstance(screen, dict) or int(screen.get("data_points") or 0) <= 0:
        return None
    if screen.get("model_version") == STRUCTURE_MODEL_VERSION:
        return dict(screen)
    migrated = dict(screen)
    source_version = str(migrated.get("model_version") or "")
    score = float(migrated.get("score") or 0)
    extension = _number(migrated.get("recent_low_extension_pct"))
    width = _number(migrated.get("recent_range_width_pct"))
    if source_version != "v3.1":
        if extension is not None and extension > 150:
            score -= 25
        elif extension is not None and extension > 80:
            score -= 12
        if width is not None and width > 1000:
            score -= 18
        elif width is not None and width > 300:
            score -= 8

    price_1d = _number(migrated.get("price_1d_pct"))
    price_7d = _number(migrated.get("price_7d_pct"))
    range_14d = _number(migrated.get("range_14d_pct"))
    base_days = int(migrated.get("base_days") or 0)
    controlled_test_day = bool(
        base_days >= 20
        and extension is not None
        and extension <= 45
        and price_1d is not None
        and 18 < price_1d <= 25
    )
    if source_version == "v3.1" and controlled_test_day:
        score += 18
    score_int = _clamp(score)

    test_pump = _number(migrated.get("prior_test_pump_pct"))
    volume_ratio = _number(migrated.get("volume_ratio_7d"))
    compression = _number(migrated.get("compression_ratio"))
    drawdown = _number(migrated.get("drawdown_from_high_pct")) or 0.0
    range_position = _number(migrated.get("range_position_pct")) or 100.0
    long_base = base_days >= 10
    test_retest = bool(
        base_days >= 5
        and test_pump is not None
        and test_pump >= 20
        and volume_ratio is not None
        and volume_ratio <= 0.90
    )
    strong_long_base = base_days >= 20 and score_int >= 70
    stable_base = bool(
        (compression is not None and compression <= 0.95)
        or (width is not None and width <= 30)
        or (volume_ratio is not None and volume_ratio <= 0.80)
        or (
            strong_long_base
            and compression is not None
            and compression <= 1.10
            and range_14d is not None
            and range_14d <= 45
            and volume_ratio is not None
            and volume_ratio <= 1.10
        )
    )
    extension_ok = bool(extension is not None and (extension <= 80 or (test_retest and extension <= 120)))
    price_not_extended = bool(
        (price_1d is None or price_1d <= 18 or controlled_test_day)
        and (price_7d is None or price_7d <= 45)
    )
    eligible = bool(
        (score_int >= 55 or (test_retest and score_int >= 48))
        and drawdown >= 35
        and range_position <= 50
        and extension_ok
        and price_not_extended
        and stable_base
        and (long_base or test_retest)
    )
    migrated.update(
        {
            "model_version": STRUCTURE_MODEL_VERSION,
            "score": score_int,
            "eligible": eligible,
            "state_hint": "底部觀察" if eligible and score_int >= 70 else "結構候選" if eligible else "結構未成熟",
            "setup_hint": "試盤回踩" if test_retest else "長底部" if long_base else "未成形",
            "migrated_from_previous_model": True,
        }
    )
    if eligible:
        migrated.pop("reject_reason", None)
    else:
        migrated["reject_reason"] = "舊快取依新版門檻重評後未通過"
    return migrated


def live_momentum_score(row: Any, metrics: dict[str, float | None]) -> int:
    """Short-horizon score that deliberately ignores market cap and rank."""
    score = 0.0
    oi_180s = _number(metrics.get("contracts_180s_pct"))
    oi_1h = _number(metrics.get("contracts_1h_pct"))
    price_180s = _number(metrics.get("price_180s_pct"))
    price_1h = _number(metrics.get("price_1h_pct"))
    funding = _number(getattr(row, "funding_rate_pct", None))

    if oi_180s is not None:
        score += 35 if oi_180s >= 8 else 24 if oi_180s >= 3 else 12 if oi_180s >= 1 else 0
    if oi_1h is not None:
        score += 35 if oi_1h >= 15 else 24 if oi_1h >= 5 else 12 if oi_1h >= 2 else 0
    if price_180s is not None and oi_180s is not None and price_180s > 0 and oi_180s > 0:
        score += 15
    if funding is not None:
        if abs(funding) <= 0.04:
            score += 15
        elif funding >= 0.10:
            score -= 15
        elif funding <= -0.10 and (
            (price_180s is not None and oi_180s is not None and price_180s > 0 and oi_180s > 0)
            or (price_1h is not None and oi_1h is not None and price_1h > 0 and oi_1h > 0)
        ):
            score += 10
    return _clamp(score)


def screen_structure_klines(
    symbol: str,
    klines: list[list[Any]],
    *,
    now_ms: int | None = None,
    min_candles: int = 20,
) -> dict[str, Any]:
    """Score a daily base from closed candles only, without market-cap inputs."""
    now_ms = now_ms or int(time.time() * 1000)
    closed = [row for row in klines if len(row) > 7 and int(row[6]) <= now_ms]
    if len(closed) < min_candles:
        return {
            "symbol": symbol,
            "model_version": STRUCTURE_MODEL_VERSION,
            "score": 0,
            "eligible": False,
            "state_hint": "結構未成熟",
            "reject_reason": f"已收盤日線不足 {len(closed)}/{min_candles}",
            "data_points": len(closed),
        }

    closes = [_number(row[4]) for row in closed]
    highs = [_number(row[2]) for row in closed]
    lows = [_number(row[3]) for row in closed]
    quote_volumes = [_number(row[7]) for row in closed]
    valid_highs = [value for value in highs if value is not None]
    valid_lows = [value for value in lows if value is not None]
    if not valid_highs or not valid_lows or closes[-1] is None or closes[-1] <= 0:
        return {
            "symbol": symbol,
            "model_version": STRUCTURE_MODEL_VERSION,
            "score": 0,
            "eligible": False,
            "state_hint": "結構未成熟",
            "reject_reason": "日線價格資料無效",
            "data_points": len(closed),
        }

    last = float(closes[-1])
    high = max(valid_highs)
    low = min(valid_lows)
    if high <= low:
        return {
            "symbol": symbol,
            "model_version": STRUCTURE_MODEL_VERSION,
            "score": 0,
            "eligible": False,
            "state_hint": "結構未成熟",
            "reject_reason": "日線區間不足",
            "data_points": len(closed),
        }

    recent_highs = [value for value in highs[-60:] if value is not None]
    recent_lows = [value for value in lows[-60:] if value is not None]
    recent_closes = [value for value in closes[-60:] if value is not None]
    recent_high = max(recent_highs)
    recent_low = min(recent_lows)
    recent_range_width = _pct(recent_high, recent_low)
    range_position = (last - low) / (high - low) * 100.0
    recent_position = (
        (last - recent_low) / (recent_high - recent_low) * 100.0 if recent_high > recent_low else 50.0
    )
    drawdown = (high - last) / high * 100.0
    recent_extension = _pct(last, recent_low)

    if recent_range_width is not None and recent_range_width <= 20:
        base_days = len(recent_closes[-30:])
        recent_position = min(recent_position, 35.0)
    else:
        base_cutoff = recent_low + (recent_high - recent_low) * 0.30
        base_days = sum(1 for value in recent_closes[-30:] if value <= base_cutoff)

    tr_pct: list[float | None] = []
    previous_close: float | None = None
    for high_value, low_value, close_value in zip(highs, lows, closes):
        if high_value is None or low_value is None or close_value is None or close_value <= 0:
            tr_pct.append(None)
            continue
        tr = high_value - low_value
        if previous_close is not None:
            tr = max(tr, abs(high_value - previous_close), abs(low_value - previous_close))
        tr_pct.append(tr / close_value * 100.0)
        previous_close = close_value
    recent_atr = _median(tr_pct[-14:])
    baseline_atr = _median(tr_pct[-60:-14]) or _median(tr_pct[:-14])
    compression_ratio = recent_atr / baseline_atr if recent_atr and baseline_atr and baseline_atr > 0 else None

    recent_volume = _median(quote_volumes[-7:])
    baseline_volume = _median(quote_volumes[-37:-7]) or _median(quote_volumes[:-7])
    volume_ratio = recent_volume / baseline_volume if recent_volume and baseline_volume and baseline_volume > 0 else None

    daily_returns = [
        _pct(current, previous)
        for previous, current in zip(closes, closes[1:])
        if previous is not None and current is not None
    ]
    test_window = daily_returns[-90:-7] if len(daily_returns) > 14 else daily_returns[:-3]
    prior_test_pump_pct = max(test_window) if test_window else None
    price_1d_pct = _pct(closes[-1], closes[-2] if len(closes) >= 2 else None)
    price_7d_pct = _pct(closes[-1], closes[-8] if len(closes) >= 8 else None)
    high_14d = max(value for value in highs[-14:] if value is not None)
    low_14d = min(value for value in lows[-14:] if value is not None)
    range_14d_pct = _pct(high_14d, low_14d)

    score = 0.0
    reasons: list[str] = []
    if drawdown >= 70:
        score += 24
        reasons.append(f"距歷史高點 -{drawdown:.1f}%")
    elif drawdown >= 50:
        score += 19
        reasons.append(f"距歷史高點 -{drawdown:.1f}%")
    elif drawdown >= 30:
        score += 10

    if range_position <= 25:
        score += 20
        reasons.append(f"長區間低位 {range_position:.1f}%")
    elif range_position <= 45:
        score += 12
    elif range_position <= 60:
        score += 5

    if recent_position <= 35:
        score += 14
        reasons.append(f"60日低位 {recent_position:.1f}%")
    elif recent_position <= 55:
        score += 7

    if recent_extension is not None and recent_extension <= 20:
        score += 12
        reasons.append(f"距60日低點 +{recent_extension:.1f}%")
    elif recent_extension is not None and recent_extension <= 45:
        score += 6

    if base_days >= 14:
        score += 14
        reasons.append(f"底部停留 {base_days}日")
    elif base_days >= 7:
        score += 8

    if compression_ratio is not None and compression_ratio <= 0.65:
        score += 11
        reasons.append(f"波動壓縮 x{compression_ratio:.2f}")
    elif compression_ratio is not None and compression_ratio <= 0.90:
        score += 6
    if recent_range_width is not None and recent_range_width <= 20:
        score += 8
        reasons.append(f"60日窄幅 {recent_range_width:.1f}%")

    if volume_ratio is not None and volume_ratio <= 0.80:
        score += 5
        reasons.append(f"底部量縮 x{volume_ratio:.2f}")
    elif volume_ratio is not None and 0.80 < volume_ratio <= 1.25:
        score += 3

    if prior_test_pump_pct is not None and prior_test_pump_pct >= 15:
        score += 5
        reasons.append(f"曾有試盤 +{prior_test_pump_pct:.1f}%")

    if recent_extension is not None and recent_extension > 150:
        score -= 25
    elif recent_extension is not None and recent_extension > 80:
        score -= 12
    if recent_range_width is not None and recent_range_width > 1000:
        score -= 18
    elif recent_range_width is not None and recent_range_width > 300:
        score -= 8
    if price_1d_pct is not None and price_1d_pct > 25:
        score -= 18
    if price_7d_pct is not None and price_7d_pct > 45:
        score -= 18
    if range_14d_pct is not None and range_14d_pct > 100:
        score -= 10

    score_int = _clamp(score)
    long_base = base_days >= 10
    test_retest = bool(
        base_days >= 5
        and prior_test_pump_pct is not None
        and prior_test_pump_pct >= 20
        and volume_ratio is not None
        and volume_ratio <= 0.90
    )
    strong_long_base = base_days >= 20 and score_int >= 70
    stable_base = bool(
        (compression_ratio is not None and compression_ratio <= 0.95)
        or (recent_range_width is not None and recent_range_width <= 30)
        or (volume_ratio is not None and volume_ratio <= 0.80)
        or (
            strong_long_base
            and compression_ratio is not None
            and compression_ratio <= 1.10
            and range_14d_pct is not None
            and range_14d_pct <= 45
            and volume_ratio is not None
            and volume_ratio <= 1.10
        )
    )
    low_enough = drawdown >= 35 and range_position <= 50
    extension_ok = bool(
        recent_extension is not None
        and (recent_extension <= 80 or (test_retest and recent_extension <= 120))
    )
    controlled_test_day = bool(
        strong_long_base
        and recent_extension is not None
        and recent_extension <= 45
        and price_1d_pct is not None
        and price_1d_pct <= 25
    )
    price_not_extended = bool(
        (price_1d_pct is None or price_1d_pct <= 18 or controlled_test_day)
        and (price_7d_pct is None or price_7d_pct <= 45)
    )
    eligible = bool(
        (score_int >= 55 or (test_retest and score_int >= 48))
        and low_enough
        and extension_ok
        and price_not_extended
        and stable_base
        and (long_base or test_retest)
    )
    if eligible and score_int >= 70:
        state_hint = "底部觀察"
    elif eligible:
        state_hint = "結構候選"
    else:
        state_hint = "結構未成熟"

    reject_reason = ""
    if not eligible:
        if drawdown < 35 or range_position > 50:
            reject_reason = "距高點不夠深"
        elif not extension_ok or not price_not_extended:
            reject_reason = "價格已離開底部或短線過度延伸"
        elif not (long_base or test_retest):
            reject_reason = "底部停留時間不足"
        elif not stable_base:
            reject_reason = "波動與成交量尚未收斂"
        else:
            reject_reason = "底部結構分數不足"

    return {
        "symbol": symbol,
        "model_version": STRUCTURE_MODEL_VERSION,
        "score": score_int,
        "eligible": eligible,
        "state_hint": state_hint,
        "setup_hint": "試盤回踩" if test_retest else "長底部" if long_base else "未成形",
        "reject_reason": reject_reason,
        "data_points": len(closed),
        "range_position_pct": range_position,
        "recent_range_position_pct": recent_position,
        "recent_range_width_pct": recent_range_width,
        "drawdown_from_high_pct": drawdown,
        "recent_low_extension_pct": recent_extension,
        "base_days": base_days,
        "compression_ratio": compression_ratio,
        "volume_ratio_7d": volume_ratio,
        "prior_test_pump_pct": prior_test_pump_pct,
        "price_1d_pct": price_1d_pct,
        "price_7d_pct": price_7d_pct,
        "range_14d_pct": range_14d_pct,
        "reasons": reasons[:5],
    }


def scan_structure_universe(
    candidates: list[dict[str, Any]],
    fetch_klines: Callable[..., list[list[Any]]],
    cache: dict[str, dict[str, Any]],
    *,
    cache_seconds: int = 21600,
    workers: int = 8,
    lookback_days: int = 180,
    min_candles: int = 20,
    max_refresh: int = 60,
    priority_symbols: set[str] | None = None,
) -> list[dict[str, Any]]:
    now = time.time()
    pending: dict[Any, tuple[str, dict[str, Any]]] = {}
    results: dict[str, dict[str, Any]] = {}
    priority_symbols = {str(symbol).upper() for symbol in (priority_symbols or set())}

    refresh_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        symbol = str(candidate["row"].symbol).upper()
        cached = cache.get(symbol)
        if (
            cached
            and (cached.get("screen") or {}).get("model_version") == STRUCTURE_MODEL_VERSION
            and now - float(cached.get("cached_at", 0.0)) < cache_seconds
        ):
            cached_screen = dict(cached["screen"])
            results[symbol] = cached_screen
            if cached_screen.get("migrated_from_previous_model"):
                refresh_candidates.append(candidate)
        else:
            refresh_candidates.append(candidate)
    def refresh_priority(candidate: dict[str, Any]) -> tuple[Any, ...]:
        symbol = str(candidate["row"].symbol).upper()
        cached = cache.get(symbol) or {}
        screen = cached.get("screen") or {}
        missing = not screen or int(screen.get("data_points") or 0) <= 0
        ambiguous_high_score = bool(
            screen.get("migrated_from_previous_model")
            and not screen.get("eligible")
            and float(screen.get("score") or 0) >= 55
        )
        return (
            symbol in priority_symbols,
            missing,
            ambiguous_high_score,
            float(screen.get("score") or 0),
            -float(cached.get("cached_at") or 0),
            symbol,
        )

    refresh_candidates.sort(key=refresh_priority, reverse=True)
    refresh_symbols = {
        str(candidate["row"].symbol).upper()
        for candidate in refresh_candidates[: max(1, max_refresh)]
    }

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 16))) as executor:
        for candidate in candidates:
            row = candidate["row"]
            symbol = str(row.symbol).upper()
            if symbol in results and symbol not in refresh_symbols:
                continue
            if symbol not in refresh_symbols:
                results[symbol] = {
                    "symbol": symbol,
                    "model_version": STRUCTURE_MODEL_VERSION,
                    "score": 0,
                    "eligible": False,
                    "state_hint": "待掃描",
                    "reject_reason": "等待分批建立日線結構快取",
                    "data_points": 0,
                    "pending_scan": True,
                }
                continue
            future = executor.submit(fetch_klines, symbol, interval="1d", limit=lookback_days)
            pending[future] = (symbol, candidate)

        for future in as_completed(pending):
            symbol, _ = pending[future]
            try:
                screen = screen_structure_klines(symbol, future.result(), min_candles=min_candles)
            except Exception as exc:
                fallback = results.get(symbol)
                if fallback:
                    screen = dict(fallback)
                    screen["refresh_error"] = str(exc)
                else:
                    screen = {
                        "symbol": symbol,
                        "model_version": STRUCTURE_MODEL_VERSION,
                        "score": 0,
                        "eligible": False,
                        "state_hint": "資料不足",
                        "reject_reason": f"日線讀取失敗：{exc}",
                        "data_points": 0,
                        "pending_scan": True,
                    }
            if not screen.get("pending_scan"):
                cache[symbol] = {"cached_at": now, "screen": dict(screen)}
            results[symbol] = screen

    enriched: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        symbol = str(item["row"].symbol).upper()
        screen = results.get(symbol) or {
            "symbol": symbol,
            "model_version": STRUCTURE_MODEL_VERSION,
            "score": 0,
            "eligible": False,
            "state_hint": "資料不足",
            "reject_reason": "沒有結構資料",
        }
        momentum = live_momentum_score(item["row"], item.get("metrics") or {})
        item["structure_screen"] = screen
        item["live_momentum_score"] = momentum
        effective_structure = float(screen.get("score") or 0)
        if not screen.get("eligible"):
            effective_structure = min(effective_structure, 40.0)
        item["prefilter_score"] = round(effective_structure * 0.85 + momentum * 0.15, 2)
        enriched.append(item)

    enriched.sort(
        key=lambda item: (
            item["prefilter_score"],
            item["structure_screen"].get("score") or 0,
            item.get("live_momentum_score") or 0,
            getattr(item["row"], "oi_value_usd", None) or 0,
        ),
        reverse=True,
    )
    return enriched


def assess_liquidity(
    *,
    row: Any,
    wgl: dict[str, Any] | None,
    book: Any | None,
    reference_notional_usd: float = 5_000.0,
    min_quote_volume_24h_usd: float = 5_000_000.0,
    min_quote_volume_1h_usd: float = 100_000.0,
    min_depth_multiple: float = 1.5,
    max_spread_pct: float = 0.20,
    max_slippage_pct: float = 0.30,
) -> dict[str, Any]:
    wgl = wgl or {}
    reference_notional = max(100.0, float(reference_notional_usd))
    quote_volume_24h = _number(getattr(row, "quote_volume_24h_usd", None))
    quote_volume_1h = _number(wgl.get("quote_volume_1h_usd"))
    spread = _number(getattr(book, "latest_spread_pct", None)) if book else None
    if spread is None and book:
        spread = _number(getattr(book, "spread_pct", None))
    bid_depth = _number(getattr(book, "latest_bid_depth_05pct", None)) if book else None
    ask_depth = _number(getattr(book, "latest_ask_depth_05pct", None)) if book else None
    buy_slippage = _number(getattr(book, "buy_slippage_pct", None)) if book else None
    sell_slippage = _number(getattr(book, "sell_slippage_pct", None)) if book else None

    failures: list[str] = []
    missing: list[str] = []
    score = 0.0

    if quote_volume_24h is None:
        missing.append("24H成交額")
    else:
        if quote_volume_24h < min_quote_volume_24h_usd:
            failures.append("24H成交額低於門檻")
        if quote_volume_24h >= min_quote_volume_24h_usd * 4:
            score += 30
        elif quote_volume_24h >= min_quote_volume_24h_usd * 2:
            score += 25
        elif quote_volume_24h >= min_quote_volume_24h_usd:
            score += 20
        elif quote_volume_24h >= min_quote_volume_24h_usd * 0.5:
            score += 8

    if quote_volume_1h is not None:
        if quote_volume_1h < min_quote_volume_1h_usd:
            failures.append("最近1H成交額低於門檻")
        score += 15 if quote_volume_1h >= min_quote_volume_1h_usd * 5 else 10 if quote_volume_1h >= min_quote_volume_1h_usd else 0

    if spread is None:
        missing.append("spread")
    else:
        if spread > max_spread_pct:
            failures.append("買賣價差過寬")
        score += 15 if spread <= max_spread_pct / 4 else 12 if spread <= max_spread_pct / 2 else 8 if spread <= max_spread_pct else 0

    min_side_depth = None
    if bid_depth is None or ask_depth is None:
        missing.append("0.5%雙邊深度")
    else:
        min_side_depth = min(bid_depth, ask_depth)
        depth_multiple = min_side_depth / reference_notional
        if depth_multiple < min_depth_multiple:
            failures.append("0.5%近價深度不足")
        score += 20 if depth_multiple >= 5 else 16 if depth_multiple >= 3 else 12 if depth_multiple >= 1.5 else 8 if depth_multiple >= min_depth_multiple else 0

    worst_slippage = None
    if buy_slippage is None or sell_slippage is None:
        missing.append("雙向滑價")
    else:
        worst_slippage = max(buy_slippage, sell_slippage)
        if worst_slippage > max_slippage_pct:
            failures.append("固定倉位預估滑價過高")
        score += 20 if worst_slippage <= max_slippage_pct / 3 else 16 if worst_slippage <= max_slippage_pct / 2 else 10 if worst_slippage <= max_slippage_pct else 0

    blocked = bool(failures)
    ready = not blocked and not missing
    status = "不足" if blocked else "合格" if ready else "待資料"
    return {
        "liquidity_status": status,
        "liquidity_score": _clamp(score),
        "liquidity_ready": ready,
        "liquidity_blocked": blocked,
        "liquidity_reasons": failures,
        "liquidity_missing": missing,
        "liquidity_reference_notional_usd": reference_notional,
        "quote_volume_24h_usd": quote_volume_24h,
        "quote_volume_1h_usd": quote_volume_1h,
        "liquidity_spread_pct": spread,
        "liquidity_bid_depth_05pct": bid_depth,
        "liquidity_ask_depth_05pct": ask_depth,
        "liquidity_min_side_depth_usd": min_side_depth,
        "liquidity_buy_slippage_pct": buy_slippage,
        "liquidity_sell_slippage_pct": sell_slippage,
        "liquidity_worst_slippage_pct": worst_slippage,
    }


def component_scores(
    *,
    row: Any,
    metrics: dict[str, Any],
    structure: dict[str, Any] | None,
    wgl: dict[str, Any] | None,
    book: Any | None,
    bottom: dict[str, Any] | None,
    launch: dict[str, Any] | None,
    onchain: Any | None,
    orderbook_min_snapshots: int,
    liquidity_reference_notional_usd: float = 5_000.0,
    liquidity_min_quote_volume_24h_usd: float = 5_000_000.0,
    liquidity_min_quote_volume_1h_usd: float = 100_000.0,
    liquidity_min_depth_multiple: float = 1.5,
    liquidity_max_spread_pct: float = 0.20,
    liquidity_max_slippage_pct: float = 0.30,
) -> dict[str, Any]:
    structure = structure or {}
    wgl = wgl or {}
    structure_score = float(structure.get("score") or 0)
    if structure and not structure.get("eligible"):
        structure_score = min(structure_score, 40.0)
    if bottom:
        structure_score = max(structure_score, min(100.0, float(bottom.get("score") or 0)))
    if launch:
        structure_score = max(structure_score, min(100.0, float(launch.get("score") or 0) * 0.85))

    funding = _number(getattr(row, "funding_rate_pct", None))
    oi_1h = _number(wgl.get("oi_1h_pct"))
    if oi_1h is None:
        oi_1h = _number(metrics.get("contracts_1h_pct"))
    oi_6h = _number(wgl.get("oi_6h_pct"))
    oi_24h = _number(wgl.get("oi_24h_pct"))
    price_1h = _number(wgl.get("price_1h_pct"))
    if price_1h is None:
        price_1h = _number(metrics.get("price_1h_pct"))
    price_6h = _number(wgl.get("price_6h_pct"))
    volume_ratio = _number(wgl.get("volume_ratio"))
    spot_taker_imbalance = _number(metrics.get("spot_taker_imbalance"))
    basis_pct = _number(metrics.get("basis_pct"))
    short_squeeze = bool(
        funding is not None
        and funding <= -0.10
        and price_1h is not None
        and price_1h > 0
        and oi_1h is not None
        and oi_1h > 0
    )
    liquidity = assess_liquidity(
        row=row,
        wgl=wgl,
        book=book,
        reference_notional_usd=liquidity_reference_notional_usd,
        min_quote_volume_24h_usd=liquidity_min_quote_volume_24h_usd,
        min_quote_volume_1h_usd=liquidity_min_quote_volume_1h_usd,
        min_depth_multiple=liquidity_min_depth_multiple,
        max_spread_pct=liquidity_max_spread_pct,
        max_slippage_pct=liquidity_max_slippage_pct,
    )

    capital = 0.0
    if funding is not None:
        if abs(funding) <= 0.04:
            capital += 20
        elif abs(funding) <= 0.08:
            capital += 10
        if funding < 0:
            capital += 8
        if short_squeeze:
            capital += 10
    if oi_1h is not None:
        capital += 28 if oi_1h >= 8 else 20 if oi_1h >= 3 else 10 if oi_1h > 0 else 0
    if oi_6h is not None:
        capital += 22 if oi_6h >= 8 else 14 if oi_6h >= 3 else 6 if oi_6h > 0 else 0
    elif oi_24h is not None and oi_24h > 0:
        capital += 10
    if spot_taker_imbalance is not None:
        capital += 18 if spot_taker_imbalance >= 0.15 else 10 if spot_taker_imbalance > 0 else 0
    if basis_pct is not None and -0.10 <= basis_pct <= 0.05:
        capital += 5

    book_ready = bool(book and int(getattr(book, "snapshot_count", 0)) >= orderbook_min_snapshots)
    if book_ready:
        capital += min(25.0, max(0.0, float(getattr(book, "score", 0))) * 0.25)

    onchain_verified = bool(onchain and getattr(onchain, "identity_verified", False))
    if onchain_verified:
        capital += max(-15.0, min(20.0, float(getattr(onchain, "score", 0)) * 4.0))
    capital_score = _clamp(capital)

    trigger = 0.0
    if price_1h is not None and oi_1h is not None and price_1h > 0 and oi_1h > 0:
        trigger += 25
        if price_1h >= 2 and oi_1h >= 2:
            trigger += 15
    if price_6h is not None and oi_6h is not None and price_6h > 0 and oi_6h > 0:
        trigger += 20
    if volume_ratio is not None and volume_ratio >= 1.5:
        trigger += 15
    if wgl.get("strong_pullback"):
        trigger += 30
    if launch:
        trigger += 20
    if spot_taker_imbalance is not None and spot_taker_imbalance > 0.10:
        trigger += 10
    if short_squeeze:
        trigger += 15
    trigger_score = _clamp(trigger)

    quality = 0.0
    quality += 30 if structure.get("data_points", 0) >= 60 else 20 if structure.get("data_points", 0) >= 20 else 0
    if structure.get("migrated_from_previous_model"):
        quality -= 10
    quality += 10 if funding is not None else 0
    quality += 15 if oi_1h is not None else 0
    quality += 15 if oi_6h is not None or oi_24h is not None else 0
    quality += 15 if book_ready else 0
    quality += 15 if onchain_verified else 0
    quality += 10 if spot_taker_imbalance is not None else 0
    quality_score = _clamp(quality)

    risks = [str(value) for value in wgl.get("risks") or []]
    risk_text = " ".join(risks)
    risk = 0.0
    if funding is not None and funding >= 0.08:
        risk += 30
    if funding is not None and funding >= 0.12:
        risk += 20
    if funding is not None and funding <= -0.12 and not short_squeeze:
        risk += 15
    if any(word in risk_text for word in ("派發", "OI 1H轉負", "疑似空方")):
        risk += 40
    if "偏伸" in risk_text:
        risk += 20
    if book_ready and (
        getattr(book, "verdict", "") == "偏弱/派發"
        or (_number(getattr(book, "avg_imbalance_50", None)) or 0) <= -0.25
    ):
        risk += 35
    if price_1h is not None and oi_1h is not None and price_1h < -1.5 and oi_1h > 3:
        risk += 30
    if spot_taker_imbalance is not None and spot_taker_imbalance <= -0.15 and (oi_1h or 0) > 0:
        risk += 25
    if basis_pct is not None and basis_pct >= 0.30:
        risk += 20
    if liquidity["liquidity_blocked"]:
        risk += 55
    risk_score = _clamp(risk)

    overall = _clamp(
        structure_score * 0.45
        + capital_score * 0.25
        + trigger_score * 0.20
        + quality_score * 0.10
        - risk_score * 0.35
    )

    wgl_stage = str(wgl.get("stage") or "")
    wgl_action = str(wgl.get("action") or "")
    if risk_score >= 45 or wgl_action == "不要進":
        state = "失效/派發"
    elif (
        bool(wgl.get("strong_pullback"))
        and structure_score >= 45
        and trigger_score >= 45
        and quality_score >= 50
    ):
        state = "回踩進場"
    elif (
        (launch or short_squeeze or "起漲" in wgl_stage or "軋空" in wgl_stage or wgl_action == "再確認偏強")
        and trigger_score >= 40
        and structure_score >= 40
    ):
        state = "點火確認"
    elif structure_score >= 65 and capital_score >= 30:
        state = "資金預備"
    elif structure_score >= 55:
        state = "底部觀察"
    else:
        state = "結構未成熟"

    return {
        "structure_score": _clamp(structure_score),
        "capital_score": capital_score,
        "trigger_score": trigger_score,
        "quality_score": quality_score,
        "risk_score": risk_score,
        "overall_score": overall,
        "signal_state": state,
        "state_priority": STATE_PRIORITY[state],
        "onchain_verified": onchain_verified,
        "book_ready": book_ready,
        "spot_taker_imbalance": spot_taker_imbalance,
        "basis_pct": basis_pct,
        "short_squeeze": short_squeeze,
        **liquidity,
    }
