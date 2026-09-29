"""OCR des PDF scannés via LlamaParse (SDK actuel `llama-cloud`, pas `llama-parse` : déprécié).

Le résultat de LlamaParse est déjà du Markdown structuré (titres, tableaux, paragraphes entiers).
On n'utilise donc PAS l'étape 01_extract (géométrie PyMuPDF) : on fabrique directement un
`<doc_id>.raw.md` au même format (front matter + marqueurs <!--pg:N-->), marqué
`structured_markdown: true` pour que 02_clean ne recolle aucune ligne.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

FIGURE = re.compile(r"\d[\d  .,]*\s?(?:%|FCFA|F\s?CFA|francs)|\b\d{1,3}(?:[  .]\d{3})+\b", re.I)

# LlamaParse décrit les éléments visuels qu'il ne peut pas transcrire ("logo: ...", "photo: ...",
# "icon: ..."). Utile pour un document riche en images, pur bruit pour un corpus fiscal.
NOISE_LINE = re.compile(r"^(?:logo|photo|figure|image)\s*:\s*.*$", re.I)
ICON_INLINE = re.compile(r"\[?\bicon\s*:\s*[^\]\n]*\]?\s*", re.I)


def load_env(path: Path) -> None:
    """Charge un fichier .env (KEY=VALUE) sans écraser l'environnement existant."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def parse_pdf(pdf: Path, tier: str = "agentic", languages: tuple[str, ...] = ("fr",), client=None) -> list[str]:
    """PDF -> liste de Markdown, un élément par page (dans l'ordre)."""
    if client is None:
        if not os.environ.get("LLAMA_CLOUD_API_KEY"):
            raise RuntimeError("LLAMA_CLOUD_API_KEY absente : mets-la dans .env ou dans l'environnement.")
        try:
            from llama_cloud import LlamaCloud
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("SDK manquant : pip install \"llama-cloud>=2.8\"") from e
        client = LlamaCloud()   # lit LLAMA_CLOUD_API_KEY
    file = client.files.create(file=str(pdf), purpose="parse")
    result = client.parsing.parse(
        file_id=file.id,
        tier=tier,
        version="latest",
        processing_options={"ocr_parameters": {"languages": list(languages)}},
        output_options={"markdown": {"tables": {"output_tables_as_markdown": True}}},
        expand=["markdown"],
    )
    return [(getattr(p, "markdown", None) or "") for p in result.markdown.pages]


def build_raw_md(pages: list[str], entry: dict, pdf_name: str, tier: str = "agentic") -> tuple[str, dict]:
    drops = [re.compile(r, re.I) for r in (entry.get("layout") or {}).get("drop_regex", [])]
    out: list[str] = []
    dropped = 0
    empty: list[int] = []
    for i, md in enumerate(pages, start=1):
        out += [f"<!--pg:{i}-->", ""]
        kept = []
        for line in md.replace("\r", "").split("\n"):
            line = ICON_INLINE.sub("", line.rstrip())
            stripped = line.strip()
            if NOISE_LINE.match(stripped):
                dropped += 1
                continue
            if drops and any(d.match(stripped) for d in drops):
                dropped += 1
                continue
            kept.append(line.rstrip())
        body = "\n".join(kept).strip()
        if len(body) < 20:
            empty.append(i)
        out += [body, ""]

    fm = {
        "doc_id": entry["doc_id"], "source_doc_id": entry["doc_id"], "doc_type": entry["doc_type"],
        "version_year": entry.get("version_year"), "published": entry.get("published"),
        "source_url": entry.get("source_url"), "pdf_file": pdf_name, "pdf_pages": [1, len(pages)],
        "ocr": "llamaparse", "ocr_tier": tier, "structured_markdown": True,
    }
    fm = {k: v for k, v in fm.items() if v is not None}
    text = "\n".join(out)
    report = {
        "n_pages": len(pages), "empty_pages": empty, "dropped_lines": dropped,
        "chars": len(text), "headings": len(re.findall(r"^#{1,6} ", text, flags=re.M)),
        "table_rows": len(re.findall(r"^\|", text, flags=re.M)),
    }
    return "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n" + text + "\n", report


def figures_report(pages: list[str], doc_id: str) -> str:
    """Toutes les lignes contenant un taux ou un montant, page par page.

    L'OCR se trompe sur les chiffres (un 3 lu comme 8, une virgule perdue) et, en matière fiscale, une
    erreur sur un taux est la pire erreur possible : ce rapport permet de les relire contre l'image du PDF.
    """
    lines = [f"# Chiffres à vérifier contre le PDF : {doc_id}", ""]
    total = 0
    for i, md in enumerate(pages, start=1):
        hits = [l.strip() for l in md.split("\n") if l.strip() and FIGURE.search(l) and not l.lstrip().startswith("| ---")]
        if hits:
            lines.append(f"## Page {i}")
            lines += [f"- {h[:300]}" for h in hits]
            lines.append("")
            total += len(hits)
    lines.insert(2, f"{total} lignes contenant un taux ou un montant.\n")
    return "\n".join(lines) + "\n"
