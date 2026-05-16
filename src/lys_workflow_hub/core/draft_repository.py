"""Repository delle bozze di risposta alle compagnie (M4).

Tabella `risposte_draft` in `data/lys_hub.db`. Ogni riga e' la bozza di
risposta a una mail classificata M3: 1:1 logico con `mail_classificate`
(una mail in arrivo ha al piu' una bozza corrente).

Ciclo di vita della bozza:

  STATUS_PENDING  -> creata dall'AI, mai aperta dall'editor
  STATUS_READY    -> rivista nell'editor, pronta per essere inviata
  STATUS_SENT     -> spedita via PEC/SMTP, immutabile (campo `sent_at` valorizzato)
  STATUS_CANCELLED-> chiusa senza invio (campo `cancel_reason` valorizzato)

Non si cancella mai un record (no DELETE): le bozze annullate restano per
audit. Le revisioni del corpo di un singolo draft vengono accumulate in
`body_revisions` come array JSON (history dei testi precedenti) senza
tabella separata: il volume non lo giustifica.

NB: i campi `to_address`, `subject`, `attachments` sono valorizzati al
momento della *generazione* iniziale o *dell'apertura* nell'editor. La
selezione concreta del canale (`channel` PEC/email) puo' restare nulla
finche' l'operatore non lo decide nell'editor.
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


# Stati della bozza.
STATUS_PENDING = "pending"
STATUS_READY = "ready"
STATUS_SENT = "sent"
STATUS_CANCELLED = "cancelled"

STATI = (STATUS_PENDING, STATUS_READY, STATUS_SENT, STATUS_CANCELLED)

# Etichette user-friendly per la UI.
STATUS_LABELS = {
    STATUS_PENDING: "Da rivedere",
    STATUS_READY: "Pronta",
    STATUS_SENT: "Inviata",
    STATUS_CANCELLED: "Annullata",
}

# Canali di invio supportati.
CHANNEL_PEC = "pec"
CHANNEL_EMAIL = "email"
CHANNELS = (CHANNEL_PEC, CHANNEL_EMAIL)


# --------------------------------------------------------------------------- #
#  Modello
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DraftAttachment:
    """Allegato proposto/incluso in una bozza.

    L'attributo `path` punta al file su filesystem (tipicamente sotto
    C:\\WinCar\\Archivi\\Pratiche\\<N>\\... oppure caricato ad hoc).
    `included=True` significa "sara' allegato all'invio"; `False` significa
    "proposto dall'AI ma deselezionato dall'operatore" (lo teniamo nel
    record per ricostruire la checklist originaria in caso di re-edit).
    """

    path: str
    label: str = ""  # nome friendly per la UI (default: nome file)
    included: bool = True


@dataclass(frozen=True)
class Draft:
    """Bozza di risposta a una mail classificata M3."""

    id: int | None
    mail_class_id: int
    pratica_numero: int | None
    status: str
    channel: str | None  # CHANNEL_PEC | CHANNEL_EMAIL | None (non ancora scelto)
    to_address: str
    cc_addresses: tuple[str, ...]
    subject: str
    body_html: str
    body_revisions: tuple[str, ...] = field(default_factory=tuple)
    attachments: tuple[DraftAttachment, ...] = field(default_factory=tuple)
    ai_model: str = ""
    ai_cost_eur: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime | None = None
    sent_at: datetime | None = None
    sent_eml_path: str = ""
    cancel_reason: str = ""

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def is_editable(self) -> bool:
        """True se la bozza puo' ancora essere modificata."""
        return self.status in (STATUS_PENDING, STATUS_READY)

    @property
    def attachments_included(self) -> tuple[DraftAttachment, ...]:
        """Solo gli allegati spuntati per l'invio."""
        return tuple(a for a in self.attachments if a.included)


