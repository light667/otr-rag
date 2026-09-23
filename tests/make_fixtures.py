"""PDF synthétiques qui reproduisent les défauts observés dans les vrais PDF OTR.

Le contenu est INVENTÉ (ce n'est pas du texte de loi) ; seule la FORME est fidèle :
  * numéro de page en haut, isolé, parfois entre deux lignes d'une même phrase ;
  * notes de bas de page ("Loi N°… Exercice 2023") en police plus petite ;
  * appel de note collé au mot suivant ("1Toutefois") ;
  * "Art. N :", "Art.N :", "Art. N:" ;
  * titres sur plusieurs lignes, sous-titres "I - …" ;
  * césure "ci-" / "dessous" ;
  * barème écrit en lignes de texte ;
  * rescrits "R1 : …" avec titre sur deux lignes.
"""
from __future__ import annotations

import pymupdf

W, H = 400, 600
BODY = 11
LEAD = 14
X = 50


def _page_number(page, n: int, where: str = "top") -> None:
    y = 28 if where == "top" else H - 22
    page.insert_text((W / 2, y), str(n), fontsize=BODY, fontname="helv")


def _write(page, y: float, lines: list[tuple[str, str]]) -> float:
    """lines : (style, texte) avec style in {'n','b'}. Retourne le y suivant."""
    for style, text in lines:
        page.insert_text((X, y), text, fontsize=BODY, fontname="hebo" if style == "b" else "helv")
        y += LEAD
    return y


def _footnote(page, num: int, text: str, small: bool, superscript_marker: bool) -> None:
    y = 532
    size = 7.5 if small else BODY
    if superscript_marker:
        page.insert_text((X, y - 3), str(num), fontsize=5.5, fontname="helv")
        page.insert_text((X + 6, y), text, fontsize=size, fontname="helv")
    else:
        # le numéro seul sur sa ligne, puis le texte de la note (forme vue dans le texte extrait)
        page.insert_text((X, y), str(num), fontsize=size, fontname="helv")
        page.insert_text((X, y + 10), text, fontsize=size, fontname="helv")


def _marker(page, x: float, y: float, num: int, superscript: bool) -> float:
    """Pose l'appel de note ; renvoie le x où continuer la ligne."""
    if superscript:
        page.insert_text((x, y - 4), str(num), fontsize=5.5, fontname="helv")
        return x + 5
    page.insert_text((x, y), str(num), fontsize=BODY, fontname="helv")  # collé, même taille
    return x + 6


