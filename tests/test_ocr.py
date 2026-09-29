from types import SimpleNamespace

from otr_rag.chunking import chunk_document
from otr_rag.cleaning import clean_md, split_front_matter
from otr_rag.ocr import build_raw_md, figures_report, load_env, parse_pdf

ENTRY = {"doc_id": "cahier_fiscal_2026", "doc_type": "generic", "version_year": 2026,
         "published": "2026-07-23", "source_url": "https://example.org/c26",
         "layout": {"drop_regex": [r"^MESURES PHARES DE LA LOI DE FINANCES,?\s*EXERCICE\s*2026\d*$"]}}

PAGES = [
    "# CAHIER FISCAL\n\n## PRINCIPALES DISPOSITIONS DE LA LOI DE FINANCES 2026\n",
    ("# LES MESURES NOUVELLES\n\nLes mesures adoptées sont au nombre de deux : elles concernent les droits d'accises "
     "et les droits de douane applicables aux entreprises certifiées par les organismes de l’Etat.\n\n"
     "**Article 12 de la LOFI :**\n\nUn taux réduit de 10 % est appliqué du 1er janvier au 31 décembre 2026 "
     "sur certains produits transformés localement. Un acte règlementaire précise les modalités.\n\n"
     "| Produit | Taux |\n| --- | --- |\n| Gasoil | 50 % |\n| Huiles | 10 % |\n\n"
     "MESURES PHARES DE LA LOI DE FINANCES, EXERCICE 20262"),
    "",
]


class FakeClient:
    """Reproduit la forme d'appel documentée du SDK `llama-cloud` (sans réseau)."""
    def __init__(self, pages):
        self.calls = []
        self.files = SimpleNamespace(create=self._create)
        self.parsing = SimpleNamespace(parse=self._parse)
        self._pages = pages

    def _create(self, file, purpose):
        self.calls.append(("files.create", file, purpose))
        return SimpleNamespace(id="file-123")

    def _parse(self, **kw):
        self.calls.append(("parsing.parse", kw))
        pages = [SimpleNamespace(markdown=m) for m in self._pages]
        return SimpleNamespace(markdown=SimpleNamespace(pages=pages))


def test_parse_pdf_call_shape(tmp_path):
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    fc = FakeClient(PAGES)
    pages = parse_pdf(pdf, tier="agentic", languages=("fr",), client=fc)
    assert pages == PAGES
    assert fc.calls[0] == ("files.create", str(pdf), "parse")
    kw = fc.calls[1][1]
    assert kw["file_id"] == "file-123" and kw["tier"] == "agentic" and kw["version"] == "latest"
    assert kw["processing_options"] == {"ocr_parameters": {"languages": ["fr"]}}
    assert kw["expand"] == ["markdown"]


def test_missing_api_key_gives_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("LLAMA_CLOUD_API_KEY", raising=False)
    try:
        parse_pdf(tmp_path / "x.pdf")
    except RuntimeError as e:
        assert "LLAMA_CLOUD_API_KEY" in str(e)
    else:
        raise AssertionError("aurait dû échouer sans clé")


def test_load_env_does_not_override_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# commentaire\nLLAMA_CLOUD_API_KEY="llx-from-file"\nAUTRE=1\n', encoding="utf-8")
    monkeypatch.setenv("AUTRE", "deja")
    monkeypatch.delenv("LLAMA_CLOUD_API_KEY", raising=False)
    load_env(env)
    import os
    assert os.environ["LLAMA_CLOUD_API_KEY"] == "llx-from-file" and os.environ["AUTRE"] == "deja"


def test_build_raw_md_markers_frontmatter_and_footer_drop():
    md, rep = build_raw_md(PAGES, ENTRY, "CAHIER-FISCAL-2026.pdf")
    assert md.count("<!--pg:") == 3 and "<!--pg:1-->" in md and "<!--pg:3-->" in md
    assert "structured_markdown: true" in md and "ocr: llamaparse" in md
    assert "MESURES PHARES" not in md and rep["dropped_lines"] == 1     # pied de page + n° collé retiré
    assert rep["empty_pages"] == [3] and rep["table_rows"] == 4


