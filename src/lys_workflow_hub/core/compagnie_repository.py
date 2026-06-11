"""Anagrafica delle compagnie assicurative.

Storage SQLite locale (`data/lys_hub.db`). Dati gestiti dall'operatore tramite
le pagine `/compagnie/...`. Vengono usati dal workflow di richiesta risarcimento
per atti vandalici (M2) per:

  1. precompilare l'indirizzo PEC della compagnia del cliente;
  2. precompilare indirizzo postale e riferimenti ufficio sinistri;
  3. fornire l'elenco completo nella schermata di gestione.

Il `lookup_by_name` normalizza i nomi prima del confronto (case + spazi +
suffissi tipo "S.p.A.") in modo che il valore letto da WinCar (campo F_DEASCL)
possa essere collegato all'anagrafica anche se scritto in modo leggermente
diverso.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Modello
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Compagnia:
    """Record di una compagnia assicurativa nell'anagrafica."""

    id: int | None
    nome: str
    pec: str
    email: str = ""
    indirizzo: str = ""
    cap: str = ""
    citta: str = ""
    provincia: str = ""
    telefono: str = ""
    ufficio_sinistri: str = ""
    note: str = ""
    created_at: datetime | None = field(default=None)
    updated_at: datetime | None = field(default=None)
    # SLA personalizzati (M6.1): None = usa i default globali da config.
    sla_sollecito_giorni: int | None = field(default=None)
    sla_formale_giorni: int | None = field(default=None)
    sla_diffida_giorni: int | None = field(default=None)

    # --- proprietà calcolate ---

    @property
    def indirizzo_compatto(self) -> str:
        """Es. 'Via XYZ 12, 00100 Roma (RM)' (omette le parti vuote)."""
        parti: list[str] = []
        if self.indirizzo:
            parti.append(self.indirizzo)
        cap_citta = " ".join(p for p in [self.cap, self.citta] if p)
        if cap_citta:
            parti.append(cap_citta)
        if self.provincia:
            parti.append(f"({self.provincia})")
        return ", ".join(p for p in parti if p)


def _normalizza_nome(nome: str) -> str:
    """Forma comparabile di un nome compagnia (case + spazi + suffissi)."""
    s = (nome or "").lower().strip()
    # Punteggiatura via, spazi multipli via.
    s = re.sub(r"[.,;:'\"]", "", s)
    s = re.sub(r"\s+", " ", s)
    # Suffissi societari più comuni.
    suffissi = (
        " spa", " s p a", " s.p.a", " s.p.a.",
        " srl", " s r l", " s.r.l", " s.r.l.",
        " sas", " s.a.s.", " snc", " s.n.c.",
        " assicurazioni", " ass.ni", " ass",
        " compagnia",
        " italia", " italy",
    )
    changed = True
    while changed:
        changed = False
        for suf in suffissi:
            if s.endswith(suf):
                s = s[: -len(suf)].rstrip()
                changed = True
    return s


