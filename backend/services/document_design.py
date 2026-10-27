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
import math
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
    PageBreak,
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

# Фирменный цвет Voksy AI (design-system/README.md) — запасной, если бизнес свой не задал.
DEFAULT_ACCENT = "#2a5ce8"

# Пишется в свойства PDF (Creator). У старого fpdf2-рендера это поле пустое,
# поэтому по нему сразу видно, каким кодом собран присланный клиенту файл.
RENDERER_SIGNATURE = "Voksy AI documents v2 (document_design)"

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


class CardGrid(Flowable):
    """
    Карточки преимуществ: две в ряд, подложка фирменного цвета, галочка,
    жирный заголовок и пояснение. Списком такие блоки читаются хуже — глаз
    цепляется за карточку, а не за ряд одинаковых строк.
    """

    GAP = 5 * mm
    PAD = 5 * mm
    ICON = 4.6 * mm

    def __init__(self, cards: List[Tuple[str, str]], accent: colors.Color,
                 st: Dict[str, ParagraphStyle], width: float, columns: int = 2):
        super().__init__()
        self.cards = cards
        self.accent = accent
        self.st = st
        self.width = width
        self.columns = max(1, columns)
        self._rows: List[List[Tuple[Paragraph, Optional[Paragraph], float]]] = []
        self.height = 0.0

    def _col_width(self) -> float:
        return (self.width - self.GAP * (self.columns - 1)) / self.columns

    def wrap(self, avail_w, avail_h):
        self.width = avail_w
        col_w = self._col_width()
        text_w = col_w - 2 * self.PAD - self.ICON - 2 * mm
        cells = []
        for title, body in self.cards:
            p_title = Paragraph(_inline(title), self.st["card_h"])
            p_body = Paragraph(_inline(body), self.st["card_p"]) if body else None
            h = p_title.wrap(text_w, avail_h)[1]
            if p_body is not None:
                h += 2 + p_body.wrap(text_w, avail_h)[1]
            cells.append((p_title, p_body, h + 2 * self.PAD))
        self._rows = [cells[i:i + self.columns] for i in range(0, len(cells), self.columns)]
        self.height = sum(max(c[2] for c in row) for row in self._rows) \
            + self.GAP * max(0, len(self._rows) - 1)
        return avail_w, self.height

    def draw(self):
        c = self.canv
        col_w = self._col_width()
        text_w = col_w - 2 * self.PAD - self.ICON - 2 * mm
        y = self.height
        for row in self._rows:
            row_h = max(cell[2] for cell in row)
            y -= row_h
            for i, (p_title, p_body, _h) in enumerate(row):
                x = i * (col_w + self.GAP)
                c.setFillColor(_tint(self.accent, 0.94))
                c.roundRect(x, y, col_w, row_h, 5, stroke=0, fill=1)
                # Галочка в кружке фирменного цвета.
                cx = x + self.PAD + self.ICON / 2
                cy = y + row_h - self.PAD - self.ICON / 2
                c.setFillColor(self.accent)
                c.circle(cx, cy, self.ICON / 2, stroke=0, fill=1)
                c.setFillColor(colors.white)
                c.setFont(FONT_BOLD, 7.5)
                c.drawCentredString(cx, cy - 2.6, "✓")
                tx = x + self.PAD + self.ICON + 2 * mm
                ty = y + row_h - self.PAD
                th = p_title.wrap(text_w, row_h)[1]
                p_title.drawOn(c, tx, ty - th)
                if p_body is not None:
                    bh = p_body.wrap(text_w, row_h)[1]
                    p_body.drawOn(c, tx, ty - th - 2 - bh)
            y -= self.GAP


