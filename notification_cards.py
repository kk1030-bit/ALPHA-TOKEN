from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
import os
from pathlib import Path
import re
import time
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


CARD_WIDTH = 1200
BACKGROUND = "#0D1117"
PANEL = "#171D25"
PANEL_ALT = "#1D2530"
TEXT = "#F3F6FA"
MUTED = "#94A0AF"
BORDER = "#2A3441"
GREEN = "#27C281"
AMBER = "#F0B84B"
RED = "#F06464"
CYAN = "#4EB8C4"


@dataclass(frozen=True)
class SummaryRow:
    symbol: str
    decision: str
    stage: str
    score: str
    confidence: str
    liquidity: str
    entry: str
    tp1: str
    tp2: str
    stop_loss: str
    reason: str


@dataclass(frozen=True)
class CardContent:
    title: str
    symbol: str
    decision: str
    subtitle: str
    metrics: tuple[tuple[str, str], ...]
    reason: str
    action: str


def _font_candidates(*, bold: bool) -> list[Path]:
    env_name = "CARD_FONT_BOLD_PATH" if bold else "CARD_FONT_PATH"
    configured = os.environ.get(env_name, "").strip()
    names = [
        configured,
        r"C:\Windows\Fonts\msjhbd.ttc" if bold else r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\msjh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    return [Path(value) for value in names if value]


@lru_cache(maxsize=32)
def card_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for path in _font_candidates(bold=bold):
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _clean(text: object) -> str:
    value = " ".join(str(text or "").replace("\u200b", "").split())
    return value.lstrip("🟡🔴🟢🟠🔵⚠️✅❌ ")


def _tone(value: str) -> str:
    text = value.upper()
    if any(word in text for word in ("不交易", "不要進", "做空", "出場", "停損", "失效", "SL", "偏空")):
        return RED
    if any(word in text for word in ("可開", "做多", "埋伏", "TP", "停利", "多頭")):
        return GREEN
    if any(word in text for word in ("再確認", "待確認", "點火", "不足")):
        return AMBER
    return CYAN


def _fit(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
    value = _clean(text)
    if draw.textlength(value, font=font) <= width:
        return value
    suffix = "…"
    while value and draw.textlength(value + suffix, font=font) > width:
        value = value[:-1]
    return value.rstrip() + suffix


def _font_for_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    width: int,
    *,
    preferred_size: int,
    minimum_size: int = 16,
    bold: bool = False,
) -> ImageFont.ImageFont:
    value = _clean(text)
    for size in range(preferred_size, minimum_size - 1, -1):
        font = card_font(size, bold)
        if draw.textlength(value, font=font) <= width:
            return font
    return card_font(minimum_size, bold)


def _event_metric_widths(metrics: tuple[tuple[str, str], ...]) -> tuple[tuple[int, ...], int]:
    labels = tuple(label for label, _ in metrics[:4])
    if labels == ("進場區", "TP1", "TP2", "SL"):
        return (370, 216, 216, 216), 18
    return (252, 252, 252, 252), 24


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    width: int,
    max_lines: int,
) -> list[str]:
    source = _clean(text)
    if not source:
        return []
    lines: list[str] = []
    current = ""
    for char in source:
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(current.rstrip())
            current = char
            if len(lines) >= max_lines:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current.rstrip())
    if lines and "".join(lines) != source:
        lines[-1] = _fit(draw, lines[-1] + source[len("".join(lines)) :], font, width)
    return lines[:max_lines]


def _field_map(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw in block.splitlines():
        line = _clean(raw)
        if "：" not in line:
            continue
        key, value = line.split("：", 1)
        fields[key.strip()] = value.strip()
    return fields


def parse_hourly_rows(text: str) -> list[SummaryRow]:
    blocks = re.split(r"\n\s*-{3,}\s*\n", text)
    rows: list[SummaryRow] = []
    for block in blocks:
        fields = _field_map(block)
        symbol = fields.get("幣種")
        if not symbol:
            continue
        total = fields.get("總分", "-").split("｜", 1)[0]
        liquidity = fields.get("流動性", "待資料").split("｜", 1)[0]
        rows.append(
            SummaryRow(
                symbol=_clean(symbol),
                decision=_clean(fields.get("方向", fields.get("決策", "不交易"))),
                stage=_clean(fields.get("階段", "-")).split("｜", 1)[0],
                score=_clean(total),
                confidence=_clean(fields.get("信心", "0/100").split("｜", 1)[0]),
                liquidity=_clean(liquidity),
                entry=_clean(fields.get("進場區($)", "-")),
                tp1=_clean(fields.get("TP1($)", "-").split("｜", 1)[0]),
                tp2=_clean(fields.get("TP2($)", "-").split("｜", 1)[0]),
                stop_loss=_clean(fields.get("SL($)", "-").split("｜", 1)[0]),
                reason=_clean(fields.get("理由", "未通過交易門檻")),
            )
        )
    return rows[:5]


def _report_meta(text: str, row_count: int) -> tuple[str, str]:
    summary = ""
    updated = time.strftime("%m/%d %H:%M")
    for raw in text.splitlines():
        line = _clean(raw)
        if line.startswith("摘要："):
            summary = line.removeprefix("摘要：")
        if line.startswith("資料更新："):
            updated = line.removeprefix("資料更新：").split("（", 1)[0]
    return summary or f"綜合候選 {row_count} 檔", updated


def _badge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    *,
    font: ImageFont.ImageFont,
    color: str,
) -> int:
    label = _clean(text)
    width = int(draw.textlength(label, font=font)) + 38
    draw.rounded_rectangle((x, y, x + width, y + 48), radius=12, fill=color)
    draw.text((x + 19, y + 9), label, font=font, fill="#0B1014")
    return width


