"""Modello dati per i verbali di consegna/riconsegna veicolo di cortesia."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from lys_workflow_hub.core.wincar_repository import Pratica

TIPO_USCITA = "uscita"
TIPO_RIENTRO = "rientro"

LIVELLI_CARBURANTE = ["pieno", "3/4", "1/2", "1/4", "riserva", "vuoto"]


@dataclass(frozen=True)
class VerbaleData:
    tipo: str  # TIPO_USCITA or TIPO_RIENTRO
    numero_pratica: int

    # Locatario
    locatario_nome: str
    codice_fiscale: str
    indirizzo: str
    localita: str
    cap: str
    telefono: str

    # Patente (manual)
    patente_numero: str
    patente_rilasciata_da: str
    patente_data_rilascio: str
    patente_validita: str

    # Veicolo
    marca_modello: str
    telaio: str
    targa: str

    # Veicolo — campi manuali
    km: str
    livello_carburante: str
    omologato_per: str
    max_km_mese: str
    max_km_giorno: str
    tariffa_km_eccedenti: str
    accessori: str

    # Franchigie (rilevanti solo uscita, per ora editabili ma vuoti)
    rca: str
    kasco: str
    furto_incendio: str
    importo_giornaliero: str

    # Danni: lista di (parte, dettaglio)
    danni: list[tuple[str, str]] = field(default_factory=list)

    # Note e data/ora evento
    note: str = ""
    data_ora: str = ""  # "DD/MM/YYYY HH:MM"

    @property
    def label_tipo(self) -> str:
        return "Uscita" if self.tipo == TIPO_USCITA else "Rientro"

    @property
    def label_km(self) -> str:
        return "Km alla consegna" if self.tipo == TIPO_USCITA else "Km alla riconsegna"


def from_pratica(
    pratica: Pratica,
    tipo: str,
    overrides: dict[str, Any] | None = None,
) -> VerbaleData:
    overrides = overrides or {}

    def _get(key: str, default: Any) -> Any:
        return overrides[key] if key in overrides else default

    tel = pratica.cliente.cellulare or pratica.cliente.telefono or ""
    marca_mod = " ".join(
        v for v in [pratica.veicolo.marca, pratica.veicolo.modello] if v
    ).strip()

    default_data_ora = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Danni da overrides: 3 coppie di campi (parte1/det1, parte2/det2, parte3/det3)
    danni: list[tuple[str, str]] = []
    for i in range(1, 4):
        p = str(overrides.get(f"danno_parte_{i}", "")).strip()
        d = str(overrides.get(f"danno_det_{i}", "")).strip()
        if p or d:
            danni.append((p, d))

    return VerbaleData(
        tipo=tipo,
        numero_pratica=pratica.numero,
        locatario_nome=_get("locatario_nome", pratica.cliente.nominativo or ""),
        codice_fiscale=_get("codice_fiscale", pratica.cliente.codice_fiscale or ""),
        indirizzo=_get("indirizzo", pratica.cliente.via or ""),
        localita=_get("localita", pratica.cliente.citta or ""),
        cap=_get("cap", pratica.cliente.cap or ""),
        telefono=_get("telefono", tel),
        patente_numero=_get("patente_numero", ""),
        patente_rilasciata_da=_get("patente_rilasciata_da", ""),
        patente_data_rilascio=_get("patente_data_rilascio", ""),
        patente_validita=_get("patente_validita", ""),
        marca_modello=_get("marca_modello", marca_mod),
        telaio=_get("telaio", pratica.veicolo.telaio or ""),
        targa=_get("targa", pratica.veicolo.targa or ""),
        km=_get("km", ""),
        livello_carburante=_get("livello_carburante", ""),
        omologato_per=_get("omologato_per", ""),
        max_km_mese=_get("max_km_mese", ""),
        max_km_giorno=_get("max_km_giorno", ""),
        tariffa_km_eccedenti=_get("tariffa_km_eccedenti", ""),
        accessori=_get("accessori", ""),
        rca=_get("rca", ""),
        kasco=_get("kasco", ""),
        furto_incendio=_get("furto_incendio", ""),
        importo_giornaliero=_get("importo_giornaliero", "Gratuito"),
        danni=danni,
        note=_get("note", ""),
        data_ora=_get("data_ora", default_data_ora),
    )
