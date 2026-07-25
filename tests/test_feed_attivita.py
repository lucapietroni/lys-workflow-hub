"""Unit test per `_costruisci_feed_attivita` (timeline unica di note/eventi/
cambi stato/upload su una pratica, usata da /pratiche/{numero} e
/portale/pratiche/{numero})."""
from __future__ import annotations

from datetime import date, datetime

from lys_workflow_hub.core.pratica_eventi_repository import Evento
from lys_workflow_hub.core.pratica_note_repository import Nota
from lys_workflow_hub.core.pratica_stato_repository import PraticaStato
from lys_workflow_hub.web.routes import _costruisci_feed_attivita


def _nota(created_at: datetime) -> Nota:
    return Nota(
        id=1, pratica_numero=766, utente_id=1, autore_nome="Mario",
        testo="una nota", created_at=created_at,
    )


def _evento(created_at: datetime) -> Evento:
    return Evento(
        id=1, pratica_numero=766, titolo="Perizia", data_evento=date(2026, 8, 5),
        creato_da=1, creato_da_nome="Mario", created_at=created_at,
    )


def _stato(changed_at: datetime) -> PraticaStato:
    return PraticaStato(
        id=1, pratica_numero=766, stato="periziata", changed_at=changed_at,
        changed_by="Mario", note="",
    )


def test_feed_ordina_dal_piu_recente() -> None:
    voci = _costruisci_feed_attivita(
        note=[_nota(datetime(2026, 1, 1, 10, 0))],
        eventi=[_evento(datetime(2026, 1, 3, 10, 0))],
        stato_storia=[_stato(datetime(2026, 1, 2, 10, 0))],
        stato_labels={"periziata": "Periziata"},
        foto=[], documenti=[],
    )
    assert [v["tipo"] for v in voci] == ["evento", "stato", "nota"]


def test_feed_rispetta_il_limite() -> None:
    note = [_nota(datetime(2026, 1, i, 10, 0)) for i in range(1, 11)]
    voci = _costruisci_feed_attivita(
        note=note, eventi=[], stato_storia=[], stato_labels={}, foto=[], documenti=[], limit=3,
    )
    assert len(voci) == 3
    assert voci[0]["timestamp"] == datetime(2026, 1, 10, 10, 0)


def test_feed_include_upload_con_solo_data() -> None:
    voci = _costruisci_feed_attivita(
        note=[], eventi=[], stato_storia=[], stato_labels={},
        foto=[{"nome_file": "IMG_001.jpg", "data_modifica": date(2026, 1, 5)}],
        documenti=[{"nome_file": "polizza.pdf", "data_modifica": date(2026, 1, 6)}],
    )
    assert len(voci) == 2
    assert voci[0]["tipo"] == "documento"
    assert voci[0]["solo_data"] is True
    assert 'Nuovo documento caricato: "polizza.pdf"' == voci[0]["label"]
    assert voci[1]["tipo"] == "foto"
    assert 'Nuova foto caricata: "IMG_001.jpg"' == voci[1]["label"]


def test_feed_scarta_voci_senza_timestamp() -> None:
    voci = _costruisci_feed_attivita(
        note=[_nota(None)], eventi=[], stato_storia=[], stato_labels={}, foto=[], documenti=[],
    )
    assert voci == []
