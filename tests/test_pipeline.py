"""Bout en bout sur PDF synthétiques (contenu inventé, forme fidèle aux défauts observés)."""
import re

import pymupdf
import pytest

from make_fixtures import build_code_pdf, build_rescrits_pdf
from otr_rag.cleaning import clean_md, qa_report
from otr_rag.layout import LayoutCfg, extract_pages
from otr_rag.rawmd import BuildMeta, build_code_md, build_rescrits_md

CFG = {"drop_regex": [r"^CODE G[ÉE]N[ÉE]RAL DES IMP[ÔO]TS$"]}


def run_code(path):
    doc = pymupdf.open(path)
    cfg = LayoutCfg.from_dict(CFG)
    pages, stats = extract_pages(doc, cfg)
    meta = BuildMeta()
    raw = build_code_md(pages, stats, cfg, meta)
    clean, rep, _ = clean_md("---\np95_len: %d\n---\n" % round(stats.p95_len) + raw)
    return pages, meta, raw, clean, rep


@pytest.fixture(params=["exposant_petite_police", "collé_meme_taille"])
def code_result(request, tmp_path):
    sup = request.param == "exposant_petite_police"
    pdf = tmp_path / "code.pdf"
    build_code_pdf(str(pdf), superscript=sup, small_footnote=sup)
    return run_code(str(pdf))


def test_page_numbers_removed_and_recorded(code_result):
    pages, meta, raw, clean, _ = code_result
    assert [p.printed_page for p in pages] == [1, 2, 3]
    assert not re.search(r"^\d{1,3}$", clean, flags=re.M)
    assert "CODE GENERAL DES" not in clean


def test_headings_hierarchy_and_multiline_merge(code_result):
    *_, clean, _ = code_result
    assert "# LIVRE PREMIER : IMPÔTS AU PROFIT DU BUDGET DE L'ETAT" in clean
    assert "## PREMIERE PARTIE : IMPÔTS DIRECTS ET TAXES ASSIMILEES" in clean
    assert "#### CHAPITRE I : IMPÔT SUR LE REVENU DES PERSONNES PHYSIQUES" in clean
    assert "###### Paragraphe 1 : Personnes physiques dont le domicile fiscal est situé hors du Togo" in clean
    assert "**I - Définition et revenu imposable**" in clean


def test_all_article_forms_detected(code_result):
    *_, clean, _ = code_result
    assert re.findall(r"<!--art:(\d+)-->", clean) == ["1", "2", "3", "4", "5", "84", "6", "7"]


def test_footnote_attached_to_its_article(code_result):
    pages, meta, raw, clean, _ = code_result
    art4 = clean.split("<!--art:4-->")[1].split("<!--art:5-->")[0]
    assert "[^fn1] Toutefois" in art4 or "[^fn1]Toutefois" in art4
    assert "[^fn1]: Loi N°2022-022 du 27 décembre 2022 portant Loi de Finances, Exercice" in art4
    assert meta.unresolved_markers == [] and meta.orphan_footnotes == []


def test_paragraph_continues_across_page_break(code_result):
    *_, clean, _ = code_result
    assert "fixé par la <!--pg:2--> loi de finances de l'année." in clean


def test_hyphenation_decisions(code_result):
    *_, clean, rep = code_result
    assert "ci-dessous" in clean and "exemptions" in clean and "exemp-" not in clean


def test_bareme_table_and_ordinal(code_result):
    *_, clean, _ = code_result
    assert "| de 900 001 à 3 000 000 | 3% |" in clean
    assert "| plus de 20 000 000 | 35% |" in clean
    if "1er janvier" not in clean:  # cas "collé même taille" : pas d'exposant "er" en petit
        assert "1 er janvier" not in clean
    assert "avant le 1er" in clean.replace("1 er", "1er") or "avant le 1" in clean


def test_qa_flags_the_deliberate_order_anomaly(code_result):
    *_, clean, _ = code_result
    qa = qa_report(clean)
    assert qa["order_violations"] == ["6"]      # l'Art. 84 placé avant l'Art. 6 dans la fixture
    assert qa["footnote_refs"] == 1 and qa["footnote_defs"] == 1
    assert qa["refs_without_def"] == []
    assert qa["cross_refs"]["by_target"].get("CGI") == 1


def test_rescrits(tmp_path):
    pdf = tmp_path / "r.pdf"
    build_rescrits_pdf(str(pdf))
    doc = pymupdf.open(str(pdf))
    cfg = LayoutCfg()
    pages, stats = extract_pages(doc, cfg)
    meta = BuildMeta()
    raw = build_rescrits_md(pages, stats, cfg, meta)
    clean, _, _ = clean_md("---\np95_len: %d\n---\n" % round(stats.p95_len) + raw)
    assert "# RESCRITS FISCAUX" in clean
    assert "## R1 : Retenue à la source des sommes versées à des non-résidents" in clean
    assert "## R2 : Sollicitation d'une extension des exonérations d'une structure parrainée par elle" in clean
    assert [r["id"] for r in meta.rescrits] == ["1", "2"]
    qa = qa_report(clean, "rescrits")
    assert qa["n_rescrits"] == 2 and qa["cross_refs"]["by_target"] == {"CGI": 2, "LPF": 1}
