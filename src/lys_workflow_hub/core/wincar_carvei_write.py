"""Unica scrittura verso il database di WinCar in tutto il progetto.

``wincar_repository.py`` resta interamente read-only (``ReadOnly=1`` in
connessione, zero metodi di scrittura — invariante esplicita e voluta).
Questo modulo esiste apposta separato per rendere impossibile confonderlo
col connettore di lettura generale: è una deroga isolata, concordata con
l'utente, a un solo campo, per un solo motivo.

Motivo: WinCar mostra l'iconcina "fotocamera" nella colonna Foto
dell'elenco pratiche solo se ``CARVEI.F_FOTO = -1`` (booleano Access: True
è -1, non 1). Verificato empiricamente confrontando un dump della riga
CARVEI di una pratica prima e dopo un upload foto fatto da dentro WinCar
stesso (``scripts/dump_pratica_carvei.py``): l'unico campo cambiato oltre
al timestamp di modifica è stato ``F_FOTO`` 0 -> -1. Il nostro upload salva
già i file sul filesystem e aggiorna ``Thumbs.thumb`` (vedi
``wincar_thumbs_index.py``), ma senza questo flag l'icona in elenco non
compare — il resto della UI di WinCar (apertura pratica, gallery foto)
funziona comunque solo coi file su disco.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pyodbc

logger = logging.getLogger(__name__)

_DB_FILE = "wcArchivi.mdb"

# WinCar potrebbe avere la stessa pratica aperta in modifica (lock Jet/ACE a
# livello di pagina) proprio mentre arriva un upload — senza un timeout
# esplicito la query potrebbe restare in attesa indefinitamente, occupando
# un worker thread del pool di Starlette (le route di upload sono `def`
# sincrone) e facendo restare appesa la risposta HTTP anche se la foto è
# già salvata su disco a quel punto. NB: il parametro `timeout=` di
# `pyodbc.connect()` controlla solo il login timeout, non il timeout della
# query — va impostato su `conn.timeout` DOPO la connessione.
_QUERY_TIMEOUT_SECONDI = 5


def _aggiorna_flag_foto(
    *, archivio_root: Path, odbc_driver: str, numero_pratica: int, valore: int
) -> None:
    """Nucleo condiviso da `marca_foto_presente`/`marca_foto_assente`:
    imposta ``CARVEI.F_FOTO = valore`` (no-op se è già a quel valore).
    Connessione dedicata, aperta e chiusa qui, mai in ``ReadOnly`` — a
    differenza di ogni altra connessione al DB WinCar nel progetto.

    Solleva l'eccezione originale in caso di errore (driver assente, file
    bloccato da WinCar in quel momento, timeout, ecc.): è responsabilità del
    chiamante trattarla come un passaggio best-effort che non deve mai
    bloccare l'azione vera e propria (salvataggio/eliminazione foto) — vedi
    i commenti accanto alle chiamate in ``pratica_files.py``.
    """
    db_path = Path(archivio_root) / _DB_FILE
    # Niente ReadOnly=1 qui: è l'unico punto del progetto che scrive
    # deliberatamente sul database di WinCar.
    conn_str = f"DRIVER={{{odbc_driver}}};DBQ={db_path};"
    conn = pyodbc.connect(conn_str, autocommit=True)
    conn.timeout = _QUERY_TIMEOUT_SECONDI
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE CARVEI SET F_FOTO = ?, F_DATAGG = ? "
            "WHERE F_NUMPRA = ? AND (F_FOTO <> ? OR F_FOTO IS NULL)",
            valore,
            datetime.now(),
            numero_pratica,
            valore,
        )
    finally:
        conn.close()


def marca_foto_presente(
    *, archivio_root: Path, odbc_driver: str, numero_pratica: int
) -> None:
    """Imposta ``CARVEI.F_FOTO = -1`` (booleano Access True) dopo l'upload di
    una foto, così l'icona fotocamera compare nell'elenco pratiche di
    WinCar."""
    _aggiorna_flag_foto(
        archivio_root=archivio_root,
        odbc_driver=odbc_driver,
        numero_pratica=numero_pratica,
        valore=-1,
    )


def marca_foto_assente(
    *, archivio_root: Path, odbc_driver: str, numero_pratica: int
) -> None:
    """Azzera ``CARVEI.F_FOTO`` quando l'ultima foto di una pratica viene
    eliminata dalla nostra app. WinCar non lo fa da solo: cancellando tutte
    le foto direttamente da WinCar l'icona fotocamera resta accesa comunque
    (bug osservato dall'utente) — qui almeno evitiamo di riprodurre lo
    stesso comportamento quando l'eliminazione passa da noi."""
    _aggiorna_flag_foto(
        archivio_root=archivio_root,
        odbc_driver=odbc_driver,
        numero_pratica=numero_pratica,
        valore=0,
    )
