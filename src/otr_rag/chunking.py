"""Chunking structuré du Markdown propre (sortie de 02_clean).

Une stratégie par nature de document, comme pour l'extraction :

* `code`     : UN ARTICLE = UNE UNITÉ (c'est l'unité de citation). Un article trop long est
               découpé par alinéa, puis par phrase, sans jamais couper un tableau. La hiérarchie
               (Livre > Partie > Titre > Chapitre > Section > Paragraphe) est portée en métadonnée
               ET répétée dans `embed_text` (contexte pour l'embedding).
* `rescrits` : un chunk par rescrit `R<n>`.
* `generic`  : un chunk par section (titre) ou par sous-titre gras ; les mini-sections sont
               fusionnées avec la suivante.

Chaque chunk a :
  text        texte pur (sans marqueurs, sans appels de note) : c'est ce qu'on cite à l'utilisateur ;
  embed_text  contexte + texte : c'est ce qu'on envoie au modèle d'embedding ;
  amended_by  lois de finances qui ont modifié l'article (notes dont l'appel a été retrouvé) ;
  page_notes  notes de bas de page des MÊMES pages, dont l'appel n'a pas été retrouvé (rattachement
              approximatif, à présenter comme tel).
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from .cleaning import split_front_matter
from .patterns import ART_MARK, FN_DEF, PAGE_MARK, SUBHEADING, extract_refs

LEVEL_KIND = {1: "livre", 2: "partie", 3: "titre", 4: "chapitre", 5: "section", 6: "paragraphe"}
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
BOLD_LINE = re.compile(r"^\*\*(.+?)\*\*\s*$")
RESCRIT_HEADING = re.compile(r"^R\s?(\d{1,3})\s*[:\-–]\s*(.+)$")
FN_REF_ANY = re.compile(r"\[\^fn\d+\]")
LAW = re.compile(r"\b(Loi|Ordonnance|D[ée]cret|Arr[êe]t[ée])\s+N°\s*([\w./-]+?)\s+du\s+"
                 r"(\d{1,2}(?:er)?\s+[A-Za-zéèûôàç]+\s+\d{4})", re.I)
EXERCICE = re.compile(r"Exercice\s+(\d{4})", re.I)


# --------------------------------------------------------------------------
# Texte
# --------------------------------------------------------------------------
def clean_text(s: str) -> str:
    """Texte pur : ni marqueurs de page, ni appels de note, ni gras Markdown."""
    s = PAGE_MARK.sub("", s)
    s = FN_REF_ANY.sub("", s)
    s = s.replace("**", "")
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r" +\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# --------------------------------------------------------------------------
# Découpage d'un texte trop long : alinéa -> ligne -> phrase -> mot
# --------------------------------------------------------------------------
_SENT = re.compile(r"(?<=[.;:!?])\s+(?=[«(\"A-ZÀ-ÖØ-Þ0-9-])")


def _hard_split(s: str, max_chars: int) -> list[str]:
    out = []
    while len(s) > max_chars:
        cut = s.rfind(" ", 0, max_chars)
        if cut < max_chars * 0.5:
            cut = max_chars
        out.append(s[:cut].strip())
        s = s[cut:].strip()
    if s:
        out.append(s)
    return out


def _pack(units: list[str], sep: str, max_chars: int, splitter) -> list[str]:
    """Assemble les unités jusqu'à max_chars ; une unité trop grande est découpée par `splitter`."""
    out: list[str] = []
    cur = ""
    for u in units:
        for piece in ([u] if len(u) <= max_chars else splitter(u, max_chars)):
            if cur and len(cur) + len(sep) + len(piece) > max_chars:
                out.append(cur)
                cur = piece
            else:
                cur = piece if not cur else cur + sep + piece
    if cur:
        out.append(cur)
    return out


def _split_sentences(s: str, max_chars: int) -> list[str]:
    return _pack(_SENT.split(s), " ", max_chars, _hard_split)


