"""Generazione dei movimenti dai costi ricorrenti (Fase 5).

Per ogni template attivo (:mod:`contabilita_costo_ricorrente_repository`),
crea i ``contabilita_movimento`` mancanti per i periodi scaduti, da
``data_inizio`` (o dal periodo successivo a ``ultimo_periodo``) fino a oggi.

Idempotente via il watermark ``ultimo_periodo``: un periodo generato non viene
mai ricreato, anche se l'operatore elimina il movimento.

Chiamato dal ciclo giornaliero (``scripts/run_polling.py``) e dal bottone
"Genera movimenti scaduti" in ``/contabilita/ricorrenti``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lys_workflow_hub.core.contabilita_costo_ricorrente_repository import (
    ContabilitaCostoRicorrenteRepository,
    CostoRicorrente,
)
from lys_workflow_hub.core.contabilita_movimento_repository import (
    ORIGINE_RICORRENTE,
    STATO_CONFERMATO,
    TIPO_USCITA,
    ContabilitaMovimentoRepository,
)

logger = logging.getLogger(__name__)


@dataclass
class RicorrentiSummary:
    template_esaminati: int = 0
    movimenti_creati: int = 0
    errori: list[str] = field(default_factory=list)


def _add_mesi(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, 28))


def _primo_periodo(costo: CostoRicorrente) -> date:
    """Data-àncora del primo periodo: mese di ``data_inizio``, giorno
    ``giorno_mese``."""
    g = max(1, min(28, costo.giorno_mese))
    return date(costo.data_inizio.year, costo.data_inizio.month, g)


def periodi_da_generare(costo: CostoRicorrente, oggi: date) -> list[date]:
    """Date-àncora dei periodi ancora da generare fino a ``oggi`` (incluso)."""
    passo = costo.passo_mesi
    if costo.ultimo_periodo is None:
        periodo = _primo_periodo(costo)
    else:
        periodo = _add_mesi(costo.ultimo_periodo, passo)
    out: list[date] = []
    # guardia anti-loop: max ~10 anni di periodi mensili
    for _ in range(2000):
        if periodo > oggi:
            break
        out.append(periodo)
        periodo = _add_mesi(periodo, passo)
    return out


def genera_movimenti_ricorrenti(db_path: Path, *, oggi: date | None = None) -> RicorrentiSummary:
    oggi = oggi or date.today()
    costo_repo = ContabilitaCostoRicorrenteRepository(db_path=db_path)
    mov_repo = ContabilitaMovimentoRepository(db_path=db_path)

    summary = RicorrentiSummary()
    for costo in costo_repo.list_all(solo_attivi=True):
        summary.template_esaminati += 1
        try:
            for periodo in periodi_da_generare(costo, oggi):
                descr = costo.descrizione or costo.nome
                descr = f"{descr} ({periodo.strftime('%m/%Y')})"
                mov_repo.create(
                    data=periodo,
                    importo=costo.importo,
                    tipo=TIPO_USCITA,
                    categoria_id=costo.categoria_id,
                    descrizione=descr,
                    origine=ORIGINE_RICORRENTE,
                    stato=STATO_CONFERMATO,
                    importo_iva=costo.importo_iva,
                )
                costo_repo.segna_periodo_generato(costo.id, periodo)
                summary.movimenti_creati += 1
        except Exception as exc:  # noqa: BLE001
            summary.errori.append(f"{costo.nome}: {exc}")
            logger.warning("Costo ricorrente %s: generazione fallita: %s", costo.nome, exc)
    return summary
