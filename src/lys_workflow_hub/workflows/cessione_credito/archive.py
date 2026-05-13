"""Archiviazione dei documenti di cessione firmati.

Il PDF scansionato firmato viene salvato in:
  1. La cartella allegati della pratica in WinCar:
     `<wincar_archivio>/Pratiche/<numpra>/Pubblici/Allegati/Cessione_credito_<YYYYMMDD>_firmata.pdf`
     -> da li' WinCar lo visualizza come allegato della pratica.

  2. (Opzionale) Un archivio centrale dell'app, partizionato per anno:
     `<app_archivio_cessioni>/<anno>/Cessione_<numpra>_<YYYYMMDD>.pdf`
     -> visione cronologica indipendente da WinCar.

Se il file di destinazione esiste gia' (es. ricaricamento), viene rinominato
in `<nome>.backup-<timestamp>.pdf` cosi' la storia non si perde mai.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class CessioneFirmata:
    """Riferimento a un PDF di cessione firmata gia' archiviato."""

    path: Path
    nome_file: str
    dimensione_bytes: int
    data_archiviazione: date

    @property
    def size_label(self) -> str:
        kb = self.dimensione_bytes / 1024.0
        if kb < 1024:
            return f"{kb:.0f} KB"
        return f"{kb / 1024:.1f} MB"


@dataclass(frozen=True)
class SaveResult:
    """Esito del salvataggio."""

    pratica_path: Path
    """Dove e' stato salvato il file dentro WinCar (Pratiche/<n>/Pubblici/Allegati/...)."""

    archivio_path: Path | None
    """Eventuale copia salvata nell'archivio centrale dell'app."""

    backup_path: Path | None
    """Se esisteva gia' un file con lo stesso nome, dove e' stato spostato il vecchio."""


# Pattern dei nomi file che riconosciamo come "cessione firmata".
_PATTERN_FIRMATA = "Cessione_credito_*_firmata*.pdf"


def _allegati_dir(archivio_root: Path, numero_pratica: int) -> Path:
    """Cartella WinCar dove vanno gli allegati della pratica.

    WinCar organizza la struttura come:
        Pratiche/<numpra>/Pubblici/Allegati/   <- allegati visibili nella pratica
        Pratiche/<numpra>/Privati/             <- altri file riservati
    """
    return (
        Path(archivio_root)
        / "Pratiche" / str(numero_pratica) / "Pubblici" / "Allegati"
    )


def filename_firmata(today: date | None = None) -> str:
    """Nome standard del PDF firmato salvato in WinCar."""
    today = today or date.today()
    return f"Cessione_credito_{today.strftime('%Y%m%d')}_firmata.pdf"


def save_signed_pdf(
    *,
    archivio_root: Path,
    numero_pratica: int,
    pdf_bytes: bytes,
    central_archive_root: Path | None = None,
) -> SaveResult:
    """Salva il PDF firmato in WinCar (+ archivio centrale opzionale).

    - Crea `Pratiche/<n>/Pubblici/Allegati/` se non esiste.
    - Se il file di destinazione esiste, lo rinomina con suffisso `.backup-<ts>.pdf`.
    - Se `central_archive_root` e' valorizzato, salva anche una copia
      partizionata per anno.
    """
    if not pdf_bytes:
        raise ValueError("PDF vuoto.")
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("Il file caricato non sembra un PDF valido.")

    allegati = _allegati_dir(archivio_root, numero_pratica)
    allegati.mkdir(parents=True, exist_ok=True)

    target = allegati / filename_firmata()
    backup_path: Path | None = None
    if target.exists():
        ts = int(time.time())
        backup_path = target.with_name(f"{target.stem}.backup-{ts}.pdf")
        target.rename(backup_path)

    target.write_bytes(pdf_bytes)

    archivio_path: Path | None = None
    if central_archive_root:
        anno = date.today().year
        year_dir = Path(central_archive_root) / str(anno)
        year_dir.mkdir(parents=True, exist_ok=True)
        archivio_path = year_dir / f"Cessione_{numero_pratica}_{date.today().strftime('%Y%m%d')}.pdf"
        archivio_path.write_bytes(pdf_bytes)

    return SaveResult(
        pratica_path=target,
        archivio_path=archivio_path,
        backup_path=backup_path,
    )


def list_signed_pdfs(archivio_root: Path, numero_pratica: int) -> list[CessioneFirmata]:
    """Restituisce i PDF di cessione firmata gia' archiviati per la pratica."""
    allegati = _allegati_dir(archivio_root, numero_pratica)
    if not allegati.exists():
        return []
    results: list[CessioneFirmata] = []
    for path in sorted(allegati.glob(_PATTERN_FIRMATA), reverse=True):
        stat = path.stat()
        results.append(
            CessioneFirmata(
                path=path,
                nome_file=path.name,
                dimensione_bytes=stat.st_size,
                data_archiviazione=date.fromtimestamp(stat.st_mtime),
            )
        )
    return results
