"""Nettoyage du Markdown brut -> Markdown propre + rapport de contrôle.

Règles issues de ce qu'on voit dans le texte réel des PDF OTR :

* les césures de fin de ligne ("ci-" / "dessous", "exemp-" / "tion") : on garde
  le trait d'union pour les composés français (ci-dessous, eux-mêmes, N°2022-022,
  Etats-Unis…), on le supprime pour une vraie coupure de mot ;
* des lignes de ponctuation isolées (":" ou ";" seuls sur une ligne) ;
* des puces de plusieurs formes (₋, •, …) ;
* des barèmes présentés en lignes de texte ("de 900 001 A 3 000 000 3%") -> tableau ;
* des paragraphes qui traversent un saut de page : on garde un marqueur <!--pg:N-->
  DANS le paragraphe (pour retrouver page_debut / page_fin plus tard).
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from .patterns import (ART_MARK, BAREME_LAST, BAREME_ROW, FN_DEF, FN_REF, LIST_START,
                       LONE_PUNCT, MONTHS, PAGE_MARK, TABLE_CAPTION, article_sort_key,
                       extract_refs)

KEEP_PREFIX = {
    "ci", "celui", "celle", "ceux", "celles", "eux", "elles", "lui", "elle", "non", "sous",
    "quasi", "semi", "demi", "mi", "vice", "ex", "anti", "extra", "inter", "intra", "multi",
    "micro", "mini", "auto", "contre", "co", "porte", "peut", "c'est", "n'est",
    "dix", "vingt", "trente", "quarante", "cinquante", "soixante", "quatre", "quatre-vingt",
    "cent", "mille", "deux", "trois", "cinq", "six", "sept", "huit", "neuf",
}
KEEP_SUFFIX = {"même", "mêmes", "ci", "là", "dessus", "dessous", "après", "avant", "delà",
               "dire", "à-dire", "aussi", "mêmes", "joint", "jointe", "contre", "annexé"}

# Mots dont le trait d'union a été perdu par l'extraction ("cidessous", "euxmêmes").
KNOWN_GLUED = [
    (re.compile(r"\bci(dessous|dessus|après|apres|joint|jointe|contre|annexé|annexée|avant)\b", re.I), r"ci-\1"),
    (re.compile(r"\b(eux|elles|lui|elle|moi|soi|nous|vous|toi)(mêmes?)\b", re.I), r"\1-\2"),
]

BULLET_START = re.compile(r"^[•·▪‣₋–—*\uf0b7\u2022\u25cf\u25aa]\s*")
LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)


@dataclass
class CleanReport:
    counters: Counter = field(default_factory=Counter)
    hyphen_decisions: list[dict] = field(default_factory=list)
    samples: dict[str, list[str]] = field(default_factory=dict)

    def note(self, key: str, sample: str = "", limit: int = 8) -> None:
        self.counters[key] += 1
        if sample:
            lst = self.samples.setdefault(key, [])
            if len(lst) < limit:
                lst.append(sample)


# --------------------------------------------------------------------------
# Caractères
# --------------------------------------------------------------------------
def normalize_chars(s: str, rep: CleanReport) -> str:
    s = unicodedata.normalize("NFC", s)
    for k, v in LIGATURES.items():
        if k in s:
            s = s.replace(k, v)
            rep.note("ligature_fixed")
    s = s.translate(ZERO_WIDTH)
    s = s.replace("\u00ad", "")                               # trait d'union conditionnel
    s = re.sub(r"[\u00a0\u202f\u2009\u2007]", " ", s)         # espaces insécables -> espace
    s = s.replace("’", "'").replace("‘", "'").replace("ʼ", "'")
    s = re.sub(r"[ \t]+", " ", s)
    if "\ufffd" in s:
        rep.note("replacement_char", s[:80])
    return s.rstrip()


# --------------------------------------------------------------------------
# Jonction de deux lignes (césures)
# --------------------------------------------------------------------------
def join_pair(acc: str, line: str, rep: CleanReport) -> str:
    if acc.endswith("-") and len(acc) > 1 and acc[-2] != " " and (acc[-2].isalnum()):
        left = acc[:-1]
        first = line.split(" ", 1)[0]
        m = re.search(r"[\wÀ-ÿ']+$", left)
        lw = (m.group(0) if m else "").lower()
        f = first.lower().strip(".,;:)")
        reasons = []
        if first[:1].isupper():
            reasons.append("majuscule")
        if left[-1:].isdigit() or first[:1].isdigit():
            reasons.append("chiffre")
        if lw in KEEP_PREFIX:
            reasons.append("prefixe")
        if f in KEEP_SUFFIX:
            reasons.append("suffixe")
        if reasons:
            rep.note("hyphen_kept")
            rep.hyphen_decisions.append({"action": "gardé", "why": ",".join(reasons),
                                         "text": f"{lw}-|{first}"})
            return acc + line
        rep.note("hyphen_joined")
        rep.hyphen_decisions.append({"action": "recollé", "text": f"{lw}-|{first} -> {lw}{first}"})
        return left + line
    return acc + " " + line


def join_block(items: list[tuple[str, str]], rep: CleanReport) -> tuple[str, list[str]]:
    """items : ('t', ligne) | ('pg', marqueur). Les marqueurs de page restent
    dans le paragraphe, juste avant le premier mot de la nouvelle page. Un marqueur
    qui n'est suivi d'aucun texte est renvoyé à part : il ouvrira le bloc suivant."""
    acc = ""
    held: list[str] = []
    for kind, val in items:
        if kind == "pg":
            held.append(val)
            continue
        if not acc:
            acc = " ".join(held + [val]) if held else val
            held = []
            continue
        if held:
            tokens = " ".join(held)
            if acc.endswith("-") and len(acc) > 1 and acc[-2] != " " and acc[-2].isalnum() and " " in val:
                first, rest = val.split(" ", 1)
                acc = join_pair(acc, first, rep) + f" {tokens} " + rest
            elif acc.endswith("-") and " " not in val:
                acc = join_pair(acc, val, rep) + f" {tokens}"
            else:
                acc = acc + f" {tokens} " + val
            held = []
        else:
            acc = join_pair(acc, val, rep)
    return acc, held


# --------------------------------------------------------------------------
# Texte : corrections de fond
# --------------------------------------------------------------------------
def fix_text(s: str, rep: CleanReport) -> str:
    for pat, repl in KNOWN_GLUED:
        s, n = pat.subn(repl, s)
        if n:
            rep.counters["glued_compound_fixed"] += n
    s, n = re.subn(rf"\b(\d+) (er|ère|ème|èmes|e)\b(?= (?:{MONTHS}|trimestre|alinéa|jour|janv))", r"\1\2", s, flags=re.I)
    if n:
        rep.counters["ordinal_fixed"] += n
    s, n = re.subn(r"\s+([,.])(?=\s|$)", r"\1", s)          # " ," -> ","  (on garde " :" et " ;" à la française)
    s, n2 = re.subn(r"\(\s+", "(", s)
    s, n3 = re.subn(r"\s+\)", ")", s)
    if n + n2 + n3:
        rep.counters["spacing_fixed"] += n + n2 + n3
    return re.sub(r" {2,}", " ", s).strip()


# --------------------------------------------------------------------------
# Barème -> tableau Markdown
# --------------------------------------------------------------------------
def bareme_table(rows: list[str], rep: CleanReport) -> list[str]:
    def clean_num(x: str) -> str:
        return re.sub(r"\s+", " ", x).strip()

    out = ["| Tranche (FCFA) | Taux |", "| --- | --- |"]
    for r in rows:
        m = BAREME_ROW.match(r)
        if m:
            out.append(f"| de {clean_num(m.group(1))} à {clean_num(m.group(2))} | {m.group(3).strip()} |")
            continue
        m = BAREME_LAST.match(r)
        if m:
            out.append(f"| plus de {clean_num(m.group(1))} | {m.group(2).strip()} |")
    rep.note("bareme_table_built", f"{len(rows)} lignes")
    return out


def _is_bareme(line: str) -> bool:
    return bool(BAREME_ROW.match(line) or BAREME_LAST.match(line))


# --------------------------------------------------------------------------
# Reflow
# --------------------------------------------------------------------------
def reflow(lines: list[str], p95_len: float, rep: CleanReport) -> list[str]:
    out: list[str] = []
    buf: list[tuple[str, str]] = []   # ('t'|'pg', valeur)
    pending_pg: list[str] = []
    bareme: list[str] = []
    last_text = ""

    def flush_para() -> None:
        nonlocal buf, last_text
        if buf:
            out.extend(pending_flush())
            text, trailing = join_block(buf, rep)
            out.append(fix_text(text, rep))
            out.append("")
            pending_pg.extend(trailing)
        buf = []
        last_text = ""

    def pending_flush() -> list[str]:
        nonlocal pending_pg
        res = list(pending_pg)
        pending_pg = []
        return res

    def flush_bareme() -> None:
        nonlocal bareme
        if bareme:
            out.append("")
            out.extend(bareme_table(bareme, rep))
            out.append("")
            bareme = []

    def start_para(text: str) -> None:
        nonlocal buf, last_text
        flush_para()
        buf = [("t", text)]
        last_text = text

    for raw in lines:
        line = raw.strip()
        if not line:
            flush_bareme()
            flush_para()
            continue

        # marqueur de page
        if PAGE_MARK.fullmatch(line):
            if buf:
                buf.append(("pg", line))
            else:
                pending_pg.append(line)
            continue

        # ligne de barème
        if _is_bareme(line):
            flush_para()
            bareme.append(line)
            continue
        flush_bareme()

        # ponctuation isolée -> collée à la ligne précédente
        if LONE_PUNCT.match(line):
            if buf:
                idx = max(i for i, (k, _) in enumerate(buf) if k == "t")
                buf[idx] = ("t", buf[idx][1].rstrip() + " " + line.strip())
            rep.note("lone_punct_attached", line)
            continue

        # blocs structurels : on ne les mélange jamais à un paragraphe
        if line.startswith("#") or ART_MARK.fullmatch(line) or FN_DEF.match(line) \
                or re.fullmatch(r"\*\*[^*]+\*\*", line):
            flush_para()
            out.extend(pending_flush())
            out.append(line if not line.startswith("#") else fix_text(line, rep))
            if not ART_MARK.fullmatch(line):
                out.append("")
            continue

        line = BULLET_START.sub("- ", line) if BULLET_START.match(line) and not line.startswith("**") else line
        if line.startswith("- ") and raw.strip()[:1] != "-":
            rep.note("bullet_normalized", raw.strip()[:40])

        new_para = False
        if line.startswith("**Art.") or TABLE_CAPTION.match(line) or LIST_START.match(line):
            new_para = True
        elif buf:
            prev = last_text
            if (prev.endswith((".", "!", "?", ":", ".»", "»")) and len(prev) < 0.9 * p95_len
                    and (line[:1].isupper() or line[:1] in "«(")):
                new_para = True
        if new_para or not buf:
            start_para(line)
        else:
            buf.append(("t", line))
            last_text = line

    flush_bareme()
    flush_para()
    out.extend(pending_flush())
    # compacter les blancs multiples
    compact: list[str] = []
    for l in out:
        if l == "" and compact and compact[-1] == "":
            continue
        compact.append(l)
    # puces consécutives : liste "serrée" (pas de ligne vide entre deux puces)
    tight: list[str] = []
    for idx, l in enumerate(compact):
        if (l == "" and tight and tight[-1].startswith("- ") and idx + 1 < len(compact)
                and compact[idx + 1].startswith("- ")):
            continue
        tight.append(l)
    return tight


# --------------------------------------------------------------------------
# Entrée principale
# --------------------------------------------------------------------------
def split_front_matter(md: str) -> tuple[dict, str]:
    if md.startswith("---\n"):
        end = md.find("\n---\n", 4)
        if end != -1:
            import yaml
            fm = yaml.safe_load(md[4:end]) or {}
            return fm, md[end + 5:]
    return {}, md


def clean_md(raw_md: str) -> tuple[str, CleanReport, dict]:
    rep = CleanReport()
    fm, body = split_front_matter(raw_md)
    lines = [normalize_chars(l, rep) for l in body.split("\n")]
    text_lengths = [len(l) for l in lines if l and not l.startswith(("#", "<!--", "[^", "**"))]
    p95 = float(fm.get("p95_len") or (sorted(text_lengths)[int(0.95 * (len(text_lengths) - 1))] if text_lengths else 80))
    cleaned = reflow(lines, p95, rep)
    return "\n".join(cleaned).strip() + "\n", rep, fm


# --------------------------------------------------------------------------
# Contrôle qualité
# --------------------------------------------------------------------------
def qa_report(md: str, kind: str = "code") -> dict:
    q: dict = {}
    if kind == "code":
        ids = ART_MARK.findall(md)
        q["n_articles"] = len(ids)
        dup = [k for k, c in Counter(ids).items() if c > 1]
        q["duplicate_articles"] = dup
        ints = sorted({int(re.match(r"\d+", i).group(0)) for i in ids if re.match(r"\d+", i)})
        if ints:
            q["first_last"] = [ints[0], ints[-1]]
            q["missing_numbers"] = sorted(set(range(ints[0], ints[-1] + 1)) - set(ints))[:60]
        keys = [article_sort_key(i) for i in ids]
        q["order_violations"] = [ids[i] for i in range(1, len(ids)) if keys[i] <= keys[i - 1]][:30]
        parts = re.split(r"<!--art:[^\s>]+-->", md)[1:]
        lens = [len(PAGE_MARK.sub("", p)) for p in parts]
        if lens:
            q["article_chars"] = {"min": min(lens), "median": int(statistics.median(lens)), "max": max(lens),
                                  "over_3000": sum(1 for x in lens if x > 3000)}
            top = sorted(zip(ids, lens), key=lambda t: -t[1])[:10]
            q["longest_articles"] = [{"art": a, "chars": n} for a, n in top]
        q["headings"] = dict(Counter(len(re.match(r"#+", l).group(0)) for l in md.split("\n") if l.startswith("#")))
    else:
        q["n_rescrits"] = len(re.findall(r"^## R\d+", md, flags=re.M))
    refs_in = FN_REF.findall("\n".join(l for l in md.split("\n") if not FN_DEF.match(l)))
    defs = {m.group(1) for l in md.split("\n") if (m := FN_DEF.match(l))}
    q["footnote_refs"] = len(refs_in)
    q["footnote_defs"] = len(defs)
    q["refs_without_def"] = sorted(set(refs_in) - defs)[:20]
    q["defs_without_ref"] = sorted(defs - set(refs_in))[:20]
    q["page_markers"] = len(PAGE_MARK.findall(md))
    q["tables"] = md.count("| --- |")
    q["private_use_chars"] = sum(1 for c in md if 0xE000 <= ord(c) <= 0xF8FF)
    q["replacement_chars"] = md.count("\ufffd")
    q["very_long_paragraphs"] = sum(1 for l in md.split("\n") if len(l) > 2500)
    q["digit_only_paragraphs"] = sum(1 for l in md.split("\n") if re.fullmatch(r"\d{1,4}", l.strip()))
    body_only = re.sub(r"\*\*Art\. \S+ :\*\*", "", PAGE_MARK.sub("", ART_MARK.sub("", md)))
    refs = extract_refs(body_only)
    q["cross_refs"] = {"total": len(refs), "by_target": dict(Counter(r["target"] or "non précisé" for r in refs))}
    return q
