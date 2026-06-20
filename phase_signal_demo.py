from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import math
import sys
from typing import Any

import openpyxl


ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "data" / "reports"


@dataclass
class PhaseResult:
    decision: str
    phase: str
    reason: str
    price_3m_pct: float | None
    oi_3m_pct: float | None
    price_15m_pct: float | None
    oi_15m_pct: float | None
    price_1h_pct: float | None
    oi_1h_pct: float | None
    funding_rate_pct: float | None
    confidence: int


def num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old * 100.0


def sample_at_or_before(samples: list[dict[str, Any]], ts: float) -> dict[str, Any] | None:
    result = None
    for sample in samples:
        if float(sample["ts"]) <= ts:
            result = sample
        else:
            break
    return result


def change_pair(samples: list[dict[str, Any]], window_seconds: int) -> tuple[float | None, float | None]:
    if len(samples) < 2:
        return None, None
    now = float(samples[-1]["ts"])
    old = sample_at_or_before(samples, now - window_seconds)
    if old is None:
        old = samples[0]
    return (
        pct(num(samples[-1].get("price")), num(old.get("price"))),
        pct(num(samples[-1].get("open_interest")), num(old.get("open_interest"))),
    )


def washout_reclaim(samples: list[dict[str, Any]], lookback: int = 8) -> tuple[bool, float | None]:
    if len(samples) < 5:
        return False, None
    tail = samples[-lookback:]
    prior = tail[:-3]
    if not prior:
        return False, None
    support = min(num(item.get("price")) for item in prior if num(item.get("price")) is not None)
    last_price = num(tail[-1].get("price"))
    intraday_low = min(num(item.get("price")) for item in tail if num(item.get("price")) is not None)
    if support is None or last_price is None or intraday_low is None or support <= 0:
        return False, None
    breakdown_pct = pct(intraday_low, support)
    reclaimed = breakdown_pct is not None and breakdown_pct <= -0.8 and last_price >= support
    return reclaimed, breakdown_pct


def classify_phase(samples: list[dict[str, Any]]) -> PhaseResult:
    samples = sorted(samples, key=lambda item: float(item["ts"]))
    current = samples[-1]
    funding = num(current.get("funding_rate_pct"))
    p3, o3 = change_pair(samples, 180)
    p15, o15 = change_pair(samples, 900)
    p1h, o1h = change_pair(samples, 3600)
    reclaimed, breakdown_pct = washout_reclaim(samples)

    funding_hot = funding is not None and abs(funding) >= 0.10
    funding_extreme = funding is not None and abs(funding) >= 0.25
    p3v = p3 or 0.0
    o3v = o3 or 0.0
    p15v = p15 or 0.0
    o15v = o15 or 0.0
    p1hv = p1h or 0.0
    o1hv = o1h or 0.0

    if funding_extreme:
        return PhaseResult(
            "不要進",
            "FUNDING_OVERHEATED",
            "funding 已極端，容易變成尾端擁擠或插針",
            p3, o3, p15, o15, p1h, o1h, funding, 90,
        )

    if funding_hot and p15v > 0:
        return PhaseResult(
            "不要進",
            "FUNDING_CROWDED_MARKUP",
            "價格上漲但 funding 已偏熱，容易進入擁擠尾段",
            p3, o3, p15, o15, p1h, o1h, funding, 84,
        )

    if p3v > 0.5 and o3v <= -0.8:
        return PhaseResult(
            "不要進",
            "SHORT_COVER_TAIL",
            "價格漲但 OI 降，較像空頭回補推升，不是新多主動進場",
            p3, o3, p15, o15, p1h, o1h, funding, 82,
        )

    if p3v >= 1.5 and o3v < 1.0:
        return PhaseResult(
            "不要進",
            "PRICE_UP_WITHOUT_OI",
            "價格已先拉但 OI 沒跟，追價風險高",
            p3, o3, p15, o15, p1h, o1h, funding, 78,
        )

    if reclaimed and not funding_hot and o15v >= -3.0:
        return PhaseResult(
            "埋伏B",
            "WASHOUT_RECLAIM",
            f"短線跌破後收回，疑似洗盤或誘空；跌破幅度 {breakdown_pct:+.2f}%",
            p3, o3, p15, o15, p1h, o1h, funding, 76,
        )

    if p3v > 0.4 and o3v > 0.8 and p15v > 0 and o15v > 1.5 and not funding_hot:
        if p1hv <= 8.0:
            return PhaseResult(
                "埋伏A",
                "EARLY_MARKUP",
                "價格與 OI 同步上升，且 funding 未過熱，符合早期推升",
                p3, o3, p15, o15, p1h, o1h, funding, 84,
            )
        return PhaseResult(
            "再確認",
            "LATE_MARKUP",
            "價格與 OI 同步上升，但 1h 已拉太多，等回踩或洗盤",
            p3, o3, p15, o15, p1h, o1h, funding, 66,
        )

    if p3v < -0.4 and o3v > 0.8:
        return PhaseResult(
            "再確認",
            "SHORT_BUILD_OR_PRESSURE",
            "價格跌但 OI 增，可能是空頭開倉或壓盤，不能直接做多",
            p3, o3, p15, o15, p1h, o1h, funding, 72,
        )

    if p3v < -0.4 and o3v < -0.8:
        return PhaseResult(
            "再確認",
            "LONG_FLUSH_WAIT_RECLAIM",
            "價格跌且 OI 降，可能是多頭平倉或爆倉尾端，要等收回",
            p3, o3, p15, o15, p1h, o1h, funding, 70,
        )

    if p1hv > 0 and o1hv < 0:
        return PhaseResult(
            "不要進",
            "UPTREND_WITH_OI_DRAIN",
            "1h 價格上漲但 OI 流失，較像回補行情尾端",
            p3, o3, p15, o15, p1h, o1h, funding, 68,
        )

    return PhaseResult(
        "再確認",
        "NO_EDGE_YET",
        "價格/OI 組合沒有明確優勢，先不把它列為可進場",
        p3, o3, p15, o15, p1h, o1h, funding, 45,
    )


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def print_result(name: str, result: PhaseResult) -> None:
    print(
        f"{name:18s} | {result.decision:4s} | {result.phase:24s} | "
        f"P3 {fmt_pct(result.price_3m_pct):>8s} OI3 {fmt_pct(result.oi_3m_pct):>8s} | "
        f"P15 {fmt_pct(result.price_15m_pct):>8s} OI15 {fmt_pct(result.oi_15m_pct):>8s} | "
        f"P1h {fmt_pct(result.price_1h_pct):>8s} OI1h {fmt_pct(result.oi_1h_pct):>8s} | "
        f"F {fmt_pct(result.funding_rate_pct):>8s} | {result.reason}"
    )


