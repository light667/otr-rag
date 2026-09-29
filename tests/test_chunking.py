import pymupdf

from make_fixtures import build_cahier_pdf, build_code_pdf, build_rescrits_pdf
from otr_rag.chunking import chunk_document, parse_note, split_text
from otr_rag.cleaning import clean_md, split_front_matter
from otr_rag.layout import LayoutCfg, extract_pages
from otr_rag.rawmd import BuildMeta, build_code_md, build_generic_md, build_rescrits_md

LONG_PARA = ("Le contribuable est tenu de déclarer l'ensemble de ses revenus imposables avant le 31 mars. "
             "Cette déclaration précise la nature de chaque revenu, son montant brut et les charges déductibles ; "
             "elle est accompagnée des pièces justificatives prévues par la réglementation en vigueur. ") * 3

CODE_MD = f"""---
doc_id: cgi_test
source_doc_id: cgi_test
doc_type: code
code: CGI
version_year: 2025
published: '2025-09-18'
source_url: https://example.org/cgi
pdf_pages: [1, 4]
---
<!--pg:1-->

# LIVRE PREMIER : IMPÔTS AU PROFIT DU BUDGET DE L'ETAT

## PREMIERE PARTIE : IMPÔTS DIRECTS

#### CHAPITRE I : IMPÔT SUR LE REVENU

##### Section 1 : Définition et structure

<!--art:1-->
**Art. 1 :** Il est établi un impôt annuel sur le revenu.

<!--art:2-->
**Art. 2 :** Le taux est modifié[^fn1] en conséquence <!--pg:2--> selon l'article 17 du présent code et l'article 98 du LPF.

[^fn1]: Loi N°2022-022 du 27 décembre 2022 portant Loi de Finances, Exercice 2023

##### Section 2 : Exemptions

<!--pg:3-->
<!--art:3-->
**Art. 3 :** Sont exonérés :

{LONG_PARA}

{LONG_PARA}

{LONG_PARA}

<!--art:4-->
**Art. 4 :** Le barème est le suivant :

| Tranche (FCFA) | Taux |
| --- | --- |
| de 0 à 900 000 | exonéré |
| de 900 001 à 3 000 000 | 3% |
"""


def run(md=CODE_MD, **kw):
    front, body = split_front_matter(md)
    return chunk_document("cgi_test", front, body, **kw)


def by_art(chunks, aid):
    return [c for c in chunks if c["article"] == aid]


def test_article_is_the_unit_with_full_hierarchy():
    chunks, st = run(max_chars=1500)
    c = by_art(chunks, "2")[0]
    assert c["hierarchy"] == {
        "livre": "LIVRE PREMIER : IMPÔTS AU PROFIT DU BUDGET DE L'ETAT",
        "partie": "PREMIERE PARTIE : IMPÔTS DIRECTS",
        "chapitre": "CHAPITRE I : IMPÔT SUR LE REVENU",
        "section": "Section 1 : Définition et structure"}
    assert by_art(chunks, "3")[0]["hierarchy"]["section"] == "Section 2 : Exemptions"   # la section change
    assert st["n_units"] == 4 and st["text_outside_units_chars"] == 0


def test_text_is_pure_and_context_is_in_embed_text():
    chunks, _ = run(max_chars=1500)
    c = by_art(chunks, "2")[0]
    for bad in ("[^fn", "<!--", "**"):
        assert bad not in c["text"]
    assert c["text"].startswith("Art. 2 : Le taux est modifié en conséquence")
    assert c["embed_text"].startswith("CGI 2025 | LIVRE PREMIER")
    assert "| Art. 2\n\n" in c["embed_text"]


def test_amended_by_is_parsed_from_the_footnote():
    chunks, _ = run()
    (n,) = by_art(chunks, "2")[0]["amended_by"]
    assert n["law"] == "Loi N°2022-022" and n["date"] == "27 décembre 2022" and n["exercice"] == 2023
    assert by_art(chunks, "1")[0]["amended_by"] == []