def _figure(data: bytes, caption: Optional[str], st: Dict[str, ParagraphStyle],
            width: float) -> List[Any]:
    """Картинка по ширине колонки (или уже, если мелкая) + подпись под ней."""
    try:
        reader = ImageReader(io.BytesIO(data))
        iw, ih = reader.getSize()
        if not iw or not ih:
            return []
    except Exception as e:
        logger.warning(f"[DOC] картинка не вставлена: {e}")
        return []
    # Не на всю ширину полосы: картинка во всю ширину выглядит как баннер,
    # а не как иллюстрация к тексту.
    scale = min(width * 0.8 / iw, 1.0)
    # Очень высокие картинки ужимаем, чтобы одна не занимала всю страницу.
    max_h = 85 * mm
    if ih * scale > max_h:
        scale = max_h / ih
    img = Image(io.BytesIO(data), width=iw * scale, height=ih * scale, mask="auto")
    img.hAlign = "CENTER"
    out: List[Any] = [Spacer(1, 4), img]
    if caption:
        out += [Spacer(1, 2), Paragraph(_inline(caption), st["caption"])]
    out.append(Spacer(1, 6))
    return [KeepTogether(out)]


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
        "card_h": ParagraphStyle("kh", fontName=FONT_BOLD, fontSize=10.5, leading=13.5, textColor=INK),
        "card_p": ParagraphStyle("kp", fontName=FONT_REGULAR, fontSize=9.5, leading=13,
                                 textColor=colors.HexColor("#374151")),
        "caption": ParagraphStyle("cap", fontName=FONT_REGULAR, fontSize=9, leading=12,
                                  textColor=MUTED, alignment=1),
        "cta_contacts": ParagraphStyle("cc", fontName=FONT_BOLD, fontSize=10.5, leading=14, textColor=accent),
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


def _band_layout(title: str, has_logo: bool = False) -> Tuple[List[str], float, float]:
    """Подобрать кегль заголовка и высоту шапки: (строки, кегль, высота)."""
    lines, size = _wrap_title(title, TITLE_SIZES[0], CONTENT_W), TITLE_SIZES[0]
    for candidate in TITLE_SIZES:
        lines = _wrap_title(title, candidate, CONTENT_W)
        size = candidate
        if len(lines) <= TITLE_MAX_LINES:
            break
    lines = lines[:TITLE_MAX_LINES]
    leading = size * 1.25
    # Верхняя строка шапки (логотип + название) не должна наезжать на заголовок.
    top_row = 30 * mm if has_logo else 19 * mm
    height = top_row + len(lines) * leading + 7 * mm
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

def _draw_cover(canv, brand: Dict[str, Any], accent: colors.Color, title: str,
                client_name: Optional[str], kind_label: str):
    """
    Титульная страница: верхняя треть — фирменный цвет с крупным логотипом,
    ниже на белом — тип документа, заголовок, кому и когда, внизу контакты.
    """
    canv.saveState()
    band = PAGE_H * 0.42
    canv.setFillColor(accent)
    canv.rect(0, PAGE_H - band, PAGE_W, band, stroke=0, fill=1)

    logo = _logo_image(brand, max_w=70 * mm, max_h=26 * mm)
    company = (brand.get("company_name") or "").strip()
    y = PAGE_H - band + 26 * mm
    if logo is not None:
        pad = 4 * mm
        x = MARGIN
        canv.setFillColor(colors.white)
        canv.roundRect(x - pad, y - pad, logo.drawWidth + 2 * pad,
                       logo.drawHeight + 2 * pad, 4, stroke=0, fill=1)
        logo.drawOn(canv, x, y)
        y -= 10 * mm
    if company:
        canv.setFillColor(colors.white)
        canv.setFont(FONT_BOLD, 15 if logo is not None else 22)
        canv.drawString(MARGIN, y, _clip(canv, company, FONT_BOLD,
                                         15 if logo is not None else 22, CONTENT_W))

    # Белая часть: тип документа → заголовок → кому и когда.
    y = PAGE_H - band - 22 * mm
    if kind_label:
        canv.setFillColor(accent)
        canv.setFont(FONT_BOLD, 10.5)
        canv.drawString(MARGIN, y, kind_label.upper()[:60])
        y -= 11 * mm

    size = 26
    lines = _wrap_title(title, size, CONTENT_W)
    while len(lines) > 4 and size > 17:
        size -= 2
        lines = _wrap_title(title, size, CONTENT_W)
    canv.setFillColor(INK)
    canv.setFont(FONT_BOLD, size)
    for line in lines[:4]:
        canv.drawString(MARGIN, y, line)
        y -= size * 1.3
    y -= 4 * mm

    canv.setStrokeColor(accent)
    canv.setLineWidth(2.4)
    canv.line(MARGIN, y, MARGIN + 30 * mm, y)
    y -= 10 * mm

    canv.setFont(FONT_REGULAR, 11.5)
    canv.setFillColor(MUTED)
    if client_name:
        canv.drawString(MARGIN, y, _clip(canv, f"Для: {client_name}", FONT_REGULAR, 11.5, CONTENT_W))
        y -= 7 * mm
    canv.drawString(MARGIN, y, datetime.now().strftime("%d.%m.%Y"))

    contacts = (brand.get("contacts") or "").strip()
    if contacts:
        canv.setFont(FONT_BOLD, 11)
        canv.setFillColor(accent)
        canv.drawString(MARGIN, FOOTER_H + 6 * mm,
                        _clip(canv, contacts, FONT_BOLD, 11, CONTENT_W))
    canv.restoreState()


