from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


BINANCE_BASE_URL = "https://fapi.binance.com"
DEFAULT_TIMEOUT = 10
BINANCE_BACKOFF_UNTIL = 0.0


class OrderbookError(RuntimeError):
    pass


def _extract_ban_until(body: str) -> float | None:
    marker = "banned until "
    if marker not in body:
        return None
    tail = body.split(marker, 1)[1]
    digits = []
    for char in tail:
        if char.isdigit():
            digits.append(char)
        else:
            break
    if not digits:
        return None
    try:
        return int("".join(digits)) / 1000.0
    except ValueError:
        return None


def _raise_if_binance_backoff() -> None:
    if time.time() < BINANCE_BACKOFF_UNTIL:
        until = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(BINANCE_BACKOFF_UNTIL))
        raise OrderbookError(f"Binance rate limit cooldown until {until}")


@dataclass
class DepthSnapshot:
    symbol: str
    ts: float
    mid_price: float | None
    spread_pct: float | None
    bid_notional_20: float | None
    ask_notional_20: float | None
    bid_notional_50: float | None
    ask_notional_50: float | None
    imbalance_20: float | None
    imbalance_50: float | None
    bid_ask_ratio_50: float | None
    taker_buy_notional: float | None
    taker_sell_notional: float | None
    trade_imbalance: float | None


@dataclass
class OrderbookSignal:
    symbol: str
    verdict: str
    score: int
    snapshot_count: int
    lookback_seconds: int
    latest_ts: float | None
    latest_imbalance_50: float | None = None
    avg_imbalance_50: float | None = None
    positive_imbalance_ratio: float | None = None
    latest_bid_ask_ratio: float | None = None
    bid_ask_ratio_change_pct: float | None = None
    ask_depth_change_pct: float | None = None
    bid_depth_change_pct: float | None = None
    depth_churn_pct: float | None = None
    spread_pct: float | None = None
    avg_trade_imbalance: float | None = None
    latest_trade_imbalance: float | None = None
    reasons: list[str] | None = None


