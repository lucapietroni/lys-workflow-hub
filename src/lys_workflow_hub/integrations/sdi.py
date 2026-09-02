"""Client per il Sistema di Interscambio (SDI) — fatturazione elettronica.

Contabilità gestionale, Fase 3. NON è contabilità fiscale: qui trasmettiamo
allo SDI gli XML delle fatture attive **già generati da WinCar** e scarichiamo
le fatture passive che lo SDI ci recapita. La generazione dell'XML FatturaPA
non è compito nostro.

Il client vive dietro un'interfaccia minima (:class:`SdiClient`) così il
provider è sostituibile:

    invia_fattura(payload)  -> InvioResult
    ricevi_fatture(since)   -> list[FatturaPassivaRaw]
    ottieni_pdf(sdi_id)     -> bytes

Implementazioni:
  - :class:`FakeSdiClient` — nessuna rete. Invii simulati (sempre OK, id
    fittizio), nessuna passiva. Default in sviluppo/test.
  - :class:`OpenapiSdiClient` — provider Openapi (openapi.com). Gli endpoint
    REST vanno validati in sandbox prima del passaggio in produzione: sono
    isolati qui e nient'altro nel progetto ne dipende.

Factory: :func:`build_sdi_client(settings)`.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

logger = logging.getLogger(__name__)


PROVIDER_FAKE = "fake"
PROVIDER_OPENAPI = "openapi"


# --------------------------------------------------------------------------- #
#  DTO
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FatturaAttivaPayload:
    """XML di una fattura attiva pronto per la trasmissione allo SDI."""

    numero: str
    xml_bytes: bytes
    filename: str


@dataclass(frozen=True)
class FatturaPassivaRaw:
    """Fattura passiva recapitata dallo SDI, così come arriva dal provider."""

    sdi_id: str
    xml_bytes: bytes
    filename: str


@dataclass(frozen=True)
class InvioResult:
    ok: bool
    sdi_id: str = ""
    stato: str = ""      # es. 'inviata', 'scartata'
    messaggio: str = ""


# --------------------------------------------------------------------------- #
#  Interfaccia
# --------------------------------------------------------------------------- #


class SdiClient(Protocol):
    def invia_fattura(self, payload: FatturaAttivaPayload) -> InvioResult: ...

    def ricevi_fatture(self, since: date | None) -> list[FatturaPassivaRaw]: ...

    def ottieni_pdf(self, sdi_id: str) -> bytes: ...


# --------------------------------------------------------------------------- #
#  Fake
# --------------------------------------------------------------------------- #


@dataclass
class FakeSdiClient:
    """Client fittizio: nessuna chiamata di rete.

    - ``invia_fattura`` registra il payload e risponde sempre OK con un id
      sintetico ``FAKE-<numero>``.
    - ``ricevi_fatture`` restituisce ciò che è stato messo in ``inbox``
      (vuoto di default).
    - ``ottieni_pdf`` restituisce ``b""``.
    """

    inviate: list[FatturaAttivaPayload] = field(default_factory=list)
    inbox: list[FatturaPassivaRaw] = field(default_factory=list)

    def invia_fattura(self, payload: FatturaAttivaPayload) -> InvioResult:
        self.inviate.append(payload)
        sdi_id = f"FAKE-{payload.numero}"
        logger.info("FakeSdiClient: invio simulato fattura %s -> %s", payload.numero, sdi_id)
        return InvioResult(ok=True, sdi_id=sdi_id, stato="inviata", messaggio="simulato")

    def ricevi_fatture(self, since: date | None) -> list[FatturaPassivaRaw]:
        return list(self.inbox)

    def ottieni_pdf(self, sdi_id: str) -> bytes:
        return b""


# --------------------------------------------------------------------------- #
#  Openapi
# --------------------------------------------------------------------------- #


class OpenapiSdiClient:
    """Client per Openapi (openapi.com).

    ATTENZIONE: gli endpoint/campi qui sotto sono la migliore ipotesi dalla
    documentazione pubblica e vanno confermati in sandbox
    (``sdi_test_mode=True``) prima della produzione. Se il provider cambia,
    si tocca solo questa classe.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openapi.com",
        test_mode: bool = True,
        timeout: int = 30,
    ) -> None:
        if not api_key:
            raise ValueError("SDI_API_KEY non configurata: client Openapi non utilizzabile.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.test_mode = test_mode
        self.timeout = timeout

    # -- helpers -------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict) -> dict:
        import requests  # lazy: come in integrations/notifier.py

        url = f"{self.base_url}{path}"
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _get(self, path: str, params: dict | None = None) -> dict:
        import requests

        url = f"{self.base_url}{path}"
        resp = requests.get(url, params=params or {}, headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # -- API ---------------------------------------------------------------

    def invia_fattura(self, payload: FatturaAttivaPayload) -> InvioResult:
        body = {
            "test": self.test_mode,
            "filename": payload.filename,
            "xml_base64": base64.b64encode(payload.xml_bytes).decode("ascii"),
        }
        try:
            data = self._post("/sdi/invoices", body)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Openapi invia_fattura %s fallito: %s", payload.numero, exc)
            return InvioResult(ok=False, messaggio=str(exc))
        sdi_id = str(data.get("id") or data.get("uuid") or "")
        stato = str(data.get("status") or data.get("stato") or "inviata")
        return InvioResult(ok=True, sdi_id=sdi_id, stato=stato, messaggio="ok")

    def ricevi_fatture(self, since: date | None) -> list[FatturaPassivaRaw]:
        params: dict[str, str] = {}
        if since is not None:
            params["from"] = since.isoformat()
        try:
            data = self._get("/sdi/invoices/received", params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Openapi ricevi_fatture fallito: %s", exc)
            return []
        out: list[FatturaPassivaRaw] = []
        for item in data.get("data") or data.get("items") or []:
            xml_b64 = item.get("xml_base64") or item.get("xml") or ""
            if not xml_b64:
                continue
            try:
                xml_bytes = base64.b64decode(xml_b64)
            except (ValueError, TypeError):
                continue
            out.append(
                FatturaPassivaRaw(
                    sdi_id=str(item.get("id") or item.get("uuid") or ""),
                    xml_bytes=xml_bytes,
                    filename=str(item.get("filename") or "fattura.xml"),
                )
            )
        return out

    def ottieni_pdf(self, sdi_id: str) -> bytes:
        import requests

        url = f"{self.base_url}/sdi/invoices/{sdi_id}/pdf"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=self.timeout)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:  # noqa: BLE001
            logger.warning("Openapi ottieni_pdf %s fallito: %s", sdi_id, exc)
            return b""


# --------------------------------------------------------------------------- #
#  Factory
# --------------------------------------------------------------------------- #


def build_sdi_client(settings) -> SdiClient:
    """Costruisce il client SDI in base a ``settings.sdi_provider``."""
    provider = (getattr(settings, "sdi_provider", PROVIDER_FAKE) or PROVIDER_FAKE).lower()
    if provider == PROVIDER_OPENAPI:
        return OpenapiSdiClient(
            api_key=settings.sdi_api_key,
            base_url=settings.sdi_base_url,
            test_mode=bool(settings.sdi_test_mode),
        )
    if provider != PROVIDER_FAKE:
        logger.warning("SDI_PROVIDER '%s' sconosciuto: uso il client fake.", provider)
    return FakeSdiClient()
