"""Anagrafica utenti applicativi e autenticazione (v3.0).

Storage SQLite locale (`data/lys_hub.db`). Due ruoli:
  - "admin": accesso completo (operatori carrozzeria).
  - "esterno": accesso limitato alle sole pratiche assegnate (agenzie
    pratiche auto, avvocati) — pagine e permessi introdotti nelle fasi
    successive (assegnazione pratiche, collaborazione, calendario).

Le password non sono mai salvate in chiaro: solo l'hash bcrypt
(`password_hash`). Il blocco account dopo troppi tentativi falliti
(`failed_login_count` / `locked_until`) è gestito qui in `authenticate()`,
non nel layer web, cosi' resta valido anche per eventuali script CLI.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Literal

import bcrypt


logger = logging.getLogger(__name__)

Ruolo = Literal["admin", "esterno"]

RUOLI = ("admin", "esterno")


@dataclass(frozen=True)
class Utente:
    """Record di un utente applicativo."""

    id: int
    email: str
    nome: str
    ruolo: Ruolo
    attivo: bool
    created_at: datetime | None
    last_login: datetime | None

    @property
    def is_admin(self) -> bool:
        return self.ruolo == "admin"


class AuthError(Exception):
    """Errore di autenticazione, con messaggio pensato per l'utente finale."""


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS utenti (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    email                 TEXT NOT NULL,
    password_hash         TEXT NOT NULL,
    nome                  TEXT NOT NULL DEFAULT '',
    ruolo                 TEXT NOT NULL DEFAULT 'esterno',
    attivo                INTEGER NOT NULL DEFAULT 1,
    failed_login_count    INTEGER NOT NULL DEFAULT 0,
    locked_until          TEXT,
    created_at            TEXT NOT NULL,
    last_login            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_utenti_email
    ON utenti(email);
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        # hash corrotto/formato inatteso: mai far passare per valido.
        return False


class UtentiRepository:
    """CRUD utenti + verifica credenziali con blocco anti-bruteforce."""

    def __init__(
        self,
        db_path: Path,
        *,
        max_attempts: int = 5,
        lockout_minutes: int = 15,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max_attempts
        self.lockout_minutes = lockout_minutes
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except AuthError:
            # `authenticate()` scrive bookkeeping (failed_login_count,
            # locked_until) e POI solleva AuthError nello stesso blocco: senza
            # questo commit esplicito, il rollback implicito alla chiusura
            # della connessione cancellerebbe il conteggio tentativi falliti,
            # rendendo il blocco anti-bruteforce inefficace. Limitato ad
            # AuthError (non un `except Exception` generico) per non
            # persistere stato parziale in caso di bug/eccezione impreviste
            # altrove nel blocco — in quel caso vogliamo il rollback normale.
            conn.commit()
            raise
        finally:
            conn.close()

    @staticmethod
    def _row_to_utente(row: sqlite3.Row) -> Utente:
        d = dict(row)
        return Utente(
            id=d["id"],
            email=d["email"],
            nome=d.get("nome") or "",
            ruolo=d["ruolo"],
            attivo=bool(d["attivo"]),
            created_at=_parse_dt(d.get("created_at")),
            last_login=_parse_dt(d.get("last_login")),
        )

    # -- query -----------------------------------------------------------

    def get(self, utente_id: int) -> Utente | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM utenti WHERE id = ?", (int(utente_id),)
            ).fetchone()
        return self._row_to_utente(row) if row else None

    def get_by_email(self, email: str) -> Utente | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM utenti WHERE email = ?",
                ((email or "").strip().lower(),),
            ).fetchone()
        return self._row_to_utente(row) if row else None

    def list_all(self) -> list[Utente]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM utenti ORDER BY ruolo, nome COLLATE NOCASE"
            ).fetchall()
        return [self._row_to_utente(r) for r in rows]

    def list_esterni(self) -> list[Utente]:
        """Utenti esterni attivi — usato per l'assegnazione pratiche (fase 3)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM utenti WHERE ruolo = 'esterno' AND attivo = 1 "
                "ORDER BY nome COLLATE NOCASE"
            ).fetchall()
        return [self._row_to_utente(r) for r in rows]

    # -- mutate ------------------------------------------------------------

    def create(
        self,
        *,
        email: str,
        password: str,
        nome: str = "",
        ruolo: Ruolo = "esterno",
    ) -> Utente:
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise ValueError("Email non valida.")
        if not password or len(password) < 8:
            raise ValueError("La password deve avere almeno 8 caratteri.")
        if ruolo not in RUOLI:
            raise ValueError(f"Ruolo non valido: {ruolo!r}")
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO utenti "
                    "(email, password_hash, nome, ruolo, attivo, created_at) "
                    "VALUES (?, ?, ?, ?, 1, ?)",
                    (email, _hash_password(password), nome.strip(), ruolo, now),
                )
                new_id = cur.lastrowid
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Email '{email}' già registrata.") from exc
        result = self.get(int(new_id))  # type: ignore[arg-type]
        assert result is not None
        return result

    def set_password(self, utente_id: int, new_password: str) -> None:
        if not new_password or len(new_password) < 8:
            raise ValueError("La password deve avere almeno 8 caratteri.")
        with self._connect() as conn:
            conn.execute(
                "UPDATE utenti SET password_hash = ?, "
                "failed_login_count = 0, locked_until = NULL WHERE id = ?",
                (_hash_password(new_password), int(utente_id)),
            )

    def set_attivo(self, utente_id: int, attivo: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE utenti SET attivo = ? WHERE id = ?",
                (1 if attivo else 0, int(utente_id)),
            )

    def set_ruolo(self, utente_id: int, ruolo: Ruolo) -> None:
        if ruolo not in RUOLI:
            raise ValueError(f"Ruolo non valido: {ruolo!r}")
        with self._connect() as conn:
            conn.execute(
                "UPDATE utenti SET ruolo = ? WHERE id = ?",
                (ruolo, int(utente_id)),
            )

    def set_nome(self, utente_id: int, nome: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE utenti SET nome = ? WHERE id = ?",
                (nome.strip(), int(utente_id)),
            )

    def delete(self, utente_id: int) -> bool:
        """Hard delete. Preferire `set_attivo(id, False)` quando possibile:
        dalla fase 3 in poi `utenti.id` sarà referenziato da FK (assegnazione
        pratiche, note), e un hard delete orfanerebbe quei record."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM utenti WHERE id = ?", (int(utente_id),))
            return cur.rowcount > 0

    # -- autenticazione ------------------------------------------------------

    def authenticate(self, email: str, password: str) -> Utente:
        """Verifica le credenziali, gestendo il blocco anti-bruteforce.

        Solleva `AuthError` (messaggio adatto a essere mostrato all'utente) se
        le credenziali non sono valide, l'account è disattivato o è
        temporaneamente bloccato. Non distingue "email inesistente" da
        "password errata" nel messaggio, per non facilitare l'enumerazione
        delle email registrate. I messaggi "account disattivato"/"troppi
        tentativi" invece RIVELANO che l'email esiste — accettabile per
        un'app single-tenant su LAN con pochi utenti noti, da rivedere se in
        futuro il portale esterno (fase 3+) espone il login a più persone.
        """
        email = (email or "").strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM utenti WHERE email = ?", (email,)
            ).fetchone()

            if row is None:
                # Nessun record: esegui comunque un hash "a vuoto" per rendere
                # il tempo di risposta simile al caso "utente esistente ma
                # password sbagliata" (mitigazione timing/enumeration).
                bcrypt.hashpw(b"dummy", bcrypt.gensalt())
                raise AuthError("Email o password non corretti.")

            d = dict(row)
            utente_id = d["id"]

            if not d["attivo"]:
                raise AuthError("Account disattivato. Contatta l'amministratore.")

            locked_until = _parse_dt(d.get("locked_until"))
            if locked_until and locked_until > datetime.now():
                minuti = max(1, int((locked_until - datetime.now()).total_seconds() // 60) + 1)
                raise AuthError(
                    f"Troppi tentativi falliti. Riprova tra {minuti} minuti."
                )

            if _verify_password(password, d["password_hash"]):
                conn.execute(
                    "UPDATE utenti SET failed_login_count = 0, locked_until = NULL, "
                    "last_login = ? WHERE id = ?",
                    (datetime.now().isoformat(timespec="seconds"), utente_id),
                )
                # Ri-leggo la riga aggiornata: `row` è la snapshot pre-UPDATE,
                # non conterrebbe il nuovo `last_login`.
                updated = conn.execute(
                    "SELECT * FROM utenti WHERE id = ?", (utente_id,)
                ).fetchone()
                return self._row_to_utente(updated)

            # Password errata: incrementa il contatore ATOMICAMENTE via SQL
            # (non leggendo `d["failed_login_count"]` e riscrivendo +1 da
            # Python): due `authenticate()` concorrenti sulla stessa email
            # aprono connessioni sqlite3 distinte, quindi un read-then-write
            # in Python perderebbe incrementi sotto tentativi paralleli e
            # ritarderebbe/eluderebbe il lockout.
            lock_until_iso = (
                datetime.now() + timedelta(minutes=self.lockout_minutes)
            ).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE utenti SET "
                "failed_login_count = failed_login_count + 1, "
                "locked_until = CASE WHEN failed_login_count + 1 >= ? "
                "THEN ? ELSE locked_until END "
                "WHERE id = ?",
                (self.max_attempts, lock_until_iso, utente_id),
            )
            nuovo_conteggio = conn.execute(
                "SELECT failed_login_count FROM utenti WHERE id = ?", (utente_id,)
            ).fetchone()[0]
            logger.warning("Login fallito per %s (tentativo %d)", email, nuovo_conteggio)
            raise AuthError("Email o password non corretti.")
