"""Modello dati per i verbali di consegna/riconsegna veicolo di cortesia."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from lys_workflow_hub.core.wincar_repository import Pratica

if TYPE_CHECKING:
    from lys_workflow_hub.core.auto_cortesia_repository import AutoCortesia, VerbaleRecord

TIPO_USCITA = "uscita"
TIPO_RIENTRO = "rientro"

LIVELLI_CARBURANTE = ["pieno", "3/4", "1/2", "1/4", "riserva", "vuoto"]


@dataclass(frozen=True)
class VerbaleData:
    tipo: str  # TIPO_USCITA or TIPO_RIENTRO
    numero_pratica: int
    auto_id: int | None  # FK auto_cortesia

    # Locatario (da WinCar)
    locatario_nome: str
    codice_fiscale: str
    indirizzo: str
    localita: str
    cap: str
    telefono: str

    # Patente (manuale)
    patente_numero: str
    patente_rilasciata_da: str
    patente_data_rilascio: str
    patente_validita: str

    # Veicolo (da AutoCortesia)
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

    # Franchigie (solo uscita, editabili)
    rca: str
    kasco: str
    furto_incendio: str
    importo_giornaliero: str

    # Danni: lista di (parte, dettaglio)
    danni: list[tuple[str, str]] = field(default_factory=list)

    # Note e data/ora evento
    note: str = ""
    data_ora: str = ""  # "DD/MM/YYYY HH:MM"

    # Dichiarazione necessità auto sostitutiva (pagina 2, solo uscita)
    # Veicolo CLIENTE (da WinCar pratica, non auto cortesia)
    cliente_marca: str = ""
    cliente_modello: str = ""
    cliente_targa: str = ""
    # Campi manuali dichiarazione
    dich_assicurazione: str = ""   # compagnia assicurativa
    dich_polizza: str = ""         # numero polizza
    dich_data_sinistro: str = ""   # data sinistro
    dich_motivazione: str = ""     # "lavoro"|"familiare"|"unico_mezzo"|"altro"
    dich_luogo: str = "Roma"

    @property
    def label_tipo(self) -> str:
        return "Uscita" if self.tipo == TIPO_USCITA else "Rientro"

    @property
    def label_km(self) -> str:
        return "Km alla consegna" if self.tipo == TIPO_USCITA else "Km alla riconsegna"


def from_pratica(
    pratica: Pratica,
    tipo: str,
    auto: AutoCortesia | None = None,
    last_rientro: VerbaleRecord | None = None,
    overrides: dict[str, Any] | None = None,
) -> VerbaleData:
    """Costruisce VerbaleData da pratica WinCar + auto di cortesia selezionata.

    - Locatario: da WinCar (pratica.cliente)
    - Veicolo: da auto (AutoCortesia), non da WinCar
    - km + danni: da last_rientro se tipo==uscita (ereditati dall'ultimo rientro)
    - Tutto sovrascrivibile via overrides (form POST)
    """
    overrides = overrides or {}

    def _get(key: str, default: Any) -> Any:
        return overrides[key] if key in overrides else default

    tel = pratica.cliente.cellulare or pratica.cliente.telefono or ""
    default_data_ora = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Veicolo da AutoCortesia (non da WinCar)
    auto_id = auto.id if auto else None
    targa = auto.targa if auto else ""
    marca_modello = auto.marca_modello if auto else ""
    telaio = auto.telaio if auto else ""

    # Km e danni ereditati dall'ultimo verbale rientro per questa auto
    default_km = ""
    default_danni: list[tuple[str, str]] = []
    if last_rientro and tipo == TIPO_USCITA:
        default_km = last_rientro.km
        default_danni = last_rientro.danni

    # Danni da overrides: 3 coppie (danno_parte_1/danno_det_1, ecc.)
    if any(f"danno_parte_{i}" in overrides or f"danno_det_{i}" in overrides
           for i in range(1, 4)):
        danni: list[tuple[str, str]] = []
        for i in range(1, 4):
            p = str(overrides.get(f"danno_parte_{i}", "")).strip()
            d = str(overrides.get(f"danno_det_{i}", "")).strip()
            if p or d:
                danni.append((p, d))
    else:
        danni = default_danni

    # Data firma dichiarazione = solo parte data di data_ora
    default_data_firma = datetime.now().strftime("%d/%m/%Y")

    return VerbaleData(
        tipo=tipo,
        numero_pratica=pratica.numero,
        auto_id=_get("auto_id", auto_id),
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
        marca_modello=_get("marca_modello", marca_modello),
        telaio=_get("telaio", telaio),
        targa=_get("targa", targa),
        km=_get("km", default_km),
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
        # Dichiarazione: veicolo cliente da WinCar
        cliente_marca=_get("cliente_marca", pratica.veicolo.marca or ""),
        cliente_modello=_get("cliente_modello", pratica.veicolo.modello or ""),
        cliente_targa=_get("cliente_targa", pratica.veicolo.targa or ""),
        # Dichiarazione: campi manuali
        dich_assicurazione=_get("dich_assicurazione", ""),
        dich_polizza=_get("dich_polizza", ""),
        dich_data_sinistro=_get("dich_data_sinistro", ""),
        dich_motivazione=_get("dich_motivazione", ""),
        dich_luogo=_get("dich_luogo", "Roma"),
    )
