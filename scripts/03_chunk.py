"""Étape 3 - Markdown propre -> chunks (JSONL) + rapport.

Entrées (data/interim/) : <stem>.clean.md, et si présents <stem>.extract.json (pages imprimées)
                          et <stem>.orphan_notes.json (notes sans appel détecté).
Sorties : data/chunks/<stem>.jsonl  (un chunk JSON par ligne)
          data/reports/chunks_<stem>.json

Usage :
    python scripts/03_chunk.py
    python scripts/03_chunk.py --stem cgi_2023 --show 3         # affiche 3 chunks
    python scripts/03_chunk.py --max-chars 2400 --min-chars 200
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from otr_rag.chunking import chunk_document  
from otr_rag.cleaning import split_front_matter  
from otr_rag.config import load_manifest  


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stem", nargs="*")
    ap.add_argument("--max-chars", type=int, default=3000, help="taille max d'un chunk (~750 tokens)")
    ap.add_argument("--min-chars", type=int, default=250)
    ap.add_argument("--show", type=int, default=0, help="afficher N chunks (les plus longs découpés)")
    ap.add_argument("--manifest", default=None)
    a = ap.parse_args()

    manifest = load_manifest(a.manifest)
    interim = ROOT / manifest["interim_dir"]
    out_dir = ROOT / manifest.get("chunks_dir", "data/chunks")
    rep_dir = ROOT / manifest.get("reports_dir", "data/reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    rep_dir.mkdir(parents=True, exist_ok=True)

    cleans = sorted(interim.glob("*.clean.md"))
    if a.stem:
        cleans = [p for p in cleans if p.name[: -len(".clean.md")] in a.stem]
    if not cleans:
        sys.exit("Aucun .clean.md trouvé : lance d'abord 01_extract.py puis 02_clean.py")

    total = 0
    for path in cleans:
        stem = path.name[: -len(".clean.md")]
        front, body = split_front_matter(path.read_text(encoding="utf-8"))

        page_map: dict[int, int | None] = {}
        ej = interim / f"{stem}.extract.json"
        if ej.exists():
            page_map = {p["pdf"]: p.get("printed") for p in json.loads(ej.read_text(encoding="utf-8")).get("page_map", [])}
        orphans = []
        oj = interim / f"{stem}.orphan_notes.json"
        if oj.exists():
            orphans = json.loads(oj.read_text(encoding="utf-8"))

        chunks, st = chunk_document(stem, front, body, a.max_chars, a.min_chars, page_map, orphans)
        with open(out_dir / f"{stem}.jsonl", "w", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        (rep_dir / f"chunks_{stem}.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        total += len(chunks)

        print(f"\n=== {stem} ({st['doc_type']})")
        if not chunks:
            print("    ⚠ aucun chunk : document vide ou scanné (OCR requis) ?")
            continue
        print(f"    {st['n_chunks']} chunks pour {st['n_units']} unités "
              f"({st['units_split']} unités découpées) ; taille (caractères) : {st['chars']}")
        print(f"    métadonnées : {st['with_refs']} avec renvois, {st['with_amended_by']} avec loi modificatrice, "
              f"{st['with_page_notes']} avec notes de la page")
        flags = []
        if st["chunks_over_max"]:
            flags.append(f"{st['chunks_over_max']} chunks trop grands")
        if st["empty_units"]:
            flags.append(f"{st['empty_units']} unités vides")
        if st["doc_type"] == "code" and st["text_outside_units_chars"] > 5000:
            flags.append(f"{st['text_outside_units_chars']} caractères hors de tout article (sommaire/couverture non exclus ?)")
        print("    ⚠ À REGARDER : " + " ; ".join(flags) if flags else "    ✓ contrôles OK")

        if a.show:
            multi = [c for c in chunks if c["n_parts"] > 1 and c["part"] == 1] or chunks
            for c in multi[: a.show]:
                print(f"\n--- {c['chunk_id']}  pages {c['page_start']}-{c['page_end']}  {c['n_chars']} car.")
                print(f"contexte : {c['context']}")
                print(f"amended_by : {[n.get('law') or n['text'][:50] for n in c['amended_by']]}")
                print(f"refs : {c['refs'][:3]}")
                print(c["text"][:400] + (" …" if len(c["text"]) > 400 else ""))
    print(f"\n→ {total} chunks écrits dans {out_dir}")


if __name__ == "__main__":
    main()
