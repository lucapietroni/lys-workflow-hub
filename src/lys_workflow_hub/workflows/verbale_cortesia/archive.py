"""Archiviazione dei verbali di cortesia nella cartella WinCar della pratica.

Salva in: <wincar_archivio>/Pratiche/<numpra>/Pubblici/Allegati/
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from lys_workflow_hub.workflows.verbale_cortesia.data import TIPO_USCITA


@dataclass(frozen=True)
class VerbaleArchiviato:
    path: Path
    nome_file: str
    dimensione_bytes: int
    data_archiviazione: date

    @property
    def size_label(self) -> str:
        kb = self.dimensione_bytes / 1024.0
        return f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB"


def _allegati_dir(archivio_root: Path, numero_pratica: int) -> Path:
    return (
        Path(archivio_root)
        / "Pratiche" / str(numero_pratica) / "Pubblici" / "Allegati"
    )


def _filename(tipo: str, today: date | None = None) -> str:
    today = today or date.today()
    label = "Uscita" if tipo == TIPO_USCITA else "Rientro"
    return f"Verbale_{label}_{today.strftime('%Y%m%d')}.pdf"


def save_verbale(
    *,
    archivio_root: Path,
    numero_pratica: int,
    tipo: str,
    pdf_bytes: bytes,
) -> Path:
    """Salva il PDF nella cartella WinCar. Rinomina l'eventuale file esistente."""
    if not pdf_bytes:
        raise ValueError("PDF vuoto.")
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("Il file non sembra un PDF valido.")

    allegati = _allegati_dir(archivio_root, numero_pratica)
    allegati.mkdir(parents=True, exist_ok=True)

    target = allegati / _filename(tipo)
    if target.exists():
        ts = int(time.time())
        target.rename(target.with_name(f"{target.stem}.backup-{ts}.pdf"))

    target.write_bytes(pdf_bytes)
    return target


def list_verbali(archivio_root: Path, numero_pratica: int) -> list[VerbaleArchiviato]:
    """Restituisce i verbali di cortesia già archiviati per la pratica."""
    allegati = _allegati_dir(archivio_root, numero_pratica)
    if not allegati.exists():
        return []
    results = []
    for path in sorted(allegati.glob("Verbale_*.pdf"), reverse=True):
        stat = path.stat()
        results.append(
            VerbaleArchiviato(
                path=path,
                nome_file=path.name,
                dimensione_bytes=stat.st_size,
                data_archiviazione=date.fromtimestamp(stat.st_mtime),
            )
        )
    return results