def _story_height(story: List[Any], width: float, avail_h: float) -> float:
    """Примерная высота содержимого — чтобы решить, влезет ли оно на страницу."""
    total = 0.0
    for f in story:
        try:
            total += f.wrap(width, avail_h)[1]
        except Exception:
            continue
    return total


def _doc_meta(brand: Dict[str, Any], client_name: Optional[str]) -> str:
    parts = []
    if client_name:
        parts.append(f"Для: {client_name}")
    parts.append(datetime.now().strftime("%d.%m.%Y"))
    return "   ·   ".join(parts)


# Пункт вида «**Заголовок** — пояснение» — это карточка преимущества, а не
# строка списка. Два и больше таких подряд рисуются сеткой карточек.
_CARD_RE = re.compile(r"^\*\*(?P<title>[^*]{2,60})\*\*\s*[—–:-]\s*(?P<body>.+)$")


def _as_cards(items: List[str]) -> Optional[List[Tuple[str, str]]]:
    """Список → карточки, если так оформлено большинство пунктов."""
    pairs = []
    for item in items:
        m = _CARD_RE.match(item.strip())
        if not m:
            return None
        pairs.append((m.group("title").strip(), m.group("body").strip()))
    return pairs if len(pairs) >= 2 else None


def _build_story(blocks, table, images, st, accent, brand, compress: float) -> List[Any]:
    """
    Содержимое документа. compress < 1 поджимает вертикальные отступы: так
    хвост в пару строк не уезжает один на новую страницу (у владельца это
    выглядело как «вторая страница пустая»).
    """
    def sp(h: float) -> Spacer:
        return Spacer(1, max(1.0, h * compress))

    story: List[Any] = []
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
            head = [sp(7 if story else 0), Paragraph(_inline(text), style)]
            head += [sp(3), HeadingRule(accent), sp(6)] if level <= 1 else [sp(3)]
            # Заголовок не должен остаться один внизу страницы.
            emit([KeepTogether(head)] if not in_cta else head)
        elif kind == "p":
            emit([Paragraph(_inline(payload), st["cta_p"] if in_cta else st["p"])])
        elif kind in ("ul", "ol"):
            cards = _as_cards(payload) if kind == "ul" and not in_cta else None
            if cards:
                emit([CardGrid(cards, accent, st, CONTENT_W), sp(6)])
            else:
                emit(_bullet_list(payload, st, accent, numbered=(kind == "ol")))

    if table:
        story.extend(_price_table(table, st, accent))

    for img in (images or []):
        data = img.get("bytes") if isinstance(img, dict) else img
        if data:
            story.extend(_figure(data, (img.get("caption") if isinstance(img, dict) else None),
                                 st, CONTENT_W))

    paras = [f for f in cta_buffer if isinstance(f, Paragraph)]
    if paras:
        contacts = (brand.get("contacts") or "").strip()
        if contacts:
            # Контакты в самом призыве: в колонтитуле их почти не замечают.
            paras.append(Paragraph(_inline(contacts), st["cta_contacts"]))
        story.append(sp(10))
        story.append(CalloutBox(paras, accent, CONTENT_W))

    return story or [Paragraph(" ", st["p"])]


