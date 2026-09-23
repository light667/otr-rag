"""Motifs partagés par les étapes inspect / extract / clean.

Chaque motif vient d'une observation sur le texte réel des documents OTR
(CGI mis à jour 2023, rescrits fiscaux). Les commentaires "observé :" citent
la forme rencontrée, pour que tu puisses vérifier et ajuster sur tes PDF.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Hiérarchie du code : LIVRE > PARTIE > TITRE > CHAPITRE > Section > Paragraphe
# --------------------------------------------------------------------------
_ORD_M = (r"(?:PREMIER|SECOND|DEUXI[ÈE]ME|TROISI[ÈE]ME|QUATRI[ÈE]ME|CINQUI[ÈE]ME|"
          r"SIXI[ÈE]ME|SEPTI[ÈE]ME|HUITI[ÈE]ME|NEUVI[ÈE]ME|DIXI[ÈE]ME)")
_ORD_F = (r"(?:PREMI[ÈE]RE|DEUXI[ÈE]ME|TROISI[ÈE]ME|QUATRI[ÈE]ME|CINQUI[ÈE]ME|"
          r"SIXI[ÈE]ME|SEPTI[ÈE]ME|HUITI[ÈE]ME|NEUVI[ÈE]ME|DIXI[ÈE]ME)")
_NUM = r"(?:[IVXLC]+|\d+)"
_SEP = r"[:\-–—]"

# (niveau markdown, clé, regex). Le titre peut être vide : il continue alors
# sur la ligne suivante (gérée dans rawmd.py).
# observé : "LIVRE PREMIER : IMPÔTS AU PROFIT DU BUDGET DE L'ETAT",
#           "PREMIERE PARTIE : IMPÔTS DIRECTS ET TAXES ASSIMILEES",
#           "TITRE PREMIER : IMPÔTS DIRECTS",
#           "CHAPITRE I : IMPÔT SUR LE REVENU DES PERSONNES PHYSIQUES",
#           "Section 1 : Définition et structure",
#           "Paragraphe 1 : Revenus fonciers" / "Paragraphe 1 - Personnes physiques…"
HEADING_PATTERNS: list[tuple[int, str, re.Pattern]] = [
    (1, "livre", re.compile(rf"^LIVRE\s+({_ORD_M}|{_NUM})\b(?:\s*{_SEP}\s*(.*))?$", re.I)),
    (2, "partie", re.compile(rf"^({_ORD_F}|{_NUM}(?:[ÈE]RE|[ÈE]ME)?)\s+PARTIE\b(?:\s*{_SEP}\s*(.*))?$", re.I)),
    (3, "titre", re.compile(rf"^TITRE\s+({_ORD_M}|{_NUM})\b(?:\s*{_SEP}\s*(.*))?$", re.I)),
    (4, "chapitre", re.compile(rf"^CHAPITRE\s+({_ORD_M}|{_NUM})\b(?:\s*{_SEP}\s*(.*))?$", re.I)),
    (5, "section", re.compile(rf"^SECTION\s+({_NUM})\s*{_SEP}\s*(.*)$", re.I)),
    (6, "paragraphe", re.compile(rf"^PARAGRAPHE\s+({_NUM})\s*{_SEP}\s*(.*)$", re.I)),
]

# Titres de sous-partie du type "I - Définition…", "II – Exonérations",
# "A-Revenus d'emplois", "IV-Période d'imposition" (observé, lignes isolées
# courtes, toujours suivies d'un article ou d'un autre sous-titre).
SUBHEADING = re.compile(r"^(?:I{1,3}|IV|VI{0,3}|IX|X|[A-H])\s*[-–—]\s*\S.{2,90}$")

# --------------------------------------------------------------------------
# Articles
# --------------------------------------------------------------------------
# observé : "Art. premier : …", "Art. 2 : 1 - Sous réserve…", "Art.50 : …",
#           "Art. 84: Sont exonérées…"
ARTICLE_START = re.compile(
    r"^Art(?:icle)?s?\.?\s*(premier|1er|\d{1,3}(?:\s?(?:bis|ter|quater|quinquies))?(?:-\d{1,2})?)"
    r"\s*(?:er)?\s*(?::|[.\-–](?=\s))\s*(.*)$"
)


def article_id(raw: str) -> str:
    """'premier' / '1er' -> '1' ; '12 bis' -> '12bis'."""
    raw = raw.strip().lower().replace(" ", "")
    return "1" if raw in {"premier", "1er"} else raw


def article_sort_key(aid: str) -> tuple[int, str]:
    m = re.match(r"(\d+)(.*)", aid)
    return (int(m.group(1)), m.group(2)) if m else (10**6, aid)


# --------------------------------------------------------------------------
# Rescrits : "R1 : Retenue à la source des sommes versées à des non-résidents"
# --------------------------------------------------------------------------
RESCRIT_START = re.compile(r"^R\s?(\d{1,3})\s*[:\-–]\s*(.+)$")

# --------------------------------------------------------------------------
# Artefacts d'extraction observés
# --------------------------------------------------------------------------
# Numéro de page isolé sur sa ligne ("12"), y compris en plein milieu d'une phrase.
DIGITS_ONLY = re.compile(r"^\s*\d{1,4}\s*$")

# Début d'une note de bas de page : la loi de finances qui a modifié le texte.
# observé : "Loi N°2022-022 du 27 décembre 2022 portant Loi de Finances, Exercice 2023",
#           "Loi N°2020-019 précitée."
LEGAL_START = re.compile(r"^(Loi|Ordonnance|D[ée]cret|Arr[êe]t[ée]|Directive|R[èe]glement)\b")

# Marqueur de note collé au texte suivant, quand l'extraction a perdu l'exposant.
# observé : "1Toutefois, …", "2Le revenu d'emploi…", "6Art. 74 : …",
#           "36-La prime…" (marqueur 3 + item "6-"), "83 - Les associés…" (marqueur 8 + item "3 -")
GLUED_MARKER = re.compile(r"^(\d{1,2})(?=(?:Art\.|[A-ZÉÈÀÂ][a-zà-ÿ]+|\d{1,2}\s*[-–]\s*\S))")

# Puces et énumérations (début de bloc).
# observé : "- revenus fonciers ;", "₋ 100 % si…", "a) …", "1 - …", "1- …", "I - …"
BULLET_CHARS = "-•·▪‣₋–—*\uf0b7\u2022\u25cf\u25aa"
LIST_START = re.compile(
    rf"^(?:[{re.escape(BULLET_CHARS)}]\s+"
    r"|\d{1,2}\s*[).]\s+"          # 1) 1.
    r"|\d{1,2}\s*[-–—]\s*\S"       # 1 - / 1-
    r"|\d{1,2}°\s"                 # 1° 
    r"|[a-z]\)\s*"                 # a)
    r"|[ivx]{1,4}\)\s*"            # ii)
    r")"
)

# Ligne isolée de ponctuation (observé : ":" ou ";" seul sur sa ligne après un retour à la ligne)
LONE_PUNCT = re.compile(r"^\s*[:;,.]\s*$")

# Barème en lignes de texte (observé, Tableau 1 de l'art. 74) :
#   "de 0 A 900 000 exonéré", "de 900 001 A 3 000 000 3%", "Plus de 20 000 000 35%"
_RATE = r"(\d+(?:[.,]\d+)?\s?%|exon[ée]r[ée]e?s?|n[ée]ant)"
BAREME_ROW = re.compile(rf"^(?:de)\s+(\d[\d\s]*?)\s+[AÀaà]\s+(\d[\d\s]*?)\s+{_RATE}\s*$", re.I)
BAREME_LAST = re.compile(rf"^(?:plus de|au-del[àa] de|sup[ée]rieur[e]? [àa])\s+(\d[\d\s]*?)\s+{_RATE}\s*$", re.I)
TABLE_CAPTION = re.compile(r"^Tableau\s+\d+\s*:", re.I)

# Marqueurs posés par l'étape extract
PAGE_MARK = re.compile(r"<!--pg:(\d+)-->")
ART_MARK = re.compile(r"<!--art:([^\s>]+)-->")
FN_DEF = re.compile(r"^\[\^(fn\d+)\]:\s*(.*)$")
FN_REF = re.compile(r"\[\^(fn\d+)\]")

# Sup. ordinaux extraits comme exposants ("1", "er")
ORDINAL_SUFFIXES = {"er", "ère", "re", "e", "ème", "eme", "èmes", "emes", "nd", "nde", "ers", "ères"}
MONTHS = ("janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|"
          "octobre|novembre|décembre|decembre")

# --------------------------------------------------------------------------
# Références croisées ("l'article 17 du présent code", "articles 14 à 68",
# "article 98 du LPF", "article 92-k du CGI")
# --------------------------------------------------------------------------
_ART_NUM = r"\d{1,3}(?:-[a-z0-9]{1,2})?"
REF_PATTERN = re.compile(
    rf"\b[Aa]rt(?:icles?|\.)\s*(?P<nums>{_ART_NUM}(?:\s*(?:,|et|à|au)\s*{_ART_NUM})*)"
    r"(?:\s+(?:du|de la|des|dudit)\s+(?P<target>CGI|LPF|C\.G\.I\.?|L\.P\.F\.?|"
    r"Code G[ée]n[ée]ral des Imp[ôo]ts|Livre des Proc[ée]dures Fiscales|"
    r"pr[ée]sent code|pr[ée]sent livre|code des investissements|code des douanes|"
    r"code du travail|code civil))?",
    re.I,
)


def extract_refs(text: str) -> list[dict]:
    """Références d'articles trouvées dans un texte.

    -> [{"nums": ["14","68"], "range": True, "target": "self"|"CGI"|"LPF"|None|autre}]
    """
    out = []
    for m in REF_PATTERN.finditer(text):
        nums = re.findall(_ART_NUM, m.group("nums"))
        is_range = bool(re.search(r"\b(à|au)\b", m.group("nums")))
        t = (m.group("target") or "").lower().replace(".", "").strip()
        if t in {"présent code", "present code", "présent livre", "present livre"}:
            target = "self"
        elif t in {"cgi", "code général des impôts", "code general des impots"}:
            target = "CGI"
        elif t in {"lpf", "livre des procédures fiscales", "livre des procedures fiscales"}:
            target = "LPF"
        else:
            target = t or None
        out.append({"nums": nums, "range": is_range, "target": target})
    return out