def build_code_pdf(path, superscript: bool = True, small_footnote: bool = True) -> None:
    doc = pymupdf.open()

    # ---------------- page 1 ----------------
    p = doc.new_page(width=W, height=H)
    _page_number(p, 1)
    y = 70
    y = _write(p, y, [
        ("n", "CODE GENERAL DES IMPÔTS"),
        ("b", "LIVRE PREMIER : IMPÔTS AU PROFIT DU"),
        ("b", "BUDGET DE L'ETAT"),
        ("b", "PREMIERE PARTIE : IMPÔTS DIRECTS ET"),
        ("b", "TAXES ASSIMILEES"),
        ("b", "TITRE PREMIER : IMPÔTS DIRECTS"),
        ("b", "CHAPITRE I : IMPÔT SUR LE REVENU DES"),
        ("b", "PERSONNES PHYSIQUES"),
        ("b", "Section 1 : Définition et structure"),
        ("n", "Art. premier : Il est établi un impôt annuel sur le"),
        ("n", "revenu des personnes physiques, assis sur les"),
        ("n", "revenus nets catégoriels ci-après :"),
        ("n", "- revenus fonciers ;"),
        ("n", "- traitements, salaires et pensions ;"),
        ("n", "- revenus de capitaux mobiliers."),
        ("n", "Art. 2 : 1 - Sont passibles de l'impôt les personnes"),
        ("n", "qui ont leur domicile fiscal sur le territoire, à"),
        ("n", "raison de leurs revenus, conformément aux"),
        ("n", "dispositions ci-"),
        ("n", "dessous et à l'article 17 du présent code."),
        ("b", "Section 2 : Exemptions"),
        ("n", "Art.3 : Sont affranchis de l'impôt les revenus qui"),
        ("n", "ne dépassent pas un seuil annuel fixé par la"),
    ])

    # ---------------- page 2 (continuation de phrase après le saut de page) ----------------
    p = doc.new_page(width=W, height=H)
    _page_number(p, 2)
    y = 70
    y = _write(p, y, [
        ("n", "loi de finances de l'année."),
        ("b", "I - Définition et revenu imposable"),
        ("n", "Art. 4 : Le revenu net foncier est égal à la"),
        ("n", "différence entre le revenu brut et les charges."),
    ])
    # paragraphe amendé : appel de note collé au premier mot
    xx = _marker(p, X, y, 1, superscript)
    p.insert_text((xx, y), "Toutefois, le contribuable peut opter pour les frais", fontsize=BODY, fontname="helv")
    y += LEAD
    y = _write(p, y, [
        ("n", "réels sur demande écrite avant le 30 novembre."),
        ("n", "Art. 5: Le taux applicable aux salaires est fixé"),
        ("n", "par le barème prévu à l'article 6 du CGI."),
        ("n", "Art. 84: Sont exonérées les plus-values des"),
        ("n", "exemp-"),
        ("n", "tions prévues par la loi."),
    ])
    _footnote(p, 1, "Loi N°2022-022 du 27 décembre 2022 portant Loi de Finances, Exercice 2023",
              small_footnote, superscript)

    # ---------------- page 3 : barème ----------------
    p = doc.new_page(width=W, height=H)
    _page_number(p, 3)
    y = 70
    y = _write(p, y, [
        ("n", "Art. 6 : Le revenu net imposable fait l'objet du"),
        ("n", "barème par tranches ci-après :"),
        ("n", "Tableau 1 : Nouveau Barème par tranches de revenu"),
        ("n", "de 0 A 900 000 exonéré"),
        ("n", "de 900 001 A 3 000 000 3%"),
        ("n", "de 3 000 001 A 6 000 000 10%"),
        ("n", "Plus de 20 000 000 35%"),
        ("n", "Le produit obtenu est arrondi à la dizaine de"),
        ("n", "francs inférieure."),
        ("b", "Paragraphe 1 - Personnes physiques dont le domicile"),
        ("b", "fiscal est situé hors du Togo"),
    ])
    # exposant ordinal : "1" puis "er" en petit et surélevé, comme dans "le 1er janvier"
    line = "Art. 7 : Les revenus sont déclarés avant le 1"
    p.insert_text((X, y), line, fontsize=BODY, fontname="helv")
    w = pymupdf.get_text_length(line, fontname="helv", fontsize=BODY)
    p.insert_text((X + w, y - 4), "er", fontsize=5.5, fontname="helv")
    y += LEAD
    _write(p, y, [("n", "janvier de chaque année.")])
    doc.save(path)
    doc.close()


def build_rescrits_pdf(path) -> None:
    doc = pymupdf.open()
    p = doc.new_page(width=W, height=H)
    y = 60
    y = _write(p, y, [
        ("b", "RESCRITS FISCAUX"),
        ("b", "R1 : Retenue à la source des sommes versées à des non-résidents"),
    ])
    y = _write(p, y, [
        ("n", "Une association fait appel à des consultants"),
        ("n", "non-résidents et demande des éclaircissements."),
        ("n", "En réponse, l'Administration fiscale fait les"),
        ("n", "observations suivantes :"),
        ("n", "L'article 2 du CGI dispose que les personnes"),
        ("n", "sont passibles de l'impôt. L'article 98 du LPF"),
        ("n", "prévoit une retenue à la source."),
    ])
    y += 10
    y = _write(p, y, [
        ("b", "R2 : Sollicitation d'une extension des exonérations d'une"),
        ("b", "structure parrainée par elle"),
        ("n", "Une structure à but lucratif sollicite un régime"),
        ("n", "dérogatoire."),
        ("n", "En réponse, l'Administration fiscale précise que"),
        ("n", "l'article 92-k du CGI reste applicable."),
    ])
    doc.save(path)
    doc.close()