def _split_table(block: str, max_chars: int) -> list[str]:
    """Tableau trop grand : on découpe par lignes en RÉPÉTANT l'en-tête dans chaque morceau."""
    lines = block.split("\n")
    head, rows = lines[:2], lines[2:]
    hlen = len("\n".join(head)) + 1
    out, cur, size = [], [], hlen
    for r in rows:
        if cur and size + len(r) + 1 > max_chars:
            out.append("\n".join(head + cur))
            cur, size = [], hlen
        cur.append(r)
        size += len(r) + 1
    if cur:
        out.append("\n".join(head + cur))
    return out


def _split_block(block: str, max_chars: int) -> list[str]:
    if block.startswith("|") and block.count("\n") >= 2:
        return _split_table(block, max_chars)
    lines = block.split("\n")
    if len(lines) == 1:
        return _split_sentences(block, max_chars)
    return _pack(lines, "\n", max_chars, _split_sentences)


def split_text(text: str, max_chars: int, min_chars: int) -> list[str]:
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", text) if b.strip()]
    parts = _pack(blocks, "\n\n", max_chars, _split_block)
    if len(parts) > 1 and len(parts[-1]) < min_chars and len(parts[-2]) + 2 + len(parts[-1]) <= max_chars * 1.25:
        parts[-2:] = [parts[-2] + "\n\n" + parts[-1]]     # pas de mini-chunk final orphelin
    return parts


# --------------------------------------------------------------------------
# Lecture du Markdown : unités (article / rescrit / section)
# --------------------------------------------------------------------------
@dataclass
class Unit:
    kind: str                       # article | rescrit | section
    key: str = ""                   # n° d'article ou de rescrit
    title: str = ""
    hierarchy: dict = field(default_factory=dict)
    lines: list = field(default_factory=list)
    footnotes: dict = field(default_factory=dict)
    page_start: int = 1
    page_end: int = 1

    def body(self) -> str:
        return clean_text("\n".join(self.lines))


def parse_units(body: str, doc_type: str, first_page: int = 1) -> tuple[list[Unit], int]:
    """-> (unités, nb de caractères de texte hors de toute unité : à surveiller sur un code)."""
    is_code, is_resc = doc_type == "code", doc_type == "rescrits"
    units: list[Unit] = []
    levels: dict[int, str] = {}
    sub: str | None = None    # sous-titre "I - ...", "II- ...", "A-..." : voir plus bas
    cur: Unit | None = None
    page = first_page
    outside = 0

    def snapshot() -> dict:
        if is_code:
            d = {LEVEL_KIND.get(l, f"h{l}"): t for l, t in sorted(levels.items())}
        else:
            d = {f"h{l}": t for l, t in sorted(levels.items())}
        if sub:
            d["sous"] = sub
        return d

    def close() -> None:
        nonlocal cur
        if cur is not None:
            units.append(cur)
            cur = None

    for raw in body.split("\n"):
        s = raw.strip()
        if not s:
            if cur is not None:
                cur.lines.append("")
            continue
        pms = PAGE_MARK.findall(s)
        if pms and PAGE_MARK.fullmatch(s):
            page = int(pms[0])
            continue

        hm = HEADING.match(s)
        if hm:
            level, text = len(hm.group(1)), hm.group(2).strip()
            if is_resc and level >= 2:
                rm = RESCRIT_HEADING.match(text)
                if rm:
                    close()
                    cur = Unit("rescrit", rm.group(1), rm.group(2).strip(), snapshot(), [], {}, page, page)
                    continue
            close()
            levels[level] = text
            for deeper in [l for l in levels if l > level]:
                del levels[deeper]
            sub = None                      # un nouveau titre efface le sous-titre en cours
            if not is_code and not is_resc:
                cur = Unit("section", "", "", snapshot(), [], {}, page, page)
            continue

        if is_code:
            am = ART_MARK.fullmatch(s)
            if am:
                close()
                cur = Unit("article", am.group(1), "", snapshot(), [], {}, page, page)
                continue
            bm = BOLD_LINE.fullmatch(s)
            if bm and SUBHEADING.match(bm.group(1).strip()):
                # "I - Définition...", "II- Exonérations"... : un sous-titre s'applique aux
                # articles qui SUIVENT, pas à celui qui précède (avant ce correctif, ce texte
                # restait collé en fin de l'article précédent : "II- Exonérations" à la fin de
                # l'article 14 au lieu d'être une métadonnée de l'article 15).
                close()
                sub = bm.group(1).strip()
                continue

        fm = FN_DEF.match(s)
        if fm:
            if cur is not None:
                cur.footnotes[fm.group(1)] = fm.group(2).strip()
            continue

        if not is_code and not is_resc:
            bm = BOLD_LINE.fullmatch(s)
            if bm:                                     # sous-titre gras : nouvelle unité
                hier = cur.hierarchy if cur else snapshot()
                close()
                cur = Unit("section", "", bm.group(1).strip(), dict(hier), [], {}, page, page)
                continue
            if cur is None:
                cur = Unit("section", "", "", snapshot(), [], {}, page, page)

        if cur is None:
            outside += len(s)
            continue
        cur.lines.append(raw.rstrip())
        if pms:
            page = int(pms[-1])
        cur.page_end = max(cur.page_end, page)
    close()
    return units, outside