def test_figures_report_lists_rates_and_amounts():
    r = figures_report(PAGES + ["Franchise de 3 000 000 FCFA par an."], "cahier_fiscal_2026")
    assert "10 %" in r and "50 %" in r and "3 000 000 FCFA" in r
    assert "## Page 2" in r and "## Page 4" in r and "## Page 1" not in r


def test_ocr_markdown_goes_through_clean_and_chunking_without_losing_tables():
    raw, _ = build_raw_md(PAGES, ENTRY, "CAHIER-FISCAL-2026.pdf")
    clean, rep, fm = clean_md(raw)
    assert fm["structured_markdown"] is True
    assert "| Gasoil | 50 % |" in clean and "| Huiles | 10 % |" in clean
    assert "l'Etat" in clean and "’" not in clean                      # apostrophes normalisées
    assert "**Article 12 de la LOFI :**" in clean                       # sous-titre gras conservé

    front, body = split_front_matter(clean_with_front(raw, clean))
    chunks, st = chunk_document("cahier_fiscal_2026", front, body, min_chars=100)
    art = [c for c in chunks if c["title"] == "Article 12 de la LOFI :"]
    assert len(art) == 1
    assert "| Gasoil | 50 % |\n| Huiles | 10 % |" in art[0]["text"]     # tableau intact dans le chunk
    assert art[0]["hierarchy"] == {"h1": "LES MESURES NOUVELLES"}
    assert art[0]["page_start"] == 2
    assert not any("MESURES PHARES" in c["text"] for c in chunks)


def clean_with_front(raw, clean):
    header_end = raw.index("\n---\n", 4) + 5
    return raw[:header_end] + clean


def test_logo_photo_icon_noise_is_stripped():
    """Reproduit fidèlement le bruit trouvé dans cahier_fiscal_2026_ocr_test.md :
    lignes 'logo:'/'photo:' entières, icônes seules, et une icône EN LIGNE dans du texte utile."""
    pages = [
        "logo: OTR OFFICE TOGOLAIS DES RECETTES\n\nphoto: person reviewing financial documents\n\n# CAHIER FISCAL",
        ("icon: warning sign\n* de payer des frais via mobile money\n\n"
         "**[icon: telephone] 8201 POUR TOUTES INFORMATIONS**\n\n"
         "icon: facebook icon: instagram icon: youtube icon: twitter icon: tiktok\n"
         "Office Togolais des Recettes - OTR"),
    ]
    entry = {"doc_id": "d", "doc_type": "generic", "layout": {}}
    md, rep = build_raw_md(pages, entry, "d.pdf")
    assert "logo:" not in md and "photo:" not in md
    assert "icon:" not in md
    assert "# CAHIER FISCAL" in md
    assert "* de payer des frais via mobile money" in md            # le vrai contenu reste
    assert "**8201 POUR TOUTES INFORMATIONS**" in md                # icône retirée, texte gardé
    assert "Office Togolais des Recettes - OTR" in md
    assert rep["dropped_lines"] >= 2   # logo: + photo: (les lignes 'icon:' seules deviennent vides, pas comptées ici)


def test_document_specific_footer_boilerplate_is_dropped():
    pages = ["# Titre\n\nContenu utile.\n\nFEDERER POUR BATIR\nwww.otr.tg"]
    entry = {"doc_id": "d", "doc_type": "generic",
             "layout": {"drop_regex": [r"^FEDERER POUR BATIR$", r"^www\.otr\.tg$"]}}
    md, rep = build_raw_md(pages, entry, "d.pdf")
    assert "Contenu utile." in md
    assert "FEDERER POUR BATIR" not in md and "www.otr.tg" not in md
