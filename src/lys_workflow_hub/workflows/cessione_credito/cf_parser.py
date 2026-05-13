"""Estrazione di data e luogo di nascita dal codice fiscale italiano.

Wrapper sopra la libreria `codicefiscale`: serve principalmente a:
- tollerare CF assenti o invalidi senza far crashare l'app;
- normalizzare il nome del comune (la libreria lo restituisce in maiuscolo).

Esempio:
    >>> parse_codice_fiscale("RSSMRA80A01H501Z").data_nascita
    datetime.date(1980, 1, 1)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DatiAnagraficiCF:
    """Quello che riusciamo a dedurre da un codice fiscale."""

    data_nascita: date | None
    luogo_nascita: str | None
    sesso: str | None  # "M" oppure "F"
    valido: bool
    motivo_invalidita: str | None = None

    @property
    def articolo(self) -> str:
        """Articolo per il testo "Il/la sottoscritto/a"."""
        if self.sesso == "F":
            return "La"
        return "Il"

    @property
    def desinenza_nato(self) -> str:
        """Desinenza per "nato/a"."""
        return "a" if self.sesso == "F" else "o"


_VUOTO = DatiAnagraficiCF(
    data_nascita=None,
    luogo_nascita=None,
    sesso=None,
    valido=False,
    motivo_invalidita="codice fiscale non fornito",
)


def parse_codice_fiscale(cf: str | None) -> DatiAnagraficiCF:
    """Decodifica un CF italiano. Restituisce sempre un oggetto, mai None.

    Se il CF e' assente, vuoto, e' una P.IVA (11 cifre) o e' invalido,
    `valido` sara' False e tutti i campi anagrafici saranno None.
    """
    if not cf:
        return _VUOTO

    cf = cf.strip().upper()
    if not cf:
        return _VUOTO

    # 11 cifre = partita IVA, non codice fiscale anagrafico
    if cf.isdigit() and len(cf) == 11:
        return DatiAnagraficiCF(
            data_nascita=None,
            luogo_nascita=None,
            sesso=None,
            valido=False,
            motivo_invalidita="il codice indicato sembra una partita IVA, non un codice fiscale",
        )

    if len(cf) != 16:
        return DatiAnagraficiCF(
            data_nascita=None,
            luogo_nascita=None,
            sesso=None,
            valido=False,
            motivo_invalidita=f"lunghezza non valida: {len(cf)} caratteri (attesi 16)",
        )

    try:
        from codicefiscale import codicefiscale
    except ImportError:
        return DatiAnagraficiCF(
            data_nascita=None,
            luogo_nascita=None,
            sesso=None,
            valido=False,
            motivo_invalidita=(
                "libreria 'python-codicefiscale' non installata. "
                "Esegui:  pip install python-codicefiscale"
            ),
        )

    try:
        info = codicefiscale.decode(cf)
    except Exception as exc:  # noqa: BLE001
        return DatiAnagraficiCF(
            data_nascita=None,
            luogo_nascita=None,
            sesso=None,
            valido=False,
            motivo_invalidita=f"decodifica fallita: {exc}",
        )

    bd = info.get("birthdate")
    if hasattr(bd, "date"):
        data_nasc: date | None = bd.date()
    elif isinstance(bd, date):
        data_nasc = bd
    else:
        data_nasc = None

    luogo = None
    birthplace = info.get("birthplace") or {}
    raw_name = birthplace.get("name")
    if raw_name:
        # La libreria restituisce "ROMA"; lo trasformiamo in "Roma" (Title case).
        luogo = raw_name.title()
        prov = birthplace.get("province")
        if prov:
            luogo = f"{luogo} ({prov})"

    # Le versioni recenti di python-codicefiscale espongono la chiave come
    # "gender"; le piu' vecchie come "sex". Accettiamo entrambe.
    sesso_raw = info.get("gender") or info.get("sex")
    sesso = sesso_raw.upper() if isinstance(sesso_raw, str) else None

    return DatiAnagraficiCF(
        data_nascita=data_nasc,
        luogo_nascita=luogo,
        sesso=sesso,
        valido=True,
        motivo_invalidita=None,
    )
