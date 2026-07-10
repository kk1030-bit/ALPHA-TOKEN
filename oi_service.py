from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BINANCE_BASE_URL = "https://fapi.binance.com"
CRYPTOBUBBLES_URL = "https://cryptobubbles.net/backend/data/bubbles1000.usd.json"
DEFAULT_TIMEOUT = 12
BINANCE_BACKOFF_UNTIL = 0.0


class ApiError(RuntimeError):
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


def _format_backoff_until(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds")


def _raise_if_binance_backoff() -> None:
    if time.time() < BINANCE_BACKOFF_UNTIL:
        raise ApiError(f"Binance rate limit cooldown until {_format_backoff_until(BINANCE_BACKOFF_UNTIL)}")


@dataclass
class OiAnalysis:
    symbol: str
    timestamp_utc: str
    price: float | None
    mark_price: float | None
    open_interest: float | None
    oi_value_usd: float | None
    funding_rate_pct: float | None
    price_change_1h_pct: float | None
    price_change_4h_pct: float | None
    price_change_24h_pct: float | None
    oi_change_1h_pct: float | None
    oi_change_4h_pct: float | None
    oi_change_24h_pct: float | None
    quote_volume_24h_usd: float | None
    score: int
    regime: str
    notes: list[str]


@dataclass
class OiSnapshot:
    rank: int
    symbol: str
    price: float | None
    mark_price: float | None
    open_interest: float | None
    oi_value_usd: float | None
    funding_rate_pct: float | None
    timestamp_utc: str
    market_rank: int | None = None
    marketcap_usd: float | None = None
    market_symbol: str | None = None
    source: str = "manual"
    provider_id: str | None = None


@dataclass
class WatchSymbol:
    symbol: str
    market_symbol: str
    market_rank: int | None
    marketcap_usd: float | None
    source: str
    provider_id: str | None = None


def normalize_symbol(text: str) -> str:
    symbol = text.strip().upper().replace("/", "").replace("-", "").replace("_", "")
    if not symbol:
        raise ValueError("empty symbol")
    if symbol.endswith("PERP"):
        symbol = symbol[:-4]
    if not symbol.endswith("USDT"):
        symbol = f"{symbol}USDT"
    return symbol


def _get_json(path: str, params: dict[str, Any] | None = None, *, timeout: int = DEFAULT_TIMEOUT) -> Any:
    global BINANCE_BACKOFF_UNTIL
    _raise_if_binance_backoff()
    query = urllib.parse.urlencode(params or {})
    url = f"{BINANCE_BASE_URL}{path}"
    if query:
        url = f"{url}?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "oi-telegram-bot/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code in {418, 429}:
            until = _extract_ban_until(body)
            if until is None:
                until = time.time() + (600 if exc.code == 418 else 90)
            BINANCE_BACKOFF_UNTIL = max(BINANCE_BACKOFF_UNTIL, until)
        raise ApiError(f"Binance HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Binance network error: {exc}") from exc


def _get_url_json(url: str, *, timeout: int = DEFAULT_TIMEOUT) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "oi-telegram-bot/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Network error: {exc}") from exc


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old * 100.0


def _nearest_before(rows: list[dict[str, Any]], target_ms: int) -> dict[str, Any] | None:
    best = None
    for row in rows:
        ts = int(row.get("timestamp", 0))
        if ts <= target_ms:
            best = row
        else:
            break
    return best


