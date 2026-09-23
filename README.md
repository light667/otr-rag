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

2. **Diagnostic**  :
   ```bash
   python scripts/00_inspect.py
   python scripts/00_inspect.py --doc cgi_2023 --dump 1,12    # + liste des lignes des pages 1 et 12
   ```
   
3. **Extraction** : `python scripts/01_extract.py` -> `data/interim/<doc>.raw.md` + `.extract.json`
4. **Nettoyage + contrôle** : `python scripts/02_clean.py [--show 3]` -> `<doc>.clean.md` + `.clean.json`
   Le script affiche `⚠ À REGARDER` si la numérotation des articles a des trous, des doublons, un ordre
   non croissant, des appels de note sans note, ou des numéros de page restés dans le texte.



