"""Bout en bout sur PDF synthétiques (contenu inventé, forme fidèle aux défauts observés)."""
import re

import pymupdf
import pytest

from make_fixtures import build_code_pdf, build_rescrits_pdf
from otr_rag.cleaning import clean_md, qa_report
from otr_rag.layout import LayoutCfg, extract_pages
from otr_rag.rawmd import BuildMeta, build_code_md, build_generic_md, build_rescrits_md

CFG = {"drop_regex": [r"^CODE G[ÉE]N[ÉE]RAL DES IMP[ÔO]TS$"]}


def run_code(path, cfg_extra=None):
    doc = pymupdf.open(path)
    cfg = LayoutCfg.from_dict({**CFG, **(cfg_extra or {})})
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
    # Le cas "collé_meme_taille" simule un marqueur de note SANS exposant : c'est justement
    # le cas que enable_glued_fallback est censé rattraper. Sur les vrais documents OTR, ce
    # repli reste désactivé par défaut (voir layout.py) car aucune note réelle n'en a besoin.
    extra = {} if sup else {"enable_glued_fallback": True}
    return run_code(str(pdf), extra)


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


def test_footnote_resolved_via_global_fallback_when_far_from_marker(tmp_path):
    """Régression cgi_2023 : sur le vrai document, la plupart des notes n'étaient PAS
    trouvées via la fenêtre ±1 page (215 notes orphelines). Puisque la numérotation des
    notes est continue sur tout le document (jamais remise à 0), un repli par recherche
    globale doit retrouver une définition même à plus d'une page de son marqueur."""
    doc = pymupdf.open()
    p1 = doc.new_page(width=400, height=600)
    p1.insert_text((50, 60), "Art. 1 : Un premier article sans rapport.", fontsize=11, fontname="helv")

    p2 = doc.new_page(width=400, height=600)
    x = 50
    p2.insert_text((x, 60), "Art. 2 : Le taux est modifié", fontsize=11, fontname="helv")
    p2.insert_text((x + 195, 56), "1", fontsize=5.5, fontname="helv")  # exposant, marqueur fn:1
    p2.insert_text((x + 200, 60), "en conséquence.", fontsize=11, fontname="helv")

    p3 = doc.new_page(width=400, height=600)  # note à plus d'1 page de son marqueur
    p3.insert_text((50, 60), "Art. 3 : Un troisième article.", fontsize=11, fontname="helv")
    p3.insert_text((50, 532), "1", fontsize=7.5, fontname="helv")
    p3.insert_text((56, 532), "Loi N°2022-022 du 27 décembre 2022 portant Loi de Finances", fontsize=7.5, fontname="helv")

    pdf = tmp_path / "far.pdf"
    doc.save(str(pdf))
    doc.close()
    *_, clean, _ = run_code(str(pdf))
    assert "[^fn1]" in clean
    assert "[^fn1]: Loi N°2022-022 du 27 décembre 2022 portant Loi de Finances" in clean


def test_small_font_text_before_first_note_label_stays_in_body(tmp_path):
    """Régression cgi_lpf_2025 : l'article 212 est passé de 15 937 à 29 888 caractères quand
    on a resserré footnote_min_y, car du texte en petite police dans le bas de page était classé
    'note' puis JETÉ (aucune étiquette de note au-dessus -> ligne perdue sans trace)."""
    doc = pymupdf.open()
    p = doc.new_page(width=400, height=600)
    y = 60
    for i in range(12):
        p.insert_text((50, y), f"Le contribuable déclare ses revenus, ligne {i:02d}.", fontsize=12, fontname="helv")
        y += 16
    p.insert_text((50, 500), "Ce paragraphe secondaire est imprime en plus petit", fontsize=9, fontname="helv")
    p.insert_text((50, 512), "et il continue sur cette seconde ligne de texte.", fontsize=9, fontname="helv")
    p.insert_text((50, 557), "1", fontsize=5, fontname="helv")   # étiquette de la VRAIE note
    p.insert_text((56, 560), "Loi N°2022-022 du 27 décembre 2022 portant Loi de Finances", fontsize=7, fontname="helv")
    pdf = tmp_path / "small.pdf"
    doc.save(str(pdf))
    doc.close()

    pages, _ = extract_pages(pymupdf.open(str(pdf)), LayoutCfg())
    body_text = " ".join(l.text for l in pages[0].lines)
    assert "Ce paragraphe secondaire est imprime en plus petit" in body_text
    assert "et il continue sur cette seconde ligne de texte." in body_text
    assert pages[0].reclaimed == 2
    assert list(pages[0].footnotes.values()) == ["Loi N°2022-022 du 27 décembre 2022 portant Loi de Finances"]


