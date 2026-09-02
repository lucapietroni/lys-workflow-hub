"""Smistamento delle fatture passive (Fase 4).

Una fattura passiva arriva dallo SDI (Fase 3) con un unico movimento in stato
``proposto``, senza categoria né pratica. Lo smistamento:

  1. assegna una **categoria** (una sola, vale per tutta la fattura);
  2. la collega a 0..N **pratiche** con un importo ciascuna (split);
  3. l'eventuale residuo (totale − somma assegnata) resta come movimento
     senza pratica (es. quota parte non attribuibile / spesa generale);
  4. sostituisce il movimento ``proposto`` con i movimenti definitivi in stato
     ``confermato`` (entrano nel margine) e riscrive la tabella ponte
     ``contabilita_fattura_pratica``.

La coda = fatture con almeno un movimento ancora ``proposto``.
"""
from __future__ import annotations

from dataclasses import dataclass

from lys_workflow_hub.core.contabilita_fattura_repository import (
    TIPO_ATTIVA,
    ContabilitaFatturaRepository,
    Fattura,
)
from lys_workflow_hub.core.contabilita_movimento_repository import (
    ORIGINE_FATTURA_SDI,
    STATO_CONFERMATO,
    STATO_PROPOSTO,
    TIPO_ENTRATA,
    TIPO_USCITA,
    ContabilitaMovimentoRepository,
    Movimento,
)


@dataclass(frozen=True)
class VoceCoda:
    fattura: Fattura
    movimento: Movimento  # il movimento proposto rappresentativo


@dataclass(frozen=True)
class Assegnazione:
    pratica_id: int
    importo: float


def coda_passive(
    fattura_repo: ContabilitaFatturaRepository,
    movimento_repo: ContabilitaMovimentoRepository,
) -> list[VoceCoda]:
    """Fatture (passive o attive) con almeno un movimento ancora proposto."""
    ids = movimento_repo.fattura_ids_con_proposti()
    voci: list[VoceCoda] = []
    for fid in ids:
        fattura = fattura_repo.get(fid)
        if fattura is None:
            continue
        proposti = [m for m in movimento_repo.list_by_fattura(fid) if m.stato == STATO_PROPOSTO]
        if not proposti:
            continue
        voci.append(VoceCoda(fattura=fattura, movimento=proposti[0]))
    voci.sort(key=lambda v: (v.fattura.data, v.fattura.id or 0), reverse=True)
    return voci


class SmistamentoError(ValueError):
    pass


def smista_fattura(
    *,
    fattura_repo: ContabilitaFatturaRepository,
    movimento_repo: ContabilitaMovimentoRepository,
    fattura_id: int,
    categoria_id: int | None,
    assegnazioni: list[Assegnazione],
) -> list[Movimento]:
    """Applica lo smistamento. Ritorna i movimenti confermati creati.

    Solleva :class:`SmistamentoError` se la somma degli importi assegnati
    supera il totale della fattura o se un importo è negativo.
    """
    fattura = fattura_repo.get(fattura_id)
    if fattura is None:
        raise SmistamentoError(f"Fattura id={fattura_id} non trovata.")

    # Dedup: più righe sulla stessa pratica → una sola, importi sommati
    # (altrimenti la tabella ponte terrebbe solo l'ultimo importo mentre i
    # movimenti li conterebbero tutti).
    assegnazioni = _dedup(assegnazioni)

    totale = round(fattura.importo_totale, 2)
    somma = round(sum(a.importo for a in assegnazioni), 2)
    if any(a.importo < 0 for a in assegnazioni):
        raise SmistamentoError("Gli importi assegnati non possono essere negativi.")
    if somma - totale > 0.01:
        raise SmistamentoError(
            f"La somma assegnata (€ {somma:.2f}) supera il totale della fattura "
            f"(€ {totale:.2f})."
        )
    if any(a.pratica_id <= 0 for a in assegnazioni):
        raise SmistamentoError("Numero pratica non valido in una delle assegnazioni.")

    # Direzione/data del movimento: dai movimenti generati da SDI per questa
    # fattura (proposti O già smistati — l'`origine` li identifica a
    # prescindere dallo stato, così un ri-smistamento non ribalta il segno di
    # una fattura attiva o di una nota di credito). Fallback sul tipo fattura.
    esistenti = movimento_repo.list_by_fattura(fattura_id)
    sdi_movs = [m for m in esistenti if m.origine == ORIGINE_FATTURA_SDI]
    if sdi_movs:
        tipo_mov = sdi_movs[0].tipo
        data_mov = sdi_movs[0].data
    else:
        tipo_mov = TIPO_ENTRATA if fattura.tipo == TIPO_ATTIVA else TIPO_USCITA
        data_mov = fattura.data

    # 1) via i movimenti generati da SDI (proposti o già smistati), via le
    #    vecchie righe ponte. I movimenti manuali sulla fattura restano.
    movimento_repo.delete_by_fattura(fattura_id, solo_sdi=True)
    for r in fattura_repo.list_pratiche(fattura_id):
        fattura_repo.unlink_pratica(fattura_id, r.pratica_id)

    creati: list[Movimento] = []

    # 2) un movimento confermato per ogni pratica + riga ponte.
    for a in assegnazioni:
        if a.importo <= 0:
            continue
        fattura_repo.link_pratica(fattura_id, a.pratica_id, importo_assegnato=a.importo)
        creati.append(
            movimento_repo.create(
                data=data_mov,
                importo=a.importo,
                tipo=tipo_mov,
                categoria_id=categoria_id,
                pratica_id=a.pratica_id,
                fattura_id=fattura_id,
                descrizione=_descr(fattura, tipo_mov),
                origine=ORIGINE_FATTURA_SDI,
                stato=STATO_CONFERMATO,
            )
        )

    # 3) residuo → movimento senza pratica.
    residuo = round(totale - somma, 2)
    if residuo > 0.01:
        creati.append(
            movimento_repo.create(
                data=data_mov,
                importo=residuo,
                tipo=tipo_mov,
                categoria_id=categoria_id,
                fattura_id=fattura_id,
                descrizione=_descr(fattura, tipo_mov) + " (quota non attribuita a pratica)",
                origine=ORIGINE_FATTURA_SDI,
                stato=STATO_CONFERMATO,
            )
        )

    # Se non è stato assegnato nulla ma c'è una categoria: un unico movimento
    # confermato pari all'intero totale (caso spesa generale: affitto, utenze).
    if not creati:
        creati.append(
            movimento_repo.create(
                data=data_mov,
                importo=totale,
                tipo=tipo_mov,
                categoria_id=categoria_id,
                fattura_id=fattura_id,
                descrizione=_descr(fattura, tipo_mov),
                origine=ORIGINE_FATTURA_SDI,
                stato=STATO_CONFERMATO,
            )
        )

    return creati


def _dedup(assegnazioni: list[Assegnazione]) -> list[Assegnazione]:
    acc: dict[int, float] = {}
    for a in assegnazioni:
        acc[a.pratica_id] = round(acc.get(a.pratica_id, 0.0) + a.importo, 2)
    return [Assegnazione(pratica_id=p, importo=i) for p, i in acc.items()]


def _descr(fattura: Fattura, tipo_mov: str) -> str:
    verso = "da" if tipo_mov == TIPO_USCITA else "a"
    return f"Fattura {fattura.numero}/{fattura.anno} {verso} {fattura.controparte_nome}".strip()