def synthetic_samples(prices: list[float], oi: list[float], funding: float = 0.02) -> list[dict[str, Any]]:
    start = 0
    return [
        {"ts": start + idx * 180, "price": price, "open_interest": open_interest, "funding_rate_pct": funding}
        for idx, (price, open_interest) in enumerate(zip(prices, oi))
    ]


def run_synthetic_demo() -> None:
    cases = {
        "早期推升": synthetic_samples([100, 100.6, 101.2, 102.0, 103.0, 104.0], [1000, 1012, 1025, 1048, 1080, 1120], 0.02),
        "回補尾端": synthetic_samples([100, 101.0, 102.2, 103.5, 104.0, 104.4], [1000, 990, 975, 960, 948, 940], 0.03),
        "空頭加倉": synthetic_samples([100, 99.4, 98.8, 98.0, 97.5, 97.0], [1000, 1020, 1048, 1070, 1095, 1120], 0.01),
        "爆倉尾端": synthetic_samples([100, 99.0, 98.0, 97.2, 96.8, 96.5], [1000, 985, 960, 940, 920, 900], 0.01),
        "跌破收回": synthetic_samples([100, 100.4, 100.1, 99.8, 96.9, 100.0, 100.8], [1000, 1005, 1008, 1002, 990, 1000, 1018], 0.02),
        "過熱推升": synthetic_samples([100, 100.8, 101.5, 102.6, 103.2, 104.0], [1000, 1020, 1045, 1070, 1100, 1135], 0.16),
    }
    print("=== 合成案例：把文章邏輯轉成階段判斷 ===")
    for name, samples in cases.items():
        print_result(name, classify_phase(samples))


def file_time(path: Path) -> datetime:
    return datetime.strptime(path.stem.replace("oi_report_", ""), "%Y%m%d_%H%M%S")


def read_report_rows(path: Path) -> list[dict[str, Any]]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(value) if value is not None else "" for value in rows[0]]
    out = []
    ts = file_time(path).timestamp()
    for values in rows[1:]:
        item = {headers[idx]: values[idx] if idx < len(values) else None for idx in range(len(headers))}
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        out.append(
            {
                "ts": ts,
                "symbol": symbol,
                "price": num(item.get("mark_price")) or num(item.get("price")),
                "open_interest": num(item.get("open_interest")),
                "funding_rate_pct": num(item.get("funding_rate_pct")),
                "report_decision": item.get("decision"),
                "report_reason": item.get("reason"),
                "file": path.name,
            }
        )
    return out


def run_report_demo() -> None:
    files = sorted(REPORT_DIR.glob("oi_report_*.xlsx"), key=file_time)
    if not files:
        print("\n沒有 Excel 報表可示範。")
        return
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in files[-18:]:
        for row in read_report_rows(path):
            by_symbol[row["symbol"]].append(row)

    classified = []
    for symbol, samples in by_symbol.items():
        samples = [item for item in samples if item.get("price") is not None and item.get("open_interest") is not None]
        if len(samples) < 3:
            continue
        result = classify_phase(samples)
        classified.append((symbol, result, samples[-1]))

    order = {"埋伏A": 0, "埋伏B": 1, "再確認": 2, "不要進": 3}
    classified.sort(key=lambda item: (order.get(item[1].decision, 9), -item[1].confidence, item[0]))

    print("\n=== 現有報表資料示範：只用 Excel 粗粒度資料 ===")
    print("注意：Excel 是小時級，不足以完整判斷 1m/3m 洗盤，只能示範方向。")
    for symbol, result, latest in classified[:15]:
        print_result(symbol, result)
        print(f"  最新報表：{latest['file']}｜原本報表判定：{latest.get('report_decision')}｜原原因：{latest.get('report_reason')}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    run_synthetic_demo()
    run_report_demo()


if __name__ == "__main__":
    main()
