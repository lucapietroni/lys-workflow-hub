"""Repository delle mail in arrivo (PEC + email ordinaria) e delle relative
classificazioni AI (M3).

Due tabelle nello stesso `data/lys_hub.db`:

  - **mail_in**: una riga per ogni messaggio scaricato via IMAP. Tiene il
    minimo indispensabile (header utili al matching + summary del body +
    riferimento al .eml grezzo archiviato su filesystem).
  - **mail_classificate**: classificazione AI di una mail + collegamento alla
    PEC inviata "padre" (se trovata dal matcher) e alla pratica WinCar.

Le due sono in relazione 1:1 logica (una mail può avere al più una
classificazione corrente). La tabella separata permette di rieseguire la
classificazione in futuro senza toccare la riga "raw" della mail.
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


# Caselle riconosciute (etichetta interna, non l'indirizzo).
CASELLA_PEC = "PEC"
CASELLA_EMAIL = "EMAIL"


# Tassonomia di classificazione AI (M3).
CAT_PRESA_IN_CARICO = "presa_in_carico"
CAT_NOMINA_PERITO = "nomina_perito"
CAT_RICHIESTA_DOCUMENTI = "richiesta_documenti"
CAT_LIQUIDAZIONE = "liquidazione"
CAT_ALTRO = "altro"

CATEGORIE = (
    CAT_PRESA_IN_CARICO,
    CAT_NOMINA_PERITO,
    CAT_RICHIESTA_DOCUMENTI,
    CAT_LIQUIDAZIONE,
    CAT_ALTRO,
)

# Etichette user-friendly per la UI.
CATEGORIA_LABELS = {
    CAT_PRESA_IN_CARICO: "Presa in carico",
    CAT_NOMINA_PERITO: "Nomina perito",
    CAT_RICHIESTA_DOCUMENTI: "Richiesta documenti",
    CAT_LIQUIDAZIONE: "Liquidazione / pagamento",
    CAT_ALTRO: "Altro",
}


# --------------------------------------------------------------------------- #
#  Modello
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MailIn:
    """Singolo messaggio scaricato via IMAP."""

    id: int | None
    casella: str  # CASELLA_PEC | CASELLA_EMAIL
    uid_imap: int
    message_id: str
    in_reply_to: str
    references: str  # spazio-separato (può contenere più message-id)
    sender: str
    recipients: str
    subject: str
    body_text: str  # estratto del corpo testo (primi ~5000 char)
    has_attachments: bool
    raw_eml_path: str  # path assoluto del .eml grezzo
    ricevuto_at: datetime
    fetched_at: datetime


@dataclass(frozen=True)
class MailClassificata:
    """Classificazione AI di una mail + matching con pratica/PEC inviata."""

    id: int | None
    mail_in_id: int
    pec_inviata_id: int | None
    pratica_numero: int | None
    categoria: str  # uno dei valori in CATEGORIE
    confidence: float  # 0..1
    summary: str  # max 300 char
    action_required: bool
    key_facts: dict  # json libero (numero sinistro, importo, perito, scadenza)
    ai_model: str
    ai_cost_eur: float
    classified_at: datetime
    match_method: str  # "header_in_reply_to" | "header_references" | "heuristic" | "none"
    match_confidence: float = 0.0

    @property
    def categoria_label(self) -> str:
        return CATEGORIA_LABELS.get(self.categoria, self.categoria)


@dataclass(frozen=True)
class MailConClassificazione:
    """Vista combinata mail + classificazione (per UI lista)."""

    mail: MailIn
    classificazione: MailClassificata | None

    @property
    def categoria(self) -> str:
        return self.classificazione.categoria if self.classificazione else CAT_ALTRO

    @property
    def categoria_label(self) -> str:
        return (
            self.classificazione.categoria_label
            if self.classificazione
            else "Non classificata"
        )

    @property
    def action_required(self) -> bool:
        return bool(self.classificazione and self.classificazione.action_required)

    @property
    def pratica_numero(self) -> int | None:
        return self.classificazione.pratica_numero if self.classificazione else None


# --------------------------------------------------------------------------- #
#  Schema
# --------------------------------------------------------------------------- #


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mail_in (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    casella           TEXT NOT NULL,
    uid_imap          INTEGER NOT NULL,
    message_id        TEXT NOT NULL DEFAULT '',
    in_reply_to       TEXT NOT NULL DEFAULT '',
    "references"      TEXT NOT NULL DEFAULT '',
    sender            TEXT NOT NULL DEFAULT '',
    recipients        TEXT NOT NULL DEFAULT '',
    subject           TEXT NOT NULL DEFAULT '',
    body_text         TEXT NOT NULL DEFAULT '',
    has_attachments   INTEGER NOT NULL DEFAULT 0,
    raw_eml_path      TEXT NOT NULL DEFAULT '',
    ricevuto_at       TEXT NOT NULL,
    fetched_at        TEXT NOT NULL
);

-- Evita duplicati su Message-ID per la stessa casella.
CREATE UNIQUE INDEX IF NOT EXISTS uq_mail_in_msgid
    ON mail_in(casella, message_id)
    WHERE message_id <> '';

-- Fallback su (casella, uid_imap) quando il Message-ID manca.
CREATE UNIQUE INDEX IF NOT EXISTS uq_mail_in_uid
    ON mail_in(casella, uid_imap);

CREATE INDEX IF NOT EXISTS idx_mail_in_ricevuto
    ON mail_in(ricevuto_at DESC);


CREATE TABLE IF NOT EXISTS mail_classificate (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    mail_in_id        INTEGER NOT NULL,
    pec_inviata_id    INTEGER,
    pratica_numero    INTEGER,
    categoria         TEXT NOT NULL,
    confidence        REAL NOT NULL DEFAULT 0,
    summary           TEXT NOT NULL DEFAULT '',
    action_required   INTEGER NOT NULL DEFAULT 0,
    key_facts_json    TEXT NOT NULL DEFAULT '{}',
    ai_model          TEXT NOT NULL DEFAULT '',
    ai_cost_eur       REAL NOT NULL DEFAULT 0,
    classified_at     TEXT NOT NULL,
    match_method      TEXT NOT NULL DEFAULT 'none',
    match_confidence  REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (mail_in_id) REFERENCES mail_in(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_mail_class_mail
    ON mail_classificate(mail_in_id);

CREATE INDEX IF NOT EXISTS idx_mail_class_pratica
    ON mail_classificate(pratica_numero);
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
#  Repository
# --------------------------------------------------------------------------- #


class MailRepository:
    """CRUD per mail_in + mail_classificate (singolo DB SQLite)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            # Migrazione: colonna ignorata (soft-delete per evitare re-download).
            try:
                conn.execute(
                    "ALTER TABLE mail_in ADD COLUMN ignorata INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass  # già presente

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- mail_in -------------------------------------------------------------

    @staticmethod
    def _row_to_mail(row: sqlite3.Row) -> MailIn:
        return MailIn(
            id=row["id"],
            casella=row["casella"],
            uid_imap=int(row["uid_imap"]),
            message_id=row["message_id"] or "",
            in_reply_to=row["in_reply_to"] or "",
            references=row["references"] or "",
            sender=row["sender"] or "",
            recipients=row["recipients"] or "",
            subject=row["subject"] or "",
            body_text=row["body_text"] or "",
            has_attachments=bool(row["has_attachments"]),
            raw_eml_path=row["raw_eml_path"] or "",
            ricevuto_at=_parse_dt(row["ricevuto_at"]) or datetime.now(),
            fetched_at=_parse_dt(row["fetched_at"]) or datetime.now(),
        )

    def insert_mail(
        self,
        *,
        casella: str,
        uid_imap: int,
        message_id: str,
        in_reply_to: str,
        references: str,
        sender: str,
        recipients: str,
        subject: str,
        body_text: str,
        has_attachments: bool,
        raw_eml_path: Path | str,
        ricevuto_at: datetime,
    ) -> MailIn | None:
        """Inserisce una mail, restituisce None se già presente (per dedup)."""
        if casella not in (CASELLA_PEC, CASELLA_EMAIL):
            raise ValueError(f"Casella non valida: {casella!r}")
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    'INSERT INTO mail_in (casella, uid_imap, message_id, in_reply_to, '
                    '"references", sender, recipients, subject, body_text, '
                    " has_attachments, raw_eml_path, ricevuto_at, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        casella,
                        int(uid_imap),
                        message_id,
                        in_reply_to,
                        references,
                        sender,
                        recipients,
                        subject,
                        (body_text or "")[:8000],
                        1 if has_attachments else 0,
                        str(raw_eml_path),
                        ricevuto_at.isoformat(timespec="seconds"),
                        now_iso,
                    ),
                )
                new_id = cur.lastrowid
            except sqlite3.IntegrityError:
                logger.debug(
                    "Mail già presente (casella=%s, uid=%s, msgid=%s)",
                    casella, uid_imap, message_id,
                )
                return None
        return self.get_mail(int(new_id))

    def get_mail(self, mail_id: int) -> MailIn | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mail_in WHERE id = ?", (int(mail_id),)
            ).fetchone()
        return self._row_to_mail(row) if row else None

    def list_mail(self, limit: int = 200) -> list[MailIn]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mail_in WHERE ignorata = 0 "
                "ORDER BY ricevuto_at DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._row_to_mail(r) for r in rows]

    def max_uid(self, casella: str) -> int:
        """UID IMAP più alto già scaricato per la casella (per fetch incrementale).

        Considera TUTTE le mail incluse quelle soft-deleted (ignorata=1):
        così il fetcher non ri-scarica mai una mail eliminata dal cruscotto.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(uid_imap) AS m FROM mail_in WHERE casella = ?",
                (casella,),
            ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0

    def conta_da_classificare(self) -> int:
        """Mail in arrivo per cui non c'è ancora classificazione (ignora soft-delete)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM mail_in mi "
                "LEFT JOIN mail_classificate mc ON mc.mail_in_id = mi.id "
                "WHERE mc.id IS NULL AND mi.ignorata = 0"
            ).fetchone()
        return int(row["n"]) if row else 0

    def list_da_classificare(self, limit: int = 50) -> list[MailIn]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT mi.* FROM mail_in mi "
                "LEFT JOIN mail_classificate mc ON mc.mail_in_id = mi.id "
                "WHERE mc.id IS NULL AND mi.ignorata = 0 "
                "ORDER BY mi.ricevuto_at ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._row_to_mail(r) for r in rows]

    # -- mail_classificate ---------------------------------------------------

    @staticmethod
    def _row_to_class(row: sqlite3.Row) -> MailClassificata:
        try:
            key_facts = json.loads(row["key_facts_json"] or "{}")
        except (TypeError, ValueError):
            key_facts = {}
        return MailClassificata(
            id=row["id"],
            mail_in_id=int(row["mail_in_id"]),
            pec_inviata_id=row["pec_inviata_id"],
            pratica_numero=row["pratica_numero"],
            categoria=row["categoria"] or CAT_ALTRO,
            confidence=float(row["confidence"] or 0),
            summary=row["summary"] or "",
            action_required=bool(row["action_required"]),
            key_facts=key_facts if isinstance(key_facts, dict) else {},
            ai_model=row["ai_model"] or "",
            ai_cost_eur=float(row["ai_cost_eur"] or 0),
            classified_at=_parse_dt(row["classified_at"]) or datetime.now(),
            match_method=row["match_method"] or "none",
            match_confidence=float(row["match_confidence"] or 0),
        )

    def save_classification(
        self,
        *,
        mail_in_id: int,
        pec_inviata_id: int | None,
        pratica_numero: int | None,
        categoria: str,
        confidence: float,
        summary: str,
        action_required: bool,
        key_facts: dict,
        ai_model: str,
        ai_cost_eur: float,
        match_method: str = "none",
        match_confidence: float = 0.0,
    ) -> MailClassificata:
        if categoria not in CATEGORIE:
            raise ValueError(f"Categoria non valida: {categoria!r}")
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO mail_classificate (mail_in_id, pec_inviata_id, "
                " pratica_numero, categoria, confidence, summary, action_required, "
                " key_facts_json, ai_model, ai_cost_eur, classified_at, "
                " match_method, match_confidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(mail_in_id),
                    int(pec_inviata_id) if pec_inviata_id is not None else None,
                    int(pratica_numero) if pratica_numero is not None else None,
                    categoria,
                    float(confidence),
                    (summary or "")[:300],
                    1 if action_required else 0,
                    json.dumps(key_facts or {}, ensure_ascii=False),
                    ai_model,
                    float(ai_cost_eur),
                    now_iso,
                    match_method,
                    float(match_confidence),
                ),
            )
            new_id = cur.lastrowid
        return self.get_classification(int(new_id))  # type: ignore[return-value]

    def get_classification(self, class_id: int) -> MailClassificata | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mail_classificate WHERE id = ?",
                (int(class_id),),
            ).fetchone()
        return self._row_to_class(row) if row else None

    def get_classification_for_mail(self, mail_in_id: int) -> MailClassificata | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mail_classificate WHERE mail_in_id = ?",
                (int(mail_in_id),),
            ).fetchone()
        return self._row_to_class(row) if row else None

    # -- viste combinate -----------------------------------------------------

    def list_con_classificazione(
        self,
        limit: int = 200,
        *,
        solo_matched: bool = True,
    ) -> list[MailConClassificazione]:
        """Lista cronologica delle mail con eventuale classificazione.

        - `solo_matched=True` (default): mostra solo le mail collegate a una
          PEC inviata della carrozzeria (`pec_inviata_id IS NOT NULL`).
          Filtra automaticamente il rumore: newsletter, ricevute PEC di
          sistema, spam che ha superato i filtri della casella.
        - `solo_matched=False`: mostra tutte le mail in archivio.
        """
        if solo_matched:
            where = "WHERE mc.pec_inviata_id IS NOT NULL AND mi.ignorata = 0"
        else:
            where = "WHERE mi.ignorata = 0"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT mi.id AS m_id, mc.id AS c_id FROM mail_in mi "
                "LEFT JOIN mail_classificate mc ON mc.mail_in_id = mi.id "
                f"{where} "
                "ORDER BY mi.ricevuto_at DESC, mi.id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        out: list[MailConClassificazione] = []
        for r in rows:
            mail = self.get_mail(int(r["m_id"]))
            if mail is None:
                continue
            classif = (
                self.get_classification(int(r["c_id"])) if r["c_id"] else None
            )
            out.append(MailConClassificazione(mail=mail, classificazione=classif))
        return out

    def list_action_required_per_pratica(
        self, numero_pratica: int
    ) -> list[MailClassificata]:
        """Risposte classificate come 'action_required' su una specifica pratica."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mail_classificate "
                "WHERE pratica_numero = ? AND action_required = 1 "
                "ORDER BY classified_at DESC",
                (int(numero_pratica),),
            ).fetchall()
        return [self._row_to_class(r) for r in rows]

    def count_action_required(self) -> int:
        """Conta globalmente le mail con action_required=True collegate a una
        pratica (usato per il KPI sulla home)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM mail_classificate mc "
                "JOIN mail_in mi ON mi.id = mc.mail_in_id "
                "WHERE mc.action_required = 1 AND mc.pec_inviata_id IS NOT NULL "
                "AND mi.ignorata = 0"
            ).fetchone()
        return int(row["n"]) if row else 0

    def ai_cost_mese_corrente(self) -> float:
        """Somma costi AI per i record classificati nel mese corrente."""
        prefisso = datetime.now().strftime("%Y-%m")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(ai_cost_eur), 0) AS s "
                "FROM mail_classificate WHERE classified_at LIKE ?",
                (f"{prefisso}%",),
            ).fetchone()
        return float(row["s"]) if row else 0.0

    # -- operazioni manuali dal cruscotto ------------------------------------

    def update_body_text(self, mail_id: int, body_text: str) -> None:
        """Aggiorna body_text di una mail (usato dalla riclassificazione M3)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE mail_in SET body_text = ? WHERE id = ?",
                ((body_text or "")[:8000], int(mail_id)),
            )

    def delete_classification_for_mail(self, mail_in_id: int) -> bool:
        """Cancella la classificazione esistente per una mail.

        Rende la mail nuovamente "da classificare": il prossimo polling la
        riprenderà (o la route riclassifica la ri-processa subito).
        """
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM mail_classificate WHERE mail_in_id = ?",
                (int(mail_in_id),),
            )
            return cur.rowcount > 0

    def delete_mail(self, mail_id: int) -> bool:
        """Soft-delete: imposta ignorata=1 su mail_in e cancella mail_classificate.

        La riga rimane in DB con ignorata=1 così max_uid non scende e il fetcher
        non riscarica mai la mail. Tutte le query di lista filtrano ignorata=0.
        """
        with self._connect() as conn:
            # Cancella classificazione (così può essere riclassificata se necessario).
            conn.execute(
                "DELETE FROM mail_classificate WHERE mail_in_id = ?",
                (int(mail_id),),
            )
            cur = conn.execute(
                "UPDATE mail_in SET ignorata = 1 WHERE id = ?",
                (int(mail_id),),
            )
            return cur.rowcount > 0