# --------------------------------------------------------------------------- #
#  Schema
# --------------------------------------------------------------------------- #


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS risposte_draft (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    mail_class_id     INTEGER NOT NULL,
    pratica_numero    INTEGER,
    status            TEXT NOT NULL DEFAULT 'pending',
    channel           TEXT,
    to_address        TEXT NOT NULL DEFAULT '',
    cc_addresses_json TEXT NOT NULL DEFAULT '[]',
    subject           TEXT NOT NULL DEFAULT '',
    body_html         TEXT NOT NULL DEFAULT '',
    body_revisions_json TEXT NOT NULL DEFAULT '[]',
    attachments_json  TEXT NOT NULL DEFAULT '[]',
    ai_model          TEXT NOT NULL DEFAULT '',
    ai_cost_eur       REAL NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT,
    sent_at           TEXT,
    sent_eml_path     TEXT NOT NULL DEFAULT '',
    cancel_reason     TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (mail_class_id) REFERENCES mail_classificate(id)
);

-- 1:1 logico mail_classificata <-> draft: una sola bozza per classificazione.
CREATE UNIQUE INDEX IF NOT EXISTS uq_risposte_draft_class
    ON risposte_draft(mail_class_id);

CREATE INDEX IF NOT EXISTS idx_risposte_draft_pratica
    ON risposte_draft(pratica_numero);

CREATE INDEX IF NOT EXISTS idx_risposte_draft_status
    ON risposte_draft(status);

CREATE INDEX IF NOT EXISTS idx_risposte_draft_created
    ON risposte_draft(created_at DESC);
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
#  Repository
# --------------------------------------------------------------------------- #


