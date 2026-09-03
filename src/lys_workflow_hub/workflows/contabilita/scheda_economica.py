"""Scheda economica di una pratica (Fase 2).

Vista aggregata — SOLO una query, nessuna tabella nuova — che mostra per una
pratica WinCar:
  - entrate collegate (movimenti confermati, tipo entrata)
  - uscite collegate (movimenti confermati, tipo uscita)
  - margine = entrate - uscite
  - ripartizione per categoria
  - fatture SDI collegate (tabella ponte) come riferimento

Il margine si calcola SOLO sui ``contabilita_movimento`` in stato
``confermato``: i movimenti ``proposto`` (generati da fatture SDI e non ancora
validati) sono contati a parte come promemoria, non nel totale. Le fatture
collegate sono mostrate come elenco di riferimento e NON sommate nel margine,
per non contarle due volte (un movimento generato da una fattura è già nei
movimenti).

Riservata agli admin: mai esposta nel portale esterno (le agenzie / gli
avvocati non devono vedere i margini della carrozzeria).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lys_workflow_hub.core.contabilita_categoria_repository import (
    ContabilitaCategoriaRepository,
)
from lys_workflow_hub.core.contabilita_fattura_repository import (
    ContabilitaFatturaRepository,
    Fattura,
)
from lys_workflow_hub.core.contabilita_movimento_repository import (
    STATO_CONFERMATO,
    STATO_PROPOSTO,
    ContabilitaMovimentoRepository,
    Movimento,
)


@dataclass(frozen=True)
class RigaCategoria:
    categoria_id: int | None
    nome: str
    tipo: str  # 'ricavo' | 'costo' | '' (senza categoria)
    totale: float


@dataclass(frozen=True)
class FatturaCollegata:
    fattura: Fattura
    importo_assegnato: float


@dataclass(frozen=True)
class SchedaEconomica:
    pratica_numero: int
    movimenti: list[Movimento] = field(default_factory=list)
    entrate_tot: float = 0.0
    uscite_tot: float = 0.0
    per_categoria: list[RigaCategoria] = field(default_factory=list)
    movimenti_proposti_n: int = 0
    fatture: list[FatturaCollegata] = field(default_factory=list)

    @property
    def margine(self) -> float:
        return round(self.entrate_tot - self.uscite_tot, 2)

    @property
    def ha_dati(self) -> bool:
        return bool(self.movimenti or self.fatture or self.movimenti_proposti_n)


def costruisci_scheda_economica(
    db_path: Path,
    pratica_numero: int,
) -> SchedaEconomica:
    """Aggrega movimenti + fatture collegati alla pratica indicata."""
    mov_repo = ContabilitaMovimentoRepository(db_path=db_path)
    cat_repo = ContabilitaCategoriaRepository(db_path=db_path)
    fat_repo = ContabilitaFatturaRepository(db_path=db_path)

    movimenti = mov_repo.list(
        pratica_id=pratica_numero, stato=STATO_CONFERMATO, limit=1000
    )
    totali = mov_repo.totali(pratica_id=pratica_numero, stato=STATO_CONFERMATO)
    proposti = mov_repo.list(
        pratica_id=pratica_numero, stato=STATO_PROPOSTO, limit=1000
    )

    cat_by_id = {c.id: c for c in cat_repo.list_all()}

    # Ripartizione per categoria (segno: +entrata, -uscita).
    acc: dict[int | None, float] = {}
    for m in movimenti:
        acc[m.categoria_id] = round(
            acc.get(m.categoria_id, 0.0) + m.importo_con_segno, 2
        )
    righe: list[RigaCategoria] = []
    for cat_id, tot in acc.items():
        cat = cat_by_id.get(cat_id) if cat_id is not None else None
        righe.append(
            RigaCategoria(
                categoria_id=cat_id,
                nome=cat.nome if cat else "(senza categoria)",
                tipo=cat.tipo if cat else "",
                totale=tot,
            )
        )
    righe.sort(key=lambda r: (-r.totale, r.nome))

    fatture = [
        FatturaCollegata(fattura=f, importo_assegnato=imp)
        for f, imp in fat_repo.list_fatture_per_pratica(pratica_numero)
    ]

    return SchedaEconomica(
        pratica_numero=pratica_numero,
        movimenti=movimenti,
        entrate_tot=totali.entrate,
        uscite_tot=totali.uscite,
        per_categoria=righe,
        movimenti_proposti_n=len(proposti),
        fatture=fatture,
    )


__all__ = [
    "SchedaEconomica",
    "RigaCategoria",
    "FatturaCollegata",
    "costruisci_scheda_economica",
]