def test_orphan_note_keeps_its_full_text(tmp_path):
    """Les notes sans appel détecté ne sont plus tronquées à 120 caractères : elles sont écrites
    en entier dans <doc>.orphan_notes.json puis rattachées aux articles par page au chunking."""
    doc = pymupdf.open()
    p = doc.new_page(width=400, height=600)
    p.insert_text((50, 60), "Art. 1 : Un article sans aucun appel de note.", fontsize=11, fontname="helv")
    for i in range(10):   # le corps doit dominer en nombre de caractères (sinon body_size = taille de la note)
        p.insert_text((50, 80 + 16 * i), f"Le contribuable déclare ses revenus, ligne {i:02d}.", fontsize=11, fontname="helv")
    line1 = "Loi N°2019-016 du 30 décembre 2019 portant Loi de Finances, Exercice 2020,"
    line2 = "modifiée par la loi rectificative du 15 juin 2020 (article 4 et annexe fiscale)."
    long_note = line1 + " " + line2          # > 120 caractères, sur deux lignes de note
    p.insert_text((50, 528), "1", fontsize=7.5, fontname="helv")
    p.insert_text((56, 528), line1, fontsize=7.5, fontname="helv")
    p.insert_text((56, 538), line2, fontsize=7.5, fontname="helv")
    pdf = tmp_path / "orphan.pdf"
    doc.save(str(pdf))
    doc.close()
    _, meta, *_ = run_code(str(pdf))
    assert len(meta.orphan_footnotes) == 1
    assert len(long_note) > 120
    assert meta.orphan_footnotes[0]["text"] == long_note


def test_two_digit_enumeration_is_not_mistaken_for_a_glued_footnote(tmp_path):
    """Régression : sur le CGI 2023 réel, une énumération '9- ... 10- ... 11- ...' était
    scindée à tort en 'marqueur de note 1' + '0- ...' / '1- ...'. Avec enable_glued_fallback
    désactivé (défaut), ces lignes doivent traverser le pipeline intactes."""
    doc = pymupdf.open()
    p = doc.new_page(width=400, height=600)
    y = 60
    for line in ["Art. 84 : Sont exonérées de la taxe :",
                 "9- les actes passés par les collectivités ;",
                 "10- les immeubles et leurs dépendances ;",
                 "11- les immeubles servant exclusivement à"]:
        p.insert_text((50, y), line, fontsize=11, fontname="helv")
        y += 14
    pdf = tmp_path / "enum.pdf"
    doc.save(str(pdf))
    doc.close()
    *_, clean, _ = run_code(str(pdf))  # enable_glued_fallback reste False (défaut)
    assert "10- les immeubles et leurs dépendances ;" in clean
    assert "11- les immeubles servant exclusivement à" in clean
    assert "{{fn:" not in clean and "[^fn" not in clean


def test_footnote_enabled_false_keeps_small_font_text_in_body(tmp_path):
    """Régression cahier fiscal 2025 : un paragraphe en police plus petite que le corps
    (préambule en 12pt vs corps en 14pt) n'est PAS une note d'amendement. Sans
    footnote_enabled=False, il serait classé comme note et perdu du corps du texte."""
    doc = pymupdf.open()
    p = doc.new_page(width=400, height=600)
    y = 60
    for line in ["CAHIER FISCAL 2025", "Présentation des principales mesures fiscales",
                 "et douanières issues de la loi de finances,", "exercice 2025, applicables aux contribuables",
                 "et aux redevables des droits et taxes prévus", "par le Code Général des Impôts en vigueur."]:
        p.insert_text((50, y), line, fontsize=14, fontname="helv")  # corps du document, dominant
        y += 18
    y = 500  # dans le bas de page : c'est justement là que la note serait cherchée
    for line in ["Adoptée par la représentation nationale le 27 décembre",
                 "2024 et promulguée par le Président de la République"]:
        p.insert_text((50, y), line, fontsize=12, fontname="helv")  # < 92% du corps (14pt)
        y += 15
    pdf = tmp_path / "cahier.pdf"
    doc.save(str(pdf))
    doc.close()

    cfg_off = LayoutCfg.from_dict({"footnote_enabled": False})
    pages, _ = extract_pages(pymupdf.open(str(pdf)), cfg_off)
    assert pages[0].footnotes == {}
    body_text = " ".join(l.text for l in pages[0].lines)
    assert "Adoptée par la représentation nationale" in body_text

    # Depuis la correction "petite police sans étiquette de note -> reste dans le corps", même avec
    # footnote_enabled=True ce préambule n'est plus perdu. footnote_enabled=False reste une sécurité
    # supplémentaire pour les documents narratifs (aucune note d'amendement à chercher).
    cfg_on = LayoutCfg.from_dict({"footnote_enabled": True})
    pages_on, _ = extract_pages(pymupdf.open(str(pdf)), cfg_on)
    body_text_on = " ".join(l.text for l in pages_on[0].lines)
    assert "Adoptée par la représentation nationale" in body_text_on
    assert pages_on[0].footnotes == {} and pages_on[0].reclaimed == 2


