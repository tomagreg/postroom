"""Analyse des mails, sur des messages synthétiques (aucun vrai mail dans le dépôt)."""

from email.message import EmailMessage

from postroom.ingestion import CORPS_MAX, analyser, html_vers_texte


def _mail(**entetes) -> EmailMessage:
    m = EmailMessage()
    m["From"] = entetes.get("expediteur", "Boutique <commandes@boutique.example>")
    m["To"] = "Moi <moi@example.org>"
    m["Cc"] = "autre@example.org"
    m["Subject"] = entetes.get("objet", "Votre commande")
    m["Date"] = entetes.get("date", "Mon, 16 Nov 2026 10:30:00 +0100")
    m["Message-ID"] = "<abc@boutique.example>"
    return m


def test_multipart_texte_et_html():
    m = _mail()
    m.set_content("Bonjour,\n\nVotre commande 123 est partie : https://suivi.example/colis/123.\n" + "x" * 300)
    m.add_alternative(
        "<html><head><style>p{color:red}</style><title>t</title></head><body>"
        "<p>Bonjour</p><script>alert(1)</script><a href='https://www.boutique.example/c/123'>Suivre</a>"
        "</body></html>",
        subtype="html",
    )
    a = analyser(m.as_bytes())
    assert a.objet == "Votre commande"
    assert a.expediteur == "Boutique <commandes@boutique.example>"
    assert a.destinataires == ["moi@example.org", "autre@example.org"]
    assert a.message_id == "<abc@boutique.example>"
    assert a.date == "2026-11-16T09:30:00+00:00"
    assert a.corps_texte.startswith("Bonjour,\n\nVotre commande 123")
    assert {l["url"] for l in a.liens} == {"https://www.boutique.example/c/123", "https://suivi.example/colis/123"}
    assert {l["domaine"] for l in a.liens} == {"boutique.example", "suivi.example"}
    assert a.pieces_jointes == []


def test_html_seul_sans_script_ni_style():
    m = _mail()
    m.set_content(
        "<html><head><style>.a{}</style></head><body><h1>Soldes</h1><p>-50&nbsp;% sur tout</p>"
        "<script>var x = 1;</script><a href='mailto:x@y'>m</a><a href='https://promo.example/?a=1'>ici</a></body></html>",
        subtype="html",
    )
    a = analyser(m.as_bytes())
    assert "Soldes" in a.corps_texte and "-50 % sur tout" in a.corps_texte
    assert "var x" not in a.corps_texte and ".a{}" not in a.corps_texte and "<" not in a.corps_texte
    assert [l["url"] for l in a.liens] == ["https://promo.example/?a=1"]


def test_texte_bouchon_remplace_par_le_html():
    m = _mail()
    m.set_content("Affichez ce mail dans votre navigateur.")
    m.add_alternative("<p>" + "Contenu réel de la newsletter. " * 20 + "</p>", subtype="html")
    assert analyser(m.as_bytes()).corps_texte.startswith("Contenu réel")


def test_objet_encode_et_latin1():
    brut = (
        b"From: =?iso-8859-1?q?Andr=E9?= <a@example.org>\r\n"
        b"Subject: =?utf-8?b?UsOpc2VydmF0aW9uIGNvbmZpcm3DqWU=?=\r\n"
        b"Date: Mon, 16 Nov 2026 10:30:00 +0000\r\n"
        b"Content-Type: text/plain; charset=iso-8859-1\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n\r\n"
        b"Caf\xe9 \xe0 15 h.\r\n"
    )
    a = analyser(brut)
    assert a.objet == "Réservation confirmée"
    assert a.expediteur == "André <a@example.org>"
    assert a.corps_texte == "Café à 15 h."


def test_date_mal_formee_utilise_la_date_interne():
    brut = b"From: a@example.org\r\nSubject: x\r\nDate: pas une date\r\n\r\ncorps\r\n"
    assert analyser(brut, "2026-11-01T08:00:00+00:00").date == "2026-11-01T08:00:00+00:00"
    assert analyser(b"Subject: x\r\n\r\ncorps\r\n").date is None


def test_charset_inconnu():
    brut = b"Subject: x\r\nContent-Type: text/plain; charset=x-inconnu\r\n\r\nbonjour\r\n"
    assert analyser(brut).corps_texte == "bonjour"


def test_vraie_piece_jointe_et_image_integree():
    m = _mail(objet="Facture")
    m.set_content("Voir la facture jointe.")
    m.add_related(b"\x89PNG logo", maintype="image", subtype="png", cid="<logo@x>")
    m.add_attachment(b"%PDF-1.4 facture", maintype="application", subtype="pdf", filename="facture.pdf")
    a = analyser(m.as_bytes())
    assert a.pieces_jointes == [{"nom": "facture.pdf", "taille": 16, "type": "application/pdf"}]
    assert a.corps_texte == "Voir la facture jointe."


def test_image_envoyee_en_piece_jointe_compte():
    m = _mail()
    m.set_content("photo")
    m.add_attachment(b"\xff\xd8 jpeg", maintype="image", subtype="jpeg", filename="photo.jpg")
    assert [p["nom"] for p in analyser(m.as_bytes()).pieces_jointes] == ["photo.jpg"]


def test_mail_transfere_en_piece_jointe():
    interne = _mail(objet="Interne")
    interne.set_content("texte interne")
    m = _mail()
    m.set_content("voir le mail joint")
    m.add_attachment(interne)
    a = analyser(m.as_bytes())
    assert [p["type"] for p in a.pieces_jointes] == ["message/rfc822"]
    assert a.corps_texte == "voir le mail joint"


def test_reponse_entetes_de_fil():
    brut = (
        b"From: a@example.org\r\nSubject: Re: Question\r\nIn-Reply-To: <q1@example.org>\r\n"
        b"References: <q0@example.org>\r\n <q1@example.org>\r\n\r\nOui.\r\n"
    )
    a = analyser(brut)
    assert a.in_reply_to == "<q1@example.org>"
    assert a.refs == "<q0@example.org> <q1@example.org>"


def test_corps_tronque_et_liens_plafonnes():
    m = _mail()
    liens = " ".join(f"https://l{i}.example/" for i in range(80))
    m.set_content(liens + "\n" + "a" * (CORPS_MAX + 10))
    a = analyser(m.as_bytes())
    assert len(a.corps_texte) <= CORPS_MAX + 20 and a.corps_texte.endswith("[…tronqué]")
    assert len(a.liens) == 50


def test_html_vers_texte_blocs():
    texte, liens = html_vers_texte("<div>a</div><div>b<br>c</div><table><tr><td>d</td></tr></table>")
    assert [l for l in texte.split("\n") if l] == ["a", "b", "c", "d"]
    assert liens == []


def test_head_ouvert_deux_fois():
    texte, _ = html_vers_texte("<html><head><head><title>t</title><style>x</style></head><body><p>Visible</p></body></html>")
    assert texte == "Visible"


def test_html_dans_la_partie_texte():
    m = _mail()
    m.set_content("<html><body><div>Bonjour</div><p>Suite</p><a href='https://a.example/x'>lien</a></body></html>")
    m.add_alternative("<p>autre</p>", subtype="html")
    a = analyser(m.as_bytes())
    assert "<" not in a.corps_texte and "Bonjour" in a.corps_texte
    assert [l["url"] for l in a.liens] == ["https://a.example/x"]
