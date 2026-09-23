from __future__ import annotations

import argparse
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymupdf  # noqa: E402

from otr_rag.config import load_manifest, resolve_pdf, select_docs  # noqa: E402
from otr_rag.layout import FN_PLACEHOLDER, LayoutCfg, page_lines, page_tables, scan_document  # noqa: E402
from otr_rag.patterns import (ARTICLE_START, DIGITS_ONLY, GLUED_MARKER, HEADING_PATTERNS,  # noqa: E402
                              RESCRIT_START, article_id)

TOC_LEADER = re.compile(r"(\.{4,}|…{2,}|(?:\s\.){4,})\s*\d{1,4}\s*$")
CODE_TITLES = [
    ("CGI", re.compile(r"^CODE G[ÉE]N[ÉE]RAL DES IMP[ÔO]TS\s*$", re.I)),
    ("LPF", re.compile(r"^LIVRE DES PROC[ÉE]DURES FISCALES\s*$", re.I)),
]


def pct(vals, q):
    vals = sorted(vals)
    return vals[int(q * (len(vals) - 1))] if vals else None


def inspect_doc(entry: dict, manifest: dict, dump_pages: set[int]) -> str:
    raw_dir = ROOT / manifest["raw_dir"]
    pdf = resolve_pdf(entry, raw_dir)
    doc = pymupdf.open(pdf)
    cfg = LayoutCfg.from_dict(entry.get("layout"))
    n = len(doc)
    R: list[str] = []
    w = R.append

    w(f"# Inspection : {entry['doc_id']}")
    w(f"- fichier : `{pdf.name}` - {n} pages - nature déclarée : **{entry['doc_type']}**")
    meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    w(f"- métadonnées PDF : {meta or 'aucune'}")

    # ---- texte natif ? -------------------------------------------------------
    chars = [len(doc[i].get_text().strip()) for i in range(min(n, 30))]
    avg = statistics.mean(chars) if chars else 0
    w(f"- caractères par page (30 premières) : moyenne {avg:.0f}, min {min(chars) if chars else 0}"
      + ("  ⚠ **très faible : PDF probablement scanné (OCR nécessaire)**" if avg < 200 else "  ✓ texte natif"))

    # ---- statistiques globales ----------------------------------------------
    stats = scan_document(doc, cfg)
    top_sizes = sorted(stats.size_hist.items(), key=lambda kv: -kv[1])[:6]
    total = sum(stats.size_hist.values()) or 1
    w("\n## Polices")
    w("- tailles (points) les plus fréquentes : "
      + ", ".join(f"{s} pt ({100*c/total:.0f} %)" for s, c in top_sizes))
    w(f"- taille du corps retenue : **{stats.body_size} pt** ; longueur de ligne p95 : {stats.p95_len:.0f} caractères ; "
      f"interligne médian : {stats.median_leading:.1f} pt")
    w(f"- lignes répétées dans les marges (en-têtes/pieds détectés) : {sorted(stats.repeated) or 'aucune'}")

    # ---- balayage page par page ----------------------------------------------
    digits_pos = []            # (page, y%, x%)
    foot_pages = Counter()
    foot_samples = []
    sup_marks = 0
    glued = []
    headings = Counter()
    heading_samples = []
    art_seq: list[tuple[int, str]] = []
    toc_pages = []
    rescrits = []
    title_pages = defaultdict(list)
    x_right = 0
    x_total = 0
    gap_big = 0
    gap_all = 0
    dump_out: list[str] = []
    page_tables_found = {}

    table_pages = range(1, n + 1) if (n <= 60 or entry["doc_type"] == "generic") else range(1, 41)

    for i in range(1, n + 1):
        page = doc[i - 1]
        H, W = page.rect.height, page.rect.width
        lines = page_lines(page, i)
        leaders = 0
        for ln in lines:
            t = ln.text
            if DIGITS_ONLY.match(t):
                digits_pos.append((i, ln.cy / H, (ln.x0 + ln.x1) / 2 / W))
            if ln.size <= cfg.footnote_size_ratio * stats.body_size and ln.cy > cfg.footnote_min_y * H:
                foot_pages[i] += 1
                if len(foot_samples) < 6:
                    foot_samples.append(f"p{i} y={ln.cy/H:.0%} {ln.size}pt : {t[:90]}")
            sup_marks += len(FN_PLACEHOLDER.findall(t))
            if GLUED_MARKER.match(t) and not DIGITS_ONLY.match(t):
                glued.append(f"p{i}: {t[:70]}")
            for level, kind, pat in HEADING_PATTERNS:
                if pat.match(t):
                    headings[kind] += 1
                    if len(heading_samples) < 14:
                        heading_samples.append(f"p{i} [{kind}] {t[:90]}")
                    break
            am = ARTICLE_START.match(t)
            if am:
                art_seq.append((i, article_id(am.group(1))))
            if TOC_LEADER.search(t):
                leaders += 1
            m = RESCRIT_START.match(t)
            if m:
                rescrits.append((i, m.group(1), m.group(2)[:70]))
            for code, pat in CODE_TITLES:
                if pat.match(t):
                    title_pages[code].append(i)
            if abs(ln.size - stats.body_size) <= 0.6 and H * cfg.top_zone < ln.cy < H * (1 - cfg.bottom_zone):
                x_total += 1
                if ln.x0 > 0.45 * W:
                    x_right += 1
            if 0 < ln.gap_before < 3 * stats.body_size:
                gap_all += 1
                if ln.gap_before > cfg.gap_factor * stats.median_leading:
                    gap_big += 1
        if leaders >= 8:
            toc_pages.append(i)
        if i in dump_pages:
            dump_out.append(f"\n### Lignes de la page {i} (taille {W:.0f}x{H:.0f} pt)")
            dump_out.append("| y% | x% | pt | B | texte |\n|---|---|---|---|---|")
            for ln in lines:
                dump_out.append(f"| {ln.cy/H:.0%} | {ln.x0/W:.0%} | {ln.size} | {'B' if ln.bold else ''} | "
                                f"{ln.text[:110].replace('|', '¦')} |")

    # ---- numéro de page ---------------------------------------------------------
    w("\n## Numéro de page")
    if digits_pos:
        top = [d for d in digits_pos if d[1] < 0.5]
        bot = [d for d in digits_pos if d[1] >= 0.5]
        ys = [d[1] for d in digits_pos]
        w(f"- lignes 'chiffres seuls' : {len(digits_pos)} sur {n} pages ; "
          f"{len(top)} en haut, {len(bot)} en bas")
        w(f"- hauteur relative (0 = haut de page) : min {min(ys):.0%}, médiane {statistics.median(ys):.0%}, max {max(ys):.0%}")
        inside = [d for d in digits_pos if cfg.top_zone <= d[1] <= 1 - cfg.bottom_zone]
        w(f"- **hors des zones marge actuelles (top_zone={cfg.top_zone}, bottom_zone={cfg.bottom_zone}) : {len(inside)}** "
          + ("✓ tout sera retiré comme numéro de page" if not inside else
             "⚠ ces chiffres resteraient dans le texte : agrandis top_zone/bottom_zone ou vérifie ce sont de vrais numéros"))
        if inside[:5]:
            w("  exemples : " + ", ".join(f"p{p} y={y:.0%}" for p, y, _ in inside[:5]))
    else:
        w("- aucune ligne 'chiffres seuls' trouvée : le numéro de page est peut-être dans une autre forme (ex. « Page 3 »).")

    # ---- notes de bas de page ----------------------------------------------------
    w("\n## Notes de bas de page")
    w(f"- lignes en petite police (≤ {cfg.footnote_size_ratio:.0%} du corps) dans le bas de page : "
      f"{sum(foot_pages.values())} sur {len(foot_pages)} pages")
    for s in foot_samples:
        w(f"  - {s}")
    w(f"- appels de note détectés comme exposants : {sup_marks}")
    w(f"- marqueurs collés au mot suivant (type « 1Toutefois ») : {len(glued)}"
      + ("  → le repli par regex de 01_extract les traitera" if glued else ""))
    for g in glued[:5]:
        w(f"  - {g}")
    if not foot_pages and not sup_marks:
        w("- ⚠ ni notes en petite police ni exposants : soit ce document n'a pas de notes, soit la mise en page "
          "est différente de ce qu'on attend. Vérifie avec --dump sur une page où le CGI cite une loi de finances.")

    # ---- colonnes ------------------------------------------------------------------
    w("\n## Mise en page")
    frac = x_right / x_total if x_total else 0
    w(f"- lignes de corps commençant dans la moitié droite de la page : {frac:.0%}"
      + ("  ⚠ **probable mise en page sur 2 colonnes** : l'ordre de lecture sera faux, dis-le moi" if frac > 0.25 else "  ✓ une colonne"))
    if gap_all:
        w(f"- interlignes supérieurs à {cfg.gap_factor}× la médiane : {gap_big}/{gap_all} ({gap_big/gap_all:.0%}) "
          "→ ce sont les sauts de paragraphe visibles ; 0 % veut dire que les paragraphes ne sont pas séparés dans le PDF")

    # ---- structure ---------------------------------------------------------------------
    if entry["doc_type"] in {"code", "generic"}:
        w("\n## Structure détectée")
        w(f"- titres : {dict(headings) or 'aucun'}")
        for h in heading_samples:
            w(f"  - {h}")
        ids = [a for _, a in art_seq]
        w(f"- articles détectés (`Art. N :` en début de ligne) : **{len(ids)}**")
        if art_seq:
            ints = [(p, int(re.match(r"\d+", a).group(0))) for p, a in art_seq if re.match(r"\d+", a)]
            resets = [(p1, a1, p2, a2) for (p1, a1), (p2, a2) in zip(ints, ints[1:]) if a1 >= 20 and a2 <= 3]
            nums = [a for _, a in ints]
            missing = sorted(set(range(min(nums), max(nums) + 1)) - set(nums))
            w(f"- premier / dernier : Art. {art_seq[0][1]} (p{art_seq[0][0]}) / Art. {art_seq[-1][1]} (p{art_seq[-1][0]})")
            w(f"- numéros manquants : {missing[:40]}{' …' if len(missing) > 40 else ''}"
              + ("  ✓ numérotation continue" if not missing else "  ⚠ vérifie : article non détecté ou vraie lacune"))
            dup = [k for k, c in Counter(ids).items() if c > 1]
            w(f"- doublons : {dup[:20] or 'aucun'}")
            if resets:
                p1, a1, p2, a2 = resets[0]
                w(f"- ⚠ **la numérotation retombe** (Art. {a1} p{p1} → Art. {a2} p{p2}) : "
                  f"début probable d'un SECOND code dans ce PDF, page **{p2}**. "
                  f"→ renseigne `subdocs` dans config/docs.yaml avec first_page: {p2}")
        for code, pgs in title_pages.items():
            w(f"- titre « {code} » vu seul sur une ligne en pages : {pgs[:12]}")
        if toc_pages:
            w(f"- ⚠ pages ressemblant à un SOMMAIRE (lignes à points de conduite + n° de page) : {toc_pages[:20]} "
              "→ à mettre dans `layout.skip_pages`, sinon chaque titre apparaîtra deux fois")
    if entry["doc_type"] == "rescrits" or rescrits:
        w("\n## Rescrits détectés")
        w(f"- {len(rescrits)} titres `R<n> : …`")
        for p, num, t in rescrits[:12]:
            w(f"  - p{p} R{num} : {t}")
        nums = [int(r[1]) for r in rescrits]
        if nums and nums != list(range(nums[0], nums[0] + len(nums))):
            w(f"  ⚠ numérotation non continue : {nums}")

    # ---- tableaux --------------------------------------------------------------------------
    w("\n## Tableaux (détection PyMuPDF)")
    for i in table_pages:
        try:
            t = page_tables(doc[i - 1])
        except Exception:
            t = []
        if t:
            page_tables_found[i] = t
    w(f"- pages avec tableau détecté : {sorted(page_tables_found)[:40] or 'aucune'}"
      + ("" if len(list(table_pages)) == n else f" (pages 1-{max(table_pages)} seulement)"))
    for i, t in list(page_tables_found.items())[:3]:
        bbox, rows = t[0]
        w(f"  - p{i} : {len(rows)} lignes × {max(len(r) for r in rows)} col. ; 1re ligne : {rows[0]}")
    if not page_tables_found:
        w("  (un barème sans lignes de grille n'est pas détecté ici ; 02_clean le reconnaît par sa forme de texte)")

    R += dump_out
    return "\n".join(R) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc", nargs="*", help="doc_id à inspecter (défaut : tous)")
    ap.add_argument("--dump", default="", help="pages à lister ligne par ligne, ex. 1,12,40")
    ap.add_argument("--manifest", default=None)
    a = ap.parse_args()
    manifest = load_manifest(a.manifest)
    out_dir = ROOT / manifest["inspect_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    dump = {int(x) for x in a.dump.split(",") if x.strip()}
    for entry in select_docs(manifest, a.doc):
        try:
            report = inspect_doc(entry, manifest, dump)
        except FileNotFoundError as e:
            print(f"✗ {e}\n")
            continue
        path = out_dir / f"{entry['doc_id']}.md"
        path.write_text(report, encoding="utf-8")
        print(report)
        print(f"→ rapport écrit : {os.path.relpath(path, ROOT)}\n" + "-" * 70)


if __name__ == "__main__":
    main()