def merge_small_units(units: list[Unit], min_chars: int) -> list[Unit]:
    """Documents narratifs : une unité minuscule (ex. une phrase d'introduction) est fusionnée
    avec la suivante de même hiérarchie ; les unités sans texte (titre seul) sont écartées."""
    out: list[Unit] = []
    carry: Unit | None = None
    for u in units:
        if not u.body():
            continue
        if carry is not None:
            if carry.hierarchy == u.hierarchy:
                u.lines = carry.lines + [""] + u.lines
                u.page_start = min(carry.page_start, u.page_start)
            else:
                out.append(carry)
            carry = None
        if len(u.body()) < min_chars:
            carry = u
        else:
            out.append(u)
    if carry is not None:
        if out and out[-1].hierarchy == carry.hierarchy:
            out[-1].lines += [""] + carry.lines
            out[-1].page_end = max(out[-1].page_end, carry.page_end)
        else:
            out.append(carry)
    return out


# --------------------------------------------------------------------------
# Métadonnées
# --------------------------------------------------------------------------
def parse_note(text: str) -> dict:
    d: dict = {"text": text[:400]}
    m = LAW.search(text)
    if m:
        d["law"] = f"{m.group(1).capitalize()} N°{m.group(2)}"
        d["date"] = m.group(3)
    ex = EXERCICE.search(text)
    if ex:
        d["exercice"] = int(ex.group(1))
    return d


_OWN_LABEL = re.compile(r"^Art(?:icle)?s?\.?\s*\S+\s*:\s*")


def normalize_refs(text: str, own_code: str | None) -> list[dict]:
    out, seen = [], set()
    # L'étiquette "Art. 17 :" en tête d'un article n'est pas un renvoi : sans ce retrait, chaque
    # chunk se citerait lui-même.
    for r in extract_refs(_OWN_LABEL.sub("", text, count=1)):
        assumed = r["target"] in (None, "self")
        code = own_code if assumed else r["target"]
        key = (code, tuple(r["nums"]), r["range"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"code": code, "articles": r["nums"], "range": r["range"], "assumed": assumed})
    return out


def doc_label(front: dict) -> str:
    dt = front.get("doc_type")
    if dt == "code":
        return " ".join(str(x) for x in (front.get("code"), front.get("version_year")) if x)
    if dt == "rescrits":
        return "Rescrits fiscaux OTR"
    return str(front.get("doc_id", "")).split("__")[0].replace("_", " ").title()


def build_context(label: str, u: Unit, part: int, n_parts: int) -> str:
    hier = " > ".join(u.hierarchy.values())
    if u.kind == "article":
        tail = f"Art. {u.key}"
    elif u.kind == "rescrit":
        tail = f"R{u.key} : {u.title}"
    else:
        tail = u.title
    bits = [label, hier, tail]
    ctx = " | ".join(b for b in bits if b)
    return ctx + (f" (partie {part + 1}/{n_parts})" if n_parts > 1 else "")


