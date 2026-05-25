"""Repository per la policy di generazione bozze per categoria AI (M5.2).

Sposta ``DEFAULT_POLICY_PER_CATEGORIA`` da costante Python a tabella SQLite,
permettendo all'operatore di modificarla dalla UI senza ricompilare o
riavviare l'app (basta un reload della pagina /impostazioni).

La tabella viene pre-popolata con i default al primo accesso.

Il modulo rimane retrocompatibile con ``categorie_policy.py``:
``policy_per()`` e ``deve_generare_auto()`` di questo repo hanno la stessa
firma dei corrispondenti nel modulo statico — basta sostituire le chiamate.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator


logger = logging.getLogger(__name__)


# Valori policy possibili (speculari a categorie_policy.py).
BOZZA_AUTO = "auto"
BOZZA_OPT_IN = "opt_in"
BOZZA_NESSUNA = "nessuna"

POLICIES = (BOZZA_AUTO, BOZZA_OPT_IN, BOZZA_NESSUNA)

POLICY_LABELS = {
    BOZZA_AUTO: "Bozza automatica",
    BOZZA_OPT_IN: "Solo su richiesta",
    BOZZA_NESSUNA: "Nessuna bozza",
}

# Default identici a categorie_policy.py — usati per pre-popolare la tabella.
_DEFAULTS: dict[str, str] = {
    "presa_in_carico": BOZZA_NESSUNA,
    "nomina_perito": BOZZA_OPT_IN,
    "richiesta_documenti": BOZZA_AUTO,
    "liquidazione": BOZZA_AUTO,
    "altro": BOZZA_OPT_IN,
}

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS categoria_policy (
    categoria   TEXT PRIMARY KEY,
    policy      TEXT NOT NULL DEFAULT 'opt_in',
    updated_at  TEXT NOT NULL
);
"""


class CategoriaPolicyRepository:
    """CRUD per la policy di generazione bozze per categoria.

    Usa la stessa ``db_path`` degli altri repository (``data/lys_hub.db``).
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
        self._ensure_defaults()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_defaults(self) -> None:
        """Pre-popola la tabella con i valori di default se è vuota."""
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            for cat, pol in _DEFAULTS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO categoria_policy (categoria, policy, updated_at) "
                    "VALUES (?, ?, ?)",
                    (cat, pol, now_iso),
                )

    # ---------------------------------------------------------------- lettura -

    def get_all(self) -> dict[str, str]:
        """Ritorna mapping categoria → policy da DB (con fallback ai default)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT categoria, policy FROM categoria_policy ORDER BY categoria"
            ).fetchall()
        result = dict(_DEFAULTS)  # comincia dai default
        for r in rows:
            result[r["categoria"]] = r["policy"]
        return result

    def policy_per(self, categoria: str) -> str:
        """Policy per una categoria specifica. Fallback conservativo: opt_in."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT policy FROM categoria_policy WHERE categoria = ?",
                (categoria,),
            ).fetchone()
        if row:
            return row["policy"]
        return _DEFAULTS.get(categoria, BOZZA_OPT_IN)

    def deve_generare_auto(self, categoria: str) -> bool:
        """True se la policy prevede generazione automatica della bozza."""
        return self.policy_per(categoria) == BOZZA_AUTO

    # --------------------------------------------------------------- scrittura -

    def set_policy(self, categoria: str, policy: str) -> None:
        """Aggiorna la policy per una categoria. Raise su valori non validi."""
        if policy not in POLICIES:
            raise ValueError(f"Policy non valida: {policy!r}")
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO categoria_policy (categoria, policy, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(categoria) DO UPDATE SET policy=excluded.policy, "
                "updated_at=excluded.updated_at",
                (categoria, policy, now_iso),
            )
        logger.info("Policy categoria '%s' aggiornata a '%s'", categoria, policy)
