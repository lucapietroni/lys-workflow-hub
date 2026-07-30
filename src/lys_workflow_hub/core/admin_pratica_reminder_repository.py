"""Reminder ricorrente per notifiche admin non gestite.

Quando un collaboratore esterno agisce su una pratica (nota, evento, stato,
upload — vedi `_notifica_admin` in `web/routes_portale.py`), oltre alla
notifica una tantum viene tenuto un reminder "attivo" per quella pratica.
Se l'admin non agisce (nota o upload foto/documenti, vedi `routes.py`) né lo
silenzia manualmente entro la soglia (default 24h), `scripts/run_polling.py`
lo rimanda ad ogni ciclo finché non viene risolto.

Un solo reminder attivo per pratica alla volta, verificato in Python
(query-then-write) invece di un indice parziale — coerente con lo stile del
resto del codebase.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

STATO_ATTIVO = "attivo"
STATO_RISOLTO = "risolto"


@dataclass(frozen=True)
class Reminder:
    id: int
    pratica_numero: int
    titolo: str
    messaggio: str
    stato: str
    creato_il: datetime | None
    ultimo_promemoria_il: datetime | None
    risolto_il: datetime | None
    risolto_da: str | None


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS admin_pratica_reminder (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    pratica_numero        INTEGER NOT NULL,
    titolo                TEXT NOT NULL,
    messaggio             TEXT NOT NULL,
    stato                 TEXT NOT NULL DEFAULT 'attivo',
    creato_il             TEXT NOT NULL,
    ultimo_promemoria_il  TEXT NOT NULL,
    risolto_il            TEXT,
    risolto_da            TEXT
);

CREATE INDEX IF NOT EXISTS idx_admin_pratica_reminder_pratica
    ON admin_pratica_reminder(pratica_numero);
CREATE INDEX IF NOT EXISTS idx_admin_pratica_reminder_stato
    ON admin_pratica_reminder(stato);

-- Un solo reminder attivo per pratica: indice parziale (stesso pattern di
-- unicità già usato altrove nel progetto, es. sollecito_repository.py,
-- pratica_assegnazioni_repository.py) invece del solo check Python in
-- upsert_attivo() — chiude la race fra due upsert quasi-simultanei sulla
-- stessa pratica (es. due collaboratori assegnati che agiscono insieme).
CREATE UNIQUE INDEX IF NOT EXISTS uq_admin_pratica_reminder_attivo
    ON admin_pratica_reminder(pratica_numero) WHERE stato = 'attivo';
"""


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class AdminPraticaReminderRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_reminder(row: sqlite3.Row) -> Reminder:
        d = dict(row)
        return Reminder(
            id=d["id"],
            pratica_numero=d["pratica_numero"],
            titolo=d["titolo"],
            messaggio=d["messaggio"],
            stato=d["stato"],
            creato_il=_parse_dt(d.get("creato_il")),
            ultimo_promemoria_il=_parse_dt(d.get("ultimo_promemoria_il")),
            risolto_il=_parse_dt(d.get("risolto_il")),
            risolto_da=d.get("risolto_da"),
        )

    def upsert_attivo(self, pratica_numero: int, *, titolo: str, messaggio: str) -> None:
        """Crea un reminder attivo per la pratica, o aggiorna testo/titolo
        di quello già attivo senza toccare `ultimo_promemoria_il` — un
        collaboratore che tocca ripetutamente la stessa pratica non deve
        poter posticipare all'infinito il resend.

        UPSERT atomico sull'indice parziale `uq_admin_pratica_reminder_attivo`
        (non un SELECT-then-INSERT/UPDATE separato): due chiamate
        quasi-simultanee sulla stessa pratica non possono più creare due
        righe "attive"."""
        now = _iso_now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO admin_pratica_reminder "
                "(pratica_numero, titolo, messaggio, stato, creato_il, ultimo_promemoria_il) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(pratica_numero) WHERE stato = 'attivo' "
                "DO UPDATE SET titolo = excluded.titolo, messaggio = excluded.messaggio",
                (pratica_numero, titolo, messaggio, STATO_ATTIVO, now, now),
            )

    def list_attivi(self) -> list[Reminder]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM admin_pratica_reminder WHERE stato = ? ORDER BY creato_il DESC",
                (STATO_ATTIVO,),
            )
            return [self._row_to_reminder(r) for r in cur.fetchall()]

    def list_scaduti(self, *, soglia_ore: int = 24) -> list[Reminder]:
        soglia = (datetime.now() - timedelta(hours=soglia_ore)).isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM admin_pratica_reminder "
                "WHERE stato = ? AND ultimo_promemoria_il <= ? "
                "ORDER BY ultimo_promemoria_il ASC",
                (STATO_ATTIVO, soglia),
            )
            return [self._row_to_reminder(r) for r in cur.fetchall()]

    def segna_rimandato(self, reminder_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE admin_pratica_reminder SET ultimo_promemoria_il = ? WHERE id = ?",
                (_iso_now(), reminder_id),
            )

    def risolvi_per_pratica(self, pratica_numero: int, *, risolto_da: str) -> None:
        """No-op se non c'è nulla di attivo per questa pratica — chiamata
        best-effort da azioni admin che non sempre hanno un reminder da
        chiudere."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE admin_pratica_reminder "
                "SET stato = ?, risolto_il = ?, risolto_da = ? "
                "WHERE pratica_numero = ? AND stato = ?",
                (STATO_RISOLTO, _iso_now(), risolto_da, pratica_numero, STATO_ATTIVO),
            )
