from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any


DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
ARKHAM_BASE_URL = "https://api.arkm.com"
DEFAULT_TIMEOUT = 15


@dataclass
class OnchainSignal:
    symbol: str
    query_symbol: str
    verdict: str
    score: int
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    chain_id: str | None = None
    token_address: str | None = None
    token_name: str | None = None
    pair_url: str | None = None
    liquidity_usd: float | None = None
    volume_24h_usd: float | None = None
    volume_1h_usd: float | None = None
    price_change_24h_pct: float | None = None
    price_change_1h_pct: float | None = None
    buys_24h: int | None = None
    sells_24h: int | None = None
    buys_1h: int | None = None
    sells_1h: int | None = None
    marketcap_usd: float | None = None
    fdv_usd: float | None = None
    cex_inflow_24h_usd: float | None = None
    bitget_inflow_24h_usd: float | None = None
    cex_outflow_24h_usd: float | None = None


def _get_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    query = urllib.parse.urlencode(params or {}, doseq=True)
    full_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(
        full_url,
        headers={
            "User-Agent": "oi-phase-onchain/1.0",
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _nested(row: dict[str, Any], *keys: str) -> Any:
    current: Any = row
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _base_symbol(raw_symbol: str) -> str:
    symbol = raw_symbol.strip().upper().replace("/", "").replace("-", "").replace("_", "")
    if symbol.endswith("PERP"):
        symbol = symbol[:-4]
    if symbol.endswith("USDT"):
        symbol = symbol[:-4]
    for prefix in ("1000000", "10000", "1000"):
        if symbol.startswith(prefix) and len(symbol) > len(prefix) + 2:
            return symbol[len(prefix) :]
    return symbol


def _pair_token_for_symbol(pair: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
    quote = pair.get("quoteToken") if isinstance(pair.get("quoteToken"), dict) else {}
    if str(base.get("symbol", "")).upper() == symbol:
        return base
    if str(quote.get("symbol", "")).upper() == symbol:
        return quote
    return None


def _pair_activity(pair: dict[str, Any]) -> tuple[int, float, int, float]:
    liquidity = _to_float(_nested(pair, "liquidity", "usd")) or 0.0
    volume_24h = _to_float(_nested(pair, "volume", "h24")) or 0.0
    volume_1h = _to_float(_nested(pair, "volume", "h1")) or 0.0
    buys_24h = _to_int(_nested(pair, "txns", "h24", "buys")) or 0
    sells_24h = _to_int(_nested(pair, "txns", "h24", "sells")) or 0
    txns_24h = buys_24h + sells_24h
    score = 0
    if volume_24h >= 10_000:
        score += 3
    elif volume_24h >= 1_000:
        score += 1
    if volume_1h >= 1_000:
        score += 1
    if txns_24h >= 50:
        score += 2
    elif txns_24h >= 10:
        score += 1
    if liquidity >= 50_000:
        score += 1
    if liquidity >= 100_000 and volume_24h < 10_000:
        score -= 4
    if liquidity > 0 and volume_24h / liquidity < 0.001 and txns_24h < 10:
        score -= 3
    return score, volume_24h, txns_24h, liquidity


def _pair_score(pair: dict[str, Any], symbol: str) -> tuple[int, int, int, float, int, float]:
    base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
    quote = pair.get("quoteToken") if isinstance(pair.get("quoteToken"), dict) else {}
    base_exact = 1 if str(base.get("symbol", "")).upper() == symbol else 0
    quote_exact = 1 if str(quote.get("symbol", "")).upper() == symbol else 0
    activity, volume_24h, txns_24h, liquidity = _pair_activity(pair)
    return base_exact, quote_exact, activity, volume_24h, txns_24h, liquidity


def search_dex_pair(query_symbol: str) -> dict[str, Any] | None:
    if len(query_symbol.strip()) < 2:
        return None
    data = _get_json(DEXSCREENER_SEARCH_URL, {"q": query_symbol})
    pairs = data.get("pairs") if isinstance(data, dict) else None
    if not isinstance(pairs, list):
        return None
    exact_pairs = [pair for pair in pairs if isinstance(pair, dict) and _pair_token_for_symbol(pair, query_symbol)]
    active_exact_pairs = [pair for pair in exact_pairs if _pair_activity(pair)[0] > 0]
    candidates = active_exact_pairs or exact_pairs or [pair for pair in pairs if isinstance(pair, dict)]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: _pair_score(pair, query_symbol))


def _transfer_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("transfers", "items", "results", "data"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _transfer_usd(row: dict[str, Any]) -> float:
    candidates = [
        row.get("historicalUSD"),
        row.get("historicalUsd"),
        row.get("usd"),
        row.get("usdValue"),
        row.get("valueUsd"),
        _nested(row, "value", "usd"),
    ]
    for value in candidates:
        number = _to_float(value)
        if number is not None:
            return number
    return 0.0


def _arkham_token_id(chain_id: str | None, token_address: str | None) -> str | None:
    if not chain_id or not token_address:
        return None
    if not token_address.startswith("0x"):
        return None
    chain_map = {
        "ethereum": "ethereum",
        "eth": "ethereum",
        "bsc": "bsc",
        "binance-smart-chain": "bsc",
        "base": "base",
        "arbitrum": "arbitrum",
        "polygon": "polygon",
        "optimism": "optimism",
        "avalanche": "avalanche",
    }
    chain = chain_map.get(chain_id.lower())
    if not chain:
        return None
    return f"{chain}:{token_address}"


def _arkham_transfers(
    api_key: str,
    *,
    chain_id: str | None,
    token_address: str | None,
    to: list[str] | None = None,
    from_: list[str] | None = None,
    usd_gte: float = 50_000.0,
) -> tuple[float, list[dict[str, Any]], str | None]:
    token_id = _arkham_token_id(chain_id, token_address)
    if not token_id:
        return 0.0, [], "Arkham does not support this chain/token format yet."
    params: dict[str, Any] = {
        "chains": [chain_id],
        "tokens": [token_id],
        "timeLast": "24h",
        "usdGte": str(usd_gte),
        "sortKey": "usd",
        "sortDir": "desc",
        "limit": 10,
    }
    if to:
        params["to"] = to
    if from_:
        params["from"] = from_
    payload = _get_json(
        f"{ARKHAM_BASE_URL}/transfers",
        params,
        headers={"API-Key": api_key},
    )
    rows = _transfer_rows(payload)
    return sum(_transfer_usd(row) for row in rows), rows, None


def _format_usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"${value / 1_000:.2f}K"
    return f"${value:.2f}"


def _format_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def analyze_onchain(
    raw_symbol: str,
    *,
    market_symbol: str | None = None,
    arkham_api_key: str | None = None,
    arkham_min_usd: float | None = None,
) -> OnchainSignal:
    query_symbol = _base_symbol(market_symbol or raw_symbol)
    signal = OnchainSignal(symbol=raw_symbol.upper(), query_symbol=query_symbol, verdict="中性", score=0)

    pair = search_dex_pair(query_symbol)
    if pair is None:
        signal.verdict = "再確認"
        signal.warnings.append("DexScreener 找不到主要交易對，無法做鏈上/DEX 初篩。")
        return signal

    token = _pair_token_for_symbol(pair, query_symbol) or pair.get("baseToken") or {}
    signal.chain_id = str(pair.get("chainId") or "")
    signal.token_address = str(token.get("address") or "")
    signal.token_name = str(token.get("name") or "")
    signal.pair_url = str(pair.get("url") or "")
    signal.liquidity_usd = _to_float(_nested(pair, "liquidity", "usd"))
    signal.volume_24h_usd = _to_float(_nested(pair, "volume", "h24"))
    signal.volume_1h_usd = _to_float(_nested(pair, "volume", "h1"))
    signal.price_change_24h_pct = _to_float(_nested(pair, "priceChange", "h24"))
    signal.price_change_1h_pct = _to_float(_nested(pair, "priceChange", "h1"))
    signal.buys_24h = _to_int(_nested(pair, "txns", "h24", "buys"))
    signal.sells_24h = _to_int(_nested(pair, "txns", "h24", "sells"))
    signal.buys_1h = _to_int(_nested(pair, "txns", "h1", "buys"))
    signal.sells_1h = _to_int(_nested(pair, "txns", "h1", "sells"))
    signal.marketcap_usd = _to_float(pair.get("marketCap"))
    signal.fdv_usd = _to_float(pair.get("fdv"))

    liq = signal.liquidity_usd or 0.0
    vol24 = signal.volume_24h_usd or 0.0
    vol1 = signal.volume_1h_usd or 0.0
    buy24 = signal.buys_24h or 0
    sell24 = signal.sells_24h or 0
    buy1 = signal.buys_1h or 0
    sell1 = signal.sells_1h or 0
    price24 = signal.price_change_24h_pct
    dex_inactive = liq >= 100_000 and vol24 < 10_000
    dex_low_activity = liq >= 100_000 and liq > 0 and vol24 / liq < 0.02

    if liq < 50_000:
        signal.score -= 2
        signal.reasons.append(f"DEX 流動性太薄：{_format_usd(liq)}。")
    elif liq >= 300_000:
        signal.score += 1
        signal.reasons.append(f"DEX 流動性足夠：{_format_usd(liq)}。")

    if dex_inactive:
        signal.score -= 2
        signal.reasons.append(f"DEX 成交幾乎沒有：24h 量 {_format_usd(vol24)}，鏈上承接不足。")
    elif dex_low_activity:
        signal.score -= 1
        signal.reasons.append(f"24h 成交量/流動性只有 {vol24 / liq:.4f}x，活躍度偏低。")

    if signal.marketcap_usd and signal.marketcap_usd > 0 and liq > 0:
        liq_to_mcap = liq / signal.marketcap_usd * 100.0
        if liq_to_mcap >= 2:
            signal.score += 1
            signal.reasons.append(f"流動性/市值 {liq_to_mcap:.2f}%，承接較健康。")
        elif liq_to_mcap < 0.5:
            signal.score -= 1
            signal.reasons.append(f"流動性/市值只有 {liq_to_mcap:.2f}%，承接偏弱。")

    if liq > 0 and vol24 / liq >= 1.5 and (price24 is None or price24 > -10):
        signal.score += 1
        signal.reasons.append(f"24h 成交量/流動性 {vol24 / liq:.2f}x，市場有活動。")
    if vol24 >= 10_000 and vol1 >= 1_000 and vol1 / vol24 >= 0.08:
        signal.score += 1
        signal.reasons.append("近 1h 成交佔比偏高，短線資金正在動。")

    if buy1 >= max(3, sell1 * 1.15):
        signal.score += 1
        signal.reasons.append(f"1h DEX 買壓較強：買 {buy1} / 賣 {sell1}。")
    if buy24 >= max(10, sell24 * 1.15):
        signal.score += 1
        signal.reasons.append(f"24h DEX 買壓較強：買 {buy24} / 賣 {sell24}。")
    if sell1 >= max(3, buy1 * 1.4):
        signal.score -= 2
        signal.reasons.append(f"1h DEX 賣壓明顯：買 {buy1} / 賣 {sell1}。")
    if sell24 >= max(10, buy24 * 1.25):
        signal.score -= 2
        signal.reasons.append(f"24h DEX 賣壓明顯：買 {buy24} / 賣 {sell24}。")

    if price24 is not None:
        if 5 <= price24 <= 45 and buy24 >= sell24:
            signal.score += 1
            signal.reasons.append(f"24h 價格上升但未過熱：{_format_pct(price24)}。")
        elif price24 > 60:
            signal.score -= 3
            signal.reasons.append(f"24h 已大漲 {_format_pct(price24)}，不適合追高。")
        elif price24 < -15:
            signal.score -= 1
            signal.reasons.append(f"24h 跌幅偏大：{_format_pct(price24)}。")

    api_key = arkham_api_key or os.environ.get("ARKHAM_API_KEY", "")
    min_usd = arkham_min_usd or _to_float(os.environ.get("ONCHAIN_ARKHAM_MIN_USD")) or 50_000.0
    if api_key:
        try:
            bitget_usd, _, warn = _arkham_transfers(
                api_key,
                chain_id=signal.chain_id,
                token_address=signal.token_address,
                to=["bitget", "deposit:bitget"],
                usd_gte=min_usd,
            )
            time.sleep(1.05)
            cex_in_usd, _, warn2 = _arkham_transfers(
                api_key,
                chain_id=signal.chain_id,
                token_address=signal.token_address,
                to=["type:cex"],
                usd_gte=min_usd,
            )
            time.sleep(1.05)
            cex_out_usd, _, warn3 = _arkham_transfers(
                api_key,
                chain_id=signal.chain_id,
                token_address=signal.token_address,
                from_=["type:cex"],
                usd_gte=min_usd,
            )
            signal.bitget_inflow_24h_usd = bitget_usd
            signal.cex_inflow_24h_usd = cex_in_usd
            signal.cex_outflow_24h_usd = cex_out_usd
            for warn_item in (warn, warn2, warn3):
                if warn_item:
                    signal.warnings.append(warn_item)

            if bitget_usd >= min_usd:
                signal.score -= 4
                signal.reasons.append(f"24h 大額轉入 Bitget：{_format_usd(bitget_usd)}，強偏空。")
            if cex_in_usd >= min_usd:
                signal.score -= 3
                signal.reasons.append(f"24h 大額轉入 CEX：{_format_usd(cex_in_usd)}，偏空。")
            if cex_out_usd >= min_usd and cex_out_usd > cex_in_usd * 1.3:
                signal.score += 2
                signal.reasons.append(f"24h CEX 出金大於入金：{_format_usd(cex_out_usd)}，偏多。")
            elif cex_out_usd >= min_usd:
                signal.score += 1
                signal.reasons.append(f"24h 有 CEX 出金：{_format_usd(cex_out_usd)}。")
        except Exception as exc:
            signal.warnings.append(f"Arkham 查詢失敗：{exc}")
    else:
        signal.warnings.append("尚未設定 ARKHAM_API_KEY，所以未檢查 Bitget/CEX 入金。")

    if dex_inactive:
        signal.score = min(signal.score, -1)
    elif dex_low_activity:
        signal.score = min(signal.score, 0)
    if price24 is not None and price24 > 70:
        signal.score = min(signal.score, 0)

    if signal.score >= 4:
        signal.verdict = "偏多"
    elif signal.score >= 1:
        signal.verdict = "偏多觀察"
    elif signal.score <= -4:
        signal.verdict = "偏空"
    elif signal.score < 0:
        signal.verdict = "警戒"
    else:
        signal.verdict = "中性"
    return signal


def format_onchain_report(signal: OnchainSignal) -> str:
    lines = [
        f"鏈上檢查｜{signal.symbol}",
        f"判定：{signal.verdict}｜分數 {signal.score:+d}",
    ]
    if signal.token_address:
        lines.append(f"鏈：{signal.chain_id}｜合約：{signal.token_address}")
    if signal.token_name:
        lines.append(f"名稱：{signal.token_name}")
    lines.append(
        "DEX："
        f"流動性 {_format_usd(signal.liquidity_usd)}｜"
        f"24h量 {_format_usd(signal.volume_24h_usd)}｜"
        f"1h量 {_format_usd(signal.volume_1h_usd)}"
    )
    lines.append(
        "價格/買賣："
        f"24h {_format_pct(signal.price_change_24h_pct)}｜"
        f"1h {_format_pct(signal.price_change_1h_pct)}｜"
        f"24h買賣 {signal.buys_24h or 0}/{signal.sells_24h or 0}｜"
        f"1h買賣 {signal.buys_1h or 0}/{signal.sells_1h or 0}"
    )
    if signal.cex_inflow_24h_usd is not None or signal.bitget_inflow_24h_usd is not None:
        lines.append(
            "CEX："
            f"Bitget入金 {_format_usd(signal.bitget_inflow_24h_usd)}｜"
            f"CEX入金 {_format_usd(signal.cex_inflow_24h_usd)}｜"
            f"CEX出金 {_format_usd(signal.cex_outflow_24h_usd)}"
        )
    if signal.reasons:
        lines.append("")
        lines.append("原因：")
        lines.extend(f"- {reason}" for reason in signal.reasons[:8])
    if signal.warnings:
        lines.append("")
        lines.append("限制：")
        lines.extend(f"- {warning}" for warning in signal.warnings[:4])
    if signal.pair_url:
        lines.append("")
        lines.append(signal.pair_url)
    return "\n".join(lines)
