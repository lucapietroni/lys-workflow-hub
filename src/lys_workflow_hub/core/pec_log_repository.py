"""Registro storico delle PEC inviate (M2-bis).

Storage SQLite locale nello stesso file `data/lys_hub.db` usato per
l'anagrafica compagnie. Una tabella separata `pec_inviate` tiene traccia
di ogni invio (vero o dry-run) con tutti i metadati utili per audit:

- numero_pratica, compagnia destinataria (nome + indirizzo PEC)
- subject + estratto del body (primi 300 caratteri)
- elenco JSON dei file allegati
- path assoluto del file .eml archiviato sul filesystem
- message_id RFC-822 generato in fase di costruzione MIME
- data invio + esito ("OK", "DRY_RUN", "KO")
- eventuale messaggio di errore in caso di KO

Non viene mai memorizzato il body completo della PEC: per leggerlo si apre
il file .eml archiviato (riferito da `path_eml`).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator


logger = logging.getLogger(__name__)


ESITO_OK = "OK"
ESITO_DRY_RUN = "DRY_RUN"
ESITO_KO = "KO"


# --------------------------------------------------------------------------- #
#  Modello
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PecInviata:
    """Record di un invio PEC (vero o dry-run)."""

    id: int | None
    numero_pratica: int
    compagnia_id: int | None
    compagnia_nome: str
    destinatario_pec: str
    mittente_pec: str
    oggetto: str
    body_excerpt: str
    allegati: list[str]  # nomi dei file allegati
    path_eml: str  # percorso assoluto del .eml archiviato (stringa per portabilita')
    message_id: str
    data_invio: datetime
    esito: str  # ESITO_OK | ESITO_DRY_RUN | ESITO_KO
    errore: str = ""
    email_destinatario: str = ""
    email_esito: str = ""
    created_at: datetime | None = field(default=None)

    @property
    def is_ok(self) -> bool:
        return self.esito == ESITO_OK

    @property
    def is_dry_run(self) -> bool:
        return self.esito == ESITO_DRY_RUN

    @property
    def esito_label(self) -> str:
        if self.esito == ESITO_OK:
            if self.email_esito == ESITO_OK:
                return "Inviata PEC + email"
            if self.email_esito == ESITO_KO:
                return "Inviata PEC (email fallita)"
            return "Inviata PEC"
        return {
            ESITO_DRY_RUN: "Dry-run",
            ESITO_KO: "Errore",
        }.get(self.esito, self.esito)


# --------------------------------------------------------------------------- #
#  Repository
# --------------------------------------------------------------------------- #


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pec_inviate (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_pratica   INTEGER NOT NULL,
    compagnia_id     INTEGER,
    compagnia_nome   TEXT NOT NULL DEFAULT '',
    destinatario_pec TEXT NOT NULL,
    mittente_pec     TEXT NOT NULL DEFAULT '',
    oggetto          TEXT NOT NULL,
    body_excerpt     TEXT NOT NULL DEFAULT '',
    allegati_json    TEXT NOT NULL DEFAULT '[]',
    path_eml         TEXT NOT NULL DEFAULT '',
    message_id       TEXT NOT NULL DEFAULT '',
    data_invio           TEXT NOT NULL,
    esito                TEXT NOT NULL,
    errore               TEXT NOT NULL DEFAULT '',
    email_destinatario   TEXT NOT NULL DEFAULT '',
    email_esito          TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pec_inviate_pratica
    ON pec_inviate(numero_pratica);

CREATE INDEX IF NOT EXISTS idx_pec_inviate_data
    ON pec_inviate(data_invio DESC);
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class PecLogRepository:
    """CRUD/log delle PEC inviate."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            for col_def in (
                "email_destinatario TEXT NOT NULL DEFAULT ''",
                "email_esito TEXT NOT NULL DEFAULT ''",
            ):
                try:
                    conn.execute(f"ALTER TABLE pec_inviate ADD COLUMN {col_def}")
                except sqlite3.OperationalError:
                    pass

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
    def _row_to_pec(row: sqlite3.Row) -> PecInviata:
        try:
            allegati = json.loads(row["allegati_json"] or "[]")
        except (TypeError, ValueError):
            allegati = []
        return PecInviata(
            id=row["id"],
            numero_pratica=int(row["numero_pratica"]),
            compagnia_id=row["compagnia_id"],
            compagnia_nome=row["compagnia_nome"] or "",
            destinatario_pec=row["destinatario_pec"] or "",
            mittente_pec=row["mittente_pec"] or "",
            oggetto=row["oggetto"] or "",
            body_excerpt=row["body_excerpt"] or "",
            allegati=allegati if isinstance(allegati, list) else [],
            path_eml=row["path_eml"] or "",
            message_id=row["message_id"] or "",
            data_invio=_parse_dt(row["data_invio"]) or datetime.now(),
            esito=row["esito"] or ESITO_KO,
            errore=row["errore"] or "",
            email_destinatario=row["email_destinatario"] or "",
            email_esito=row["email_esito"] or "",
            created_at=_parse_dt(row["created_at"]),
        )

    # -- inserimento ---------------------------------------------------------

    def log(
        self,
        *,
        numero_pratica: int,
        compagnia_id: int | None,
        compagnia_nome: str,
        destinatario_pec: str,
        mittente_pec: str,
        oggetto: str,
        body: str,
        allegati: list[str],
        path_eml: Path | str,
        message_id: str,
        esito: str,
        errore: str = "",
        data_invio: datetime | None = None,
    ) -> PecInviata:
        """Registra un nuovo invio (vero, dry-run o fallito)."""
        if esito not in (ESITO_OK, ESITO_DRY_RUN, ESITO_KO):
            raise ValueError(f"Esito non valido: {esito!r}")
        when = data_invio or datetime.now()
        now_iso = datetime.now().isoformat(timespec="seconds")
        body_excerpt = (body or "")[:300]
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO pec_inviate (numero_pratica, compagnia_id, compagnia_nome, "
                " destinatario_pec, mittente_pec, oggetto, body_excerpt, allegati_json, "
                " path_eml, message_id, data_invio, esito, errore, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(numero_pratica),
                    int(compagnia_id) if compagnia_id is not None else None,
                    compagnia_nome,
                    destinatario_pec,
                    mittente_pec,
                    oggetto,
                    body_excerpt,
                    json.dumps(allegati, ensure_ascii=False),
                    str(path_eml),
                    message_id,
                    when.isoformat(timespec="seconds"),
                    esito,
                    errore,
                    now_iso,
                ),
            )
            new_id = cur.lastrowid
        return self.get(int(new_id))  # type: ignore[arg-type]

    # -- letture -------------------------------------------------------------

    def get(self, pec_id: int) -> PecInviata | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pec_inviate WHERE id = ?", (int(pec_id),)
            ).fetchone()
        return self._row_to_pec(row) if row else None

    def list_all(self, limit: int = 200) -> list[PecInviata]:
        """Lista cronologica delle PEC inviate (più recenti prima)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pec_inviate ORDER BY data_invio DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._row_to_pec(r) for r in rows]

    def list_by_pratica(self, numero_pratica: int) -> list[PecInviata]:
        """Tutte le PEC inviate per una specifica pratica (più recenti prima)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pec_inviate WHERE numero_pratica = ? "
                "ORDER BY data_invio DESC, id DESC",
                (int(numero_pratica),),
            ).fetchall()
        return [self._row_to_pec(r) for r in rows]

    def last_ok_for_pratica(self, numero_pratica: int) -> PecInviata | None:
        """L'ultima PEC inviata con esito OK per una pratica (per banner UI)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pec_inviate "
                "WHERE numero_pratica = ? AND esito = ? "
                "ORDER BY data_invio DESC, id DESC LIMIT 1",
                (int(numero_pratica), ESITO_OK),
            ).fetchone()
        return self._row_to_pec(row) if row else None

    def aggiorna_email_esito(
        self, pec_id: int, email_destinatario: str, email_esito: str
    ) -> None:
        """Aggiorna i campi email_destinatario e email_esito di un invio."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE pec_inviate SET email_destinatario = ?, email_esito = ? WHERE id = ?",
                (email_destinatario, email_esito, int(pec_id)),
            )

    def pec_ids_con_risposta(self) -> set[int]:
        """IDs delle PEC che hanno ricevuto almeno una risposta classificata."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT pec_inviata_id FROM mail_classificate "
                    "WHERE pec_inviata_id IS NOT NULL"
                ).fetchall()
            return {r[0] for r in rows}
        except Exception:
            return set()
