"""Test del pipeline M3: matcher, classifier, repository, notifier (mocked)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from lys_workflow_hub.core.mail_in_repository import (
    CASELLA_PEC,
    CAT_ALTRO,
    CAT_LIQUIDAZIONE,
    CAT_NOMINA_PERITO,
    CAT_PRESA_IN_CARICO,
    MailIn,
    MailRepository,
)
from lys_workflow_hub.core.pec_log_repository import (
    ESITO_OK,
    PecLogRepository,
)
from lys_workflow_hub.integrations.ai_classifier import (
    _compute_cost_eur,
    _safe_parse_json,
    classify,
)
from lys_workflow_hub.integrations.notifier import (
    _format_push,
    _format_summary_body,
    notify_batch,
)
from lys_workflow_hub.workflows.risposte.matcher import (
    METHOD_HEADER_IN_REPLY_TO,
    METHOD_HEURISTIC,
    METHOD_NONE,
    _estrai_segnali,
    match_mail,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pec(
    pec_repo: PecLogRepository,
    *,
    numero_pratica: int = 789,
    message_id: str = "<177874@lysauto.local>",
    oggetto: str = "Richiesta - Veicolo targato AB123CD - Polizza n. POL-12345",
    destinatario: str = "sin@pec.generali.it",
):
    return pec_repo.log(
        numero_pratica=numero_pratica,
        compagnia_id=1,
        compagnia_nome="Generali Italia",
        destinatario_pec=destinatario,
        mittente_pec="info@pec.lysauto.it",
        oggetto=oggetto,
        body="Spett.le Compagnia",
        allegati=["x.pdf"],
        path_eml="x.eml",
        message_id=message_id,
        esito=ESITO_OK,
    )


def _make_mail(
    mail_repo: MailRepository,
    *,
    uid: int = 1,
    subject: str = "Re: Richiesta",
    body: str = "Abbiamo preso in carico.",
    in_reply_to: str = "",
    sender: str = "sinistri@pec.generali.it",
):
    return mail_repo.insert_mail(
        casella=CASELLA_PEC,
        uid_imap=uid,
        message_id=f"<{uid}@pec.generali.it>",
        in_reply_to=in_reply_to,
        references="",
        sender=sender,
        recipients="info@pec.lysauto.it",
        subject=subject,
        body_text=body,
        has_attachments=False,
        raw_eml_path="/tmp/x.eml",
        ricevuto_at=datetime(2026, 5, 15, 10, 30),
    )


# ---------------------------------------------------------------------------
# Matcher: estrattore segnali
# ---------------------------------------------------------------------------


def test_estrai_segnali_riconosce_targa_pratica_polizza():
    s = _estrai_segnali(
        "Sinistro AB123CD - Ns rif. pratica 789 - polizza POL-12345"
    )
    assert "AB123CD" in s["targhe"]
    assert 789 in s["pratiche"]
    assert "POL-12345" in s["polizze"]


def test_estrai_segnali_normalizza_targa_con_spazi():
    s = _estrai_segnali("Targa AB 123 CD danneggiata")
    assert "AB123CD" in s["targhe"]


def test_estrai_segnali_string_vuota_ritorna_set_vuoti():
    s = _estrai_segnali("")
    assert s == {"targhe": set(), "pratiche": set(), "polizze": set()}


# ---------------------------------------------------------------------------
# Matcher: match_mail
# ---------------------------------------------------------------------------


def test_match_per_in_reply_to(tmp_path: Path):
    pec_repo = PecLogRepository(db_path=tmp_path / "pec.db")
    mail_repo = MailRepository(db_path=tmp_path / "pec.db")
    pec = _make_pec(pec_repo, message_id="<aaa@lysauto.local>")
    mail = _make_mail(
        mail_repo, uid=1, in_reply_to="<aaa@lysauto.local>",
    )
    r = match_mail(mail, pec_repo)
    assert r.method == METHOD_HEADER_IN_REPLY_TO
    assert r.confidence == 1.0
    assert r.pratica_numero == 789
    assert r.pec_inviata_id == pec.id


def test_match_euristico_quando_no_header(tmp_path: Path):
    pec_repo = PecLogRepository(db_path=tmp_path / "pec.db")
    mail_repo = MailRepository(db_path=tmp_path / "pec.db")
    _make_pec(pec_repo)  # pratica 789, targa AB123CD, polizza POL-12345
    mail = _make_mail(
        mail_repo,
        uid=2,
        subject="Sinistro AB123CD - presa in carico",
        body="Ns rif. sinistro 789 - polizza POL-12345.",
    )
    r = match_mail(mail, pec_repo)
    assert r.method == METHOD_HEURISTIC
    assert r.pratica_numero == 789


def test_match_none_se_nessun_segnale(tmp_path: Path):
    pec_repo = PecLogRepository(db_path=tmp_path / "pec.db")
    mail_repo = MailRepository(db_path=tmp_path / "pec.db")
    _make_pec(pec_repo)
    mail = _make_mail(
        mail_repo,
        uid=3,
        subject="Newsletter mensile",
        body="Articoli vari sul mondo assicurativo.",
        sender="news@unipol.it",
    )
    r = match_mail(mail, pec_repo)
    assert r.method == METHOD_NONE
    assert r.pratica_numero is None


# ---------------------------------------------------------------------------
# AI Classifier
# ---------------------------------------------------------------------------


def test_classifier_in_modalita_disabled_ritorna_altro():
    result = classify(
        subject="Test",
        sender="x@y.it",
        body="Corpo del messaggio.",
        api_key="anyway",
        disabled=True,
    )
    assert result.categoria == CAT_ALTRO
    assert result.confidence == 0.0
    assert result.ai_cost_eur == 0


def test_classifier_senza_api_key_ritorna_altro():
    result = classify(
        subject="Test", sender="x@y.it", body="Corpo.",
        api_key="", disabled=False,
    )
    assert result.categoria == CAT_ALTRO


def test_classifier_con_mock_anthropic():
    """Mock dell'SDK Anthropic per testare il parsing della risposta JSON."""
    fake_response_text = (
        '{"categoria":"nomina_perito","confidence":0.92,'
        '"summary":"Incarico STIMAUTO ROMA, contatto entro 5gg.",'
        '"action_required":true,'
        '"key_facts":{"numero_sinistro":"NS-2026-0034","importo_eur":null,'
        '"perito":"STIMAUTO ROMA","scadenza":"2026-05-22"}}'
    )
    mock_msg = MagicMock()
    block = MagicMock()
    block.text = fake_response_text
    mock_msg.content = [block]
    mock_msg.usage = MagicMock(input_tokens=300, output_tokens=120)

    mock_anthropic = MagicMock()
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    mock_anthropic.Anthropic.return_value = mock_client

    with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
        result = classify(
            subject="Re: Richiesta - Pratica 789",
            sender="sinistri@pec.generali.it",
            body="La compagnia incarica STIMAUTO ROMA...",
            api_key="sk-test-fake",
        )

    assert result.categoria == CAT_NOMINA_PERITO
    assert result.confidence == 0.92
    assert result.action_required is True
    assert result.key_facts["perito"] == "STIMAUTO ROMA"
    assert result.key_facts["scadenza"] == "2026-05-22"
    assert result.ai_cost_eur > 0


