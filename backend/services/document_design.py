"""
Оформление PDF-документов агента (ветка 1310-kp-design).

Зачем: старый рендер (`fpdf2`, `_render_pdf` в agent_media_service) клал текст
построчно ячейками `multi_cell`. У него две беды, из-за которых клиенту уходил
плохой файл:

  • при автопереносе на следующую страницу посреди ячейки курсор «уезжал» и
    хвост пункта терялся — в реальном КП у клиента пропала цена
    («• от » и обрыв), а пункты обрывались на «• Ор», «• Со»;
  • никакого оформления: один шрифт, нет шапки, логотипа, цвета, таблицы цен.

Здесь — reportlab Platypus: разбиение на страницы делает сам фреймворк
(flowables), поэтому текст физически не может потеряться, плюс настоящая
вёрстка: цветная шапка с логотипом, заголовки секций, маркеры списка,
таблица цен, блок «следующий шаг», колонтитул с контактами и номером страницы.

reportlab — чистый Python (колёса без системных библиотек), в отличие от
WeasyPrint (нужны pango/cairo) и headless-браузера: на Render стоит только
`pip install -r requirements.txt`, браузера там нет.

Вход — тот же markdown-ish текст, который агент уже умеет писать
(`#` заголовки, `- ` пункты, `**жирный**`), поэтому старые вызовы
`create_document` продолжают работать, просто выглядят лучше.
"""

import io
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from backend.core.logging import get_logger

logger = get_logger(__name__)

FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")

FONT_REGULAR = "VoksiSans"
FONT_BOLD = "VoksiSans-Bold"

# Фирменный цвет VoksiAI (design-system/README.md) — запасной, если бизнес свой не задал.
DEFAULT_ACCENT = "#2a5ce8"

INK = colors.HexColor("#111827")        # основной текст
MUTED = colors.HexColor("#6b7280")      # подписи, колонтитул
HAIRLINE = colors.HexColor("#e5e7eb")   # разделители

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

# Шапка первой страницы: высоту считаем под реальный заголовок (_band_layout),
# это её минимум. Слим-шапка следующих страниц и колонтитул — фиксированные.
BAND_MIN_H = 32 * mm
SLIM_H = 14 * mm
FOOTER_H = 14 * mm

# Заголовок КП бывает длинным («Коммерческое предложение … — голосовой ИИ-агент»):
# переносим по словам и уменьшаем кегль, пока не уложится в 3 строки, вместо
# обрезки многоточием.
TITLE_SIZES = (18, 16, 14)
TITLE_MAX_LINES = 3

_FONTS_READY = False


