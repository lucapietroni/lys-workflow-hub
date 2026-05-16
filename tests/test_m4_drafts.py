"""Test M4: repository drafts + policy + scaffold + allegati + generator + sender."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lys_workflow_hub.core.draft_repository import (
    CHANNEL_PEC,
    Draft,
    DraftAttachment,
    DraftRepository,
    STATI,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_READY,
    STATUS_SENT,
)
from lys_workflow_hub.core.mail_in_repository import (
    CASELLA_PEC,
    CAT_ALTRO,
    CAT_LIQUIDAZIONE,
    CAT_NOMINA_PERITO,
    CAT_PRESA_IN_CARICO,
    CAT_RICHIESTA_DOCUMENTI,
    MailRepository,
)
from lys_workflow_hub.workflows.risposte import (
    BOZZA_AUTO,
    BOZZA_NESSUNA,
    BOZZA_OPT_IN,
    BodyGenerationResult,
    EsitoSpedizione,
    ParametriSpedizione,
    ScaffoldContext,
    aggiorna_bozza,
    annulla_bozza,
    anonimizza_testo_originale,
    build_body,
    build_subject,
    conta_inclusi,
    crea_bozza_se_serve,
    deve_generare_auto,
    genera_body,
    genera_bozza,
    invia_bozza,
    policy_per,
    spedisci,
    suggerisci,
)


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def _make_classificazione(
    mail_repo: MailRepository,
    *,
    categoria: str,
    uid: int = 1,
    pratica_numero: int = 789,
    subject: str = "",
):
    subj = subject or f"Re: pratica {pratica_numero} - uid {uid}"
    mail = mail_repo.insert_mail(
        casella=CASELLA_PEC,
        uid_imap=uid,
        message_id=f"<{uid}@pec.generali.it>",
        in_reply_to="",
        references="",
        sender="sinistri@pec.generali.it",
        recipients="info@pec.lysauto.it",
        subject=subj,
        body_text="Vi chiediamo cortesemente di integrare con fotografie aggiuntive.",
        has_attachments=False,
        raw_eml_path=f"/tmp/x{uid}.eml",
        ricevuto_at=datetime(2026, 5, 15, 10, 30),
    )
    assert mail is not None
    classif = mail_repo.save_classification(
        mail_in_id=mail.id,
        pec_inviata_id=None,
        pratica_numero=pratica_numero,
        categoria=categoria,
        confidence=0.9,
        summary="La compagnia chiede integrazione foto",
        action_required=True,
        key_facts={"numero_sinistro": "S-2026-0001"},
        ai_model="haiku",
        ai_cost_eur=0.001,
    )
    return mail, classif


def _make_pratica_folder(tmp_path: Path, numero: int, *, foto: list[str], allegati: list[str]) -> Path:
    """Costruisce una cartella WinCar fittizia con foto e allegati di test."""
    root = tmp_path / "WinCar"
    foto_dir = root / "Pratiche" / str(numero) / "Pubblici" / "Foto"
    all_dir = root / "Pratiche" / str(numero) / "Pubblici" / "Allegati"
    foto_dir.mkdir(parents=True, exist_ok=True)
    all_dir.mkdir(parents=True, exist_ok=True)
    for name in foto:
        (foto_dir / name).write_bytes(b"fakephoto")
    for name in allegati:
        (all_dir / name).write_bytes(b"fakedoc")
    return root


# --------------------------------------------------------------------------- #
#  Policy
# --------------------------------------------------------------------------- #


def test_policy_richiesta_documenti_e_liquidazione_sono_auto():
    assert policy_per(CAT_RICHIESTA_DOCUMENTI) == BOZZA_AUTO
    assert policy_per(CAT_LIQUIDAZIONE) == BOZZA_AUTO
    assert deve_generare_auto(CAT_RICHIESTA_DOCUMENTI) is True


def test_policy_presa_in_carico_e_nessuna():
    assert policy_per(CAT_PRESA_IN_CARICO) == BOZZA_NESSUNA


def test_policy_nomina_perito_e_altro_sono_opt_in():
    assert policy_per(CAT_NOMINA_PERITO) == BOZZA_OPT_IN
    assert policy_per(CAT_ALTRO) == BOZZA_OPT_IN


def test_policy_categoria_sconosciuta_fallback_opt_in():
    assert policy_per("categoria_inesistente") == BOZZA_OPT_IN


# --------------------------------------------------------------------------- #
#  Repository
# --------------------------------------------------------------------------- #


def test_insert_e_get_round_trip(tmp_path: Path):
    repo = DraftRepository(db_path=tmp_path / "drafts.db")
    d = repo.insert_draft(
        mail_class_id=1, pratica_numero=789,
        subject="Re: test", body_html="<p>ciao</p>",
        to_address="sinistri@pec.x.it",
        cc_addresses=("audit@lysauto.it",),
        attachments=(
            DraftAttachment(path="/tmp/foto1.bmp", label="Danni esterni"),
            DraftAttachment(path="/tmp/perizia.pdf", label="Perizia", included=False),
        ),
        ai_model="claude-haiku-4-5", ai_cost_eur=0.0012,
    )
    assert d.id is not None
    assert d.status == STATUS_PENDING
    assert d.channel is None
    assert d.attachments[1].included is False
    assert len(d.attachments_included) == 1
    assert repo.get_draft(d.id) == d


def test_unicita_su_mail_class_id(tmp_path: Path):
    import sqlite3
    repo = DraftRepository(db_path=tmp_path / "drafts.db")
    repo.insert_draft(mail_class_id=10, pratica_numero=789)
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_draft(mail_class_id=10, pratica_numero=789)


def test_update_versiona_il_corpo(tmp_path: Path):
    repo = DraftRepository(db_path=tmp_path / "drafts.db")
    d = repo.insert_draft(mail_class_id=1, pratica_numero=1, body_html="<p>v1</p>")
    d2 = repo.update_draft(d.id, body_html="<p>v2</p>")
    assert d2.body_revisions == ("<p>v1</p>",)
    d3 = repo.update_draft(d.id, body_html="<p>v3</p>")
    assert d3.body_revisions == ("<p>v1</p>", "<p>v2</p>")


def test_mark_sent_rende_immutabile(tmp_path: Path):
    repo = DraftRepository(db_path=tmp_path / "drafts.db")
    d = repo.insert_draft(mail_class_id=1, pratica_numero=1)
    sent = repo.mark_sent(d.id, sent_eml_path="/tmp/out.eml", channel=CHANNEL_PEC)
    assert sent.status == STATUS_SENT
    assert sent.sent_at is not None
    with pytest.raises(ValueError):
        repo.update_draft(d.id, subject="nope")


def test_mark_cancelled_idempotente(tmp_path: Path):
    repo = DraftRepository(db_path=tmp_path / "drafts.db")
    d = repo.insert_draft(mail_class_id=1, pratica_numero=1)
    c1 = repo.mark_cancelled(d.id, reason="non serve")
    c2 = repo.mark_cancelled(d.id, reason="qualcos'altro")
    assert c2.status == STATUS_CANCELLED
    assert c2.cancel_reason == "non serve"  # idempotente: prima reason vince


def test_cancel_su_sent_solleva(tmp_path: Path):
    repo = DraftRepository(db_path=tmp_path / "drafts.db")
    d = repo.insert_draft(mail_class_id=1, pratica_numero=1)
    repo.mark_sent(d.id, sent_eml_path="/tmp/o.eml", channel=CHANNEL_PEC)
    with pytest.raises(ValueError):
        repo.mark_cancelled(d.id)


def test_list_per_pratica_e_per_status(tmp_path: Path):
    repo = DraftRepository(db_path=tmp_path / "drafts.db")
    d1 = repo.insert_draft(mail_class_id=1, pratica_numero=100)
    d2 = repo.insert_draft(mail_class_id=2, pratica_numero=100)
    repo.insert_draft(mail_class_id=3, pratica_numero=200)
    repo.mark_cancelled(d2.id)
    assert len(repo.list_per_pratica(100)) == 2
    assert len(repo.list_by_status(STATUS_PENDING)) == 2


def test_conta_per_status(tmp_path: Path):
    repo = DraftRepository(db_path=tmp_path / "drafts.db")
    d1 = repo.insert_draft(mail_class_id=1, pratica_numero=1)
    repo.insert_draft(mail_class_id=2, pratica_numero=2)
    repo.mark_cancelled(d1.id)
    c = repo.conta_per_status()
    assert c[STATUS_PENDING] == 1 and c[STATUS_CANCELLED] == 1


# --------------------------------------------------------------------------- #
#  Scaffold
# --------------------------------------------------------------------------- #


def test_scaffold_subject_con_riferimenti():
    ctx = ScaffoldContext(
        compagnia_nome="Generali Italia",
        pratica_numero=789,
        sinistro_numero="S-1234",
        veicolo_targa="AB123CD",
        subject_originale="Re: Re: Richiesta documenti",
    )
    s = build_subject(ctx)
    # Solo un "Re:" anche se l'originale ne aveva due
    assert s.startswith("Re: Richiesta documenti") or s.startswith("Re: ")
    assert "pratica 789" in s
    assert "S-1234" in s
    assert "AB123CD" in s


def test_scaffold_subject_senza_originale_fallback():
    ctx = ScaffoldContext(compagnia_nome="x", pratica_numero=1)
    s = build_subject(ctx)
    assert "Riscontro" in s


def test_scaffold_body_contiene_intestazione_e_firma():
    ctx = ScaffoldContext(
        compagnia_nome="Generali Italia",
        compagnia_pec="sinistri@pec.generali.it",
        pratica_numero=789,
        sinistro_numero="S-1234",
        polizza_numero="POL-99",
        carrozzeria_referente="Mario Bianchi",
        carrozzeria_pec="info@pec.lysauto.it",
        carrozzeria_telefono="06 12345",
        carrozzeria_comune="Roma",
        subject_originale="Richiesta documenti",
    )
    body = build_body("Confermiamo l'invio dei documenti richiesti.", ctx)
    assert "Spett.le Generali Italia" in body
    assert "sinistri@pec.generali.it" in body
    assert "Pratica nostra: 789" in body
    assert "Numero sinistro: S-1234" in body
    assert "POL-99" in body
    assert "Confermiamo l'invio dei documenti richiesti." in body
    assert "Mario Bianchi" in body
    assert "Roma" in body
    assert "Distinti saluti" in body
    assert "Carrozzeria LYS Auto srl" in body


def test_scaffold_strippa_chiusure_AI_residue():
    ctx = ScaffoldContext(compagnia_nome="x", pratica_numero=1)
    body_ai = (
        "Confermiamo l'invio dei documenti richiesti.\n\n"
        "Distinti saluti.\n"
    )
    body = build_body(body_ai, ctx)
    # La chiusura residua dell'AI viene rimossa, ma quella dello scaffold resta.
    # Il body finale contiene "Distinti saluti" UNA SOLA volta (quella dello scaffold).
    assert body.count("Distinti saluti") == 1


def test_anonimizza_testo_originale_sostituisce_assicurato_e_targa():
    ctx = ScaffoldContext(
        compagnia_nome="x",
        veicolo_targa="AB123CD",
        assicurato_nome="Mario Rossi",
    )
    anon = anonimizza_testo_originale(
        "Il signor Mario Rossi possiede il veicolo AB123CD.", ctx
    )
    assert "[ASSICURATO]" in anon
    assert "[TARGA]" in anon
    assert "Mario Rossi" not in anon
    assert "AB123CD" not in anon


# --------------------------------------------------------------------------- #
#  Allegati
# --------------------------------------------------------------------------- #


def test_suggerisci_pre_spunta_foto_e_denunce_per_richiesta_documenti(tmp_path: Path):
    root = _make_pratica_folder(
        tmp_path, 789,
        foto=["danniesterni.bmp", "danniinterni.bmp"],
        allegati=["denuncia_carabinieri.pdf", "libretto.pdf"],
    )
    res = suggerisci(
        archivio_root=root, numero_pratica=789,
        categoria_m3=CAT_RICHIESTA_DOCUMENTI,
    )
    by_name = {Path(a.path).name: a for a in res.allegati}
    assert by_name["danniesterni.bmp"].included is True
    assert by_name["danniinterni.bmp"].included is True
    assert by_name["denuncia_carabinieri.pdf"].included is True
    # libretto: "altro" → con richiesta_documenti viene incluso (potrebbe servire)
    assert by_name["libretto.pdf"].included is True


def test_suggerisci_non_pre_spunta_nulla_per_liquidazione(tmp_path: Path):
    root = _make_pratica_folder(
        tmp_path, 789,
        foto=["danniesterni.bmp"], allegati=["denuncia.pdf"],
    )
    res = suggerisci(
        archivio_root=root, numero_pratica=789,
        categoria_m3=CAT_LIQUIDAZIONE,
    )
    assert all(a.included is False for a in res.allegati)


def test_suggerisci_cartella_inesistente_lista_vuota(tmp_path: Path):
    res = suggerisci(
        archivio_root=tmp_path / "non-esiste",
        numero_pratica=999,
        categoria_m3=CAT_RICHIESTA_DOCUMENTI,
    )
    assert res.allegati == []


def test_conta_inclusi():
    atts = [
        DraftAttachment(path="/a", included=True),
        DraftAttachment(path="/b", included=False),
        DraftAttachment(path="/c", included=True),
    ]
    assert conta_inclusi(atts) == 2


# --------------------------------------------------------------------------- #
#  Body generator
# --------------------------------------------------------------------------- #


def test_body_generator_disabled_fallback_safe():
    r = genera_body(
        categoria=CAT_RICHIESTA_DOCUMENTI,
        summary_m3="La compagnia chiede integrazione",
        key_facts={},
        testo_originale_anon="testo originale",
        pratica_numero=789, sinistro_numero="S-1",
        polizza_numero="POL-1", veicolo_targa="AB123CD",
        api_key="anything", disabled=True,
    )
    assert r.fallback is True
    assert r.ai_cost_eur == 0.0
    assert "BOZZA NON GENERATA" in r.body


def test_body_generator_senza_api_key_fallback_safe():
    r = genera_body(
        categoria=CAT_LIQUIDAZIONE, summary_m3="ok", key_facts={},
        testo_originale_anon="", pratica_numero=1, sinistro_numero="",
        polizza_numero="", veicolo_targa="",
        api_key="", disabled=False,
    )
    assert r.fallback is True
    assert r.ai_model == "(fallback)"


def test_body_generator_con_mock_anthropic():
    """Mock anthropic SDK per testare il path felice."""
    fake_text = "Confermiamo la ricezione della Vs. comunicazione. Provvederemo a trasmettere i documenti richiesti entro 7 giorni lavorativi."
    mock_msg = MagicMock()
    block = MagicMock()
    block.text = fake_text
    mock_msg.content = [block]
    mock_msg.usage = MagicMock(input_tokens=400, output_tokens=80)

    mock_anthropic = MagicMock()
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    mock_anthropic.Anthropic.return_value = mock_client

    with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
        r = genera_body(
            categoria=CAT_RICHIESTA_DOCUMENTI,
            summary_m3="ok", key_facts={"numero_sinistro": "S-1"},
            testo_originale_anon="testo",
            pratica_numero=789, sinistro_numero="S-1",
            polizza_numero="POL", veicolo_targa="AB",
            api_key="sk-test", disabled=False,
        )
    assert r.fallback is False
    assert r.body == fake_text
    assert r.ai_cost_eur > 0


# --------------------------------------------------------------------------- #
#  Service: orchestrazione
# --------------------------------------------------------------------------- #


def test_crea_bozza_se_serve_per_richiesta_documenti_con_fallback_ai(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    _, classif = _make_classificazione(
        mail_repo, categoria=CAT_RICHIESTA_DOCUMENTI, uid=1,
    )
    draft = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo,
        ai_disabled=True,
    )
    assert draft is not None
    assert draft.status == STATUS_PENDING
    assert draft.pratica_numero == 789
    assert draft.channel == CHANNEL_PEC
    # body via scaffold + fallback
    assert "Spett.le" in draft.body_html
    assert "BOZZA NON GENERATA" in draft.body_html
    assert "Distinti saluti" in draft.body_html
    # destinatario pre-popolato dal sender della mail originale
    assert "@" in draft.to_address


def test_crea_bozza_idempotente(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    _, classif = _make_classificazione(mail_repo, categoria=CAT_RICHIESTA_DOCUMENTI)
    d1 = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
    )
    d2 = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
    )
    assert d1.id == d2.id


def test_crea_bozza_skip_per_presa_in_carico(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    _, classif = _make_classificazione(mail_repo, categoria=CAT_PRESA_IN_CARICO)
    assert crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
    ) is None
    forced = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
        forza=True,
    )
    assert forced is not None


def test_crea_bozza_con_archivio_pre_spunta_allegati(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    archivio = _make_pratica_folder(
        tmp_path, 789,
        foto=["danniesterni.bmp"], allegati=["denuncia.pdf"],
    )
    _, classif = _make_classificazione(mail_repo, categoria=CAT_RICHIESTA_DOCUMENTI)
    draft = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo,
        archivio_root=archivio, ai_disabled=True,
    )
    assert draft is not None
    nomi_inclusi = {Path(a.path).name for a in draft.attachments_included}
    assert "danniesterni.bmp" in nomi_inclusi
    assert "denuncia.pdf" in nomi_inclusi


def test_aggiorna_e_marca_ready(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    _, classif = _make_classificazione(mail_repo, categoria=CAT_RICHIESTA_DOCUMENTI)
    d = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
    )
    d2 = aggiorna_bozza(
        d.id, draft_repo=draft_repo,
        body_html="finale", mark_ready=True,
        to_address="s@pec.x.it",
    )
    assert d2.status == STATUS_READY
    assert d2.body_html == "finale"
    assert d2.to_address == "s@pec.x.it"


def test_annulla_bozza(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    _, classif = _make_classificazione(mail_repo, categoria=CAT_RICHIESTA_DOCUMENTI)
    d = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
    )
    can = annulla_bozza(d.id, draft_repo=draft_repo, reason="duplicata")
    assert can.status == STATUS_CANCELLED


# --------------------------------------------------------------------------- #
#  Sender
# --------------------------------------------------------------------------- #


def _params_dry(tmp_path: Path) -> ParametriSpedizione:
    return ParametriSpedizione(
        sender_email="info@pec.lysauto.it",
        sender_display="Carrozzeria LYS Auto srl",
        reply_to="",
        smtp_host="dummy",
        smtp_port=465,
        smtp_user="u",
        smtp_password="p",
        dry_run=True,
        archivio_root=tmp_path / "out",
        compagnia_nome="Generali Italia",
    )


def test_spedisci_dry_run_archivia_eml_e_marca_sent(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    _, classif = _make_classificazione(mail_repo, categoria=CAT_RICHIESTA_DOCUMENTI)
    d = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
    )
    # rendi inviabile (subject e body sono gia' valorizzati dallo scaffold)
    d = aggiorna_bozza(
        d.id, draft_repo=draft_repo,
        to_address="sinistri@pec.generali.it", mark_ready=True,
    )

    esito = spedisci(
        d, params=_params_dry(tmp_path),
        draft_repo=draft_repo, pec_log_repo=None,
    )
    assert esito.ok is True
    assert esito.dry_run is True
    assert esito.draft.status == STATUS_SENT
    assert Path(esito.eml_path).exists()
    # contenuto del .eml: il body del messaggio
    raw = Path(esito.eml_path).read_bytes()
    assert b"Spett.le" in raw or b"Subject" in raw


def test_spedisci_destinatario_mancante_ritorna_errore(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    _, classif = _make_classificazione(mail_repo, categoria=CAT_RICHIESTA_DOCUMENTI)
    d = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
    )
    # azzera il destinatario
    d = aggiorna_bozza(d.id, draft_repo=draft_repo, to_address="")
    esito = spedisci(
        d, params=_params_dry(tmp_path),
        draft_repo=draft_repo, pec_log_repo=None,
    )
    assert esito.ok is False
    assert "Destinatario" in esito.error
    assert esito.draft.status != STATUS_SENT


def test_spedisci_idempotente_su_sent(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    _, classif = _make_classificazione(mail_repo, categoria=CAT_RICHIESTA_DOCUMENTI)
    d = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
    )
    d = aggiorna_bozza(
        d.id, draft_repo=draft_repo,
        to_address="x@pec.y.it", mark_ready=True,
    )
    spedisci(d, params=_params_dry(tmp_path), draft_repo=draft_repo)
    sent = draft_repo.get_draft(d.id)
    # secondo invio: idempotente, no error
    esito2 = spedisci(sent, params=_params_dry(tmp_path), draft_repo=draft_repo)
    assert esito2.ok is True


# --------------------------------------------------------------------------- #
#  Invia bozza (wrapper)
# --------------------------------------------------------------------------- #


def test_invia_bozza_senza_params_solleva(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    _, classif = _make_classificazione(mail_repo, categoria=CAT_RICHIESTA_DOCUMENTI)
    d = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
    )
    with pytest.raises(ValueError, match="ParametriSpedizione"):
        invia_bozza(d.id, draft_repo=draft_repo, params=None)


def test_invia_bozza_dry_run_end_to_end(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    _, classif = _make_classificazione(mail_repo, categoria=CAT_RICHIESTA_DOCUMENTI)
    d = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
    )
    d = aggiorna_bozza(
        d.id, draft_repo=draft_repo,
        to_address="sinistri@pec.generali.it", mark_ready=True,
    )
    esito = invia_bozza(
        d.id, draft_repo=draft_repo, params=_params_dry(tmp_path),
    )
    assert esito.ok is True
    assert esito.draft.status == STATUS_SENT


def test_invia_bozza_su_cancelled_solleva(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    _, classif = _make_classificazione(mail_repo, categoria=CAT_RICHIESTA_DOCUMENTI)
    d = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
    )
    draft_repo.mark_cancelled(d.id)
    with pytest.raises(ValueError):
        invia_bozza(d.id, draft_repo=draft_repo, params=_params_dry(tmp_path))


def test_invia_bozza_su_sent_idempotente(tmp_path: Path):
    db = tmp_path / "all.db"
    mail_repo = MailRepository(db_path=db)
    draft_repo = DraftRepository(db_path=db)
    _, classif = _make_classificazione(mail_repo, categoria=CAT_RICHIESTA_DOCUMENTI)
    d = crea_bozza_se_serve(
        classif, draft_repo=draft_repo, mail_repo=mail_repo, ai_disabled=True,
    )
    draft_repo.mark_sent(d.id, sent_eml_path="/tmp/already.eml", channel=CHANNEL_PEC)
    esito = invia_bozza(d.id, draft_repo=draft_repo, params=None)
    assert esito.ok is True
    assert esito.draft.status == STATUS_SENT
    assert esito.eml_path == "/tmp/already.eml"
