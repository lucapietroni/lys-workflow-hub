"""Client per l'API Automotive di Openapi (openapi.com) — ricerca veicolo per targa.

Stesso provider già usato per SDI (:mod:`lys_workflow_hub.integrations.sdi`),
prodotto diverso: qui interroghiamo l'anagrafica veicoli (PRA/motorizzazione)
a partire dalla targa, per uso interno (front-desk / accettazione).

Il client vive dietro un'interfaccia minima (:class:`AutomotiveClient`) così
il provider è sostituibile:

    cerca_veicolo(targa) -> VeicoloInfo

Implementazioni:
  - :class:`FakeAutomotiveClient` — nessuna rete, dati fittizi. Default in
    sviluppo/test.
  - :class:`OpenapiAutomotiveClient` — provider Openapi. Endpoint REST
    (dominio ``automotive.openapi.com``, path ``/IT-car/{targa}``) e schema
    di risposta (``{"success", "data": {"CarMake", "CarModel", ...}}``)
    confermati in produzione l'11/09/2026. Nessun dato di proprietà/PRA:
    l'endpoint restituisce solo dati tecnici del veicolo (marca, modello,
    versione, telaio, immatricolazione...). ``raw`` porta comunque sempre
    la risposta JSON completa, per i campi non mappati sotto.

Factory: :func:`build_automotive_client(settings)`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


PROVIDER_FAKE = "fake"
PROVIDER_OPENAPI = "openapi"


# --------------------------------------------------------------------------- #
#  DTO
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VeicoloInfo:
    """Dati tecnici veicolo così come restituiti dal provider (nessun dato di
    proprietà/PRA: l'endpoint Automotive non lo fornisce).

    `raw` contiene sempre l'intera risposta JSON, mostrata in fallback nel
    template così nessun dato va perso se il parsing manca un campo.
    """

    targa: str
    trovato: bool
    marca: str = ""
    modello: str = ""
    versione: str = ""
    carrozzeria: str = ""
    alimentazione: str = ""
    cambio: str = ""
    porte: str = ""
    potenza_cv: str = ""
    data_immatricolazione: str = ""
    telaio: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    errore: str = ""


# --------------------------------------------------------------------------- #
#  Interfaccia
# --------------------------------------------------------------------------- #


class AutomotiveClient(Protocol):
    def cerca_veicolo(self, targa: str) -> VeicoloInfo: ...


# --------------------------------------------------------------------------- #
#  Fake
# --------------------------------------------------------------------------- #


class FakeAutomotiveClient:
    """Client fittizio: nessuna chiamata di rete. Risponde sempre "non trovato"."""

    def cerca_veicolo(self, targa: str) -> VeicoloInfo:
        logger.info("FakeAutomotiveClient: ricerca simulata targa %s", targa)
        return VeicoloInfo(targa=targa, trovato=False, errore="Client fake: nessun dato disponibile.")


# --------------------------------------------------------------------------- #
#  Openapi
# --------------------------------------------------------------------------- #


_TARGA_RE = re.compile(r"[^A-Z0-9]")


def normalizza_targa(targa: str) -> str:
    """Uppercase + rimozione spazi/trattini. Non valida il formato: la
    validazione stringente (schema targhe IT) non è compito nostro qui."""
    return _TARGA_RE.sub("", (targa or "").upper())


class OpenapiAutomotiveClient:
    """Client per il prodotto Automotive di Openapi (openapi.com).

    Autenticazione: serve un Token generato in console.openapi.com/it/oauth
    (sezione "Autenticazione" — NON la API Key mostrata in cima alla stessa
    pagina, quella è solo per generare token via OAuth). Token Produzione e
    Sandbox sono separati e vanno abbinati al dominio giusto: un token
    Produzione contro ``test.automotive.openapi.com`` (o viceversa) risponde
    401 "Wrong Token".
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://automotive.openapi.com",
        timeout: int = 20,
    ) -> None:
        if not api_key:
            raise ValueError("AUTOMOTIVE_API_KEY (o SDI_API_KEY) non configurata.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    def cerca_veicolo(self, targa: str) -> VeicoloInfo:
        import requests  # lazy: come in integrations/sdi.py

        targa_norm = normalizza_targa(targa)
        if not targa_norm:
            return VeicoloInfo(targa=targa, trovato=False, errore="Targa vuota.")

        url = f"{self.base_url}/IT-car/{targa_norm}"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Openapi cerca_veicolo(%s) fallito (rete): %s", targa_norm, exc)
            return VeicoloInfo(targa=targa_norm, trovato=False, errore=f"Errore di rete: {exc}")

        if resp.status_code == 404:
            return VeicoloInfo(targa=targa_norm, trovato=False, errore="Nessun veicolo trovato per questa targa.")
        if resp.status_code >= 400:
            corpo = resp.text[:500]
            logger.warning(
                "Openapi cerca_veicolo(%s) fallito (HTTP %s): %s", targa_norm, resp.status_code, corpo
            )
            # Il corpo dell'errore del provider è mostrato in pagina (route admin-only)
            # per poter diagnosticare subito (token/prodotto/parametro) senza dover
            # andare a leggere il log file.
            return VeicoloInfo(
                targa=targa_norm, trovato=False,
                errore=f"Errore provider (HTTP {resp.status_code}): {corpo}",
            )

        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            logger.warning("Openapi cerca_veicolo(%s): risposta non JSON.", targa_norm)
            return VeicoloInfo(targa=targa_norm, trovato=False, errore="Risposta del provider non interpretabile.")

        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        if not payload:
            return VeicoloInfo(targa=targa_norm, trovato=False, raw=data, errore="Risposta vuota dal provider.")

        potenza = payload.get("PowerCV")  # 0 = non disponibile (come i campi stringa vuoti)
        return VeicoloInfo(
            targa=targa_norm,
            trovato=True,
            marca=str(payload.get("CarMake") or payload.get("MakeDescription") or ""),
            modello=str(payload.get("CarModel") or payload.get("ModelDescription") or ""),
            versione=str(payload.get("Version") or ""),
            carrozzeria=str(payload.get("BodyStyle") or ""),
            alimentazione=str(payload.get("FuelType") or ""),
            cambio=str(payload.get("Transmission") or ""),
            porte=str(payload.get("NumberOfDoors") or ""),
            potenza_cv=str(potenza) if potenza else "",
            data_immatricolazione=str(payload.get("RegistrationDate") or ""),
            telaio=str(payload.get("Vin") or ""),
            raw=data,
        )


# --------------------------------------------------------------------------- #
#  Factory
# --------------------------------------------------------------------------- #


def build_automotive_client(settings) -> AutomotiveClient:
    """Costruisce il client Automotive in base a ``settings.automotive_provider``.

    Se ``automotive_api_key`` non è impostata, riusa ``sdi_api_key`` (stesso
    account Openapi) — scelta esplicita del progetto per evitare di dover
    gestire due chiavi separate per lo stesso provider.
    """
    provider = (getattr(settings, "automotive_provider", PROVIDER_FAKE) or PROVIDER_FAKE).lower()
    if provider == PROVIDER_OPENAPI:
        api_key = settings.automotive_api_key or settings.sdi_api_key
        base_url = settings.automotive_base_url or (
            "https://test.automotive.openapi.com"
            if settings.automotive_test_mode
            else "https://automotive.openapi.com"
        )
        return OpenapiAutomotiveClient(api_key=api_key, base_url=base_url)
    if provider != PROVIDER_FAKE:
        logger.warning("AUTOMOTIVE_PROVIDER '%s' sconosciuto: uso il client fake.", provider)
    return FakeAutomotiveClient()