def _get_json(path: str, params: dict[str, Any], *, timeout: int = DEFAULT_TIMEOUT) -> Any:
    global BINANCE_BACKOFF_UNTIL
    _raise_if_binance_backoff()
    query = urllib.parse.urlencode(params)
    url = f"{BINANCE_BASE_URL}{path}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "oi-phase-orderbook/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code in {418, 429}:
            until = _extract_ban_until(body)
            if until is None:
                until = time.time() + (600 if exc.code == 418 else 90)
            BINANCE_BACKOFF_UNTIL = max(BINANCE_BACKOFF_UNTIL, until)
        raise OrderbookError(f"Binance HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise OrderbookError(f"Binance network error: {exc}") from exc


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_notional(levels: list[list[str]], limit: int) -> float | None:
    total = 0.0
    used = 0
    for price_raw, qty_raw, *_ in levels[:limit]:
        price = _to_float(price_raw)
        qty = _to_float(qty_raw)
        if price is None or qty is None:
            continue
        total += price * qty
        used += 1
    return total if used else None


def _imbalance(bid_notional: float | None, ask_notional: float | None) -> float | None:
    if bid_notional is None or ask_notional is None:
        return None
    total = bid_notional + ask_notional
    if total <= 0:
        return None
    return (bid_notional - ask_notional) / total


def _ratio(bid_notional: float | None, ask_notional: float | None) -> float | None:
    if bid_notional is None or ask_notional is None or ask_notional <= 0:
        return None
    return bid_notional / ask_notional


def fetch_depth_snapshot(symbol: str, *, limit: int = 50) -> DepthSnapshot:
    symbol = symbol.strip().upper()
    payload = _get_json("/fapi/v1/depth", {"symbol": symbol, "limit": limit})
    bids = payload.get("bids") or []
    asks = payload.get("asks") or []
    if not bids or not asks:
        raise OrderbookError(f"{symbol} depth is empty")

    best_bid = _to_float(bids[0][0])
    best_ask = _to_float(asks[0][0])
    mid_price = (best_bid + best_ask) / 2.0 if best_bid is not None and best_ask is not None else None
    spread_pct = ((best_ask - best_bid) / mid_price * 100.0) if mid_price and best_bid and best_ask else None

    bid_20 = _sum_notional(bids, 20)
    ask_20 = _sum_notional(asks, 20)
    bid_50 = _sum_notional(bids, 50)
    ask_50 = _sum_notional(asks, 50)
    trades = _get_json("/fapi/v1/aggTrades", {"symbol": symbol, "limit": 500})
    taker_buy = 0.0
    taker_sell = 0.0
    for trade in trades if isinstance(trades, list) else []:
        price = _to_float(trade.get("p"))
        quantity = _to_float(trade.get("q"))
        if price is None or quantity is None:
            continue
        notional = price * quantity
        if bool(trade.get("m")):
            taker_sell += notional
        else:
            taker_buy += notional
    trade_total = taker_buy + taker_sell
    trade_imbalance = (taker_buy - taker_sell) / trade_total if trade_total > 0 else None
    return DepthSnapshot(
        symbol=symbol,
        ts=float(int(time.time())),
        mid_price=mid_price,
        spread_pct=spread_pct,
        bid_notional_20=bid_20,
        ask_notional_20=ask_20,
        bid_notional_50=bid_50,
        ask_notional_50=ask_50,
        imbalance_20=_imbalance(bid_20, ask_20),
        imbalance_50=_imbalance(bid_50, ask_50),
        bid_ask_ratio_50=_ratio(bid_50, ask_50),
        taker_buy_notional=taker_buy if trade_total > 0 else None,
        taker_sell_notional=taker_sell if trade_total > 0 else None,
        trade_imbalance=trade_imbalance,
    )


def init_orderbook_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orderbook_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                ts REAL NOT NULL,
                mid_price REAL,
                spread_pct REAL,
                bid_notional_20 REAL,
                ask_notional_20 REAL,
                bid_notional_50 REAL,
                ask_notional_50 REAL,
                imbalance_20 REAL,
                imbalance_50 REAL,
                bid_ask_ratio_50 REAL,
                taker_buy_notional REAL,
                taker_sell_notional REAL,
                trade_imbalance REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, ts)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orderbook_symbol_ts ON orderbook_snapshots(symbol, ts)"
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(orderbook_snapshots)")}
        for name in ("taker_buy_notional", "taker_sell_notional", "trade_imbalance"):
            if name not in columns:
                conn.execute(f"ALTER TABLE orderbook_snapshots ADD COLUMN {name} REAL")


def save_depth_snapshot(db_path: Path, snapshot: DepthSnapshot) -> None:
    init_orderbook_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO orderbook_snapshots (
                symbol, ts, mid_price, spread_pct,
                bid_notional_20, ask_notional_20,
                bid_notional_50, ask_notional_50,
                imbalance_20, imbalance_50, bid_ask_ratio_50,
                taker_buy_notional, taker_sell_notional, trade_imbalance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.symbol,
                snapshot.ts,
                snapshot.mid_price,
                snapshot.spread_pct,
                snapshot.bid_notional_20,
                snapshot.ask_notional_20,
                snapshot.bid_notional_50,
                snapshot.ask_notional_50,
                snapshot.imbalance_20,
                snapshot.imbalance_50,
                snapshot.bid_ask_ratio_50,
                snapshot.taker_buy_notional,
                snapshot.taker_sell_notional,
                snapshot.trade_imbalance,
            ),
        )


def prune_orderbook_db(db_path: Path, *, keep_days: int = 7) -> None:
    init_orderbook_db(db_path)
    cutoff = time.time() - keep_days * 86400
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM orderbook_snapshots WHERE ts < ?", (cutoff,))