def test_classifier_categoria_invalida_diventa_altro():
    """Se l'AI risponde con categoria non in tassonomia, normalizza ad 'altro'."""
    fake = '{"categoria":"qualcos_altro","confidence":0.5,"summary":"x","action_required":false,"key_facts":{}}'
    mock_msg = MagicMock()
    block = MagicMock()
    block.text = fake
    mock_msg.content = [block]
    mock_msg.usage = MagicMock(input_tokens=10, output_tokens=10)
    mock_anthropic = MagicMock()
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    mock_anthropic.Anthropic.return_value = mock_client

    with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
        r = classify(
            subject="x", sender="x@y", body="b", api_key="k", disabled=False,
        )
    assert r.categoria == CAT_ALTRO


def test_classifier_parsing_json_dentro_markdown_fence():
    raw = '```json\n{"categoria":"liquidazione","confidence":0.8,"summary":"s","action_required":false,"key_facts":{"importo_eur":1500}}\n```'
    parsed = _safe_parse_json(raw)
    assert parsed is not None
    assert parsed["categoria"] == "liquidazione"


def test_classifier_cost_calcolo():
    # Haiku 4.5: input 1.00 USD/1M, output 5.00 USD/1M. EUR 0.92.
    cost = _compute_cost_eur("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    # (1.00 + 5.00) * 0.92 = 5.52
    assert abs(cost - 5.52) < 0.01


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------


def test_notifier_disabled_skippa_tutto(tmp_path: Path):
    mail_repo = MailRepository(db_path=tmp_path / "x.db")
    mail = _make_mail(mail_repo)
    classif = mail_repo.save_classification(
        mail_in_id=mail.id, pec_inviata_id=None, pratica_numero=789,
        categoria=CAT_NOMINA_PERITO, confidence=0.9,
        summary="ok", action_required=True, key_facts={},
        ai_model="haiku", ai_cost_eur=0.001,
    )
    r = notify_batch(
        nuove=[(mail, classif)],
        ntfy_server="https://ntfy.sh", ntfy_topic="topic",
        smtp_host="x", smtp_port=587, smtp_user="u", smtp_password="p",
        smtp_sender="u", alert_email="a@b.it",
        disabled=True,
    )
    assert r.push_sent == 0 and not r.email_sent


def test_notifier_push_solo_per_action_required(tmp_path: Path):
    mail_repo = MailRepository(db_path=tmp_path / "x.db")
    mail1 = _make_mail(mail_repo, uid=1)
    mail2 = _make_mail(mail_repo, uid=2)
    c_actionable = mail_repo.save_classification(
        mail_in_id=mail1.id, pec_inviata_id=None, pratica_numero=789,
        categoria=CAT_NOMINA_PERITO, confidence=0.9, summary="urgent",
        action_required=True, key_facts={}, ai_model="haiku", ai_cost_eur=0,
    )
    c_info = mail_repo.save_classification(
        mail_in_id=mail2.id, pec_inviata_id=None, pratica_numero=789,
        categoria=CAT_PRESA_IN_CARICO, confidence=0.9, summary="info",
        action_required=False, key_facts={}, ai_model="haiku", ai_cost_eur=0,
    )

    with patch("lys_workflow_hub.integrations.notifier._send_push") as mock_push, \
         patch("lys_workflow_hub.integrations.notifier._send_summary_email") as mock_mail:
        mock_push.return_value = (True, "")
        mock_mail.return_value = (True, "")
        r = notify_batch(
            nuove=[(mail1, c_actionable), (mail2, c_info)],
            ntfy_server="https://ntfy.sh", ntfy_topic="my-topic",
            smtp_host="mail.tophost.it", smtp_port=587,
            smtp_user="u", smtp_password="p",
            smtp_sender="u", alert_email="luca@example.com",
            disabled=False,
        )
        # Solo una push (per action_required), una email riassuntiva.
        assert mock_push.call_count == 1
        assert mock_mail.call_count == 1
    assert r.push_sent == 1 and r.email_sent


def test_format_summary_body_raggruppa_per_categoria(tmp_path: Path):
    mail_repo = MailRepository(db_path=tmp_path / "x.db")
    m1 = _make_mail(mail_repo, uid=1)
    m2 = _make_mail(mail_repo, uid=2)
    c1 = mail_repo.save_classification(
        mail_in_id=m1.id, pec_inviata_id=None, pratica_numero=789,
        categoria=CAT_NOMINA_PERITO, confidence=0.9,
        summary="perito X", action_required=True, key_facts={},
        ai_model="haiku", ai_cost_eur=0,
    )
    c2 = mail_repo.save_classification(
        mail_in_id=m2.id, pec_inviata_id=None, pratica_numero=790,
        categoria=CAT_LIQUIDAZIONE, confidence=0.9,
        summary="liquidato 1500€", action_required=False, key_facts={},
        ai_model="haiku", ai_cost_eur=0,
    )
    body = _format_summary_body([(m1, c1), (m2, c2)])
    assert "NOMINA PERITO" in body
    assert "LIQUIDAZIONE" in body
    assert "perito X" in body
    assert "liquidato" in body


def test_format_push_include_pratica_e_perito(tmp_path: Path):
    mail_repo = MailRepository(db_path=tmp_path / "x.db")
    mail = _make_mail(mail_repo)
    classif = mail_repo.save_classification(
        mail_in_id=mail.id, pec_inviata_id=None, pratica_numero=789,
        categoria=CAT_NOMINA_PERITO, confidence=0.9,
        summary="Incarico STIMAUTO", action_required=True,
        key_facts={"perito": "STIMAUTO ROMA", "scadenza": "2026-05-22"},
        ai_model="haiku", ai_cost_eur=0,
    )
    title, body = _format_push(mail, classif)
    assert "Pratica 789" in title
    assert "STIMAUTO ROMA" in body
    assert "2026-05-22" in body