# --------------------------------------------------------------------------- #
#  Repository
# --------------------------------------------------------------------------- #


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS compagnie_assicurative (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    nome                  TEXT NOT NULL,
    pec                   TEXT NOT NULL,
    email                 TEXT NOT NULL DEFAULT '',
    indirizzo             TEXT NOT NULL DEFAULT '',
    cap                   TEXT NOT NULL DEFAULT '',
    citta                 TEXT NOT NULL DEFAULT '',
    provincia             TEXT NOT NULL DEFAULT '',
    telefono              TEXT NOT NULL DEFAULT '',
    ufficio_sinistri      TEXT NOT NULL DEFAULT '',
    note                  TEXT NOT NULL DEFAULT '',
    nome_norm             TEXT NOT NULL DEFAULT '',
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    sla_sollecito_giorni  INTEGER,
    sla_formale_giorni    INTEGER,
    sla_diffida_giorni    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_compagnie_nome_norm
    ON compagnie_assicurative(nome_norm);

CREATE UNIQUE INDEX IF NOT EXISTS uq_compagnie_pec
    ON compagnie_assicurative(pec)
    WHERE pec <> '';
"""


_COLUMNS = (
    "id", "nome", "pec", "email", "indirizzo", "cap", "citta", "provincia",
    "ufficio_sinistri", "note", "created_at", "updated_at",
)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class CompagnieRepository:
    """CRUD per la tabella delle compagnie assicurative."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            for col_def in (
                "sla_sollecito_giorni INTEGER",
                "sla_formale_giorni   INTEGER",
                "sla_diffida_giorni   INTEGER",
                "telefono TEXT NOT NULL DEFAULT ''",
            ):
                try:
                    conn.execute(
                        f"ALTER TABLE compagnie_assicurative ADD COLUMN {col_def}"
                    )
                except sqlite3.OperationalError:
                    pass  # già presente

    # -- connessione ---------------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- mapping -------------------------------------------------------------

    @staticmethod
    def _row_to_compagnia(row: sqlite3.Row) -> Compagnia:
        d = dict(row)
        return Compagnia(
            id=d["id"],
            nome=d["nome"],
            pec=d["pec"],
            email=d.get("email") or "",
            indirizzo=d.get("indirizzo") or "",
            cap=d.get("cap") or "",
            citta=d.get("citta") or "",
            provincia=d.get("provincia") or "",
            telefono=d.get("telefono") or "",
            ufficio_sinistri=d.get("ufficio_sinistri") or "",
            note=d.get("note") or "",
            created_at=_parse_dt(d.get("created_at")),
            updated_at=_parse_dt(d.get("updated_at")),
            sla_sollecito_giorni=d.get("sla_sollecito_giorni"),
            sla_formale_giorni=d.get("sla_formale_giorni"),
            sla_diffida_giorni=d.get("sla_diffida_giorni"),
        )

    # -- query ---------------------------------------------------------------

    def list_all(self) -> list[Compagnia]:
        """Tutte le compagnie ordinate per nome."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM compagnie_assicurative ORDER BY nome COLLATE NOCASE"
            ).fetchall()
        return [self._row_to_compagnia(r) for r in rows]

    def get(self, compagnia_id: int) -> Compagnia | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM compagnie_assicurative WHERE id = ?",
                (int(compagnia_id),),
            ).fetchone()
        return self._row_to_compagnia(row) if row else None

    def lookup_by_name(self, nome: str) -> Compagnia | None:
        """Cerca una compagnia confrontando i nomi in forma normalizzata.

        Restituisce il match più "ricco" (con PEC valorizzata) se ce ne sono più
        di uno con lo stesso nome normalizzato. None se non c'è niente.
        """
        candidates = self.lookup_all_by_name(nome)
        return candidates[0] if candidates else None

    def lookup_all_by_name(self, nome: str) -> list[Compagnia]:
        """Tutti i match per nome normalizzato con prefix matching bidirezionale.

        Trova sia match esatti sia record il cui nome normalizzato è un prefisso
        del termine cercato (o viceversa). Esempio: cercando "Unipol" restituisce
        anche "Unipol Agenzia 39622"; cercando "Unipol Agenzia 39622" restituisce
        anche "Unipol".
        Ordine: PEC presente → lunghezza nome (più generico prima) → id.
        """
        if not nome or not nome.strip():
            return []
        norm = _normalizza_nome(nome)
        if not norm:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM compagnie_assicurative "
                "WHERE nome_norm = ? "
                "   OR nome_norm LIKE ? || ' %' "
                "   OR ? LIKE nome_norm || ' %' "
                "ORDER BY CASE WHEN pec <> '' THEN 0 ELSE 1 END, "
                "         length(nome_norm), id",
                (norm, norm, norm),
            ).fetchall()
        return [self._row_to_compagnia(r) for r in rows]

    # -- mutate --------------------------------------------------------------

    def create(
        self,
        *,
        nome: str,
        pec: str,
        email: str = "",
        telefono: str = "",
        indirizzo: str = "",
        cap: str = "",
        citta: str = "",
        provincia: str = "",
        ufficio_sinistri: str = "",
        note: str = "",
        sla_sollecito_giorni: int | None = None,
        sla_formale_giorni: int | None = None,
        sla_diffida_giorni: int | None = None,
    ) -> Compagnia:
        if not (nome or "").strip():
            raise ValueError("Il nome della compagnia è obbligatorio.")
        if not (pec or "").strip() and not (email or "").strip():
            raise ValueError(
                "Inserire almeno un indirizzo PEC o un'email ordinaria."
            )
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO compagnie_assicurative "
                    "(nome, pec, email, telefono, indirizzo, cap, citta, provincia, "
                    " ufficio_sinistri, note, nome_norm, created_at, updated_at, "
                    " sla_sollecito_giorni, sla_formale_giorni, sla_diffida_giorni) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        nome.strip(),
                        pec.strip(),
                        email.strip(),
                        (telefono or "").strip(),
                        indirizzo.strip(),
                        cap.strip(),
                        citta.strip(),
                        provincia.strip().upper(),
                        ufficio_sinistri.strip(),
                        note.strip(),
                        _normalizza_nome(nome),
                        now,
                        now,
                        sla_sollecito_giorni,
                        sla_formale_giorni,
                        sla_diffida_giorni,
                    ),
                )
                new_id = cur.lastrowid
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    f"PEC '{pec}' già presente in anagrafica."
                ) from exc
        return self.get(int(new_id))  # type: ignore[arg-type]

    def update(
        self,
        compagnia_id: int,
        *,
        nome: str,
        pec: str,
        email: str = "",
        telefono: str = "",
        indirizzo: str = "",
        cap: str = "",
        citta: str = "",
        provincia: str = "",
        ufficio_sinistri: str = "",
        note: str = "",
        sla_sollecito_giorni: int | None = None,
        sla_formale_giorni: int | None = None,
        sla_diffida_giorni: int | None = None,
    ) -> Compagnia:
        existing = self.get(compagnia_id)
        if existing is None:
            raise ValueError(f"Compagnia id={compagnia_id} non trovata.")
        if not (nome or "").strip():
            raise ValueError("Il nome della compagnia è obbligatorio.")
        if not (pec or "").strip() and not (email or "").strip():
            raise ValueError(
                "Inserire almeno un indirizzo PEC o un'email ordinaria."
            )
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            try:
                conn.execute(
                    "UPDATE compagnie_assicurative SET "
                    " nome = ?, pec = ?, email = ?, telefono = ?, indirizzo = ?, cap = ?, "
                    " citta = ?, provincia = ?, ufficio_sinistri = ?, note = ?, "
                    " nome_norm = ?, updated_at = ?, "
                    " sla_sollecito_giorni = ?, sla_formale_giorni = ?, "
                    " sla_diffida_giorni = ? "
                    "WHERE id = ?",
                    (
                        nome.strip(),
                        pec.strip(),
                        email.strip(),
                        (telefono or "").strip(),
                        indirizzo.strip(),
                        cap.strip(),
                        citta.strip(),
                        provincia.strip().upper(),
                        ufficio_sinistri.strip(),
                        note.strip(),
                        _normalizza_nome(nome),
                        now,
                        sla_sollecito_giorni,
                        sla_formale_giorni,
                        sla_diffida_giorni,
                        int(compagnia_id),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    f"PEC '{pec}' già usata da un'altra compagnia."
                ) from exc
        result = self.get(compagnia_id)
        assert result is not None
        return result

    def delete(self, compagnia_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM compagnie_assicurative WHERE id = ?",
                (int(compagnia_id),),
            )
            return cur.rowcount > 0

    def get_sla_soglie_by_nome(self, nome: str) -> dict[int, int] | None:
        """Restituisce le soglie SLA personalizzate per la compagnia (M6.1).

        Formato: ``{1: giorni_sollecito, 2: giorni_formale, 3: giorni_diffida}``.
        Ritorna ``None`` se la compagnia non è trovata o non ha override.
        Valori NULL nel DB (colonne non impostate) vengono omessi dal dict
        così il chiamante può usare i default globali per quei livelli.
        """
        comp = self.lookup_by_name(nome)
        if comp is None:
            return None
        result: dict[int, int] = {}
        if comp.sla_sollecito_giorni is not None:
            result[1] = comp.sla_sollecito_giorni
        if comp.sla_formale_giorni is not None:
            result[2] = comp.sla_formale_giorni
        if comp.sla_diffida_giorni is not None:
            result[3] = comp.sla_diffida_giorni
        return result if result else None