def collect_orderbook_snapshots(
    symbols: list[str],
    db_path: Path,
    *,
    max_workers: int = 12,
    limit: int = 50,
) -> tuple[list[DepthSnapshot], dict[str, str]]:
    init_orderbook_db(db_path)
    clean_symbols = []
    seen = set()
    for raw in symbols:
        symbol = raw.strip().upper()
        if not symbol or symbol in seen:
            continue
        clean_symbols.append(symbol)
        seen.add(symbol)

    snapshots: list[DepthSnapshot] = []
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        future_map = {
            executor.submit(fetch_depth_snapshot, symbol, limit=limit): symbol
            for symbol in clean_symbols
        }
        for future in as_completed(future_map):
            symbol = future_map[future]
            try:
                snapshot = future.result()
            except Exception as exc:
                errors[symbol] = str(exc)
                continue
            save_depth_snapshot(db_path, snapshot)
            snapshots.append(snapshot)
    return snapshots, errors


def _rows_for_symbol(db_path: Path, symbol: str, lookback_seconds: int) -> list[dict[str, float | str | None]]:
    init_orderbook_db(db_path)
    cutoff = time.time() - lookback_seconds
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT symbol, ts, mid_price, spread_pct,
                   bid_notional_50, ask_notional_50,
                   imbalance_50, bid_ask_ratio_50,
                   taker_buy_notional, taker_sell_notional, trade_imbalance
            FROM orderbook_snapshots
            WHERE symbol = ? AND ts >= ?
            ORDER BY ts ASC
            """,
            (symbol.upper(), cutoff),
        ).fetchall()
    return [dict(row) for row in rows]


def _avg(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _median(values: list[float | None]) -> float | None:
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return None
    mid = len(clean) // 2
    if len(clean) % 2:
        return clean[mid]
    return (clean[mid - 1] + clean[mid]) / 2.0


def _pct_change(last: float | None, first: float | None) -> float | None:
    if last is None or first is None or first == 0:
        return None
    return (last - first) / abs(first) * 100.0


def _fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}%"


def _fmt_ratio(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def analyze_orderbook_accumulation(
    symbol: str,
    db_path: Path,
    *,
    lookback_seconds: int = 14400,
    min_snapshots: int = 12,
) -> OrderbookSignal:
    symbol = symbol.strip().upper()
    rows = _rows_for_symbol(db_path, symbol, lookback_seconds)
    if len(rows) < min_snapshots:
        return OrderbookSignal(
            symbol=symbol,
            verdict="資料不足",
            score=0,
            snapshot_count=len(rows),
            lookback_seconds=lookback_seconds,
            latest_ts=float(rows[-1]["ts"]) if rows else None,
            reasons=[f"order book 快照不足：{len(rows)}/{min_snapshots}"],
        )

    imbalances = [_to_float(row.get("imbalance_50")) for row in rows]
    ratios = [_to_float(row.get("bid_ask_ratio_50")) for row in rows]
    bid_depths = [_to_float(row.get("bid_notional_50")) for row in rows]
    ask_depths = [_to_float(row.get("ask_notional_50")) for row in rows]
    spreads = [_to_float(row.get("spread_pct")) for row in rows]
    trade_imbalances = [_to_float(row.get("trade_imbalance")) for row in rows]
    totals = [
        (bid or 0.0) + (ask or 0.0)
        for bid, ask in zip(bid_depths, ask_depths)
        if bid is not None and ask is not None
    ]

    window = max(3, len(rows) // 3)
    first_bid = _median(bid_depths[:window])
    last_bid = _median(bid_depths[-window:])
    first_ask = _median(ask_depths[:window])
    last_ask = _median(ask_depths[-window:])
    first_ratio = _median(ratios[:window])
    last_ratio = _median(ratios[-window:])
    latest = rows[-1]
    latest_imbalance = _to_float(latest.get("imbalance_50"))
    latest_trade_imbalance = _to_float(latest.get("trade_imbalance"))
    avg_imbalance = _avg(imbalances)
    avg_trade_imbalance = _avg(trade_imbalances[-window:])
    positive_ratio = (
        sum(1 for value in imbalances if value is not None and value >= 0.25)
        / max(1, len([value for value in imbalances if value is not None]))
    )
    ask_depth_change = _pct_change(last_ask, first_ask)
    bid_depth_change = _pct_change(last_bid, first_bid)
    ratio_change = _pct_change(last_ratio, first_ratio)

    churn_values: list[float] = []
    for prev, current in zip(totals, totals[1:]):
        change = _pct_change(current, prev)
        if change is not None:
            churn_values.append(abs(change))
    depth_churn = _avg(churn_values)
    spread = _avg(spreads[-window:])

    score = 0
    reasons: list[str] = []
    if avg_imbalance is not None and avg_imbalance >= 0.4 and positive_ratio >= 0.5:
        score += 26
        reasons.append(f"買賣盤長時間正失衡：均值 {_fmt_pct(avg_imbalance * 100)}，占比 {_fmt_pct(positive_ratio * 100)}")
    elif avg_imbalance is not None and avg_imbalance >= 0.15:
        score += 14
        reasons.append(f"買盤偏厚：平均 imbalance {_fmt_pct(avg_imbalance * 100)}")
    if latest_imbalance is not None and latest_imbalance >= 0.4:
        score += 12
        reasons.append(f"最新 imbalance 偏強：{_fmt_pct(latest_imbalance * 100)}")

    if ask_depth_change is not None and ask_depth_change <= -20:
        score += 22
        reasons.append(f"賣盤深度持續變薄：{_fmt_pct(ask_depth_change)}")
    elif ask_depth_change is not None and ask_depth_change <= -10:
        score += 12
        reasons.append(f"賣盤深度下降：{_fmt_pct(ask_depth_change)}")
    elif ask_depth_change is not None and ask_depth_change >= 25:
        score -= 14
        reasons.append(f"賣盤牆增厚：{_fmt_pct(ask_depth_change)}")

    if bid_depth_change is not None and bid_depth_change >= 20:
        score += 14
        reasons.append(f"買盤深度堆疊：{_fmt_pct(bid_depth_change)}")
    elif bid_depth_change is not None and bid_depth_change >= 10:
        score += 8
        reasons.append(f"買盤逐步增加：{_fmt_pct(bid_depth_change)}")
    elif bid_depth_change is not None and bid_depth_change <= -25:
        score -= 10
        reasons.append(f"買盤撤退：{_fmt_pct(bid_depth_change)}")

    if ratio_change is not None and ratio_change >= 5:
        score += 12
        reasons.append(f"買賣比緩慢墊高：{_fmt_pct(ratio_change)}")
    elif ratio_change is not None and ratio_change >= 2 and last_ratio is not None and 0.90 <= last_ratio <= 1.15:
        score += 8
        reasons.append(f"買賣比隱性墊高：{_fmt_pct(ratio_change)}，最新 {_fmt_ratio(last_ratio)}")

    if avg_trade_imbalance is not None and avg_trade_imbalance >= 0.15:
        score += 18
        reasons.append(f"實際主動買盤占優：{_fmt_pct(avg_trade_imbalance * 100)}")
    elif avg_trade_imbalance is not None and avg_trade_imbalance <= -0.15:
        score -= 18
        reasons.append(f"實際主動賣盤占優：{_fmt_pct(avg_trade_imbalance * 100)}")
    if (
        avg_imbalance is not None
        and avg_imbalance >= 0.25
        and avg_trade_imbalance is not None
        and avg_trade_imbalance <= -0.10
    ):
        score -= 22
        reasons.append("掛單顯示買牆，但實際成交偏賣，疑似假牆")

    if depth_churn is not None and depth_churn >= 12:
        if avg_trade_imbalance is not None and avg_trade_imbalance > 0.05:
            score += 5
            reasons.append(f"深度變動且成交確認：平均 {_fmt_pct(depth_churn)} / 快照")
        else:
            score -= 8
            reasons.append(f"撤掛過快且成交未確認：平均 {_fmt_pct(depth_churn)} / 快照")
    elif depth_churn is not None and depth_churn >= 6:
        score += 5
        reasons.append(f"深度結構有活動：平均 {_fmt_pct(depth_churn)} / 快照")

    if spread is not None and spread > 1.0:
        score -= 10
        reasons.append(f"spread 過寬：{_fmt_pct(spread)}")
    if avg_imbalance is not None and avg_imbalance <= -0.25:
        score -= 16
        reasons.append(f"賣盤長期壓制：平均 imbalance {_fmt_pct(avg_imbalance * 100)}")

    score = max(0, min(100, int(round(score))))
    if score >= 70:
        verdict = "吸籌中"
    elif score >= 50:
        verdict = "吸籌觀察"
    elif score <= 20 and len(rows) >= min_snapshots:
        verdict = "偏弱/派發"
    else:
        verdict = "中性"
    if not reasons:
        reasons.append("訂單簿沒有明確吸籌指紋")

    return OrderbookSignal(
        symbol=symbol,
        verdict=verdict,
        score=score,
        snapshot_count=len(rows),
        lookback_seconds=lookback_seconds,
        latest_ts=float(latest["ts"]) if latest.get("ts") is not None else None,
        latest_imbalance_50=latest_imbalance,
        avg_imbalance_50=avg_imbalance,
        positive_imbalance_ratio=positive_ratio,
        latest_bid_ask_ratio=last_ratio,
        bid_ask_ratio_change_pct=ratio_change,
        ask_depth_change_pct=ask_depth_change,
        bid_depth_change_pct=bid_depth_change,
        depth_churn_pct=depth_churn,
        spread_pct=spread,
        avg_trade_imbalance=avg_trade_imbalance,
        latest_trade_imbalance=latest_trade_imbalance,
        reasons=reasons,
    )


def format_orderbook_signal(signal: OrderbookSignal) -> str:
    reasons = signal.reasons or []
    lines = [
        f"訂單簿吸籌｜{signal.symbol}",
        f"判定：{signal.verdict}｜分數 {signal.score}｜快照 {signal.snapshot_count}",
        (
            f"Imb 最新 {_fmt_pct((signal.latest_imbalance_50 or 0) * 100) if signal.latest_imbalance_50 is not None else 'n/a'}"
            f"｜均值 {_fmt_pct((signal.avg_imbalance_50 or 0) * 100) if signal.avg_imbalance_50 is not None else 'n/a'}"
            f"｜正失衡占比 {_fmt_pct((signal.positive_imbalance_ratio or 0) * 100) if signal.positive_imbalance_ratio is not None else 'n/a'}"
        ),
        (
            f"Ask變化 {_fmt_pct(signal.ask_depth_change_pct)}｜Bid變化 {_fmt_pct(signal.bid_depth_change_pct)}"
            f"｜買賣比變化 {_fmt_pct(signal.bid_ask_ratio_change_pct)}｜深度異動 {_fmt_pct(signal.depth_churn_pct)}"
        ),
        (
            f"實際成交方向：最新 {_fmt_pct((signal.latest_trade_imbalance or 0) * 100) if signal.latest_trade_imbalance is not None else 'n/a'}"
            f"｜均值 {_fmt_pct((signal.avg_trade_imbalance or 0) * 100) if signal.avg_trade_imbalance is not None else 'n/a'}"
        ),
    ]
    if reasons:
        lines.append("原因：")
        lines.extend(f"- {reason}" for reason in reasons[:5])
    return "\n".join(lines)
