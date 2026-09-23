# otr-fiscal-rag - extraction et nettoyage (semaine 1, jours 1-2)

Pipeline PDF -> Markdown propre pour les documents fiscaux de l'OTR (Togo).
Chaque étape s'adapte à la **nature** du document (`doc_type` dans `config/docs.yaml`) :

| doc_type | documents | ce que l'extraction exploite |
|---|---|---|
| `code` | CGI 2023, CGI + LPF 2025 | hiérarchie LIVRE > PARTIE > TITRE > CHAPITRE > Section > Paragraphe, `Art. N :`, notes de bas de page (loi de finances qui a modifié l'article), barèmes |
| `rescrits` | rescrits fiscaux | un cas par `R<n> : titre`, références croisées vers CGI / LPF |
| `generic` | cahiers fiscaux 2025 et 2026 | **provisoire** : titres par taille de police, tableaux -> Markdown. À remplacer après lecture de `00_inspect` |

## Installation
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q tests          # 61 tests, ~3 s
```

## Utilisation
1. Copie les 5 PDF dans `data/raw/` et vérifie les noms dans `config/docs.yaml` (`file:`).
2. **Diagnostic** (ne modifie rien) :
   ```bash
   python scripts/00_inspect.py
   python scripts/00_inspect.py --doc cgi_2023 --dump 1,12    # + liste des lignes des pages 1 et 12
   ```
   Lis `data/inspect/*.md`. Points à trancher :
   - numéro de page hors des zones de marge -> régler `layout.top_zone` / `bottom_zone` ;
   - **CGI + LPF 2025 : la numérotation retombe à 1 quelque part** -> renseigner `subdocs` (sinon "article 17" est ambigu) ;
   - pages de sommaire -> `layout.skip_pages` ;
   - "probable 2 colonnes" -> me le dire, l'ordre de lecture serait faux.
3. **Extraction** : `python scripts/01_extract.py` -> `data/interim/<doc>.raw.md` + `.extract.json`
4. **Nettoyage + contrôle** : `python scripts/02_clean.py [--show 3]` -> `<doc>.clean.md` + `.clean.json`
   Le script affiche `⚠ À REGARDER` si la numérotation des articles a des trous, des doublons, un ordre
   non croissant, des appels de note sans note, ou des numéros de page restés dans le texte.

## Format du Markdown produit
- `# … ######` : LIVRE, PARTIE, TITRE, CHAPITRE, Section, Paragraphe (le niveau est fixe, quel que soit le document).
- `<!--art:17-->` puis `**Art. 17 :** texte` : un article = une unité, identifiant explicite.
- `<!--pg:12-->` : numéro de page PDF, **inséré dans le paragraphe** quand il traverse un saut de page (permet `page_debut` / `page_fin`).
- `[^fn3]` dans le texte + `[^fn3]: Loi N°2022-022 … Exercice 2023` après l'article : la loi de finances qui a modifié l'article (métadonnée `amended_by` au chunking).
- `**I - Définition…**` : sous-titre à l'intérieur d'un chapitre.
- barèmes -> tableaux Markdown ; le fichier `.extract.json` contient la correspondance page PDF / page imprimée.

## Ce qui est vérifié, et ce qui ne l'est pas
- Vérifié : les motifs (titres, articles, notes, barème) viennent du texte réel du CGI 2023 et des rescrits ;
  le pipeline complet est testé sur des PDF synthétiques qui reproduisent ces défauts (contenu inventé).
- **Non vérifié** : le rendu sur tes vrais PDF (position du numéro de page, taille de police des notes, exposants).
  C'est le rôle de `00_inspect` ; les réglages sont dans `config/docs.yaml`.
- **Non vu** : le contenu des cahiers fiscaux et du PDF 2025 (structure exacte, sommaire, coupure CGI / LPF).
- Limite connue : si l'exposant d'une note est perdu, un marqueur collé du type `12 - …` peut être confondu avec
  un item de liste sur une page qui porte la note n°1 (rare ; la détection par exposant évite ce cas).

## Suite : chunking (étape suivante)
Un article = une unité (les très longs, comme l'art. 99 avec ses a) à s), se découpent par alinéa en gardant
la hiérarchie en préfixe) ; les rescrits se découpent en (faits / analyse / conclusion) ; références croisées via
`otr_rag.patterns.extract_refs`, préfixées par le code (`CGI:17`, `LPF:17`).
