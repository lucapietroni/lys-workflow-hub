"""Dashboard costi/ricavi per categoria e periodo (Fase 4).

Solo aggregazione dei ``contabilita_movimento`` in stato ``confermato``:
nessuna tabella nuova. I movimenti ``proposto`` (fatture SDI non ancora
smistate) sono esclusi.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lys_workflow_hub.core.contabilita_categoria_repository import (
    ContabilitaCategoriaRepository,
)
from lys_workflow_hub.core.contabilita_movimento_repository import (
    STATO_CONFERMATO,
    ContabilitaMovimentoRepository,
)


@dataclass(frozen=True)
class RigaReport:
    categoria_id: int | None
    nome: str
    tipo: str  # 'ricavo' | 'costo' | ''
    entrate: float
    uscite: float

    @property
    def saldo(self) -> float:
        return round(self.entrate - self.uscite, 2)


@dataclass(frozen=True)
class Report:
    dal: str
    al: str
    righe: list[RigaReport] = field(default_factory=list)

    @property
    def entrate_tot(self) -> float:
        return round(sum(r.entrate for r in self.righe), 2)

    @property
    def uscite_tot(self) -> float:
        return round(sum(r.uscite for r in self.righe), 2)

    @property
    def margine(self) -> float:
        return round(self.entrate_tot - self.uscite_tot, 2)

    @property
    def ricavi(self) -> list[RigaReport]:
        return [r for r in self.righe if r.entrate or (r.tipo == "ricavo")]

    @property
    def costi(self) -> list[RigaReport]:
        return [r for r in self.righe if r.uscite or (r.tipo == "costo")]


def costruisci_report(
    db_path: Path,
    *,
    dal: Any = None,
    al: Any = None,
) -> Report:
    mov_repo = ContabilitaMovimentoRepository(db_path=db_path)
    cat_repo = ContabilitaCategoriaRepository(db_path=db_path)

    agg = mov_repo.riepilogo_per_categoria(stato=STATO_CONFERMATO, dal=dal, al=al)
    cat_by_id = {c.id: c for c in cat_repo.list_all()}

    righe: list[RigaReport] = []
    for row in agg:
        cid = row["categoria_id"]
        cat = cat_by_id.get(cid) if cid is not None else None
        righe.append(
            RigaReport(
                categoria_id=cid,
                nome=cat.nome if cat else "(senza categoria)",
                tipo=cat.tipo if cat else "",
                entrate=row["entrate"],
                uscite=row["uscite"],
            )
        )
    righe.sort(key=lambda r: (-(r.entrate + r.uscite), r.nome))

    return Report(
        dal=str(dal or ""),
        al=str(al or ""),
        righe=righe,
    )
