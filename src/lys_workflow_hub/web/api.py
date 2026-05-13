"""API REST per accedere ai dati di WinCar in sola lettura.

Endpoints disponibili in M1.1:

    GET /api/pratiche?cognome=...&targa=...&numero=...&limit=20
        Ricerca pratiche. Tutti i parametri opzionali; senza filtri restituisce
        le ultime `limit` pratiche per numero decrescente.

    GET /api/pratiche/{numero}
        Dettaglio completo di una pratica.

    GET /api/health/wincar
        Verifica la connettivita' al database WinCar (chiama repo.ping()).
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query

from lys_workflow_hub.core.wincar_repository import (
    Pratica,
    PraticaSummary,
    WinCarRepository,
)


router = APIRouter(prefix="/api", tags=["api"])


def get_repository() -> WinCarRepository:
    """Dependency: una nuova istanza per ogni richiesta (le connessioni sono short-lived)."""
    return WinCarRepository.from_settings()


@router.get("/health/wincar")
def health_wincar(repo: WinCarRepository = Depends(get_repository)) -> dict:
    """Ping al database WinCar."""
    try:
        repo.ping()
    except Exception as exc:  # noqa: BLE001 — vogliamo davvero qualsiasi errore qui
        raise HTTPException(
            status_code=503,
            detail=f"WinCar non raggiungibile: {exc}",
        ) from exc
    return {"status": "ok", "wincar": "reachable"}


@router.get("/pratiche")
def search_pratiche(
    cognome: str | None = Query(default=None, description="Match parziale su F_RAGSOC (case-insensitive)."),
    targa: str | None = Query(default=None, description="Match parziale su F_TARGAV (case-insensitive)."),
    numero: int | None = Query(default=None, description="Match esatto su F_NUMPRA."),
    limit: int = Query(default=20, ge=1, le=200),
    repo: WinCarRepository = Depends(get_repository),
) -> list[dict]:
    """Ricerca pratiche con filtri combinabili in AND."""
    results: list[PraticaSummary] = repo.search_pratiche(
        cognome=cognome,
        targa=targa,
        numero=numero,
        limit=limit,
    )
    return [asdict(r) for r in results]


@router.get("/pratiche/{numero}")
def get_pratica(
    numero: int,
    repo: WinCarRepository = Depends(get_repository),
) -> dict:
    """Dettaglio completo di una singola pratica."""
    pratica: Pratica | None = repo.get_pratica(numero)
    if pratica is None:
        raise HTTPException(status_code=404, detail=f"Pratica {numero} non trovata.")
    return asdict(pratica)
