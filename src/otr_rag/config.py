from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_manifest(path: str | Path | None = None) -> dict:
    path = Path(path) if path else ROOT / "config" / "docs.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_pdf(entry: dict, raw_dir: Path) -> Path:
    """Trouve le PDF d'une entrée du manifest : nom exact, sinon motif `file_glob`
    (insensible à la casse). Message d'erreur utile si rien ne correspond."""
    if entry.get("file"):
        p = raw_dir / entry["file"]
        if p.exists():
            return p
    pdfs = sorted(raw_dir.glob("*.pdf")) + sorted(raw_dir.glob("*.PDF"))
    pattern = (entry.get("file_glob") or "").lower()
    if pattern:
        hits = [p for p in pdfs if fnmatch.fnmatch(p.name.lower(), pattern)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise FileNotFoundError(
                f"[{entry['doc_id']}] motif '{pattern}' ambigu : {[h.name for h in hits]}. "
                "Renseigne `file:` avec le nom exact dans config/docs.yaml.")
    listing = "\n  ".join(p.name for p in pdfs) or "(aucun PDF)"
    raise FileNotFoundError(
        f"[{entry['doc_id']}] PDF introuvable dans {raw_dir}.\nFichiers présents :\n  {listing}\n"
        "Renseigne `file:` avec le nom exact dans config/docs.yaml.")


def select_docs(manifest: dict, only: list[str] | None) -> list[dict]:
    docs = manifest["docs"]
    if only:
        docs = [d for d in docs if d["doc_id"] in only]
        if not docs:
            raise SystemExit(f"Aucun doc_id parmi {only}. Disponibles : "
                             f"{[d['doc_id'] for d in manifest['docs']]}")
    return docs
