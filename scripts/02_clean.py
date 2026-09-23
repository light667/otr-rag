#!/usr/bin/env python3
"""Étape 2 - Markdown brut -> Markdown propre + rapport de contrôle qualité.

Remet les lignes en paragraphes, gère les césures, retire les artefacts observés,
transforme les barèmes en tableaux, puis vérifie la structure (numérotation des
articles, notes orphelines, caractères suspects…).

Sorties (data/interim/) :
  <stem>.clean.md
  <stem>.clean.json     compteurs de corrections, décisions de césure, contrôle qualité

Usage :
    python scripts/02_clean.py                 # tous les .raw.md
    python scripts/02_clean.py --stem cgi_2023
    python scripts/02_clean.py --stem cgi_2023 --show 3     # affiche 3 articles avant/après
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from otr_rag.cleaning import clean_md, qa_report, split_front_matter  # noqa: E402
from otr_rag.config import load_manifest  # noqa: E402


def show_articles(raw_body: str, clean_body: str, k: int) -> None:
    def article_map(t: str) -> dict[str, str]:
        parts = re.split(r"<!--art:([^\s>]+)-->", t)
        return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}

    r, c = article_map(raw_body), article_map(clean_body)
    # on montre en priorité les articles où le nettoyage a le plus travaillé
    ranked = sorted(c, key=lambda a: -abs(len(r.get(a, "")) - len(c[a]) - r.get(a, "").count("\n")))
    for a in ranked[:k]:
        print(f"\n----- Art. {a} : BRUT -----\n{r.get(a, '')[:700]}")
        print(f"\n----- Art. {a} : PROPRE -----\n{c[a][:700]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stem", nargs="*", help="ex. cgi_2023 ou cgi_lpf_2025__LPF (défaut : tous)")
    ap.add_argument("--show", type=int, default=0, help="afficher N articles avant/après")
    ap.add_argument("--manifest", default=None)
    a = ap.parse_args()
    manifest = load_manifest(a.manifest)
    interim = ROOT / manifest["interim_dir"]
    raws = sorted(interim.glob("*.raw.md"))
    if a.stem:
        raws = [p for p in raws if p.name[:-len(".raw.md")] in a.stem]
    if not raws:
        sys.exit("Aucun .raw.md trouvé : lance d'abord scripts/01_extract.py")

    for raw_path in raws:
        stem = raw_path.name[:-len(".raw.md")]
        raw = raw_path.read_text(encoding="utf-8")
        clean, rep, fm = clean_md(raw)
        kind = "code" if fm.get("doc_type") == "code" else ("rescrits" if fm.get("doc_type") == "rescrits" else "generic")
        qa = qa_report(clean, "rescrits" if kind == "rescrits" else "code" if kind == "code" else "generic")

        # front matter conservé tel quel
        end = raw.find("\n---\n", 4)
        header = raw[: end + 5] if raw.startswith("---\n") and end != -1 else ""
        (interim / f"{stem}.clean.md").write_text(header + clean, encoding="utf-8")
        report = {
            "stem": stem,
            "corrections": dict(rep.counters),
            "exemples": rep.samples,
            "cesures": rep.hyphen_decisions[:250],
            "qa": qa,
        }
        (interim / f"{stem}.clean.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

        print(f"\n=== {stem}")
        print(f"    corrections : {dict(rep.counters) or 'aucune'}")
        for k, v in qa.items():
            if v not in ([], {}, 0, None):
                print(f"    qa.{k}: {v}")
        flags = []
        if qa.get("missing_numbers"):
            flags.append("numéros d'articles manquants")
        if qa.get("duplicate_articles"):
            flags.append("articles en double")
        if qa.get("order_violations"):
            flags.append("ordre des articles non croissant")
        if qa.get("refs_without_def"):
            flags.append("appels de note sans note")
        if qa.get("digit_only_paragraphs"):
            flags.append("numéros de page restés dans le texte")
        if qa.get("very_long_paragraphs"):
            flags.append("paragraphes anormalement longs (lignes non séparées)")
        if flags:
            print("    ⚠ À REGARDER : " + " ; ".join(flags))
        else:
            print("    ✓ contrôles de structure OK")
        if a.show:
            _, raw_body = split_front_matter(raw)
            _, clean_body = split_front_matter(header + clean)
            show_articles(raw_body, clean_body, a.show)


if __name__ == "__main__":
    main()
