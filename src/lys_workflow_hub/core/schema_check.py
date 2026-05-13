"""Verifica al boot che lo schema di WinCar abbia ancora le colonne attese.

Se WinCar viene aggiornato e cambia schema, il check fallisce e l'app si rifiuta
di partire. Meglio uno startup error chiaro che un comportamento silenzioso
sbagliato in produzione.

Le colonne attese sono congelate in `EXPECTED_COLUMNS` qui sotto. Quando
identifichiamo nuove colonne necessarie per workflow futuri, le aggiungiamo
qui per renderle "richieste".
"""
from __future__ import annotations

from dataclasses import dataclass

from lys_workflow_hub.core.wincar_repository import WinCarRepository


# Colonne minime richieste per il Workflow A (Cessione del credito).
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "CARVEI": {
        "F_NUMPRA", "F_TARGAV", "F_CODCLI", "F_RAGSOC",
        "F_VIACLI", "F_CITTAC", "F_CAPCLI", "F_PROCLI", "F_PARIVA", "F_CODFIS",
        "F_TELEFO", "F_CELLUL", "F__EMAIL",
        "F_DESMAR", "F_DESMOD", "F_TELAIO",
        "F_DATASI", "F_ORASIN", "F_LOCSIN", "F_VIASIN", "F_MODSIN", "F_TIPSIN", "F_NUMSIN",
        "F_NOMECO", "F_INDCON", "F_CITCON", "F_MACCON", "F_TARCON", "F_CONDUC",
        "F_DEASCO", "F_NUMPO2",
        # Compagnia cliente (utile per M2 Vandalismo)
        "F_DEASCL", "F_INDASS", "F_CITASS", "F_CAPASS", "F_PROASS",
        "F_NUMPOL", "F_AGECLI",
    },
}


@dataclass(frozen=True)
class SchemaCheckResult:
    ok: bool
    missing: dict[str, set[str]]
    """Colonne mancanti per ciascuna tabella. Vuoto se ok=True."""

    def explain(self) -> str:
        if self.ok:
            return "Schema WinCar verificato: tutte le colonne attese sono presenti."
        lines = ["Schema WinCar non compatibile: mancano colonne attese."]
        for table, cols in self.missing.items():
            lines.append(f"  - tabella {table}: mancano {sorted(cols)}")
        return "\n".join(lines)


def run_schema_check(repo: WinCarRepository) -> SchemaCheckResult:  # pragma: no cover - TODO
    """Esegue il check confrontando le colonne reali con EXPECTED_COLUMNS.

    Implementazione prevista in M1 (fase Fondazione).
    """
    raise NotImplementedError("Implementazione prevista in M1 (fase Fondazione).")