class DraftRepository:
    """CRUD per `risposte_draft` (singolo DB SQLite condiviso con M2/M3)."""

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

    # -- mapping riga DB -> dataclass ----------------------------------------

    @staticmethod
    def _row_to_draft(row: sqlite3.Row) -> Draft:
        try:
            cc = json.loads(row["cc_addresses_json"] or "[]")
            if not isinstance(cc, list):
                cc = []
        except (TypeError, ValueError):
            cc = []
        try:
            revs = json.loads(row["body_revisions_json"] or "[]")
            if not isinstance(revs, list):
                revs = []
        except (TypeError, ValueError):
            revs = []
        try:
            atts_raw = json.loads(row["attachments_json"] or "[]")
            if not isinstance(atts_raw, list):
                atts_raw = []
        except (TypeError, ValueError):
            atts_raw = []
        atts: list[DraftAttachment] = []
        for item in atts_raw:
            if not isinstance(item, dict):
                continue
            atts.append(
                DraftAttachment(
                    path=str(item.get("path") or ""),
                    label=str(item.get("label") or ""),
                    included=bool(item.get("included", True)),
                )
            )
        return Draft(
            id=row["id"],
            mail_class_id=int(row["mail_class_id"]),
            pratica_numero=row["pratica_numero"],
            status=row["status"] or STATUS_PENDING,
            channel=row["channel"] if row["channel"] in CHANNELS else None,
            to_address=row["to_address"] or "",
            cc_addresses=tuple(str(x) for x in cc),
            subject=row["subject"] or "",
            body_html=row["body_html"] or "",
            body_revisions=tuple(str(x) for x in revs),
            attachments=tuple(atts),
            ai_model=row["ai_model"] or "",
            ai_cost_eur=float(row["ai_cost_eur"] or 0),
            created_at=_parse_dt(row["created_at"]) or datetime.now(),
            updated_at=_parse_dt(row["updated_at"]),
            sent_at=_parse_dt(row["sent_at"]),
            sent_eml_path=row["sent_eml_path"] or "",
            cancel_reason=row["cancel_reason"] or "",
        )

    @staticmethod
    def _serialize_attachments(atts: tuple[DraftAttachment, ...]) -> str:
        return json.dumps(
            [{"path": a.path, "label": a.label, "included": a.included} for a in atts],
            ensure_ascii=False,
        )

    # -- CRUD principali -----------------------------------------------------

    def insert_draft(
        self,
        *,
        mail_class_id: int,
        pratica_numero: int | None,
        subject: str = "",
        body_html: str = "",
        to_address: str = "",
        cc_addresses: tuple[str, ...] | list[str] = (),
        attachments: tuple[DraftAttachment, ...] = (),
        ai_model: str = "",
        ai_cost_eur: float = 0.0,
        channel: str | None = None,
    ) -> Draft:
        """Crea una nuova bozza in stato `pending`.

        Solleva `sqlite3.IntegrityError` se esiste gia' un draft per la
        stessa `mail_class_id` (l'indice unico lo impedisce). I chiamanti
        devono usare `get_by_classification` per testare idempotenza.
        """
        if channel is not None and channel not in CHANNELS:
            raise ValueError(f"Canale non valido: {channel!r}")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO risposte_draft (mail_class_id, pratica_numero, "
                " status, channel, to_address, cc_addresses_json, subject, "
                " body_html, attachments_json, ai_model, ai_cost_eur, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(mail_class_id),
                    int(pratica_numero) if pratica_numero is not None else None,
                    STATUS_PENDING,
                    channel,
                    to_address,
                    json.dumps(list(cc_addresses), ensure_ascii=False),
                    subject,
                    body_html,
                    self._serialize_attachments(tuple(attachments)),
                    ai_model,
                    float(ai_cost_eur),
                    _iso_now(),
                ),
            )
            new_id = cur.lastrowid
        draft = self.get_draft(int(new_id))
        assert draft is not None  # appena creato
        return draft

    def get_draft(self, draft_id: int) -> Draft | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM risposte_draft WHERE id = ?", (int(draft_id),)
            ).fetchone()
        return self._row_to_draft(row) if row else None

    def get_by_classification(self, mail_class_id: int) -> Draft | None:
        """Recupera la bozza esistente per una classificazione (se esiste)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM risposte_draft WHERE mail_class_id = ?",
                (int(mail_class_id),),
            ).fetchone()
        return self._row_to_draft(row) if row else None

    def update_draft(
        self,
        draft_id: int,
        *,
        subject: str | None = None,
        body_html: str | None = None,
        to_address: str | None = None,
        cc_addresses: tuple[str, ...] | list[str] | None = None,
        attachments: tuple[DraftAttachment, ...] | None = None,
        channel: str | None = None,
        status: str | None = None,
    ) -> Draft:
        """Aggiorna i campi indicati e marca `updated_at`.

        Se viene cambiato `body_html` rispetto al precedente, il vecchio
        contenuto viene push-ato in `body_revisions` (history) prima di
        sovrascrivere. Non sono ammessi update se lo status corrente e'
        `sent` o `cancelled`.
        """
        if channel is not None and channel not in CHANNELS:
            raise ValueError(f"Canale non valido: {channel!r}")
        if status is not None and status not in STATI:
            raise ValueError(f"Stato non valido: {status!r}")

        current = self.get_draft(draft_id)
        if current is None:
            raise ValueError(f"Draft {draft_id} inesistente")
        if not current.is_editable and status not in (STATUS_SENT, STATUS_CANCELLED):
            raise ValueError(
                f"Draft {draft_id} non e' modificabile (status={current.status})"
            )

        sets: list[str] = []
        params: list = []

        if subject is not None:
            sets.append("subject = ?")
            params.append(subject)
        if body_html is not None and body_html != current.body_html:
            # Versioning del corpo: pushiamo il vecchio in history.
            new_revisions = list(current.body_revisions)
            if current.body_html:
                new_revisions.append(current.body_html)
            sets.append("body_revisions_json = ?")
            params.append(json.dumps(new_revisions, ensure_ascii=False))
            sets.append("body_html = ?")
            params.append(body_html)
        if to_address is not None:
            sets.append("to_address = ?")
            params.append(to_address)
        if cc_addresses is not None:
            sets.append("cc_addresses_json = ?")
            params.append(json.dumps(list(cc_addresses), ensure_ascii=False))
        if attachments is not None:
            sets.append("attachments_json = ?")
            params.append(self._serialize_attachments(tuple(attachments)))
        if channel is not None:
            sets.append("channel = ?")
            params.append(channel)
        if status is not None:
            sets.append("status = ?")
            params.append(status)

        if not sets:
            return current

        sets.append("updated_at = ?")
        params.append(_iso_now())
        params.append(int(draft_id))

        with self._connect() as conn:
            conn.execute(
                f"UPDATE risposte_draft SET {', '.join(sets)} WHERE id = ?",
                params,
            )
        updated = self.get_draft(int(draft_id))
        assert updated is not None
        return updated

    def mark_sent(
        self,
        draft_id: int,
        *,
        sent_eml_path: str,
        channel: str | None = None,
    ) -> Draft:
        """Marca la bozza come inviata (immutabile dopo)."""
        if channel is not None and channel not in CHANNELS:
            raise ValueError(f"Canale non valido: {channel!r}")
        current = self.get_draft(draft_id)
        if current is None:
            raise ValueError(f"Draft {draft_id} inesistente")
        if current.status == STATUS_SENT:
            return current  # idempotente
        now = _iso_now()
        with self._connect() as conn:
            if channel is not None:
                conn.execute(
                    "UPDATE risposte_draft SET status = ?, sent_at = ?, "
                    " sent_eml_path = ?, channel = ?, updated_at = ? WHERE id = ?",
                    (STATUS_SENT, now, sent_eml_path, channel, now, int(draft_id)),
                )
            else:
                conn.execute(
                    "UPDATE risposte_draft SET status = ?, sent_at = ?, "
                    " sent_eml_path = ?, updated_at = ? WHERE id = ?",
                    (STATUS_SENT, now, sent_eml_path, now, int(draft_id)),
                )
        updated = self.get_draft(int(draft_id))
        assert updated is not None
        return updated

    def mark_cancelled(self, draft_id: int, *, reason: str = "") -> Draft:
        """Marca la bozza come annullata (non viene cancellata dal DB)."""
        current = self.get_draft(draft_id)
        if current is None:
            raise ValueError(f"Draft {draft_id} inesistente")
        if current.status == STATUS_SENT:
            raise ValueError(
                f"Draft {draft_id} gia' inviata, non puo' essere annullata"
            )
        if current.status == STATUS_CANCELLED:
            return current  # idempotente
        now = _iso_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE risposte_draft SET status = ?, cancel_reason = ?, "
                " updated_at = ? WHERE id = ?",
                (STATUS_CANCELLED, reason or "", now, int(draft_id)),
            )
        updated = self.get_draft(int(draft_id))
        assert updated is not None
        return updated

    # -- query di lista ------------------------------------------------------

    def list_per_pratica(self, numero_pratica: int) -> list[Draft]:
        """Tutte le bozze di una specifica pratica (incluse sent/cancelled)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM risposte_draft WHERE pratica_numero = ? "
                "ORDER BY created_at DESC, id DESC",
                (int(numero_pratica),),
            ).fetchall()
        return [self._row_to_draft(r) for r in rows]

    def list_by_status(self, status: str, *, limit: int = 200) -> list[Draft]:
        """Lista bozze in uno specifico stato (uso tipico: status=pending per
        sapere quante bozze attendono revisione nel cruscotto)."""
        if status not in STATI:
            raise ValueError(f"Stato non valido: {status!r}")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM risposte_draft WHERE status = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (status, int(limit)),
            ).fetchall()
        return [self._row_to_draft(r) for r in rows]

    def conta_per_status(self) -> dict[str, int]:
        """Mappa {status: count} su tutta la tabella. Utile per il badge UI."""
        out = {s: 0 for s in STATI}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM risposte_draft GROUP BY status"
            ).fetchall()
        for r in rows:
            s = r["status"] or STATUS_PENDING
            if s in out:
                out[s] = int(r["n"])
        return out
