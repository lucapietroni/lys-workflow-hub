"""Anagrafica utenti applicativi e autenticazione (v3.0).

Storage SQLite locale (`data/lys_hub.db`). Quattro ruoli:
  - "admin": accesso completo (operatori carrozzeria).
  - "esterno": accesso limitato alle sole pratiche assegnate (agenzie
    pratiche auto, avvocati) — pagine e permessi introdotti nelle fasi
    successive (assegnazione pratiche, collaborazione, calendario).
  - "supervisore": vede TUTTE le pratiche che hanno almeno un'assegnazione
    (a qualunque utente esterno, non solo a sé stesso), stesso portale
    dell'esterno ma in sola lettura — nessuna route di scrittura lo accetta
    (note, eventi, cambio stato, upload). Vedi `_richiedi_permesso_scrittura`
    in `web/routes_portale.py`.
  - "operatore": accesso ristretto a `/operatore` (creazione "ingressi
    officina" — pre-pratica con documenti scansionati, prima che la
    pratica esista in WinCar). Non vede pratiche esistenti, non ha nessun
    altro permesso. Vedi `web/routes_operatore.py`.

Le password non sono mai salvate in chiaro: solo l'hash bcrypt
(`password_hash`). Il blocco account dopo troppi tentativi falliti
(`failed_login_count` / `locked_until`) è gestito qui in `authenticate()`,
non nel layer web, cosi' resta valido anche per eventuali script CLI.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Literal

import bcrypt


logger = logging.getLogger(__name__)

Ruolo = Literal["admin", "esterno", "supervisore", "operatore"]

# Charset consigliato da ntfy.sh per i nomi dei topic. Un topic fuori da
# questo pattern (spazi, accenti, "/") non farebbe fallire la richiesta HTTP
# in modo rumoroso: `send_push` la costruisce come
# f"{server}/{topic}" e ingoia qualunque errore (mai bloccare il salvataggio
# di nota/evento per un problema di notifica) — l'utente si ritroverebbe
# semplicemente a non ricevere mai nulla, senza capirne il motivo.
_NTFY_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

RUOLI = ("admin", "esterno", "supervisore", "operatore")


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
    notify_email_enabled: bool = True
    notify_push_enabled: bool = False
    ntfy_topic: str = ""
    fcm_token: str = ""
    fcm_token_web: str = ""
    login_count: int = 0

    @property
    def is_admin(self) -> bool:
        return self.ruolo == "admin"

    @property
    def is_supervisore(self) -> bool:
        return self.ruolo == "supervisore"

    @property
    def is_operatore(self) -> bool:
        return self.ruolo == "operatore"


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

# Colonne aggiunte dopo la creazione iniziale della tabella (v3.0 fase 5,
# parte D — preferenze di notifica self-service per utenti esterni).
# ALTER TABLE avvolta in try/except: su un DB già migrato la colonna esiste
# già e SQLite solleva "duplicate column name" — pattern condiviso con le
# altre migrazioni del progetto (vedi auto_cortesia_repository.py).
_MIGRAZIONI_COLONNE = (
    "notify_email_enabled INTEGER NOT NULL DEFAULT 1",
    "notify_push_enabled INTEGER NOT NULL DEFAULT 0",
    "ntfy_topic TEXT NOT NULL DEFAULT ''",
    "fcm_token TEXT NOT NULL DEFAULT ''",
    "login_count INTEGER NOT NULL DEFAULT 0",
    "fcm_token_web TEXT NOT NULL DEFAULT ''",
)


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
            for col_def in _MIGRAZIONI_COLONNE:
                try:
                    conn.execute(f"ALTER TABLE utenti ADD COLUMN {col_def}")
                except sqlite3.OperationalError:
                    pass

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
            notify_email_enabled=bool(d.get("notify_email_enabled", 1)),
            notify_push_enabled=bool(d.get("notify_push_enabled", 0)),
            ntfy_topic=d.get("ntfy_topic") or "",
            fcm_token=d.get("fcm_token") or "",
            fcm_token_web=d.get("fcm_token_web") or "",
            login_count=d.get("login_count") or 0,
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

    def count_admin_attivi(self) -> int:
        """Usato per bloccare la disattivazione/eliminazione dell'ultimo admin
        rimasto (nessuno potrebbe più entrare per rimediare)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM utenti WHERE ruolo = 'admin' AND attivo = 1"
            ).fetchone()
        return int(row["n"])

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

    def set_notifiche(
        self,
        utente_id: int,
        *,
        notify_email_enabled: bool,
        notify_push_enabled: bool,
        ntfy_topic: str,
    ) -> None:
        """Preferenze di notifica self-service (v3.0 fase 5, parte D).

        Un topic ntfy è per definizione un "segreto debole" (chiunque lo
        conosca può leggere le notifiche): se l'utente attiva il push senza
        averne scritto uno, rifiutiamo piuttosto che salvare uno stato
        incoerente (`notify_push_enabled=True` con `ntfy_topic=""`) che
        finirebbe solo per non mandare mai nulla in silenzio.
        """
        ntfy_topic = (ntfy_topic or "").strip()
        if notify_push_enabled and not ntfy_topic:
            raise ValueError("Inserisci un topic ntfy per attivare le notifiche push.")
        if ntfy_topic and not _NTFY_TOPIC_RE.match(ntfy_topic):
            raise ValueError(
                "Topic ntfy non valido: usa solo lettere, cifre, '-' e '_' "
                "(max 64 caratteri, niente spazi)."
            )
        with self._connect() as conn:
            conn.execute(
                "UPDATE utenti SET notify_email_enabled = ?, "
                "notify_push_enabled = ?, ntfy_topic = ? WHERE id = ?",
                (
                    1 if notify_email_enabled else 0,
                    1 if notify_push_enabled else 0,
                    ntfy_topic,
                    int(utente_id),
                ),
            )

    def set_fcm_token(self, utente_id: int, fcm_token: str) -> None:
        """Registra (o cancella, se stringa vuota) il device token FCM per un
        utente esterno — chiamato da `POST /portale/fcm-token` subito dopo che
        il plugin @capacitor/push-notifications ottiene/rinnova il token sul
        device. Un utente ha un solo token app registrato alla volta: se lo
        stesso account si logga su un secondo device, il nuovo token
        sovrascrive il vecchio (niente multi-device fan-out in questa fase).
        Colonna separata da `fcm_token_web` (vedi `set_fcm_token_web`): un
        utente può avere sia l'app Android sia il portale in browser attivi
        contemporaneamente, senza che l'uno cancelli il token dell'altro."""
        fcm_token = (fcm_token or "").strip()
        with self._connect() as conn:
            conn.execute(
                "UPDATE utenti SET fcm_token = ? WHERE id = ?",
                (fcm_token, int(utente_id)),
            )

    def set_fcm_token_web(self, utente_id: int, fcm_token_web: str) -> None:
        """Come `set_fcm_token`, ma per il token Web Push registrato dal
        browser del portale (Firebase Web SDK) invece che dall'app Android —
        colonna indipendente, stesso motivo: coesistenza app+browser."""
        fcm_token_web = (fcm_token_web or "").strip()
        with self._connect() as conn:
            conn.execute(
                "UPDATE utenti SET fcm_token_web = ? WHERE id = ?",
                (fcm_token_web, int(utente_id)),
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
                    "last_login = ?, login_count = login_count + 1 WHERE id = ?",
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