def test_pages_come_from_page_markers_including_inline_ones():
    chunks, _ = run(page_map={1: 1, 2: 2})
    c = by_art(chunks, "2")[0]
    assert (c["page_start"], c["page_end"]) == (1, 2)
    assert (c["printed_page_start"], c["printed_page_end"]) == (1, 2)
    assert by_art(chunks, "3")[0]["page_start"] == 3          # marqueur de page juste avant l'article


def test_cross_references_are_normalized_with_the_code():
    chunks, _ = run()
    refs = by_art(chunks, "2")[0]["refs"]
    assert {"code": "CGI", "articles": ["17"], "range": False, "assumed": True} in refs
    assert {"code": "LPF", "articles": ["98"], "range": False, "assumed": False} in refs
    # régression : l'étiquette "Art. 2 :" en tête n'est pas un renvoi vers l'article 2 lui-même
    assert all(r["articles"] != ["2"] for r in refs) and len(refs) == 2
    assert by_art(chunks, "1")[0]["refs"] == []


def test_long_article_is_split_on_paragraphs_with_context_repeated():
    chunks, st = run(max_chars=1000, min_chars=100)
    parts = by_art(chunks, "3")
    assert len(parts) >= 3 and st["units_split"] == 1
    assert [p["part"] for p in parts] == list(range(len(parts)))
    assert all(p["n_parts"] == len(parts) and p["n_chars"] <= 1000 for p in parts)
    assert f"(partie 2/{len(parts)})" in parts[1]["context"]
    assert len({p["chunk_id"] for p in chunks}) == len(chunks)
    assert st["chunks_over_max"] == 0


def test_subheading_attaches_to_following_articles_not_the_preceding_one():
    """Régression réelle (cgi_lpf_2025, art. 14-15) : 'II- Exonérations' apparaissait collé à
    la fin de l'article 14 au lieu d'être une métadonnée de hiérarchie pour l'article 15."""
    md = """---
doc_id: cgi_test
doc_type: code
code: CGI
pdf_pages: [1, 1]
---
##### Section 8 : Détermination de l'assiette

<!--art:14-->
**Art. 14 :** Sont compris dans les revenus fonciers :

1- les revenus des propriétés bâties.

**II- Exonérations**

<!--art:15-->
**Art. 15 :** Ne sont pas compris dans les revenus imposables :

a) les revenus agricoles.

**III- Détermination du revenu imposable**

<!--art:16-->
**Art. 16 :** Le revenu net est égal à la différence.
"""
    front, body = split_front_matter(md)
    chunks, st = chunk_document("cgi_test", front, body)
    art14, art15, art16 = (by_art(chunks, a)[0] for a in ("14", "15", "16"))
    assert "II- Exonérations" not in art14["text"] and "Exonérations" not in art14["text"]
    assert art14["hierarchy"].get("sous") is None
    assert art15["hierarchy"]["sous"] == "II- Exonérations"
    assert "III- Détermination" not in art15["text"]
    assert art16["hierarchy"]["sous"] == "III- Détermination du revenu imposable"
    assert st["text_outside_units_chars"] == 0


def test_new_section_resets_the_pending_subheading():
    md = """---
doc_id: cgi_test
doc_type: code
code: CGI
pdf_pages: [1, 1]
---
##### Section 1 : Revenus fonciers

**II- Exonérations**

<!--art:20-->
**Art. 20 :** Article sous "II- Exonérations".

##### Section 2 : Autre matière

<!--art:21-->
**Art. 21 :** Un nouveau sujet, sans rapport avec les exonérations.
"""
    front, body = split_front_matter(md)
    chunks, _ = chunk_document("cgi_test", front, body)
    assert by_art(chunks, "20")[0]["hierarchy"]["sous"] == "II- Exonérations"
    assert "sous" not in by_art(chunks, "21")[0]["hierarchy"]       # nouvelle section : remis à zéro