# --------------------------------------------------------------------------
# Document -> chunks
# --------------------------------------------------------------------------
def chunk_document(stem: str, front: dict, body: str, max_chars: int = 3000, min_chars: int = 250,
                   page_map: dict[int, int | None] | None = None,
                   orphan_notes: list[dict] | None = None) -> tuple[list[dict], dict]:
    doc_type = front.get("doc_type", "generic")
    first_page = (front.get("pdf_pages") or [1])[0]
    units, outside = parse_units(body, doc_type, first_page)
    if doc_type not in ("code", "rescrits"):
        units = merge_small_units(units, min_chars)

    page_map = page_map or {}
    label = doc_label(front)
    own_code = front.get("code")
    orphans_by_page: dict[int, list[dict]] = {}
    for n in orphan_notes or []:
        orphans_by_page.setdefault(int(n["page"]), []).append(n)

    chunks: list[dict] = []
    seen_keys: Counter = Counter()
    empty_units = 0
    split_units = 0
    for u in units:
        text = u.body()
        if not text:
            empty_units += 1
            continue
        parts = split_text(text, max_chars, min_chars)
        split_units += len(parts) > 1
        uid = u.key or re.sub(r"\W+", "-", u.title.lower()).strip("-") or f"s{len(chunks)}"
        seen_keys[(u.kind, uid)] += 1
        if seen_keys[(u.kind, uid)] > 1:
            uid = f"{uid}-dup{seen_keys[(u.kind, uid)]}"

        amended, seen_txt = [], set()
        for t in u.footnotes.values():
            if t not in seen_txt:
                seen_txt.add(t)
                amended.append(parse_note(t))
        page_notes, seen_pn = [], set()
        for pg in range(u.page_start, u.page_end + 1):
            for n in orphans_by_page.get(pg, []):
                if (pg, n["text"]) not in seen_pn and n["text"] not in seen_txt:
                    seen_pn.add((pg, n["text"]))
                    page_notes.append({"page": pg, **parse_note(n["text"])})

        for k, part in enumerate(parts):
            ctx = build_context(label, u, k, len(parts))
            chunks.append({
                "chunk_id": f"{stem}::{u.kind[:3]}{uid}::{k}",
                "doc_id": front.get("doc_id", stem),
                "source_doc_id": front.get("source_doc_id"),
                "doc_type": doc_type,
                "code": own_code,
                "version_year": front.get("version_year"),
                "published": front.get("published"),
                "source_url": front.get("source_url"),
                "unit": u.kind,
                "article": u.key if u.kind == "article" else None,
                "rescrit": u.key if u.kind == "rescrit" else None,
                "title": u.title or None,
                "hierarchy": u.hierarchy,
                "part": k,
                "n_parts": len(parts),
                "page_start": u.page_start,
                "page_end": u.page_end,
                "printed_page_start": page_map.get(u.page_start),
                "printed_page_end": page_map.get(u.page_end),
                "refs": normalize_refs(part, own_code),
                "amended_by": amended,
                "page_notes": page_notes,
                "context": ctx,
                "text": part,
                "embed_text": ctx + "\n\n" + part,
                "n_chars": len(part),
                "tokens_est": len(part) // 4,
            })

    sizes = [c["n_chars"] for c in chunks]
    stats = {
        "stem": stem, "doc_type": doc_type, "n_units": len(units), "n_chunks": len(chunks),
        "units_split": split_units, "empty_units": empty_units,
        "text_outside_units_chars": outside,
        "chars": ({"min": min(sizes), "median": int(statistics.median(sizes)),
                   "p95": sorted(sizes)[int(0.95 * (len(sizes) - 1))], "max": max(sizes)} if sizes else {}),
        "chunks_over_max": sum(1 for x in sizes if x > max_chars * 1.25),
        "chunks_under_min": sum(1 for c in chunks if c["n_chars"] < min_chars and c["n_parts"] == 1),
        "with_amended_by": sum(1 for c in chunks if c["amended_by"]),
        "with_page_notes": sum(1 for c in chunks if c["page_notes"]),
        "with_refs": sum(1 for c in chunks if c["refs"]),
    }
    return chunks, stats
