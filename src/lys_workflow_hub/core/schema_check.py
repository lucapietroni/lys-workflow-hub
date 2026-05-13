"""Verifica al boot che lo schema di WinCar abbia ancora le colonne attese.

Se WinCar viene aggiornato e cambia schema, il check fallisce e l'app si rifiuta
di partire. Meglio uno startup error chiaro che un comportamento silenzioso
sbagliato in produzione.

Le colonne attese sono congelate in `EXPECTED_COLUMNS` qui sotto. Quando
identifichiamo nuove colonne necessarie per workflow futuri, le aggiungiamo
qui per renderle "richieste".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lys_workflow_hub.core.wincar_repository import WinCarRepository


# Colonne minime richieste per il Workflow A (Cessione del credito)
# + i campi della compagnia cliente, gia' utilizzati dal Workflow B (Vandalismo).
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "CARVEI": {
        # Identificativi pratica
        "F_NUMPRA", "F_DATACA",
        # Anagrafica cliente
        "F_CODCLI", "F_RAGSOC",
        "F_VIACLI", "F_CITTAC", "F_CAPCLI", "F_PROCLI",
        "F_PARIVA", "F_CODFIS",
        "F_TELEFO", "F_CELLUL", "F__EMAIL",
        # Veicolo del cliente
        "F_TARGAV", "F_DESMAR", "F_DESMOD", "F_TELAIO",
        # Sinistro
        "F_DATASI", "F_ORASIN", "F_LOCSIN", "F_VIASIN",
        "F_MODSIN", "F_TIPSIN", "F_NUMSIN",
        # Controparte
        "F_NOMECO", "F_INDCON", "F_CITCON", "F_MACCON", "F_TARCON", "F_CONDUC",
        # Assicurazione controparte (per Workflow A - Cessione)
        "F_DEASCO", "F_NUMPO2",
        # Assicurazione cliente (per Workflow B - Vandalismo)
        "F_DEASCL", "F_INDASS", "F_CITASS", "F_CAPASS", "F_PROASS",
        "F_NUMPOL", "F_AGECLI",
    },
}


@dataclass(frozen=True)
class SchemaCheckResult:
    """Esito della verifica."""

    ok: bool
    missing: dict[str, set[str]] = field(default_factory=dict)
    """Colonne mancanti per ciascuna tabella. Vuoto se ok=True."""

    extra: dict[str, set[str]] = field(default_factory=dict)
    """Colonne presenti nel DB ma non attese. Informativo, non blocca."""

    def explain(self) -> str:
        if self.ok:
            return (
                "Schema WinCar verificato: tutte le colonne attese sono presenti "
                f"({len(EXPECTED_COLUMNS)} tabelle controllate)."
            )
        lines = ["Schema WinCar non compatibile: mancano colonne attese."]
        for table, cols in self.missing.items():
            lines.append(f"  - tabella {table}: mancano {sorted(cols)}")
        return "\n".join(lines)


class SchemaCheckError(RuntimeError):
    """Sollevato quando lo schema check fallisce e l'app non deve partire."""


def _columns_of(repo: WinCarRepository, table: str) -> set[str]:
    """Restituisce l'insieme delle colonne attualmente esistenti nella tabella.

    Bypassa `cursor.columns()` (che soffre di un UnicodeDecodeError sul driver
    Access italiano) leggendo `cursor.description` dopo un SELECT TOP 1.
    """
    with repo.connect() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT TOP 1 * FROM [{table}]")
        return {d[0] for d in cursor.description}


def run_schema_check(repo: WinCarRepository) -> SchemaCheckResult:
    """Esegue il check confrontando le colonne reali con `EXPECTED_COLUMNS`.

    Non solleva eccezioni in caso di mancanza: l'eccezione la lascia decidere
    al chiamante (es. il lifespan FastAPI usa `assert_schema_ok` qui sotto).
    """
    missing: dict[str, set[str]] = {}
    extra: dict[str, set[str]] = {}
    for table, expected in EXPECTED_COLUMNS.items():
        actual = _columns_of(repo, table)
        miss = expected - actual
        if miss:
            missing[table] = miss
        ext = actual - expected
        if ext:
            extra[table] = ext
    return SchemaCheckResult(ok=not missing, missing=missing, extra=extra)


def assert_schema_ok(repo: WinCarRepository) -> SchemaCheckResult:
    """Versione "blocking": solleva `SchemaCheckError` se il check fallisce."""
    result = run_schema_check(repo)
    if not result.ok:
        raise SchemaCheckError(result.explain())
    return result