def test_small_table_is_never_cut():
    chunks, _ = run(max_chars=1000)
    (c,) = by_art(chunks, "4")
    assert "| de 900 001 à 3 000 000 | 3% |" in c["text"]


def test_orphan_notes_are_attached_by_page_only_to_articles_on_that_page():
    orphans = [{"page": 2, "num": 9, "text": "Loi N°2020-019 du 3 janvier 2020 portant Loi de Finances, Exercice 2020"}]
    chunks, st = run(orphan_notes=orphans)
    (pn,) = by_art(chunks, "2")[0]["page_notes"]
    assert pn["page"] == 2 and pn["law"] == "Loi N°2020-019"
    assert by_art(chunks, "1")[0]["page_notes"] == []          # page 1 seulement : pas concerné
    assert st["with_page_notes"] == 1


def test_split_text_giant_single_paragraph_and_big_table():
    giant = " ".join(f"Phrase numéro {i} du très long alinéa unique." for i in range(200))
    parts = split_text(giant, 500, 100)
    # contrat : <= max_chars, sauf le dernier morceau fusionné pour éviter une miette (<= 1,25 x max)
    assert all(len(p) <= 500 * 1.25 for p in parts) and sum(len(p) > 500 for p in parts) <= 1
    assert " ".join(parts).count("Phrase numéro") == 200
    table = "\n".join(["| a | b |", "| --- | --- |"] + [f"| ligne {i} | {i} |" for i in range(80)])
    tparts = split_text(table, 300, 50)
    assert len(tparts) > 1 and all(p.startswith("| a | b |\n| --- | --- |") for p in tparts)


def test_parse_note_without_law_keeps_the_text():
    assert parse_note("Voir circulaire interne") == {"text": "Voir circulaire interne"}


def test_rescrits_one_chunk_per_rescrit():
    md = """---
doc_id: rescrits_2025
doc_type: rescrits
---
<!--pg:1-->
# RESCRITS FISCAUX

## R1 : Retenue à la source des non-résidents

Une association fait appel à des consultants. L'article 2 du CGI dispose que ...

## R2 : Extension des exonérations

En réponse, l'Administration précise que l'article 92-k du CGI reste applicable.
"""
    front, body = split_front_matter(md)
    chunks, st = chunk_document("rescrits_2025", front, body)
    assert [c["rescrit"] for c in chunks] == ["1", "2"]
    assert chunks[0]["context"] == "Rescrits fiscaux OTR | RESCRITS FISCAUX | R1 : Retenue à la source des non-résidents"
    assert chunks[1]["refs"][0]["articles"] == ["92-k"]


def test_generic_bold_subheadings_split_and_tiny_intro_is_merged():
    md = """---
doc_id: cahier_fiscal_2025
doc_type: generic
---
<!--pg:1-->
# CAHIER FISCAL

### PRINCIPALES DISPOSITIONS DE LA LOI DE FINANCES 2025

<!--pg:3-->
# LES MESURES NOUVELLES CONTENUES DANS LE CORPS DE LA LOI

Les mesures nouvelles adoptées sont au nombre de deux à savoir :

**Article 15 de la LOFI :**

""" + LONG_PARA + """

**Article 16 de la LOFI :**

""" + LONG_PARA + "\n"
    front, body = split_front_matter(md)
    chunks, st = chunk_document("cahier_fiscal_2025", front, body, max_chars=3000, min_chars=250)
    assert [c["title"] for c in chunks] == ["Article 15 de la LOFI :", "Article 16 de la LOFI :"]
    assert chunks[0]["text"].startswith("Les mesures nouvelles adoptées sont au nombre de deux")   # intro fusionnée
    assert chunks[0]["hierarchy"] == {"h1": "LES MESURES NOUVELLES CONTENUES DANS LE CORPS DE LA LOI"}
    assert chunks[0]["context"].startswith("Cahier Fiscal 2025 | LES MESURES NOUVELLES")
    assert st["n_chunks"] == 2            # la couverture (titres seuls) ne produit aucun chunk