def run_generic(path, cfg_extra=None):
    doc = pymupdf.open(path)
    cfg = LayoutCfg.from_dict(cfg_extra or {})
    pages, stats = extract_pages(doc, cfg)
    meta = BuildMeta()
    raw = build_generic_md(doc, pages, stats, cfg, meta)
    clean, rep, _ = clean_md("---\np95_len: %d\n---\n" % round(stats.p95_len) + raw)
    return pages, meta, raw, clean, rep


CAHIER_CFG = {
    "footnote_enabled": False,
    "drop_regex": [r"^MESURES PHARES DE LA LOI DE FINANCES,?\s*EXERCICE\s*2025\d*$"],
}


def test_cahier_multiline_heading_is_merged_not_split(tmp_path):
    """Régression : 'LES MESURES NOUVELLES' / 'CONTENUES DANS LE CORPS' / 'DE LA LOI' est UN
    titre en 30pt sur 3 lignes physiques du PDF, pas trois titres distincts."""
    from make_fixtures import build_cahier_pdf
    pdf = tmp_path / "cahier.pdf"
    build_cahier_pdf(str(pdf))
    *_, clean, _ = run_generic(str(pdf), CAHIER_CFG)
    assert "# LES MESURES NOUVELLES CONTENUES DANS LE CORPS DE LA LOI" in clean
    assert "# LES MESURES NOUVELLES\n" not in clean          # pas trois titres séparés
    assert "# CONTENUES DANS LE CORPS" not in clean
    assert "### PRINCIPALES DISPOSITIONS DE LA LOI DE FINANCES 2025" in clean  # 2 lignes, même taille
    assert "# CAHIER FISCAL" in clean


def test_cahier_preamble_not_dropped_as_footnote(tmp_path):
    from make_fixtures import build_cahier_pdf
    pdf = tmp_path / "cahier.pdf"
    build_cahier_pdf(str(pdf))
    *_, clean, _ = run_generic(str(pdf), CAHIER_CFG)
    assert "la loi n° 2024-007 portant loi de finances, exercice 2025" in clean


def test_cahier_bold_measure_subheadings(tmp_path):
    from make_fixtures import build_cahier_pdf
    pdf = tmp_path / "cahier.pdf"
    build_cahier_pdf(str(pdf))
    *_, clean, _ = run_generic(str(pdf), CAHIER_CFG)
    assert "**Article 15 de la LOFI :**" in clean
    assert "**Article 16 de la LOFI :**" in clean
    assert "taux réduit des droits d'accises" in clean
    assert "Réduction de 50% sur le montant du droit de douane" in clean


def test_cahier_running_footer_with_glued_page_number_is_dropped(tmp_path):
    """Régression : le pied de page change à chaque page ('...EXERCICE 20253',
    '...EXERCICE 20254'...) car le numéro de page est collé sans espace, donc la
    détection générique des en-têtes/pieds répétés ne le voit pas. drop_regex le retire."""
    from make_fixtures import build_cahier_pdf
    pdf = tmp_path / "cahier.pdf"
    build_cahier_pdf(str(pdf))
    *_, clean, _ = run_generic(str(pdf), CAHIER_CFG)
    assert "MESURES PHARES" not in clean
    assert "EXERCICE 2025" not in clean


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


def test_missing_space_between_adjacent_spans_is_restored(tmp_path):
    """Régression : 'le calculde l'impôt' et 'code dutravail' trouvés dans les vrais documents -
    un run de texte PDF adjacent au suivant sans caractère espace (espacement de justification)."""
    doc = pymupdf.open()
    p = doc.new_page(width=400, height=600)
    line = "Le calcul"
    p.insert_text((50, 60), line, fontsize=11, fontname="helv")
    x1 = 50 + pymupdf.get_text_length(line, fontname="helv", fontsize=11)
    # taille très légèrement différente : force deux spans PDF distincts (comme dans le vrai document
    # Word, où deux runs adjacents sans espace proviennent de styles internes différents) ; avec la
    # même taille exacte, PyMuPDF fusionnerait lui-même les deux insert_text en un seul span, ce qui
    # ne reproduirait pas le bug réel.
    p.insert_text((x1 + pymupdf.get_text_length(" ", fontname="helv", fontsize=11), 60),
                  "de l'impôt est annuel.", fontsize=11.02, fontname="helv")
    p.insert_text((50, 90), "Le taux de 10%", fontsize=11, fontname="helv")  # pas de faux positif sur un nombre
    pdf = tmp_path / "gap.pdf"
    doc.save(str(pdf))
    doc.close()
    pages, _ = extract_pages(pymupdf.open(str(pdf)), LayoutCfg())
    texts = [l.text for l in pages[0].lines]
    assert "Le calcul de l'impôt est annuel." in texts
    assert "Le taux de 10%" in texts