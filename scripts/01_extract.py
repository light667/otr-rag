#!/usr/bin/env python3
"""Étape 1 - PDF -> Markdown BRUT (structure conservée, bruit de page retiré).

La stratégie dépend de la nature du document (`doc_type` dans config/docs.yaml) :
  code     : titres LIVRE/TITRE/CHAPITRE/Section/Paragraphe, marqueurs <!--art:N-->,
             notes de bas de page rattachées à leur article, un fichier par code
             si le PDF en contient plusieurs (`subdocs`).
  rescrits : un titre `## R<n> : …` par cas.
  generic  : titres par taille de police, tableaux -> tableaux Markdown (provisoire).

Sorties (data/interim/) :
  <doc_id>[__<CODE>].raw.md            le Markdown brut, avec front matter YAML
  <doc_id>[__<CODE>].extract.json      rapport : titres, articles, notes non résolues, avertissements

Usage :
    python scripts/01_extract.py                    # tous les documents
    python scripts/01_extract.py --doc cgi_2023
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymupdf  # noqa: E402
import yaml  # noqa: E402

from otr_rag.config import load_manifest, resolve_pdf, select_docs  # noqa: E402
from otr_rag.layout import LayoutCfg, extract_pages, scan_document  # noqa: E402
from otr_rag.rawmd import BuildMeta, build_code_md, build_generic_md, build_rescrits_md  # noqa: E402


def front_matter(entry: dict, code: str | None, pdf_name: str, first: int, last: int, stats) -> str:
    fm = {
        "doc_id": entry["doc_id"] + (f"__{code}" if code and entry.get("subdocs") else ""),
        "source_doc_id": entry["doc_id"],
        "doc_type": entry["doc_type"],
        "code": code,
        "version_year": entry.get("version_year"),
        "published": entry.get("published"),
        "modified": entry.get("modified"),
        "source_url": entry.get("source_url"),
        "pdf_file": pdf_name,
        "pdf_pages": [first, last],
        "body_size_pt": stats.body_size,
        "p95_len": round(stats.p95_len),
    }
    fm = {k: v for k, v in fm.items() if v is not None}
    return "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n"


def extract_entry(entry: dict, manifest: dict) -> None:
    raw_dir = ROOT / manifest["raw_dir"]
    out_dir = ROOT / manifest["interim_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = resolve_pdf(entry, raw_dir)
    doc = pymupdf.open(pdf)
    cfg = LayoutCfg.from_dict(entry.get("layout"))
    n = len(doc)

    stats = scan_document(doc, cfg)
    print(f"\n=== {entry['doc_id']} ({pdf.name}, {n} pages) - stratégie : {entry['doc_type']}")
    print(f"    corps {stats.body_size} pt ; en-têtes répétés retirés : {sorted(stats.repeated) or 'aucun'}")

    # découpage en sous-documents (ex. CGI + LPF dans un même PDF)
    subdocs = entry.get("subdocs") or [{"code": entry.get("code"), "first_page": 1}]
    subdocs = [s for s in subdocs if s.get("first_page")]
    bounds = []
    for i, s in enumerate(subdocs):
        last = (subdocs[i + 1]["first_page"] - 1) if i + 1 < len(subdocs) else n
        bounds.append((s.get("code"), s["first_page"], last))
    if entry["doc_type"] == "code" and len(bounds) == 1 and "cgi_lpf" in entry["doc_id"]:
        print("    ⚠ un seul code déclaré alors que ce PDF contient CGI + LPF : "
              "les numéros d'article vont se chevaucher. Renseigne `subdocs` (voir 00_inspect).")

    for code, first, last in bounds:
        pages, _ = extract_pages(doc, cfg, stats, first, last)
        meta = BuildMeta()
        kind = entry["doc_type"]
        if kind == "code":
            body = build_code_md(pages, stats, cfg, meta)
        elif kind == "rescrits":
            body = build_rescrits_md(pages, stats, cfg, meta)
        else:
            body = build_generic_md(doc, pages, stats, cfg, meta)

        suffix = f"__{code}" if code and entry.get("subdocs") else ""
        stem = f"{entry['doc_id']}{suffix}"
        (out_dir / f"{stem}.raw.md").write_text(
            front_matter(entry, code, pdf.name, first, last, stats) + body, encoding="utf-8")

        methods = Counter(p.footnote_method for p in pages)
        dropped = Counter(reason for p in pages for reason, _ in p.dropped)
        report = {
            "stem": stem, "pdf_pages": [first, last], "n_pages_processed": len(pages),
            "footnote_detection": dict(methods), "dropped_lines": dict(dropped),
            "n_headings": len(meta.headings),
            "headings_by_kind": dict(Counter(h["kind"] for h in meta.headings)),
            "n_articles": len(meta.articles), "n_rescrits": len(meta.rescrits),
            "multiline_headings": meta.multiline_headings[:60],
            "unresolved_footnote_markers": meta.unresolved_markers[:60],
            "orphan_footnote_definitions": meta.orphan_footnotes[:60],
            "warnings": meta.warnings[:100],
            "page_map": [{"pdf": p["pdf_page"], "printed": p.get("printed_page")} for p in meta.pages],
        }
        (out_dir / f"{stem}.extract.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

        print(f"  → {stem}.raw.md  pages {first}-{last}: "
              f"{report['n_headings']} titres, {report['n_articles']} articles, {report['n_rescrits']} rescrits")
        print(f"    retiré des pages : {report['dropped_lines'] or 'rien'} ; notes de bas de page : {report['footnote_detection']}")
        if meta.unresolved_markers:
            print(f"    ⚠ {len(meta.unresolved_markers)} appels de note sans note correspondante")
        if meta.orphan_footnotes:
            print(f"    ⚠ {len(meta.orphan_footnotes)} notes sans appel dans le texte")
        for wmsg in meta.warnings[:5]:
            print(f"    ⚠ {wmsg}")
        if len(meta.warnings) > 5:
            print(f"    ⚠ … {len(meta.warnings) - 5} autres avertissements dans {stem}.extract.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc", nargs="*")
    ap.add_argument("--manifest", default=None)
    a = ap.parse_args()
    manifest = load_manifest(a.manifest)
    for entry in select_docs(manifest, a.doc):
        try:
            extract_entry(entry, manifest)
        except FileNotFoundError as e:
            print(f"✗ {e}")


if __name__ == "__main__":
    main()
