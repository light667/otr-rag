from otr_rag.cleaning import CleanReport, clean_md, fix_text, join_pair, normalize_chars, reflow


def R():
    return CleanReport()


def test_hyphen_kept_for_compounds_and_removed_for_word_breaks():
    r = R()
    assert join_pair("dispositions ci-", "dessous et", r) == "dispositions ci-dessous et"
    assert join_pair("les plus-values des exemp-", "tions prévues", r) == "les plus-values des exemptions prévues"
    assert join_pair("aux personnes eux-", "mêmes", r) == "aux personnes eux-mêmes"
    assert join_pair("Loi N°2022-", "022 du", r) == "Loi N°2022-022 du"      # chiffre : on garde
    assert join_pair("les Etats-", "Unis", r) == "les Etats-Unis"            # majuscule : on garde
    assert join_pair("le montant - ", "hors taxe", r).startswith("le montant -")  # tiret d'incise : pas une césure


def test_known_glued_compounds():
    r = R()
    assert fix_text("conformément aux règles cidessous et euxmêmes", r) == "conformément aux règles ci-dessous et eux-mêmes"


def test_ordinals_and_spacing():
    r = R()
    assert fix_text("avant le 1 er janvier ( voir ) , puis", r) == "avant le 1er janvier (voir), puis"


def test_normalize_chars():
    r = R()
    assert normalize_chars("l’impôt\u00a0sur  les ﬁnances\u200b", r) == "l'impôt sur les finances"


def test_reflow_paragraphs_lists_and_lone_punct():
    lines = [
        "**Art. 14 :** Sont compris dans les revenus",
        "fonciers les revenus des propriétés bâties",
        ":",
        "a) de l'outillage des établissements ;",
        "b) de toutes installations",
        "commerciales ;",
        "- premier point ;",
        "₋ second point.",
        "Le produit est arrondi.",
        "Un nouveau paragraphe commence ici.",
    ]
    out = [l for l in reflow(lines, 40, R()) if l]
    assert out[0] == "**Art. 14 :** Sont compris dans les revenus fonciers les revenus des propriétés bâties :"
    assert out[1] == "a) de l'outillage des établissements ;"
    assert out[2] == "b) de toutes installations commerciales ;"
    assert out[3] == "- premier point ;"
    assert out[4] == "- second point."
    assert out[5] == "Le produit est arrondi."
    assert out[6] == "Un nouveau paragraphe commence ici."


def test_reflow_page_marker_inside_paragraph_and_trailing():
    lines = ["Art texte qui se poursuit sur", "<!--pg:2-->", "la page suivante.", "<!--pg:3-->", "", "<!--art:9-->"]
    out = [l for l in reflow(lines, 40, R()) if l]
    assert out[0] == "Art texte qui se poursuit sur <!--pg:2--> la page suivante."
    assert out[1] == "<!--pg:3-->"        # marqueur final : il ouvre le bloc suivant
    assert out[2] == "<!--art:9-->"


def test_bareme_becomes_table():
    lines = ["Tableau 1 : Barème", "de 0 A 900 000 exonéré", "de 900 001 A 3 000 000 3%", "Plus de 20 000 000 35%", "Fin."]
    out = reflow(lines, 40, R())
    assert "| de 0 à 900 000 | exonéré |" in out
    assert "| plus de 20 000 000 | 35% |" in out


def test_clean_md_keeps_front_matter_p95():
    md = "---\ndoc_id: x\np95_len: 40\n---\nUne ligne.\n"
    clean, rep, fm = clean_md(md)
    assert fm["doc_id"] == "x" and clean.strip() == "Une ligne."