def _latest_value(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return _to_float(rows[-1].get(key))


def get_current_oi(symbol: str) -> dict[str, Any]:
    return _get_json("/fapi/v1/openInterest", {"symbol": symbol})


def get_oi_history(symbol: str, *, period: str = "5m", limit: int = 300) -> list[dict[str, Any]]:
    data = _get_json(
        "/futures/data/openInterestHist",
        {"symbol": symbol, "period": period, "limit": min(max(limit, 2), 500)},
    )
    if not isinstance(data, list):
        raise ApiError(f"Unexpected OI history response for {symbol}: {data}")
    return sorted(data, key=lambda row: int(row.get("timestamp", 0)))


def get_klines(symbol: str, *, interval: str = "5m", limit: int = 300) -> list[list[Any]]:
    data = _get_json(
        "/fapi/v1/klines",
        {"symbol": symbol, "interval": interval, "limit": min(max(limit, 2), 500)},
    )
    if not isinstance(data, list):
        raise ApiError(f"Unexpected kline response for {symbol}: {data}")
    return data


def get_24h_ticker(symbol: str | None = None) -> Any:
    params = {"symbol": symbol} if symbol else None
    return _get_json("/fapi/v1/ticker/24hr", params)


def get_mark_price(symbol: str) -> dict[str, Any]:
    data = _get_json("/fapi/v1/premiumIndex", {"symbol": symbol})
    if not isinstance(data, dict):
        raise ApiError(f"Unexpected mark price response for {symbol}: {data}")
    return data


def get_all_mark_prices() -> dict[str, dict[str, Any]]:
    data = _get_json("/fapi/v1/premiumIndex")
    if not isinstance(data, list):
        raise ApiError(f"Unexpected mark price list response: {data}")
    return {str(row.get("symbol", "")).upper(): row for row in data if row.get("symbol")}


def get_crypto_bubbles_tokens(*, min_rank: int = 101) -> list[dict[str, Any]]:
    data = _get_url_json(CRYPTOBUBBLES_URL, timeout=20)
    if not isinstance(data, list):
        raise ApiError(f"Unexpected CryptoBubbles response: {data}")

    out: list[dict[str, Any]] = []
    for row in data:
        rank = row.get("rank")
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol or rank is None:
            continue
        try:
            rank_int = int(rank)
        except (TypeError, ValueError):
            continue
        if rank_int < min_rank:
            continue
        out.append(
            {
                "symbol": symbol,
                "rank": rank_int,
                "marketcap": _to_float(row.get("marketcap")),
                "name": row.get("name"),
                "slug": row.get("slug"),
            }
        )
    return sorted(out, key=lambda item: int(item["rank"]))


def _symbol_candidates(base_symbol: str) -> list[str]:
    base = base_symbol.strip().upper().replace("/", "").replace("-", "").replace("_", "")
    if not base:
        return []
    if base.endswith("USDT"):
        return [base]
    return [
        f"{base}USDT",
        f"1000{base}USDT",
        f"10000{base}USDT",
        f"1000000{base}USDT",
    ]


def _futures_base_symbol(symbol: str) -> str:
    base = symbol.strip().upper()
    if base.endswith("USDT"):
        base = base[:-4]
    for prefix in ("1000000", "10000", "1000"):
        if base.startswith(prefix) and len(base) > len(prefix) + 1:
            return base[len(prefix) :]
    return base


def get_dynamic_watch_symbols() -> list[WatchSymbol]:
    """Return every active Binance USD-M USDT market.

    CryptoBubbles is enrichment only. Rank and market cap never decide whether a
    symbol enters the universe, so newly listed or unmapped contracts are kept.
    """
    mark_symbols = sorted(symbol for symbol in get_all_mark_prices() if symbol.endswith("USDT"))
    token_by_symbol: dict[str, dict[str, Any]] = {}
    try:
        for token in get_crypto_bubbles_tokens(min_rank=1):
            token_by_symbol.setdefault(str(token["symbol"]).upper(), token)
    except Exception:
        # Binance remains the source of truth for the tradable universe.
        token_by_symbol = {}

    out: list[WatchSymbol] = []
    for symbol in mark_symbols:
        market_symbol = _futures_base_symbol(symbol)
        token = token_by_symbol.get(market_symbol)
        out.append(
            WatchSymbol(
                symbol=symbol,
                market_symbol=market_symbol,
                market_rank=int(token["rank"]) if token and token.get("rank") is not None else None,
                marketcap_usd=_to_float(token.get("marketcap")) if token else None,
                source="dynamic_all_binance_usdt",
                provider_id=str(token.get("cg_id") or "") or None if token else None,
            )
        )
    return out


def get_oi_snapshots(
    raw_symbols: list[str | WatchSymbol],
    *,
    max_workers: int = 16,
) -> list[OiSnapshot]:
    watch_symbols: list[WatchSymbol] = []
    seen = set()
    for item in raw_symbols:
        if isinstance(item, WatchSymbol):
            watch = item
        else:
            symbol = normalize_symbol(str(item))
            watch = WatchSymbol(
                symbol=symbol,
                market_symbol=symbol.removesuffix("USDT"),
                market_rank=None,
                marketcap_usd=None,
                source="manual",
            )
        if watch.symbol not in seen:
            watch_symbols.append(watch)
            seen.add(watch.symbol)

    mark_prices = get_all_mark_prices()
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows: list[OiSnapshot] = []

    for watch in watch_symbols:
        symbol = watch.symbol
        if symbol not in mark_prices:
            rows.append(
                OiSnapshot(
                    rank=0,
                    symbol=symbol,
                    price=None,
                    mark_price=None,
                    open_interest=None,
                    oi_value_usd=None,
                    funding_rate_pct=None,
                    timestamp_utc=timestamp,
                    market_rank=watch.market_rank,
                    marketcap_usd=watch.marketcap_usd,
                    market_symbol=watch.market_symbol,
                    source=watch.source,
                    provider_id=watch.provider_id,
                )
            )

    def fetch_watch(watch: WatchSymbol) -> OiSnapshot:
        symbol = watch.symbol
        if symbol not in mark_prices:
            raise ApiError(f"{symbol} is not in Binance mark prices")
        current_oi = get_current_oi(symbol)
        mark = mark_prices[symbol]
        mark_price = _to_float(mark.get("markPrice"))
        index_price = _to_float(mark.get("indexPrice"))
        open_interest = _to_float(current_oi.get("openInterest")) if isinstance(current_oi, dict) else None
        funding_rate = _to_float(mark.get("lastFundingRate"))
        oi_value = open_interest * mark_price if open_interest is not None and mark_price is not None else None
        return OiSnapshot(
            rank=0,
            symbol=symbol,
            price=index_price,
            mark_price=mark_price,
            open_interest=open_interest,
            oi_value_usd=oi_value,
            funding_rate_pct=funding_rate * 100.0 if funding_rate is not None else None,
            timestamp_utc=timestamp,
            market_rank=watch.market_rank,
            marketcap_usd=watch.marketcap_usd,
            market_symbol=watch.market_symbol,
            source=watch.source,
            provider_id=watch.provider_id,
        )

    futures = {}
    workers = max(1, min(max_workers, 32, len(watch_symbols) or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for watch in watch_symbols:
            if watch.symbol in mark_prices:
                futures[executor.submit(fetch_watch, watch)] = watch
        for future in as_completed(futures):
            watch = futures[future]
            try:
                rows.append(future.result())
            except Exception:
                rows.append(
                    OiSnapshot(
                        rank=0,
                        symbol=watch.symbol,
                        price=None,
                        mark_price=None,
                        open_interest=None,
                        oi_value_usd=None,
                        funding_rate_pct=None,
                        timestamp_utc=timestamp,
                        market_rank=watch.market_rank,
                        marketcap_usd=watch.marketcap_usd,
                        market_symbol=watch.market_symbol,
                        source=watch.source,
                        provider_id=watch.provider_id,
                    )
                )

    rows.sort(key=lambda item: item.oi_value_usd if item.oi_value_usd is not None else -1, reverse=True)
    for idx, row in enumerate(rows, 1):
        row.rank = idx
    return rows


def _price_changes_from_klines(rows: list[list[Any]]) -> tuple[float | None, float | None]:
    if not rows:
        return None, None
    latest_close = _to_float(rows[-1][4])
    latest_open_time = int(rows[-1][0])
    lookup = [{"timestamp": int(row[0]), "close": row[4]} for row in rows]
    one_h = _nearest_before(lookup, latest_open_time - 60 * 60 * 1000)
    four_h = _nearest_before(lookup, latest_open_time - 4 * 60 * 60 * 1000)
    one_h_close = _to_float(one_h["close"]) if one_h else None
    four_h_close = _to_float(four_h["close"]) if four_h else None
    return _pct_change(latest_close, one_h_close), _pct_change(latest_close, four_h_close)


def _oi_changes_from_history(rows: list[dict[str, Any]]) -> tuple[float | None, float | None, float | None]:
    if not rows:
        return None, None, None
    latest_ts = int(rows[-1]["timestamp"])
    latest_oi_value = _latest_value(rows, "sumOpenInterestValue")

    one_h = _nearest_before(rows, latest_ts - 60 * 60 * 1000)
    four_h = _nearest_before(rows, latest_ts - 4 * 60 * 60 * 1000)
    day = _nearest_before(rows, latest_ts - 24 * 60 * 60 * 1000)

    return (
        _pct_change(latest_oi_value, _to_float(one_h.get("sumOpenInterestValue")) if one_h else None),
        _pct_change(latest_oi_value, _to_float(four_h.get("sumOpenInterestValue")) if four_h else None),
        _pct_change(latest_oi_value, _to_float(day.get("sumOpenInterestValue")) if day else None),
    )


def _score_and_regime(
    *,
    price_1h: float | None,
    price_4h: float | None,
    price_24h: float | None,
    oi_1h: float | None,
    oi_4h: float | None,
    oi_24h: float | None,
    funding_pct: float | None,
) -> tuple[int, str, list[str]]:
    score = 0
    notes: list[str] = []

    if price_1h is not None:
        if price_1h > 0:
            score += 8
        if price_1h > 1:
            score += 8
    if price_4h is not None:
        if price_4h > 0:
            score += 10
        if price_4h > 2:
            score += 10
    if price_24h is not None:
        if price_24h > 0:
            score += 6
        if price_24h > 5:
            score += 6

    if oi_1h is not None:
        if oi_1h > 0.5:
            score += 10
        if oi_1h > 2:
            score += 8
    if oi_4h is not None:
        if oi_4h > 1:
            score += 12
        if oi_4h > 5:
            score += 8
    if oi_24h is not None:
        if oi_24h > 3:
            score += 8
        if oi_24h > 10:
            score += 6

    if funding_pct is not None:
        if -0.03 <= funding_pct <= 0.06:
            score += 6
        elif funding_pct > 0.12:
            score -= 12
            notes.append("funding is crowded; long-side risk is higher")

    price_up = (price_1h or 0) > 0 and (price_4h or 0) > 0
    oi_up = (oi_1h or 0) > 0 and (oi_4h or 0) > 0
    price_down = (price_1h or 0) < 0 and (price_4h or 0) < 0

    if price_up and oi_up:
        regime = "bullish build-up"
        notes.append("price and OI are rising together; trend participation is increasing")
    elif price_up and not oi_up:
        regime = "short squeeze / position closing"
        notes.append("price is rising but OI is not; move may be driven by closing positions")
        score -= 8
    elif price_down and oi_up:
        regime = "bearish build-up"
        notes.append("price is falling while OI rises; shorts may be building")
        score -= 18
    elif oi_up:
        regime = "compression / direction unclear"
        notes.append("OI is building but price has not confirmed direction")
        score -= 4
    else:
        regime = "low confirmation"
        notes.append("OI does not confirm a one-sided bullish move yet")
        score -= 8

    return max(0, min(100, int(round(score)))), regime, notes


def analyze_symbol(raw_symbol: str) -> OiAnalysis:
    symbol = normalize_symbol(raw_symbol)

    current_oi = get_current_oi(symbol)
    oi_history = get_oi_history(symbol)
    klines = get_klines(symbol)
    ticker = get_24h_ticker(symbol)
    mark = get_mark_price(symbol)

    oi_1h, oi_4h, oi_24h = _oi_changes_from_history(oi_history)
    price_1h, price_4h = _price_changes_from_klines(klines)
    price_24h = _to_float(ticker.get("priceChangePercent")) if isinstance(ticker, dict) else None

    mark_price = _to_float(mark.get("markPrice"))
    price = _to_float(ticker.get("lastPrice")) if isinstance(ticker, dict) else mark_price
    funding_rate = _to_float(mark.get("lastFundingRate"))
    funding_pct = funding_rate * 100.0 if funding_rate is not None else None
    score, regime, notes = _score_and_regime(
        price_1h=price_1h,
        price_4h=price_4h,
        price_24h=price_24h,
        oi_1h=oi_1h,
        oi_4h=oi_4h,
        oi_24h=oi_24h,
        funding_pct=funding_pct,
    )

    return OiAnalysis(
        symbol=symbol,
        timestamp_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        price=price,
        mark_price=mark_price,
        open_interest=_to_float(current_oi.get("openInterest")) if isinstance(current_oi, dict) else None,
        oi_value_usd=_latest_value(oi_history, "sumOpenInterestValue"),
        funding_rate_pct=funding_pct,
        price_change_1h_pct=price_1h,
        price_change_4h_pct=price_4h,
        price_change_24h_pct=price_24h,
        oi_change_1h_pct=oi_1h,
        oi_change_4h_pct=oi_4h,
        oi_change_24h_pct=oi_24h,
        quote_volume_24h_usd=_to_float(ticker.get("quoteVolume")) if isinstance(ticker, dict) else None,
        score=score,
        regime=regime,
        notes=notes,
    )


def scan_symbols(limit: int = 15) -> list[OiAnalysis]:
    tickers = get_24h_ticker()
    if not isinstance(tickers, list):
        raise ApiError("Unexpected ticker list response")

    candidates = []
    for row in tickers:
        symbol = str(row.get("symbol", ""))
        if not symbol.endswith("USDT"):
            continue
        quote_volume = _to_float(row.get("quoteVolume")) or 0.0
        candidates.append((quote_volume, symbol))

    results: list[OiAnalysis] = []
    for _, symbol in sorted(candidates, reverse=True)[: min(max(limit, 1), 30)]:
        try:
            results.append(analyze_symbol(symbol))
            time.sleep(0.15)
        except Exception:
            continue
    return sorted(results, key=lambda item: item.score, reverse=True)


def save_analysis(analysis: OiAnalysis, root: Path) -> None:
    data_dir = root / "data" / "queries"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(asdict(analysis), ensure_ascii=False) + "\n")


def fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.{digits}f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.{digits}f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.{digits}f}K"
    return f"{value:.{digits}f}"


def fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}%"