# --------------------------------------------------------------------------
# Bout en bout : PDF synthétique -> extraction -> nettoyage -> chunks
# --------------------------------------------------------------------------
def _front(doc_type, p95, **extra):
    fm = "\n".join(f"{k}: {v}" for k, v in {"doc_id": "e2e", "doc_type": doc_type, "p95_len": p95, **extra}.items())
    return f"---\n{fm}\n---\n"


def test_end_to_end_code_pdf(tmp_path):
    pdf = tmp_path / "c.pdf"
    build_code_pdf(str(pdf))
    doc = pymupdf.open(str(pdf))
    cfg = LayoutCfg.from_dict({"drop_regex": [r"^CODE G[ÉE]N[ÉE]RAL DES IMP[ÔO]TS$"]})
    pages, stats = extract_pages(doc, cfg)
    raw = build_code_md(pages, stats, cfg, BuildMeta())
    clean, _, _ = clean_md(_front("code", round(stats.p95_len)) + raw)
    front, body = split_front_matter(_front("code", 0, code="CGI", version_year=2023) + clean)
    chunks, st = chunk_document("e2e", front, body)
    assert [c["article"] for c in chunks] == ["1", "2", "3", "4", "5", "84", "6", "7"]
    art4 = by_art(chunks, "4")[0]
    assert art4["amended_by"][0]["law"] == "Loi N°2022-022" and art4["amended_by"][0]["exercice"] == 2023
    assert art4["hierarchy"]["chapitre"].startswith("CHAPITRE I : IMPÔT SUR LE REVENU")
    assert any("| de 900 001 à 3 000 000 | 3% |" in c["text"] for c in chunks)
    assert st["text_outside_units_chars"] == 0


def test_end_to_end_rescrits_and_cahier_pdfs(tmp_path):
    pdf = tmp_path / "r.pdf"
    build_rescrits_pdf(str(pdf))
    doc = pymupdf.open(str(pdf))
    pages, stats = extract_pages(doc, LayoutCfg())
    raw = build_rescrits_md(pages, stats, LayoutCfg(), BuildMeta())
    clean, _, _ = clean_md(_front("rescrits", round(stats.p95_len)) + raw)
    front, body = split_front_matter(_front("rescrits", 0) + clean)
    chunks, _ = chunk_document("r", front, body)
    assert [c["rescrit"] for c in chunks] == ["1", "2"]
    assert {"code": "CGI", "articles": ["2"], "range": False, "assumed": False} in chunks[0]["refs"]
    assert {"code": "LPF", "articles": ["98"], "range": False, "assumed": False} in chunks[0]["refs"]

    pdf2 = tmp_path / "k.pdf"
    build_cahier_pdf(str(pdf2))
    doc2 = pymupdf.open(str(pdf2))
    cfg = LayoutCfg.from_dict({"footnote_enabled": False,
                               "drop_regex": [r"^MESURES PHARES DE LA LOI DE FINANCES,?\s*EXERCICE\s*2025\d*$"]})
    pages, stats = extract_pages(doc2, cfg)
    raw = build_generic_md(doc2, pages, stats, cfg, BuildMeta())
    clean, _, _ = clean_md(_front("generic", round(stats.p95_len)) + raw)
    front, body = split_front_matter(_front("generic", 0, doc_id="cahier_fiscal_2025") + clean)
    chunks, _ = chunk_document("k", front, body, min_chars=250)
    titles = [c["title"] for c in chunks]
    assert "Article 15 de la LOFI :" in titles and "Article 16 de la LOFI :" in titles
    assert any("loi n° 2024-007" in c["text"] for c in chunks)       # le préambule est bien conservé
    assert not any("MESURES PHARES" in c["text"] for c in chunks)
