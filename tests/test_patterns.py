import pytest

from otr_rag.patterns import (ARTICLE_START, BAREME_LAST, BAREME_ROW, GLUED_MARKER, HEADING_PATTERNS,
                              LIST_START, RESCRIT_START, SUBHEADING, article_id, extract_refs)


@pytest.mark.parametrize("line,aid", [
    ("Art. premier : Il est établi un impôt", "1"),
    ("Art. 2 : 1 - Sous réserve de l'application", "2"),
    ("Art.50 : L'impôt sur le revenu n'est pas applicable", "50"),
    ("Art. 84: Sont exonérées les plus-values", "84"),
    ("Article 12 - Les sociétés", "12"),
    ("Art. 99 bis : Complément", "99bis"),
])
def test_article_start(line, aid):
    m = ARTICLE_START.match(line)
    assert m and article_id(m.group(1)) == aid


@pytest.mark.parametrize("line", [
    "l'article 17 du présent code",
    "Art. 99-a) Les rémunérations",   # référence, pas un début d'article (pas de ':' après le numéro)
    "Articles de la loi",
])
def test_not_article(line):
    assert not ARTICLE_START.match(line)


@pytest.mark.parametrize("line,kind", [
    ("LIVRE PREMIER : IMPÔTS AU PROFIT DU", "livre"),
    ("PREMIERE PARTIE : IMPÔTS DIRECTS ET", "partie"),
    ("TITRE PREMIER : IMPÔTS DIRECTS", "titre"),
    ("CHAPITRE I : IMPÔT SUR LE REVENU DES", "chapitre"),
    ("CHAPITRE II : TAXE SUR LES PLUS-VALUES", "chapitre"),
    ("Section 8 : Détermination de l'assiette", "section"),
    ("Paragraphe 1 - Personnes physiques dont le domicile", "paragraphe"),
    ("Paragraphe 2 : Revenus d'emplois", "paragraphe"),
])
def test_headings(line, kind):
    assert any(k == kind and p.match(line) for _, k, p in HEADING_PATTERNS)


def test_headings_do_not_match_prose():
    for line in ["Section 3 du présent code prévoit", "Le chapitre I de la loi", "titre exécutoire délivré"]:
        assert not any(p.match(line) for _, _, p in HEADING_PATTERNS)


@pytest.mark.parametrize("line", ["I - Définition et revenu imposable", "II – Exonérations",
                                  "A-Revenus d'emplois", "IV-Période d'imposition"])
def test_subheading(line):
    assert SUBHEADING.match(line)


@pytest.mark.parametrize("line,marker", [
    ("1Toutefois, le contribuable qui souhaite", "1"),
    ("2Le revenu d'emploi inclut également", "2"),
    ("6Art. 74 : Pour le calcul de l'impôt", "6"),
    ("36-La prime d'assurance maladie", "3"),
    ("83 - Les associés ou membres", "8"),
])
def test_glued_marker(line, marker):
    assert GLUED_MARKER.match(line).group(1) == marker


@pytest.mark.parametrize("line", ["12 janvier de l'année", "10 000 000 de francs CFA", "1 - Sous réserve",
                                  "Les personnes", "2 400 000 francs"])
def test_not_glued_marker(line):
    assert not GLUED_MARKER.match(line)


def test_bareme_rows():
    m = BAREME_ROW.match("de 900 001 A 3 000 000 3%")
    assert m.groups() == ("900 001", "3 000 000", "3%")
    assert BAREME_ROW.match("de 0 A 900 000 exonéré").group(3) == "exonéré"
    assert BAREME_LAST.match("Plus de 20 000 000 35%").group(2) == "35%"
    assert not BAREME_ROW.match("de la même manière que 3%")


def test_rescrit_start():
    assert RESCRIT_START.match("R1 : Retenue à la source des sommes").group(1) == "1"
    assert not RESCRIT_START.match("Revenus fonciers : définition")


def test_list_start():
    for l in ["- revenus fonciers ;", "₋ 100 % si la durée", "a) les revenus", "1 - Sont passibles", "1- les revenus", "1) texte"]:
        assert LIST_START.match(l), l
    for l in ["31 décembre de l'année", "revenus nets"]:
        assert not LIST_START.match(l), l


def test_extract_refs():
    refs = extract_refs("Voir l'article 17 du présent code, les articles 14 à 68 du CGI et l'article 98 du LPF. "
                        "Aussi l'article 92-k du Code Général des Impôts.")
    assert refs[0] == {"nums": ["17"], "range": False, "target": "self"}
    assert refs[1] == {"nums": ["14", "68"], "range": True, "target": "CGI"}
    assert refs[2]["target"] == "LPF"
    assert refs[3]["nums"] == ["92-k"] and refs[3]["target"] == "CGI"
