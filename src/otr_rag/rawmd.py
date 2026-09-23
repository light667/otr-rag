"""Conversion des pages analysées en Markdown BRUT, une stratégie par nature de document.

* `code`     : CGI / LPF. Hiérarchie LIVRE > PARTIE > TITRE > CHAPITRE > Section >
               Paragraphe en titres Markdown (# … ######), un marqueur <!--art:N-->
               par article, marqueurs de page <!--pg:N-->, notes de bas de page
               rattachées à l'article qui les cite.
* `rescrits` : un titre `## R<n> : …` par rescrit ; le corps reste en texte.
* `generic`  : cahiers fiscaux (à affiner après inspection) : titres par taille de
               police, tableaux PyMuPDF -> tableaux Markdown.

Le Markdown est "brut" : une ligne PDF = une ligne de texte, retours à la ligne
et césures conservés. La remise en paragraphes (reflow) est faite à l'étape clean.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

from .layout import FN_PLACEHOLDER, DocStats, LayoutCfg, Ln, PageData, page_tables
from .patterns import (ARTICLE_START, HEADING_PATTERNS, LIST_START, RESCRIT_START,
                       SUBHEADING, article_id, article_sort_key)

FUNCTION_ENDINGS = {
    "le", "la", "les", "l'", "l’", "de", "des", "du", "d'", "d’", "et", "à", "au", "aux", "en",
    "sur", "dont", "par", "pour", "ou", "un", "une", "sous", "dans", "entre", "vers", "avec",
    "sans", "que", "qui", "ci", "non", "ses", "leurs", "leur", "son", "sa", "ce", "cette",
}


@dataclass
class BuildMeta:
    headings: list[dict] = field(default_factory=list)
    articles: list[dict] = field(default_factory=list)
    rescrits: list[dict] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)
    unresolved_markers: list[dict] = field(default_factory=list)
    orphan_footnotes: list[dict] = field(default_factory=list)
    multiline_headings: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _is_upper(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _ends_with_function_word(s: str) -> bool:
    s = s.rstrip()
    if not s:
        return False
    last = s.split()[-1].lower()
    return last in FUNCTION_ENDINGS or last.endswith(("'", "’"))


def _strip_fn(text: str) -> str:
    return re.sub(r"^(?:\[\^fn\d+\])+", "", text).strip()


def _match_heading(text: str):
    t = _strip_fn(text)
    for level, kind, pat in HEADING_PATTERNS:
        m = pat.match(t)
        if m:
            return level, kind, m
    return None


def _is_structural(text: str) -> bool:
    t = _strip_fn(text)
    return bool(_match_heading(t) or ARTICLE_START.match(t) or LIST_START.match(t)
                or SUBHEADING.match(t) or RESCRIT_START.match(t))


# --------------------------------------------------------------------------
# Notes de bas de page : {{fn:N}} -> [^fnK] unique dans le document
# --------------------------------------------------------------------------
class FootnoteResolver:
    def __init__(self, pages: list[PageData], meta: BuildMeta):
        self.defs = {p.pdf_page: dict(p.footnotes) for p in pages}
        self.used: set[tuple[int, int]] = set()
        self.counter = 0
        self.pending: list[tuple[str, str]] = []
        self.meta = meta

    def resolve(self, text: str, page: int) -> str:
        def repl(m: re.Match) -> str:
            n = int(m.group(1))
            for p in (page, page + 1, page - 1):
                d = self.defs.get(p, {})
                if n in d and (p, n) not in self.used:
                    self.used.add((p, n))
                    self.counter += 1
                    fid = f"fn{self.counter}"
                    self.pending.append((fid, d[n]))
                    return f"[^{fid}]"
            self.meta.unresolved_markers.append({"page": page, "marker": n})
            return ""
        return FN_PLACEHOLDER.sub(repl, text)

    def flush(self, out: list[str]) -> None:
        if not self.pending:
            return
        out.append("")
        for fid, txt in self.pending:
            out.append(f"[^{fid}]: {txt}")
        out.append("")
        self.pending.clear()

    def report_orphans(self) -> None:
        for p, d in self.defs.items():
            for n, txt in d.items():
                if (p, n) not in self.used:
                    self.meta.orphan_footnotes.append({"page": p, "num": n, "text": txt[:120]})


# --------------------------------------------------------------------------
# Stratégie CODE
# --------------------------------------------------------------------------
def _heading_label(kind: str, num: str) -> str:
    num = num.upper() if kind in {"livre", "partie", "titre", "chapitre"} else num
    return {"livre": f"LIVRE {num}", "partie": f"{num} PARTIE", "titre": f"TITRE {num}",
            "chapitre": f"CHAPITRE {num}", "section": f"Section {num}",
            "paragraphe": f"Paragraphe {num}"}[kind]


def build_code_md(pages: list[PageData], stats: DocStats, cfg: LayoutCfg,
                  meta: BuildMeta) -> str:
    out: list[str] = []
    fn = FootnoteResolver(pages, meta)
    seq: list[tuple[str, object]] = []
    for p in pages:
        seq.append(("pg", p))
        for ln in p.lines:
            seq.append(("ln", ln))
        meta.pages.append({"pdf_page": p.pdf_page, "printed_page": p.printed_page,
                           "footnote_method": p.footnote_method,
                           "n_footnotes": len(p.footnotes),
                           "dropped": len(p.dropped), "n_lines": len(p.lines)})

    last_art: str | None = None
    i = 0
    cur_page = pages[0].pdf_page if pages else 1

    def next_line(j: int) -> Ln | None:
        return seq[j][1] if j < len(seq) and seq[j][0] == "ln" else None  # type: ignore[return-value]

    while i < len(seq):
        kind, payload = seq[i]
        if kind == "pg":
            cur_page = payload.pdf_page  # type: ignore[attr-defined]
            out.append(f"<!--pg:{cur_page}-->")
            i += 1
            continue
        ln: Ln = payload  # type: ignore[assignment]
        text = fn.resolve(ln.text, ln.page)
        stripped = _strip_fn(text)

        # ---- titres hiérarchiques -------------------------------------------------
        h = _match_heading(text)
        if h:
            level, hkind, m = h
            title = (m.group(2) or "").strip()
            lines_used = 1
            prev = text
            while lines_used < 6:
                nl = next_line(i + lines_used)
                if nl is None or _is_structural(nl.text):
                    break
                nt = nl.text
                cont = (
                    not title
                    or nt[:1].islower()
                    or _ends_with_function_word(prev)
                    or (_is_upper(prev) and _is_upper(nt))
                    or (ln.bold and nl.bold)
                )
                if not cont or prev.rstrip().endswith((".", ";", ":", "!", "?")):
                    break
                title = (title + " " + nt).strip()
                prev = nt
                lines_used += 1
            if lines_used > 1:
                meta.multiline_headings.append({"page": ln.page, "text": f"{stripped} … ({lines_used} lignes)"})
            fn.flush(out)
            label = _heading_label(hkind, m.group(1))
            out += ["", f"{'#' * level} {label}" + (f" : {title}" if title else ""), ""]
            meta.headings.append({"level": level, "kind": hkind, "label": label,
                                  "title": title, "pdf_page": ln.page})
            i += lines_used
            continue

        # ---- article --------------------------------------------------------------
        lead = re.match(r"^((?:\[\^fn\d+\])+)", text)
        am = ARTICLE_START.match(stripped)
        if am:
            aid = article_id(am.group(1))
            if last_art is not None and article_sort_key(aid) <= article_sort_key(last_art):
                meta.warnings.append(f"page {ln.page}: article {aid} après {last_art} (ordre non croissant)")
            last_art = aid
            fn.flush(out)
            rest = am.group(2).strip()
            label = f"**Art. {aid} :**" + (lead.group(1) if lead else "")
            out += ["", f"<!--art:{aid}-->", f"{label} {rest}".rstrip()]
            meta.articles.append({"id": aid, "pdf_page": ln.page})
            i += 1
            continue

        # ---- sous-titre (I - …, A - …) suivi d'un article ou d'un autre sous-titre --
        if SUBHEADING.match(stripped) and not stripped.endswith((";", ".", ",")):
            nl = next_line(i + 1)
            if nl and (ARTICLE_START.match(_strip_fn(nl.text)) or SUBHEADING.match(_strip_fn(nl.text))
                       or _match_heading(nl.text)):
                out += ["", f"**{stripped}**", ""]
                i += 1
                continue

        # ---- texte courant ---------------------------------------------------------
        if ln.gap_before > cfg.gap_factor * stats.median_leading:
            out.append("")
        out.append(text)
        i += 1

    fn.flush(out)
    fn.report_orphans()
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Stratégie RESCRITS
# --------------------------------------------------------------------------
def build_rescrits_md(pages: list[PageData], stats: DocStats, cfg: LayoutCfg,
                      meta: BuildMeta) -> str:
    out: list[str] = []
    fn = FootnoteResolver(pages, meta)
    lines: list[Ln] = []
    for p in pages:
        meta.pages.append({"pdf_page": p.pdf_page, "printed_page": p.printed_page,
                           "n_lines": len(p.lines), "dropped": len(p.dropped)})
        lines += p.lines
    last_page = None
    title_done = False
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.page != last_page:
            out.append(f"<!--pg:{ln.page}-->")
            last_page = ln.page
        text = fn.resolve(ln.text, ln.page)
        if not title_done and not RESCRIT_START.match(text) and _is_upper(text):
            out += [f"# {text}", ""]
            title_done = True
            i += 1
            continue
        m = RESCRIT_START.match(text)
        if m:
            title = m.group(2).strip()
            used = 1
            # Un titre de rescrit peut passer sur 2 lignes : ligne pleine ou gras + gras.
            while used < 3 and i + used < len(lines):
                nl = lines[i + used]
                prev_full = len(text if used == 1 else lines[i + used - 1].text) >= 0.9 * stats.p95_len
                if RESCRIT_START.match(nl.text) or nl.page != ln.page:
                    break
                if (ln.bold and nl.bold) or (prev_full and nl.text[:1].islower()) or \
                        (prev_full and _ends_with_function_word(lines[i + used - 1].text)):
                    title += " " + nl.text.strip()
                    used += 1
                else:
                    break
            if used > 1:
                meta.multiline_headings.append({"page": ln.page, "text": f"R{m.group(1)} ({used} lignes)"})
            out += ["", f"## R{m.group(1)} : {title}", ""]
            meta.rescrits.append({"id": m.group(1), "title": title, "pdf_page": ln.page})
            i += used
            continue
        if ln.gap_before > cfg.gap_factor * stats.median_leading:
            out.append("")
        out.append(text)
        i += 1
    fn.flush(out)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Stratégie GENERIC (cahiers fiscaux : à adapter après inspection)
# --------------------------------------------------------------------------
def _md_table(rows: list[list[str]]) -> list[str]:
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    esc = lambda c: c.replace("|", "\\|")
    lines = ["| " + " | ".join(esc(c) for c in rows[0]) + " |",
             "| " + " | ".join("---" for _ in range(w)) + " |"]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows[1:]]
    return lines


def build_generic_md(doc: pymupdf.Document, pages: list[PageData], stats: DocStats,
                     cfg: LayoutCfg, meta: BuildMeta, use_tables: bool = True) -> str:
    out: list[str] = []
    big = sorted({round(l.size, 1) for p in pages for l in p.lines
                  if l.size >= 1.15 * stats.body_size}, reverse=True)
    level_of = {s: min(i + 1, 4) for i, s in enumerate(big)}
    for p in pages:
        meta.pages.append({"pdf_page": p.pdf_page, "printed_page": p.printed_page,
                           "n_lines": len(p.lines), "dropped": len(p.dropped)})
        out.append(f"<!--pg:{p.pdf_page}-->")
        tables = page_tables(doc[p.pdf_page - 1]) if use_tables else []
        events: list[tuple[float, str, object]] = []
        for l in p.lines:
            if any(b[0] - 2 <= l.x0 and l.x1 <= b[2] + 2 and b[1] - 2 <= l.cy <= b[3] + 2 for b, _ in tables):
                continue
            events.append((l.y0, "ln", l))
        for b, rows in tables:
            events.append((b[1], "tb", rows))
        events.sort(key=lambda e: e[0])
        for _, k, payload in events:
            if k == "tb":
                out += [""] + _md_table(payload) + [""]  # type: ignore[arg-type]
                continue
            l: Ln = payload  # type: ignore[assignment]
            text = FN_PLACEHOLDER.sub("", l.text)
            if l.size in level_of and len(text) <= 140 and not text.endswith("."):
                out += ["", f"{'#' * level_of[l.size]} {text}", ""]
            elif l.bold and len(text) <= 100 and not text.endswith((".", ";", ",")) and l.size >= stats.body_size - 0.5:
                out += ["", f"**{text}**", ""]
            else:
                if l.gap_before > cfg.gap_factor * stats.median_leading:
                    out.append("")
                out.append(text)
        for n, t in p.footnotes.items():
            out.append(f"> note {n} : {t}")
    return "\n".join(out) + "\n"
