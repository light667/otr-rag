#!/usr/bin/env python3
"""Étape 1b - PDF SCANNÉ -> Markdown brut, via LlamaParse (à la place de 01_extract).

Traite les documents marqués `ocr: llamaparse` dans config/docs.yaml. Produit
data/interim/<doc_id>.raw.md, puis on enchaîne comme d'habitude : 02_clean.py, 03_chunk.py.

Clé API : variable d'environnement LLAMA_CLOUD_API_KEY, ou fichier .env à la racine du projet
(ne la colle jamais dans un fichier versionné ni dans un chat).

Usage :
    python scripts/01b_ocr_llamaparse.py --first 3     # ESSAI : 3 premières pages seulement (peu coûteux)
    python scripts/01b_ocr_llamaparse.py               # document complet
    python scripts/01b_ocr_llamaparse.py --force       # ré-interroge l'API (sinon le résultat en cache est réutilisé)
    python scripts/01b_ocr_llamaparse.py --tier cost_effective
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymupdf  # noqa: E402

from otr_rag.config import load_manifest, resolve_pdf  # noqa: E402
from otr_rag.ocr import build_raw_md, figures_report, load_env, parse_pdf  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc", nargs="*", help="doc_id (défaut : tous les documents `ocr: llamaparse`)")
    ap.add_argument("--tier", default="agentic", choices=["fast", "cost_effective", "agentic", "agentic_plus"])
    ap.add_argument("--lang", default="fr")
    ap.add_argument("--first", type=int, default=0, help="essai sur les N premières pages seulement")
    ap.add_argument("--force", action="store_true", help="ignorer le cache et ré-interroger l'API")
    ap.add_argument("--manifest", default=None)
    a = ap.parse_args()

    load_env(ROOT / ".env")
    manifest = load_manifest(a.manifest)
    docs = [d for d in manifest["docs"] if d.get("ocr") == "llamaparse"]
    if a.doc:
        docs = [d for d in manifest["docs"] if d["doc_id"] in a.doc]
    if not docs:
        sys.exit("Aucun document `ocr: llamaparse` dans le manifest.")

    raw_dir = ROOT / manifest["raw_dir"]
    interim = ROOT / manifest["interim_dir"]
    inspect = ROOT / manifest["inspect_dir"]
    reports = ROOT / manifest["reports_dir"]
    for d in (interim, inspect, reports):
        d.mkdir(parents=True, exist_ok=True)

    for entry in docs:
        pdf = resolve_pdf(entry, raw_dir)
        doc_id = entry["doc_id"]
        print(f"\n=== {doc_id} ({pdf.name}) - OCR LlamaParse, tier {a.tier}, langue {a.lang}")

        if a.first:                                    # ESSAI : petit PDF temporaire, aucune écriture dans interim
            src = pymupdf.open(pdf)
            src.select(list(range(min(a.first, len(src)))))
            with tempfile.TemporaryDirectory() as tmp:
                small = Path(tmp) / f"{doc_id}_first{a.first}.pdf"
                src.save(small)
                pages = parse_pdf(small, a.tier, (a.lang,))
            md, rep = build_raw_md(pages, entry, pdf.name, a.tier)
            out = inspect / f"{doc_id}_ocr_test.md"
            out.write_text(md, encoding="utf-8")
            print(f"    essai : {rep}\n    → {os.path.relpath(out, ROOT)}  (relis-le contre le PDF avant de lancer le document complet)")
            continue

        cache = interim / f"{doc_id}.llamaparse.json"
        if cache.exists() and not a.force:
            pages = json.loads(cache.read_text(encoding="utf-8"))["pages"]
            print(f"    cache réutilisé ({cache.name}) : aucun appel API, aucun crédit consommé")
        else:
            pages = parse_pdf(pdf, a.tier, (a.lang,))
            cache.write_text(json.dumps({"tier": a.tier, "lang": a.lang, "pages": pages},
                                        ensure_ascii=False, indent=1), encoding="utf-8")

        n_pdf = len(pymupdf.open(pdf))
        if len(pages) != n_pdf:
            print(f"    ⚠ {len(pages)} pages reçues pour un PDF de {n_pdf} : les numéros de page seront décalés, à vérifier")

        md, rep = build_raw_md(pages, entry, pdf.name, a.tier)
        (interim / f"{doc_id}.raw.md").write_text(md, encoding="utf-8")
        (reports / f"{doc_id}_ocr_figures.md").write_text(figures_report(pages, doc_id), encoding="utf-8")
        print(f"    → {doc_id}.raw.md : {rep}")
        if rep["empty_pages"]:
            print(f"    ⚠ pages (presque) vides : {rep['empty_pages']} (page de garde ou OCR échoué ?)")
        print(f"    → data/reports/{doc_id}_ocr_figures.md : taux et montants à relire contre le PDF")
        print(f"    Suite : python scripts/02_clean.py --stem {doc_id} && python scripts/03_chunk.py --stem {doc_id}")


if __name__ == "__main__":
    main()
