"""Test del mailer SMTP/PEC e dell'orchestratore di invio (M2-bis)."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from lys_workflow_hub.core.pec_log_repository import (
    ESITO_DRY_RUN,
    ESITO_KO,
    ESITO_OK,
    PecLogRepository,
)
from lys_workflow_hub.integrations.pec_mailer import (
    build_message,
    send_message,
)
from lys_workflow_hub.workflows.risarcimento_vandalismo.allegati import Allegato
from lys_workflow_hub.workflows.risarcimento_vandalismo.invio_pec import (
    ParametriInvio,
    _slug,
    invia,
)


# ---------------------------------------------------------------------------
# build_message
# ---------------------------------------------------------------------------


def test_build_message_produce_eml_con_subject_from_to(tmp_path: Path):
    a = tmp_path / "denuncia.pdf"
    a.write_bytes(b"%PDF-1.4 fake")
    built = build_message(
        sender_email="info@pec.lysauto.it",
        sender_display="Carrozzeria LYS Auto srl",
        recipient_email="sinistri@pec.generali.it",
        subject="Richiesta - Pratica 766",
        body_text="Spett.le Compagnia,\n\ntesto del messaggio.\n",
        attachments=[a],
    )
    assert built.message_id.startswith("<") and built.message_id.endswith(">")
    assert built.total_size_bytes > 0
    txt = built.eml_bytes.decode("utf-8", errors="replace")
    assert "Subject: Richiesta - Pratica 766" in txt
    assert "From: Carrozzeria LYS Auto srl <info@pec.lysauto.it>" in txt
    assert "To: sinistri@pec.generali.it" in txt
    assert "denuncia.pdf" in txt
    assert "Message-ID:" in txt


def test_build_message_valida_indirizzi():
    with pytest.raises(ValueError, match="Mittente"):
        build_message(
            sender_email="", sender_display="X",
            recipient_email="x@y.it", subject="t",
            body_text="b", attachments=[],
        )
    with pytest.raises(ValueError, match="Destinatario"):
        build_message(
            sender_email="x@y.it", sender_display="X",
            recipient_email="non-valido", subject="t",
            body_text="b", attachments=[],
        )
    with pytest.raises(ValueError, match="Oggetto"):
        build_message(
            sender_email="x@y.it", sender_display="X",
            recipient_email="y@z.it", subject="",
            body_text="b", attachments=[],
        )


def test_build_message_rifiuta_allegato_inesistente(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        build_message(
            sender_email="a@a.it", sender_display="A",
            recipient_email="b@b.it", subject="t",
            body_text="b", attachments=[tmp_path / "non_esiste.pdf"],
        )


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


def test_send_message_dry_run_non_apre_smtp(tmp_path: Path):
    """In dry-run NON deve mai chiamare smtplib."""
    a = tmp_path / "a.pdf"
    a.write_bytes(b"%PDF")
    built = build_message(
        sender_email="a@a.it", sender_display="A",
        recipient_email="b@b.it", subject="t",
        body_text="b", attachments=[a],
    )
    with patch("lys_workflow_hub.integrations.pec_mailer.smtplib") as mock_smtp:
        result = send_message(
            built,
            smtp_host="sendm.cert.legalmail.it", smtp_port=465,
            smtp_user="u", smtp_password="p",
            sender_email="a@a.it", recipient_email="b@b.it",
            dry_run=True,
        )
        # In dry-run smtplib non deve essere usato in alcun modo.
        mock_smtp.SMTP_SSL.assert_not_called()
        mock_smtp.SMTP.assert_not_called()
    assert result.ok and result.dry_run
    assert result.message_id == built.message_id


def test_send_message_invio_reale_chiama_smtp_ssl(tmp_path: Path):
    a = tmp_path / "a.pdf"
    a.write_bytes(b"%PDF")
    built = build_message(
        sender_email="a@a.it", sender_display="A",
        recipient_email="b@b.it", subject="t",
        body_text="b", attachments=[a],
    )
    with patch("lys_workflow_hub.integrations.pec_mailer.smtplib") as mock_smtp:
        ctx_mock = MagicMock()
        mock_smtp.SMTP_SSL.return_value.__enter__.return_value = ctx_mock
        result = send_message(
            built,
            smtp_host="sendm.cert.legalmail.it", smtp_port=465,
            smtp_user="user@pec.it", smtp_password="pass",
            sender_email="a@a.it", recipient_email="b@b.it",
            dry_run=False,
        )
        mock_smtp.SMTP_SSL.assert_called_once()
        ctx_mock.login.assert_called_once_with("user@pec.it", "pass")
        ctx_mock.sendmail.assert_called_once()
    assert result.ok and not result.dry_run


def test_send_message_mancanza_credenziali():
    built_mock = MagicMock(message_id="<x>", eml_bytes=b"x")
    result = send_message(
        built_mock,
        smtp_host="x", smtp_port=465, smtp_user="", smtp_password="",
        sender_email="a@a.it", recipient_email="b@b.it",
        dry_run=False,
    )
    assert not result.ok and "Credenziali" in result.error


# ---------------------------------------------------------------------------
# PecLogRepository
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> PecLogRepository:
    return PecLogRepository(db_path=tmp_path / "test.db")


def test_pec_log_inserisce_e_legge(repo: PecLogRepository):
    rec = repo.log(
        numero_pratica=766,
        compagnia_id=1,
        compagnia_nome="Generali Italia",
        destinatario_pec="sinistri@pec.generali.it",
        mittente_pec="info@pec.lysauto.it",
        oggetto="Richiesta - Pratica 766",
        body="Spett.le Compagnia,\n\nin allegato...",
        allegati=["denuncia.pdf", "IMG_001.jpg"],
        path_eml=r"C:\LYSApp\PEC_inviate\2026\xxx.eml",
        message_id="<abc@lysauto.local>",
        esito=ESITO_OK,
    )
    assert rec.id is not None
    assert rec.is_ok
    fetched = repo.get(rec.id)
    assert fetched is not None
    assert fetched.numero_pratica == 766
    assert fetched.allegati == ["denuncia.pdf", "IMG_001.jpg"]


def test_pec_log_list_by_pratica_ordina_per_data_decrescente(repo: PecLogRepository):
    for esito in (ESITO_OK, ESITO_DRY_RUN, ESITO_KO):
        repo.log(
            numero_pratica=42, compagnia_id=None, compagnia_nome="X",
            destinatario_pec="x@pec.it", mittente_pec="y@pec.it",
            oggetto="t", body="b", allegati=[],
            path_eml="", message_id=f"<{esito}>",
            esito=esito,
        )
    records = repo.list_by_pratica(42)
    assert len(records) == 3


def test_pec_log_last_ok_for_pratica(repo: PecLogRepository):
    repo.log(
        numero_pratica=100, compagnia_id=None, compagnia_nome="X",
        destinatario_pec="x@pec.it", mittente_pec="y@pec.it",
        oggetto="t", body="b", allegati=[],
        path_eml="", message_id="<a>", esito=ESITO_KO, errore="boom",
    )
    repo.log(
        numero_pratica=100, compagnia_id=None, compagnia_nome="X",
        destinatario_pec="x@pec.it", mittente_pec="y@pec.it",
        oggetto="t", body="b", allegati=[],
        path_eml="", message_id="<b>", esito=ESITO_OK,
    )
    last = repo.last_ok_for_pratica(100)
    assert last is not None and last.message_id == "<b>"


def test_pec_log_rifiuta_esito_invalido(repo: PecLogRepository):
    with pytest.raises(ValueError, match="Esito"):
        repo.log(
            numero_pratica=1, compagnia_id=None, compagnia_nome="",
            destinatario_pec="x@pec.it", mittente_pec="y@pec.it",
            oggetto="t", body="b", allegati=[],
            path_eml="", message_id="<x>", esito="MAYBE",
        )


# ---------------------------------------------------------------------------
# Orchestratore invia()
# ---------------------------------------------------------------------------


def _make_allegato(tmp_path: Path, name: str, categoria: str = "foto") -> Allegato:
    p = tmp_path / name
    p.write_bytes(b"x" * 1024)
    return Allegato(
        path=p, nome_file=name, categoria=categoria,
        dimensione_bytes=p.stat().st_size,
        data_modifica=date.today(),
    )


def _make_params(tmp_path: Path, *, dry_run: bool) -> ParametriInvio:
    return ParametriInvio(
        numero_pratica=789,
        compagnia_id=1,
        compagnia_nome="Generali Italia",
        sender_email="info@pec.lysauto.it",
        sender_display="Carrozzeria LYS Auto srl",
        reply_to="amministrazione@lysauto.it",
        recipient_email="sinistri@pec.generali.it",
        subject="Richiesta - Pratica 789",
        body="Corpo della PEC.",
        allegati=[_make_allegato(tmp_path, "denuncia.pdf", "denuncia"),
                  _make_allegato(tmp_path, "IMG_001.jpg", "foto")],
        smtp_host="sendm.cert.legalmail.it",
        smtp_port=465,
        smtp_user="info@pec.lysauto.it",
        smtp_password="password",
        dry_run=dry_run,
        archivio_pec_root=tmp_path / "archivio_pec",
    )


def test_invia_dry_run_archivia_eml_e_logga(repo: PecLogRepository, tmp_path: Path):
    params = _make_params(tmp_path, dry_run=True)
    esito = invia(params, repo=repo)
    assert esito.ok and esito.dry_run
    assert esito.record.esito == ESITO_DRY_RUN
    # File .eml archiviato
    eml = Path(esito.record.path_eml)
    assert eml.exists()
    # Anno = anno corrente
    anno = str(datetime.now().year)
    assert anno in str(eml)
    # Contiene subject e allegati nel testo
    eml_text = eml.read_bytes().decode("utf-8", errors="replace")
    assert "Richiesta - Pratica 789" in eml_text
    assert "denuncia.pdf" in eml_text


def test_invia_invio_reale_mocked(repo: PecLogRepository, tmp_path: Path):
    params = _make_params(tmp_path, dry_run=False)
    with patch("lys_workflow_hub.integrations.pec_mailer.smtplib") as mock_smtp:
        ctx = MagicMock()
        mock_smtp.SMTP_SSL.return_value.__enter__.return_value = ctx
        esito = invia(params, repo=repo)
    assert esito.ok and not esito.dry_run
    assert esito.record.esito == ESITO_OK
    ctx.sendmail.assert_called_once()


def test_invia_su_destinatario_invalido_registra_KO(repo: PecLogRepository, tmp_path: Path):
    params = _make_params(tmp_path, dry_run=True)
    bad = ParametriInvio(
        **{**params.__dict__, "recipient_email": ""}
    )
    esito = invia(bad, repo=repo)
    assert not esito.ok
    assert esito.record.esito == ESITO_KO
    assert "Destinatario" in esito.record.errore


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------


def test_slug_normalizza_caratteri_speciali():
    assert _slug("Generali Italia S.p.A.") == "Generali_Italia_S.p.A."
    assert _slug("UnipolSai!@# Assicurazioni") == "UnipolSai_Assicurazioni"
    assert _slug("") == "x"