def _png(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_hourly_card(text: str) -> bytes:
    rows = parse_hourly_rows(text)
    height = 250 + max(1, len(rows)) * 148
    image = Image.new("RGB", (CARD_WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, CARD_WIDTH, 12), fill=AMBER)

    title_font = card_font(46, True)
    meta_font = card_font(23)
    symbol_font = card_font(34, True)
    stage_font = card_font(21)
    score_font = card_font(23, True)
    reason_font = card_font(20)
    badge_font = card_font(20, True)

    summary, updated = _report_meta(text, len(rows))
    draw.text((64, 48), "每小時資金雷達", font=title_font, fill=TEXT)
    draw.text((64, 112), _fit(draw, summary, meta_font, 820), font=meta_font, fill=MUTED)
    update_text = f"更新 {updated}"
    draw.text((CARD_WIDTH - 64 - draw.textlength(update_text, font=meta_font), 62), update_text, font=meta_font, fill=MUTED)

    if not rows:
        draw.rounded_rectangle((64, 176, CARD_WIDTH - 64, height - 56), radius=18, fill=PANEL, outline=BORDER)
        draw.text((96, 218), "目前沒有通過條件的候選", font=card_font(34, True), fill=MUTED)
        return _png(image)

    for index, row in enumerate(rows):
        y = 172 + index * 148
        color = _tone(row.decision)
        draw.rounded_rectangle((64, y, CARD_WIDTH - 64, y + 126), radius=18, fill=PANEL, outline=BORDER, width=2)
        draw.rounded_rectangle((64, y, 72, y + 126), radius=4, fill=color)
        draw.text((92, y + 18), row.symbol, font=symbol_font, fill=TEXT)
        draw.text((92, y + 68), _fit(draw, row.stage, stage_font, 260), font=stage_font, fill=MUTED)

        draw.text((390, y + 20), f"信心 {row.confidence}", font=score_font, fill=TEXT)
        liquidity_color = GREEN if row.liquidity.startswith("合格") else AMBER if row.liquidity.startswith("待") else RED
        draw.text((570, y + 22), _fit(draw, f"流動性 {row.liquidity}", stage_font, 360), font=stage_font, fill=liquidity_color)
        levels = f"進 {row.entry}｜TP1 {row.tp1}｜TP2 {row.tp2}｜SL {row.stop_loss}"
        draw.text((390, y + 58), _fit(draw, levels, stage_font, 650), font=stage_font, fill=TEXT)
        draw.text((390, y + 91), _fit(draw, row.reason, reason_font, 650), font=reason_font, fill=MUTED)

        badge_width = int(draw.textlength(row.decision, font=badge_font)) + 38
        _badge(draw, CARD_WIDTH - 92 - badge_width, y + 22, row.decision, font=badge_font, color=color)
        draw.text((CARD_WIDTH - 92 - 180, y + 82), f"#{index + 1}", font=stage_font, fill=MUTED)

    footer_y = height - 48
    draw.text((64, footer_y), "Binance USD-M｜結構・OI・Funding・流動性綜合判定", font=card_font(18), fill=MUTED)
    return _png(image)


METRIC_PATTERNS: tuple[tuple[str, str], ...] = (
    ("進場區", r"進場區\(\$\)[：: ]+([^\n｜]+)"),
    ("TP1", r"TP1\(\$\)[：: ]+([^\n｜]+)"),
    ("TP2", r"TP2\(\$\)[：: ]+([^\n｜]+)"),
    ("SL", r"SL\(\$\)[：: ]+([^\n｜]+)"),
    ("1H 價格", r"價格(?:\s*1H|1H|變化)[：: ]+([+\-−]?\d+(?:\.\d+)?%)"),
    ("1H OI", r"(?:合約\s*OI\s*1H|OI\s*1H|OI1h)[：: ]+([+\-−]?\d+(?:\.\d+)?%)"),
    ("PnL", r"PnL[：: ]+([+\-−]?\d+(?:\.\d+)?%)"),
    ("Funding", r"Funding[：: ]+([+\-−]?\d+(?:\.\d+)?%)"),
    ("24H 成交", r"24H成交額[：: ]+\$?([\d,.]+(?:K|M|B)?)"),
    ("結構", r"結構[：: ]+(\d+(?:/100)?)"),
    ("進場", r"進場[：: ]+([\d.]+)"),
    ("OI", r"OI\s*\$([\d,.]+(?:K|M|B)?)"),
)


def _infer_decision(text: str) -> str:
    checks = (
        ("方向：做多", "做多"),
        ("方向：做空", "做空"),
        ("方向：不交易", "不交易"),
        ("停損出場", "停損出場"),
        ("立即出場", "立即出場"),
        ("剩餘半倉出場", "半倉出場"),
        ("不要進", "不要進"),
        ("策略SL", "策略 SL"),
        ("策略TP", "策略 TP"),
        ("先停利一半", "停利一半"),
        ("策略開單", "模擬開單"),
        ("可開單", "可開單"),
        ("再確認", "再確認"),
        ("多頭建倉", "多頭建倉"),
        ("觀察", "觀察"),
    )
    for needle, label in checks:
        if needle in text:
            return label
    action = re.search(r"(?:動作|判定|決策)[：:]([^\n｜]+)", text)
    return _clean(action.group(1)) if action else "注意"


def _first_prefixed(lines: Iterable[str], prefixes: tuple[str, ...]) -> str:
    for raw in lines:
        line = _clean(raw)
        for prefix in prefixes:
            if line.startswith(prefix):
                return _clean(line[len(prefix) :])
    return ""


def _labeled_value(text: str, labels: tuple[str, ...]) -> str:
    pattern = r"(?:^|[\n｜])(?:" + "|".join(re.escape(label) for label in labels) + r")[：:]([^\n]+)"
    match = re.search(pattern, text)
    return _clean(match.group(1)) if match else ""


def parse_card_content(text: str) -> CardContent:
    lines = [line for line in text.splitlines() if _clean(line)]
    first = _clean(lines[0] if lines else "資金提醒")
    parts = [_clean(part) for part in first.split("｜") if _clean(part)]
    title = parts[0] if parts else "資金提醒"
    symbol_match = re.search(r"\b[A-Z0-9]{2,}USDT\b", text.upper())
    symbol = symbol_match.group(0) if symbol_match else "市場提醒"
    decision = _infer_decision(text)
    subtitle_parts = [part for part in parts[1:] if part != symbol]
    subtitle = "｜".join(subtitle_parts[:2]) or _first_prefixed(lines, ("類型：", "方向：", "目前判定："))

    metrics: list[tuple[str, str]] = []
    for label, pattern in METRIC_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = _clean(match.group(1))
        if label in {"24H 成交", "OI"} and not value.startswith("$"):
            value = "$" + value
        metrics.append((label, value))
        if len(metrics) >= 4:
            break

    if len(metrics) < 4:
        for raw in lines[1:5]:
            for fragment in raw.split("｜"):
                clean = _clean(fragment)
                if "：" not in clean:
                    continue
                label, value = clean.split("：", 1)
                label = _clean(label)
                value = _clean(value)
                if not label or not value or any(existing == label for existing, _ in metrics):
                    continue
                metrics.append((label[:10], value))
                if len(metrics) >= 4:
                    break
            if len(metrics) >= 4:
                break

    reason = _labeled_value(text, ("原因", "理由", "來源"))
    action = _labeled_value(text, ("倉位管理", "動作", "判定", "進場條件"))
    if not reason:
        reason = subtitle or "未提供交易理由"
    if not action:
        action = decision
    return CardContent(
        title=title,
        symbol=symbol,
        decision=decision,
        subtitle=subtitle,
        metrics=tuple(metrics[:4]),
        reason=reason,
        action=action,
    )


def render_event_card(text: str) -> bytes:
    content = parse_card_content(text)
    image = Image.new("RGB", (CARD_WIDTH, 820), BACKGROUND)
    draw = ImageDraw.Draw(image)
    color = _tone(content.decision)
    draw.rectangle((0, 0, CARD_WIDTH, 12), fill=color)

    draw.text((64, 46), content.title, font=card_font(30, True), fill=MUTED)
    timestamp = time.strftime("%m/%d %H:%M")
    time_font = card_font(21)
    draw.text((CARD_WIDTH - 64 - draw.textlength(timestamp, font=time_font), 50), timestamp, font=time_font, fill=MUTED)

    draw.text((64, 114), content.symbol, font=card_font(58, True), fill=TEXT)
    if content.subtitle:
        draw.text((68, 184), _fit(draw, content.subtitle, card_font(25), 680), font=card_font(25), fill=MUTED)
    badge_font = card_font(25, True)
    badge_width = int(draw.textlength(content.decision, font=badge_font)) + 42
    _badge(draw, CARD_WIDTH - 64 - badge_width, 128, content.decision, font=badge_font, color=color)

    metric_y = 250
    metric_widths, gap = _event_metric_widths(content.metrics)
    x = 64
    for index in range(4):
        metric_width = metric_widths[index]
        draw.rounded_rectangle((x, metric_y, x + metric_width, metric_y + 130), radius=16, fill=PANEL, outline=BORDER, width=2)
        if index < len(content.metrics):
            label, value = content.metrics[index]
            value_font = _font_for_width(
                draw,
                value,
                metric_width - 40,
                preferred_size=29,
                minimum_size=16,
                bold=True,
            )
            draw.text((x + 20, metric_y + 20), _fit(draw, label, card_font(20), metric_width - 40), font=card_font(20), fill=MUTED)
            draw.text((x + 20, metric_y + 62), _clean(value), font=value_font, fill=TEXT)
        x += metric_width + gap

    draw.rounded_rectangle((64, 412, CARD_WIDTH - 64, 588), radius=16, fill=PANEL_ALT, outline=BORDER, width=2)
    draw.text((88, 434), "關鍵原因", font=card_font(20, True), fill=MUTED)
    reason_lines = _wrap(draw, content.reason, card_font(27, True), CARD_WIDTH - 176, 3)
    for index, line in enumerate(reason_lines):
        draw.text((88, 474 + index * 38), line, font=card_font(27, True), fill=TEXT)

    draw.rounded_rectangle((64, 620, CARD_WIDTH - 64, 746), radius=16, fill=color)
    draw.text((88, 640), "現在動作", font=card_font(19, True), fill="#0B1014")
    action = _fit(draw, content.action, card_font(30, True), CARD_WIDTH - 176)
    draw.text((88, 682), action, font=card_font(30, True), fill="#0B1014")
    draw.text((64, 782), "ALPHA TOKEN｜即時風險與資金監控", font=card_font(18), fill=MUTED)
    return _png(image)


def render_list_card(text: str) -> bytes:
    lines = [_clean(line) for line in text.splitlines() if _clean(line)]
    first = lines[0] if lines else "統計摘要"
    title_parts = first.split("｜", 1)
    title = title_parts[0]
    subtitle = title_parts[1] if len(title_parts) > 1 else ""
    rows = [line for line in lines if re.match(r"^\d+\.\s*", line)][:6]
    meta = [line for line in lines[1:] if not re.match(r"^\d+\.\s*", line)][:2]
    height = 300 + max(1, len(rows)) * 100
    image = Image.new("RGB", (CARD_WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, CARD_WIDTH, 12), fill=CYAN)
    draw.text((64, 48), title, font=card_font(43, True), fill=TEXT)
    if subtitle:
        draw.text((64, 108), _fit(draw, subtitle, card_font(24), 760), font=card_font(24), fill=MUTED)
    for index, line in enumerate(meta):
        draw.text((64, 158 + index * 34), _fit(draw, line, card_font(21), CARD_WIDTH - 128), font=card_font(21), fill=MUTED)

    start_y = 238
    if not rows:
        rows = ["目前沒有可列出的項目"]
    for index, row in enumerate(rows):
        y = start_y + index * 100
        draw.rounded_rectangle((64, y, CARD_WIDTH - 64, y + 78), radius=14, fill=PANEL, outline=BORDER)
        draw.text((88, y + 22), _fit(draw, row, card_font(23, True), CARD_WIDTH - 176), font=card_font(23, True), fill=TEXT)
    draw.text((64, height - 42), "ALPHA TOKEN｜摘要卡", font=card_font(18), fill=MUTED)
    return _png(image)


def render_notification_card(text: str) -> bytes:
    if parse_hourly_rows(text):
        return render_hourly_card(text)
    first_line = _clean(text.splitlines()[0] if text.splitlines() else "")
    if first_line.startswith("策略回報"):
        return render_list_card(text)
    numbered_rows = sum(1 for line in text.splitlines() if re.match(r"^\s*\d+\.\s*", line))
    if numbered_rows >= 2:
        return render_list_card(text)
    return render_event_card(text)


def notification_caption(text: str) -> str:
    rows = parse_hourly_rows(text)
    if rows:
        decisions = "｜".join(f"{row.symbol} {row.decision}" for row in rows[:3])
        return _clean(f"每小時資金雷達｜{decisions}")[:180]
    first_line = _clean(text.splitlines()[0] if text.splitlines() else "")
    if first_line.startswith("策略回報"):
        return first_line[:180]
    content = parse_card_content(text)
    return _clean(f"{content.title}｜{content.symbol}｜{content.decision}")[:180]