def render_pdf(
    title: str,
    content: str,
    table: Optional[List[List[Any]]] = None,
    brand: Optional[Dict[str, Any]] = None,
    client_name: Optional[str] = None,
    kind_label: str = "",
    images: Optional[List[Dict[str, Any]]] = None,
    cover: Optional[bool] = None,
) -> bytes:
    """
    Собрать оформленный PDF.

    brand: {company_name, color, contacts, logo_bytes} — всё опционально.
    Логотипа нет → в шапке только название компании (или ничего); цвета нет →
    фирменный синий Voksy AI.
    images: [{bytes, caption}] — картинки из библиотеки файлов агента.
    cover: титульная страница. None = сама решит по объёму (короткой памятке
    обложка не нужна, презентации и большому КП — нужна).
    """
    if not _ensure_fonts():
        raise RuntimeError("Шрифты для PDF недоступны")
    brand = dict(brand or {})
    accent = _accent(brand)
    st = _styles(accent)
    title = (title or "Документ").strip()
    subtitle = _doc_meta(brand, client_name)

    blocks = parse_blocks(content)
    # Заголовок первой секции часто дублирует заголовок документа — убираем.
    if blocks and blocks[0][0] == "h" and blocks[0][1][1].strip().lower() == title.strip().lower():
        blocks = blocks[1:]

    title_lines, title_size, band_h = _band_layout(title, bool(brand.get("logo_bytes")))
    first_h = PAGE_H - band_h - 9 * mm - FOOTER_H - 8 * mm
    later_h = PAGE_H - SLIM_H - 6 * mm - FOOTER_H - 8 * mm

    # Подбираем плотность: если содержимое чуть-чуть не влезает на страницу,
    # поджимаем отступы, вместо того чтобы отправлять хвост на пустую страницу.
    story = _build_story(blocks, table, images, st, accent, brand, 1.0)
    page_h = later_h if cover else first_h
    height = _story_height(story, CONTENT_W, page_h)
    pages = max(1, math.ceil(height / page_h))
    tail = height - (pages - 1) * page_h
    if pages > 1 and tail < page_h * 0.5:
        for factor in (0.8, 0.6, 0.45, 0.3):
            candidate = _build_story(blocks, table, images, st, accent, brand, factor)
            if _story_height(candidate, CONTENT_W, page_h) <= (pages - 1) * page_h * 0.985:
                story = candidate
                break

    if cover is None:
        # Обложка оправдана, когда за ней идёт полноценный документ.
        cover = height > later_h * 0.75

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=band_h + 9 * mm, bottomMargin=FOOTER_H + 8 * mm,
        title=title, author=(brand.get("company_name") or "").strip() or None,
        # Подпись в свойствах файла: сразу видно, каким рендером собран документ.
        # У старого fpdf2-пути Producer пустой — по этому полю отличается
        # «новый код задеплоен» от «работает прежний».
        creator=RENDERER_SIGNATURE,
    )

    def frame(height_: float, name: str) -> Frame:
        return Frame(MARGIN, FOOTER_H + 8 * mm, CONTENT_W, height_, id=name,
                     leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    def on_cover(canv, _doc):
        _draw_cover(canv, brand, accent, title, client_name, kind_label)

    def on_first(canv, _doc):
        _draw_band(canv, brand, accent, title, subtitle, kind_label,
                   band_h, title_lines, title_size)
        _draw_footer(canv, brand, accent)

    def on_later(canv, _doc):
        _draw_slim(canv, brand, accent, title)
        _draw_footer(canv, brand, accent)

    templates = [PageTemplate(id="later", frames=[frame(later_h, "later")], onPage=on_later)]
    if cover:
        # Обложка идёт первой: титул рисуется на канве, текст начинается дальше.
        templates.insert(0, PageTemplate(id="cover", frames=[frame(later_h, "cover")],
                                         onPage=on_cover))
        story = [NextPageTemplate("later"), PageBreak()] + story
    else:
        templates.insert(0, PageTemplate(id="first", frames=[frame(first_h, "first")],
                                         onPage=on_first))
        story = [NextPageTemplate("later")] + story

    doc.addPageTemplates(templates)
    doc.build(story)
    return buf.getvalue()
