"""Analyse de mise en page page par page (PyMuPDF).

Ce que le texte brut des PDF OTR nous a montré, et ce que ce module en fait :

* le numéro de page apparaît comme une ligne isolée, parfois au milieu d'une
  phrase -> on le retire par POSITION (zone haute/basse) et on le mémorise
  (`printed_page`), au lieu de le chercher au regex dans le texte ;
* les notes de bas de page ("Loi N°2022-022 … Exercice 2023") disent quelle loi
  de finances a modifié un texte -> on les SÉPARE du corps et on les garde
  (police plus petite, bas de page) ;
* les appels de note sont des exposants collés au mot suivant ("1Toutefois")
  -> on les transforme en marqueur {{fn:N}} avant qu'ils ne se collent ;
* les exposants ordinaux ("1" + "er") sont recollés ("1er").
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pymupdf

from .patterns import DIGITS_ONLY, GLUED_MARKER, LEGAL_START, ORDINAL_SUFFIXES

FN_PLACEHOLDER = re.compile(r"\{\{fn:(\d+)\}\}")


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
@dataclass
class LayoutCfg:
    top_zone: float = 0.08            # fraction de la hauteur : zone d'en-tête
    bottom_zone: float = 0.08         # fraction de la hauteur : zone de pied de page
    footnote_size_ratio: float = 0.92  # taille <= ratio * corps  => note de bas de page
    footnote_min_y: float = 0.45      # une note se trouve dans le bas de la page
    repeat_threshold: float = 0.25    # texte répété dans les marges sur >=25 % des pages
    body_size: float | None = None    # forcer la taille du corps si la détection se trompe
    drop_regex: list[str] = field(default_factory=list)  # lignes à jeter (ex. titre de couverture)
    skip_pages: list[int] = field(default_factory=list)  # pages PDF (1-based) à ignorer (sommaire…)
    gap_factor: float = 1.6           # interligne > gap_factor * médiane => nouveau paragraphe

    @classmethod
    def from_dict(cls, d: dict | None) -> "LayoutCfg":
        d = dict(d or {})
        known = {k: d[k] for k in d if k in cls.__dataclass_fields__}
        return cls(**known)


# --------------------------------------------------------------------------
# Structures
# --------------------------------------------------------------------------
@dataclass
class Span:
    text: str
    size: float
    bold: bool
    sup: bool
    x0: float
    x1: float


@dataclass
class RawLine:
    spans: list[Span]
    x0: float
    y0: float
    x1: float
    y1: float

    def merge(self, other: "RawLine") -> None:
        self.spans += other.spans
        self.x0, self.y0 = min(self.x0, other.x0), min(self.y0, other.y0)
        self.x1, self.y1 = max(self.x1, other.x1), max(self.y1, other.y1)


@dataclass
class Ln:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    page: int
    gap_before: float = 0.0  # interligne avec la ligne précédente (même page), en points

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class PageData:
    pdf_page: int
    printed_page: int | None
    height: float
    lines: list[Ln]
    footnotes: dict[int, str]
    dropped: list[tuple[str, str]]
    footnote_method: str = "none"  # "geometry" | "text" | "none"


@dataclass
class DocStats:
    n_pages: int
    body_size: float
    repeated: set[str]
    p95_len: float
    median_leading: float
    size_hist: dict[float, int]


# --------------------------------------------------------------------------
# Lecture bas niveau
# --------------------------------------------------------------------------
def _norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def read_raw_lines(page: pymupdf.Page) -> list[RawLine]:
    out: list[RawLine] = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b["lines"]:
            spans = []
            for s in l["spans"]:
                if s["text"] == "":
                    continue
                font = s.get("font", "").lower()
                bold = bool(s["flags"] & 16) or "bold" in font or font.endswith("-bd")
                spans.append(Span(s["text"], float(s["size"]), bold, bool(s["flags"] & 1),
                                  s["bbox"][0], s["bbox"][2]))
            if not spans or not "".join(sp.text for sp in spans).strip():
                continue
            x0, y0, x1, y1 = l["bbox"]
            out.append(RawLine(spans, x0, y0, x1, y1))
    return out


def merge_visual_lines(lines: list[RawLine]) -> list[RawLine]:
    """Fusionne les fragments qui sont sur la même ligne visuelle (exposants
    extraits comme lignes séparées, numéro de note puis texte de la note…)."""
    lines = sorted(lines, key=lambda l: (l.y0 + l.y1) / 2)
    groups: list[RawLine] = []
    for l in lines:
        if groups:
            g = groups[-1]
            ov = min(g.y1, l.y1) - max(g.y0, l.y0)
            h = min(g.y1 - g.y0, l.y1 - l.y0)
            if h > 0 and ov / h >= 0.5:
                g.merge(l)
                continue
        groups.append(RawLine(list(l.spans), l.x0, l.y0, l.x1, l.y1))
    return groups


def render_line(rl: RawLine) -> tuple[str, float, bool]:
    """Texte de la ligne avec marqueurs {{fn:N}} pour les exposants numériques."""
    spans = sorted(rl.spans, key=lambda s: s.x0)
    max_size = max(s.size for s in spans)
    parts: list[str] = []
    big_chars = big_size = bold_chars = total = 0
    sizes = Counter()
    for s in spans:
        st = s.text.strip()
        small = s.sup or s.size < 0.85 * max_size
        if small and st.isdigit() and len(st) <= 2:
            parts.append("{{fn:%s}}" % st)
            continue
        if small and st.lower() in ORDINAL_SUFFIXES:
            parts.append(st)  # "1" + "er" -> "1er"
            continue
        parts.append(s.text)
        n = len(s.text.strip())
        total += n
        bold_chars += n if s.bold else 0
        sizes[round(s.size, 1)] += n
    text = re.sub(r"[ \t\u00a0\u202f]+", " ", "".join(parts)).strip()
    size = sizes.most_common(1)[0][0] if sizes else max_size
    bold = total > 0 and bold_chars / total >= 0.6
    return text, float(size), bold


def page_lines(page: pymupdf.Page, pdf_index: int) -> list[Ln]:
    lines = []
    for rl in merge_visual_lines(read_raw_lines(page)):
        text, size, bold = render_line(rl)
        if text:
            lines.append(Ln(text, rl.x0, rl.y0, rl.x1, rl.y1, size, bold, pdf_index))
    lines.sort(key=lambda l: (round(l.y0, 0), l.x0))
    for prev, cur in zip(lines, lines[1:]):
        cur.gap_before = cur.y0 - prev.y0
    return lines


# --------------------------------------------------------------------------
# Passe 1 : statistiques du document
# --------------------------------------------------------------------------
def scan_document(doc: pymupdf.Document, cfg: LayoutCfg) -> DocStats:
    size_counter: Counter = Counter()
    zone_counter: Counter = Counter()
    lengths: list[int] = []
    leadings: list[float] = []
    n = len(doc)
    for i, page in enumerate(doc, start=1):
        if i in cfg.skip_pages:
            continue
        H = page.rect.height
        for ln in page_lines(page, i):
            size_counter[round(ln.size, 1)] += len(ln.text)
            in_margin = ln.cy < cfg.top_zone * H or ln.cy > (1 - cfg.bottom_zone) * H
            if in_margin and not DIGITS_ONLY.match(ln.text):
                zone_counter[_norm_key(ln.text)] += 1
    body = cfg.body_size or (size_counter.most_common(1)[0][0] if size_counter else 10.0)
    for i, page in enumerate(doc, start=1):
        if i in cfg.skip_pages:
            continue
        H = page.rect.height
        for ln in page_lines(page, i):
            if abs(ln.size - body) <= 0.6 and cfg.top_zone * H < ln.cy < (1 - cfg.bottom_zone) * H:
                lengths.append(len(ln.text))
                if 0 < ln.gap_before < 3 * body:
                    leadings.append(ln.gap_before)
    thr = max(3, int(cfg.repeat_threshold * n))
    repeated = {k for k, c in zone_counter.items() if c >= thr and n >= 4}
    lengths.sort()
    p95 = lengths[int(0.95 * (len(lengths) - 1))] if lengths else 80
    return DocStats(n, body, repeated, float(p95),
                    statistics.median(leadings) if leadings else 1.2 * body,
                    dict(size_counter))


# --------------------------------------------------------------------------
# Passe 2 : une page
# --------------------------------------------------------------------------
def _parse_footnote_lines(foot: list[Ln]) -> dict[int, str]:
    defs: dict[int, list[str]] = {}
    cur: int | None = None
    for ln in foot:
        t = ln.text.strip()
        m = re.match(r"^\{\{fn:(\d+)\}\}\s*(.*)$", t)
        if m:
            cur = int(m.group(1))
            defs.setdefault(cur, [])
            if m.group(2):
                defs[cur].append(m.group(2))
            continue
        if DIGITS_ONLY.match(t):
            cur = int(t)
            defs.setdefault(cur, [])
            continue
        m = re.match(r"^(\d{1,2})\s+(?=(?:Loi|Ordonnance|D[ée]cret|Arr[êe]t[ée]|Directive|R[èe]glement)\b)(.*)$", t)
        if m:
            cur = int(m.group(1))
            defs.setdefault(cur, []).append(m.group(2))
            continue
        if cur is not None:
            defs[cur].append(t)
    return {k: FN_PLACEHOLDER.sub("", " ".join(v)).strip() for k, v in defs.items()}


def process_page(page: pymupdf.Page, pdf_index: int, cfg: LayoutCfg, stats: DocStats) -> PageData:
    H = page.rect.height
    lines = page_lines(page, pdf_index)
    drop_res = [re.compile(r, re.I) for r in cfg.drop_regex]
    body: list[Ln] = []
    foot: list[Ln] = []
    dropped: list[tuple[str, str]] = []
    printed: int | None = None

    for ln in lines:
        in_margin = ln.cy < cfg.top_zone * H or ln.cy > (1 - cfg.bottom_zone) * H
        if in_margin:
            if DIGITS_ONLY.match(ln.text):
                printed = int(ln.text)
                dropped.append(("page_number", ln.text))
                continue
            if _norm_key(ln.text) in stats.repeated:
                dropped.append(("running_header_footer", ln.text))
                continue
        if any(r.match(ln.text) for r in drop_res):
            dropped.append(("drop_regex", ln.text))
            continue
        if ln.size <= cfg.footnote_size_ratio * stats.body_size and ln.cy > cfg.footnote_min_y * H:
            foot.append(ln)
            continue
        body.append(ln)

    method = "none"
    defs: dict[int, str] = {}
    if foot:
        defs = _parse_footnote_lines(foot)
        method = "geometry"

    # Repli textuel : la police des notes n'est pas plus petite -> on les repère
    # par leur forme ("12" seul puis "Loi N°…", ou "{{fn:1}}Loi N°…") dans le bas de page.
    if not defs:
        for i, ln in enumerate(body):
            if ln.cy <= cfg.footnote_min_y * H:
                continue
            m = FN_PLACEHOLDER.match(ln.text)
            starts_note = (
                (DIGITS_ONLY.match(ln.text) and i + 1 < len(body) and LEGAL_START.match(body[i + 1].text))
                or (m and LEGAL_START.match(FN_PLACEHOLDER.sub("", ln.text, count=1).strip()))
            )
            if starts_note:
                # tout ce qui est sous la première note est du texte de note (les notes sont en bas de page)
                defs = _parse_footnote_lines(body[i:])
                body = body[:i]
                method = "text"
                break

    # Repli : marqueurs collés ("1Toutefois", "6Art. 74") quand l'exposant n'a pas été vu.
    if defs:
        present = {int(n) for ln in body for n in FN_PLACEHOLDER.findall(ln.text)}
        for ln in body:
            m = GLUED_MARKER.match(ln.text)
            if m and int(m.group(1)) in defs and int(m.group(1)) not in present:
                n = int(m.group(1))
                ln.text = "{{fn:%d}}" % n + ln.text[len(m.group(1)):]
                present.add(n)

    # Interligne recalculé sur les lignes CONSERVÉES : la 1re ligne de corps n'a pas de "ligne
    # précédente" (sinon on mesurerait l'écart avec le numéro de page supprimé -> faux saut de paragraphe).
    for k, ln in enumerate(body):
        ln.gap_before = 0.0 if k == 0 else ln.y0 - body[k - 1].y0

    return PageData(pdf_index, printed, H, body, defs, dropped, method)


def extract_pages(doc: pymupdf.Document, cfg: LayoutCfg, stats: DocStats | None = None,
                  first: int = 1, last: int | None = None) -> tuple[list[PageData], DocStats]:
    stats = stats or scan_document(doc, cfg)
    last = last or len(doc)
    pages = []
    for i in range(first, last + 1):
        if i in cfg.skip_pages:
            continue
        pages.append(process_page(doc[i - 1], i, cfg, stats))
    return pages, stats


# --------------------------------------------------------------------------
# Tableaux (stratégie 'generic', utile pour les cahiers fiscaux)
# --------------------------------------------------------------------------
def page_tables(page: pymupdf.Page) -> list[tuple[tuple[float, float, float, float], list[list[str]]]]:
    try:
        found = page.find_tables()
    except Exception:  # pragma: no cover - dépend de la version
        return []
    out = []
    for t in found.tables:
        rows = t.extract()
        if len(rows) < 2 or max(len(r) for r in rows) < 2:
            continue
        cells = [c for r in rows for c in r]
        filled = sum(1 for c in cells if c and str(c).strip())
        if filled / max(1, len(cells)) < 0.4:
            continue
        clean = [[re.sub(r"\s+", " ", str(c or "")).strip() for c in r] for r in rows]
        out.append((tuple(t.bbox), clean))
    return out