def zh_regime(text: str) -> str:
    mapping = {
        "bullish build-up": "多頭建倉",
        "short squeeze / position closing": "空方回補 / 倉位平倉",
        "bearish build-up": "空頭建倉",
        "compression / direction unclear": "倉位堆積，方向未確認",
        "low confirmation": "確認度低",
    }
    return mapping.get(text, text)


def zh_note(text: str) -> str:
    mapping = {
        "funding is crowded; long-side risk is higher": "funding 偏擁擠，多方風險較高",
        "price and OI are rising together; trend participation is increasing": "價格與 OI 同步上升，趨勢參與度增加",
        "price is rising but OI is not; move may be driven by closing positions": "價格上升但 OI 未跟上，可能是平倉或回補推動",
        "price is falling while OI rises; shorts may be building": "價格下跌且 OI 上升，可能是空方加倉",
        "OI is building but price has not confirmed direction": "OI 正在堆積，但價格方向還沒確認",
        "OI does not confirm a one-sided bullish move yet": "OI 尚未確認單邊多頭走勢",
    }
    return mapping.get(text, text)


def format_analysis(analysis: OiAnalysis) -> str:
    notes = "\n".join(f"- {zh_note(note)}" for note in analysis.notes)
    return (
        f"{analysis.symbol} OI 分析\n"
        f"分數：{analysis.score}/100 | 狀態：{zh_regime(analysis.regime)}\n"
        f"價格：{fmt_num(analysis.price, 5)} | 標記價格：{fmt_num(analysis.mark_price, 5)}\n"
        f"當前 OI：{fmt_num(analysis.open_interest)} 張合約\n"
        f"OI 價值：${fmt_num(analysis.oi_value_usd)}\n"
        f"OI 變化：1小時 {fmt_pct(analysis.oi_change_1h_pct)} | 4小時 {fmt_pct(analysis.oi_change_4h_pct)} | 24小時 {fmt_pct(analysis.oi_change_24h_pct)}\n"
        f"價格變化：1小時 {fmt_pct(analysis.price_change_1h_pct)} | 4小時 {fmt_pct(analysis.price_change_4h_pct)} | 24小時 {fmt_pct(analysis.price_change_24h_pct)}\n"
        f"Funding：{fmt_pct(analysis.funding_rate_pct, 4)} | 24小時成交額：${fmt_num(analysis.quote_volume_24h_usd)}\n"
        f"{notes}\n"
        "非投資建議。OI 是確認資料，不是單獨買賣訊號。"
    )


def format_scan(results: list[OiAnalysis]) -> str:
    if not results:
        return "沒有掃描結果。"
    if results[0].score >= 65:
        lines = ["OI 多頭建倉候選："]
    elif results[0].score >= 45:
        lines = ["中等 OI 堆積候選："]
    else:
        lines = ["這次掃描沒有強多頭 OI 確認。最高分如下："]
    for item in results[:10]:
        lines.append(
            f"{item.symbol}: {item.score}/100，{zh_regime(item.regime)}，"
            f"4小時 OI {fmt_pct(item.oi_change_4h_pct)}，4小時價格 {fmt_pct(item.price_change_4h_pct)}"
        )
    return "\n".join(lines)