def _ensure_fonts() -> bool:
    """Зарегистрировать DejaVu (кириллица). False — если файлов шрифта нет."""
    global _FONTS_READY
    if _FONTS_READY:
        return True
    try:
        pdfmetrics.registerFont(TTFont(FONT_REGULAR, os.path.join(FONTS_DIR, "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, os.path.join(FONTS_DIR, "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily(FONT_REGULAR, normal=FONT_REGULAR, bold=FONT_BOLD,
                                      italic=FONT_REGULAR, boldItalic=FONT_BOLD)
        _FONTS_READY = True
        return True
    except Exception as e:
        logger.error(f"[DOC] шрифты не зарегистрированы: {e}")
        return False


def _accent(brand: Optional[Dict[str, Any]]) -> colors.Color:
    raw = ((brand or {}).get("color") or "").strip() or DEFAULT_ACCENT
    if not raw.startswith("#"):
        raw = "#" + raw
    try:
        return colors.HexColor(raw)
    except Exception:
        return colors.HexColor(DEFAULT_ACCENT)


def _tint(color: colors.Color, amount: float) -> colors.Color:
    """Осветлить цвет к белому (amount 0..1) — для подложек блоков."""
    amount = max(0.0, min(1.0, amount))
    return colors.Color(
        color.red + (1 - color.red) * amount,
        color.green + (1 - color.green) * amount,
        color.blue + (1 - color.blue) * amount,
    )


# ============================================================================
# РАЗБОР ТЕКСТА АГЕНТА
# ============================================================================

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_BULLET_PREFIXES = ("- ", "* ", "• ", "— ", "– ")
# «1. », «2) » — нумерованный пункт.
_NUM_RE = re.compile(r"^(\d{1,2})[.)]\s+(.+)$")


def _inline(text: str) -> str:
    """markdown-ish → разметка reportlab (<b>), с экранированием XML."""
    safe = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _BOLD_RE.sub(r"<b>\1</b>", safe)


def parse_blocks(content: str) -> List[Tuple[str, Any]]:
    """
    Текст агента → последовательность блоков:
      ("h", (level, text)) | ("p", text) | ("ul", [items]) | ("ol", [items])

    Пункты списка собираются в группы, чтобы рисовать их одним блоком с
    одинаковыми отступами, а не каждый сам по себе.
    """
    blocks: List[Tuple[str, Any]] = []
    bullets: List[str] = []
    numbered: List[str] = []

    def flush():
        nonlocal bullets, numbered
        if bullets:
            blocks.append(("ul", bullets))
            bullets = []
        if numbered:
            blocks.append(("ol", numbered))
            numbered = []

    for raw in (content or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("#"):
            flush()
            level = len(line) - len(line.lstrip("#"))
            text = line.lstrip("#").strip()
            if text:
                blocks.append(("h", (min(max(level, 1), 3), text)))
            continue
        if line.startswith(_BULLET_PREFIXES):
            if numbered:
                flush()
            bullets.append(line[2:].strip())
            continue
        m = _NUM_RE.match(line)
        if m:
            if bullets:
                flush()
            numbered.append(m.group(2).strip())
            continue
        flush()
        blocks.append(("p", line))
    flush()
    return blocks


# «Следующий шаг» в конце документа рисуем как выделенный блок-призыв.
_CTA_WORDS = ("следующий шаг", "следующие шаги", "как начать", "что дальше",
              "next step", "призыв", "давайте начнём", "давайте начнем")


def _is_cta_heading(text: str) -> bool:
    low = (text or "").strip().lower().rstrip(":")
    return any(w in low for w in _CTA_WORDS)


# ============================================================================
# ЭЛЕМЕНТЫ ОФОРМЛЕНИЯ
# ============================================================================

class HeadingRule(Flowable):
    """Короткая цветная черта под заголовком секции."""

    def __init__(self, color: colors.Color, width: float = 28, thickness: float = 2.2):
        super().__init__()
        self.color = color
        self.width = width
        self.thickness = thickness
        self.height = thickness

    def draw(self):
        self.canv.setFillColor(self.color)
        self.canv.rect(0, 0, self.width, self.thickness, stroke=0, fill=1)


class CalloutBox(Flowable):
    """
    Блок-призыв: подложка фирменного цвета (сильно осветлённая), слева
    цветная полоса, внутри — абзацы. Высоту считаем по реальной вёрстке
    абзацев, поэтому текст не обрезается.
    """

    PAD_X = 10 * mm
    PAD_Y = 6 * mm
    BAR_W = 3.2

    def __init__(self, paragraphs: List[Paragraph], accent: colors.Color, width: float):
        super().__init__()
        self.paragraphs = paragraphs
        self.accent = accent
        self.width = width
        self._heights: List[float] = []
        self.height = 0.0

    def wrap(self, avail_w, avail_h):
        self.width = avail_w
        inner = avail_w - 2 * self.PAD_X
        self._heights = []
        total = 0.0
        for i, p in enumerate(self.paragraphs):
            _, h = p.wrap(inner, avail_h)
            self._heights.append(h)
            total += h
            if i:
                total += 3
        self.height = total + 2 * self.PAD_Y
        return avail_w, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(_tint(self.accent, 0.92))
        c.roundRect(0, 0, self.width, self.height, 6, stroke=0, fill=1)
        c.setFillColor(self.accent)
        c.roundRect(0, 0, self.BAR_W * 2, self.height, 3, stroke=0, fill=1)
        c.setFillColor(_tint(self.accent, 0.92))
        c.rect(self.BAR_W, 0, self.BAR_W, self.height, stroke=0, fill=1)
        y = self.height - self.PAD_Y
        inner = self.width - 2 * self.PAD_X
        for p, h in zip(self.paragraphs, self._heights):
            y -= h
            p.drawOn(c, self.PAD_X, y)
            y -= 3


def _styles(accent: colors.Color) -> Dict[str, ParagraphStyle]:
    base = dict(fontName=FONT_REGULAR, textColor=INK, alignment=TA_LEFT, splitLongWords=1)
    return {
        "title": ParagraphStyle("t", fontName=FONT_BOLD, fontSize=21, leading=26,
                                textColor=colors.white, alignment=TA_LEFT),
        "band_company": ParagraphStyle("bc", fontName=FONT_BOLD, fontSize=12, leading=15,
                                       textColor=colors.white, alignment=TA_LEFT),
        "meta": ParagraphStyle("m", fontSize=9.5, leading=13, textColor=MUTED, **{k: v for k, v in base.items() if k != "textColor"}),
        "h1": ParagraphStyle("h1", fontName=FONT_BOLD, fontSize=14.5, leading=19,
                             textColor=accent, spaceBefore=0, spaceAfter=2),
        "h2": ParagraphStyle("h2", fontName=FONT_BOLD, fontSize=12, leading=16,
                             textColor=INK, spaceBefore=0, spaceAfter=2),
        "p": ParagraphStyle("p", fontSize=10.5, leading=15.5, spaceAfter=4, **base),
        "li": ParagraphStyle("li", fontSize=10.5, leading=15, **base),
        "cta_h": ParagraphStyle("ch", fontName=FONT_BOLD, fontSize=12.5, leading=16, textColor=accent),
        "cta_p": ParagraphStyle("cp", fontSize=10.5, leading=15.5, textColor=INK, fontName=FONT_REGULAR),
        "th": ParagraphStyle("th", fontName=FONT_BOLD, fontSize=10, leading=13, textColor=colors.white),
        "td": ParagraphStyle("td", fontSize=10, leading=13.5, **base),
    }


def _bullet_list(items: List[str], st: Dict[str, ParagraphStyle], accent: colors.Color,
                 numbered: bool = False) -> List:
    """
    Список таблицей 2×N: маркер и текст. Таблица со `splitByRow` переносит
    длинный список на следующую страницу целыми пунктами, не теряя хвост.
    """
    rows = []
    for i, item in enumerate(items, 1):
        marker = f"{i}." if numbered else "•"
        rows.append([
            Paragraph(f'<font color="#{accent.hexval()[2:]}"><b>{marker}</b></font>', st["li"]),
            Paragraph(_inline(item), st["li"]),
        ])
    if not rows:
        return []
    marker_w = 9 * mm if numbered else 6 * mm
    t = Table(rows, colWidths=[marker_w, CONTENT_W - marker_w], splitByRow=1)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return [t, Spacer(1, 4)]


def _price_table(table: List[List[Any]], st: Dict[str, ParagraphStyle],
                 accent: colors.Color) -> List:
    """Таблица: первая строка — шапка фирменного цвета, строки — через одну."""
    rows = [[("" if c is None else str(c)) for c in r]
            for r in table if isinstance(r, (list, tuple))]
    if not rows:
        return []
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    data = [[Paragraph(_inline(c), st["th"]) for c in rows[0]]]
    for r in rows[1:]:
        data.append([Paragraph(_inline(c), st["td"]) for c in r])

    # Первая колонка шире: обычно это название услуги.
    if ncols == 1:
        widths = [CONTENT_W]
    else:
        first = CONTENT_W * 0.46
        rest = (CONTENT_W - first) / (ncols - 1)
        widths = [first] + [rest] * (ncols - 1)

    t = Table(data, colWidths=widths, repeatRows=1, splitByRow=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), accent),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("LINEBELOW", (0, 0), (-1, -2), 0.6, HAIRLINE),
        ("BOX", (0, 0), (-1, -1), 0.6, HAIRLINE),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f9fafb")))
    t.setStyle(TableStyle(style))
    # Небольшую таблицу не рвём между страницами: одна строка-сирота под
    # шапкой на следующей странице выглядит как ошибка вёрстки.
    block = KeepTogether([t]) if len(data) <= 12 else t
    return [Spacer(1, 2), block, Spacer(1, 8)]


# ============================================================================
# ШАПКА И КОЛОНТИТУЛ
# ============================================================================

def _logo_image(brand: Dict[str, Any], max_w: float, max_h: float) -> Optional[Image]:
    data = (brand or {}).get("logo_bytes")
    if not data:
        return None
    try:
        reader = ImageReader(io.BytesIO(data))
        iw, ih = reader.getSize()
        if not iw or not ih:
            return None
        scale = min(max_w / iw, max_h / ih)
        return Image(io.BytesIO(data), width=iw * scale, height=ih * scale, mask="auto")
    except Exception as e:
        logger.warning(f"[DOC] логотип не отрисован: {e}")
        return None


def _wrap_title(title: str, size: float, max_w: float) -> List[str]:
    """Разбить заголовок по словам под ширину max_w при данном кегле."""
    words = (title or "").split()
    lines: List[str] = []
    cur = ""
    for w in words:
        probe = f"{cur} {w}".strip()
        if pdfmetrics.stringWidth(probe, FONT_BOLD, size) <= max_w or not cur:
            cur = probe
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _band_layout(title: str) -> Tuple[List[str], float, float]:
    """Подобрать кегль заголовка и высоту шапки: (строки, кегль, высота)."""
    lines, size = _wrap_title(title, TITLE_SIZES[0], CONTENT_W), TITLE_SIZES[0]
    for candidate in TITLE_SIZES:
        lines = _wrap_title(title, candidate, CONTENT_W)
        size = candidate
        if len(lines) <= TITLE_MAX_LINES:
            break
    lines = lines[:TITLE_MAX_LINES]
    leading = size * 1.25
    height = 17 * mm + len(lines) * leading + 7 * mm
    return lines, size, max(BAND_MIN_H, height)


def _draw_band(canv, brand: Dict[str, Any], accent: colors.Color, title: str,
               subtitle: str, kind_label: str, band_h: float,
               title_lines: List[str], title_size: float):
    """Шапка первой страницы: плашка фирменного цвета, логотип/название, заголовок."""
    canv.saveState()
    canv.setFillColor(accent)
    canv.rect(0, PAGE_H - band_h, PAGE_W, band_h, stroke=0, fill=1)
    # Тонкая тёмная полоса снизу плашки — аккуратный край.
    canv.setFillColor(colors.Color(accent.red * 0.78, accent.green * 0.78, accent.blue * 0.78))
    canv.rect(0, PAGE_H - band_h, PAGE_W, 1.6, stroke=0, fill=1)

    top = PAGE_H - 11 * mm
    company = (brand.get("company_name") or "").strip()
    logo = _logo_image(brand, max_w=46 * mm, max_h=11 * mm)
    x = MARGIN
    if logo is not None:
        # Логотипы обычно тёмные или цветные, а плашка — тоже тёмная: без
        # белой подложки фирменный знак на ней просто тонет.
        pad = 2.2 * mm
        canv.setFillColor(colors.white)
        canv.roundRect(x - pad, top - logo.drawHeight - pad + 2,
                       logo.drawWidth + 2 * pad, logo.drawHeight + 2 * pad,
                       2.5, stroke=0, fill=1)
        logo.drawOn(canv, x, top - logo.drawHeight + 2)
        x += logo.drawWidth + 6 * mm
    if company:
        canv.setFont(FONT_BOLD, 12)
        canv.setFillColor(colors.white)
        canv.drawString(x, top - 8, company[:60])

    if kind_label:
        canv.setFont(FONT_REGULAR, 9)
        canv.setFillColor(colors.Color(1, 1, 1, alpha=0.85))
        canv.drawRightString(PAGE_W - MARGIN, top - 7, kind_label[:60])

    leading = title_size * 1.25
    canv.setFillColor(colors.white)
    canv.setFont(FONT_BOLD, title_size)
    y = PAGE_H - band_h + 7 * mm + (len(title_lines) - 1) * leading + (5 * mm if subtitle else 0)
    for line in title_lines:
        canv.drawString(MARGIN, y, line)
        y -= leading
    if subtitle:
        canv.setFont(FONT_REGULAR, 9.5)
        canv.setFillColor(colors.Color(1, 1, 1, alpha=0.9))
        canv.drawString(MARGIN, PAGE_H - band_h + 7 * mm,
                        _clip(canv, subtitle, FONT_REGULAR, 9.5, CONTENT_W))
    canv.restoreState()


def _draw_slim(canv, brand: Dict[str, Any], accent: colors.Color, title: str):
    """Шапка следующих страниц: узкая полоса с названием и темой документа."""
    canv.saveState()
    canv.setFillColor(accent)
    canv.rect(0, PAGE_H - 2.4, PAGE_W, 2.4, stroke=0, fill=1)
    canv.setFont(FONT_BOLD, 9)
    canv.setFillColor(MUTED)
    company = (brand.get("company_name") or "").strip()
    if company:
        canv.drawString(MARGIN, PAGE_H - SLIM_H + 4 * mm, company[:50])
    canv.setFont(FONT_REGULAR, 9)
    canv.drawRightString(PAGE_W - MARGIN, PAGE_H - SLIM_H + 4 * mm,
                         _clip(canv, title, FONT_REGULAR, 9, CONTENT_W * 0.6))
    canv.restoreState()


def _draw_footer(canv, brand: Dict[str, Any], accent: colors.Color):
    canv.saveState()
    y = FOOTER_H
    canv.setStrokeColor(HAIRLINE)
    canv.setLineWidth(0.6)
    canv.line(MARGIN, y + 5 * mm, PAGE_W - MARGIN, y + 5 * mm)
    canv.setFont(FONT_REGULAR, 8.5)
    canv.setFillColor(MUTED)
    left = " · ".join(x for x in [(brand.get("company_name") or "").strip(),
                                 (brand.get("contacts") or "").strip()] if x)
    if left:
        canv.drawString(MARGIN, y, _clip(canv, left, FONT_REGULAR, 8.5, CONTENT_W * 0.8))
    canv.drawRightString(PAGE_W - MARGIN, y, str(canv.getPageNumber()))
    canv.restoreState()


def _clip(canv, text: str, font: str, size: float, max_w: float) -> str:
    """Обрезать строку по ширине с многоточием (шапка/колонтитул — одна строка)."""
    text = (text or "").replace("\n", " ").strip()
    if canv.stringWidth(text, font, size) <= max_w:
        return text
    ell = "…"
    while text and canv.stringWidth(text + ell, font, size) > max_w:
        text = text[:-1]
    return text + ell


# ============================================================================
# СБОРКА ДОКУМЕНТА
# ============================================================================

def _doc_meta(brand: Dict[str, Any], client_name: Optional[str]) -> str:
    parts = []
    if client_name:
        parts.append(f"Для: {client_name}")
    parts.append(datetime.now().strftime("%d.%m.%Y"))
    return "   ·   ".join(parts)


def render_pdf(
    title: str,
    content: str,
    table: Optional[List[List[Any]]] = None,
    brand: Optional[Dict[str, Any]] = None,
    client_name: Optional[str] = None,
    kind_label: str = "",
) -> bytes:
    """
    Собрать оформленный PDF.

    brand: {company_name, color, contacts, logo_bytes} — всё опционально.
    Логотипа нет → в шапке только название компании (или ничего); цвета нет →
    фирменный синий VoksiAI.
    """
    if not _ensure_fonts():
        raise RuntimeError("Шрифты для PDF недоступны")
    brand = dict(brand or {})
    accent = _accent(brand)
    st = _styles(accent)
    title = (title or "Документ").strip()
    subtitle = _doc_meta(brand, client_name)

    title_lines, title_size, band_h = _band_layout(title)

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=band_h + 9 * mm, bottomMargin=FOOTER_H + 8 * mm,
        title=title, author=(brand.get("company_name") or "").strip() or None,
    )
    first_frame = Frame(MARGIN, FOOTER_H + 8 * mm, CONTENT_W,
                        PAGE_H - band_h - 9 * mm - FOOTER_H - 8 * mm, id="first",
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    later_frame = Frame(MARGIN, FOOTER_H + 8 * mm, CONTENT_W,
                        PAGE_H - SLIM_H - 6 * mm - FOOTER_H - 8 * mm, id="later",
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    def on_first(canv, _doc):
        _draw_band(canv, brand, accent, title, subtitle, kind_label,
                   band_h, title_lines, title_size)
        _draw_footer(canv, brand, accent)

    def on_later(canv, _doc):
        _draw_slim(canv, brand, accent, title)
        _draw_footer(canv, brand, accent)

    doc.addPageTemplates([
        PageTemplate(id="first", frames=[first_frame], onPage=on_first),
        PageTemplate(id="later", frames=[later_frame], onPage=on_later),
    ])

    # Первая страница — с большой шапкой, дальше автоматически слим-шапка.
    story: List[Any] = [NextPageTemplate("later")]
    blocks = parse_blocks(content)

    # Заголовок первой секции часто дублирует заголовок документа — убираем.
    if blocks and blocks[0][0] == "h":
        if blocks[0][1][1].strip().lower() == title.strip().lower():
            blocks = blocks[1:]

    cta_buffer: List[Any] = []
    in_cta = False

    def emit(flowables: List[Any]):
        (cta_buffer if in_cta else story).extend(flowables)

    for kind, payload in blocks:
        if kind == "h":
            level, text = payload
            if _is_cta_heading(text):
                in_cta = True
                cta_buffer.append(Paragraph(_inline(text), st["cta_h"]))
                continue
            style = st["h1"] if level <= 1 else st["h2"]
            head = [Spacer(1, 7 if len(story) > 1 else 0), Paragraph(_inline(text), style)]
            if level <= 1:
                head += [Spacer(1, 3), HeadingRule(accent), Spacer(1, 6)]
            else:
                head += [Spacer(1, 3)]
            # Заголовок не должен остаться один внизу страницы.
            emit([KeepTogether(head)] if not in_cta else head)
        elif kind == "p":
            emit([Paragraph(_inline(payload), st["cta_p"] if in_cta else st["p"])])
        elif kind == "ul":
            emit(_bullet_list(payload, st, accent))
        elif kind == "ol":
            emit(_bullet_list(payload, st, accent, numbered=True))

    if table:
        story.extend(_price_table(table, st, accent))

    if cta_buffer:
        paras = [f for f in cta_buffer if isinstance(f, Paragraph)]
        if paras:
            story.append(Spacer(1, 10))
            story.append(CalloutBox(paras, accent, CONTENT_W))

    if len(story) <= 1:
        story.append(Paragraph(" ", st["p"]))

    doc.build(story)
    return buf.getvalue()
