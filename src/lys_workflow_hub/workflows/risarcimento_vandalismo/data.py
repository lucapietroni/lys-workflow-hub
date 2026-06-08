"""Modello dati per la Richiesta di risarcimento per atti vandalici (M2).

`RichiestaVandalismoData` è l'oggetto immutabile consumato dal generatore di
testo della PEC. Si costruisce a partire da:

  - una `Pratica` di WinCar (legge tutti i dati dell'assicurato, del veicolo,
    della polizza del cliente e del sinistro);
  - una `Compagnia` dell'anagrafica interna (per la PEC e i recapiti postali);
  - eventuali override forniti dall'operatore tramite la schermata di
    anteprima (es. numero di protocollo della denuncia presentata in
    Questura/Carabinieri, ora dell'evento più precisa, descrizione dei danni).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from lys_workflow_hub.core.compagnie_repository import Compagnia
from lys_workflow_hub.core.wincar_repository import Pratica
from lys_workflow_hub.workflows.cessione_credito.cf_parser import (
    DatiAnagraficiCF,
    parse_codice_fiscale,
)


# Dati anagrafici della carrozzeria (uguali a quelli usati nella cessione).
# Replichiamo qui le costanti per evitare la dipendenza incrociata tra workflow.
CARROZZERIA_NOME = "Carrozzeria LYS Auto srl"
CARROZZERIA_VIA = "Via Antonio Pucci 37"
CARROZZERIA_CAP = "00139"
CARROZZERIA_COMUNE = "Roma"
CARROZZERIA_PROVINCIA = "RM"
CARROZZERIA_PIVA = "14521721002"


# Valori validi per il tipo di autorità presso cui è stata sporta denuncia.
AUTORITA_DENUNCIA = (
    "Carabinieri",
    "Polizia di Stato",
    "Polizia Locale",
    "Polizia Stradale",
    "Guardia di Finanza",
)


@dataclass(frozen=True)
class RichiestaVandalismoData:
    """Tutti i dati necessari per generare la PEC di richiesta risarcimento."""

    numero_pratica: int

    # --- Assicurato ---
    assicurato_nome_completo: str
    assicurato_codice_fiscale: str
    assicurato_data_nascita: date | None
    assicurato_luogo_nascita: str
    assicurato_sesso: str  # "M" o "F"
    assicurato_residenza_via: str
    assicurato_residenza_comune: str
    assicurato_residenza_cap: str
    assicurato_residenza_provincia: str
    assicurato_telefono: str
    assicurato_email: str

    # --- Eventuale ditta ---
    e_ditta: bool = False
    ditta_nome: str = ""
    ditta_partita_iva: str = ""

    # --- Polizza del cliente ---
    polizza_compagnia_nome: str = ""
    polizza_numero: str = ""
    polizza_agenzia: str = ""

    # --- Veicolo ---
    veicolo_marca_modello: str = ""
    veicolo_targa: str = ""
    veicolo_telaio: str = ""

    # --- Evento ---
    evento_data: date | None = None
    evento_ora: str = ""
    evento_luogo_via: str = ""
    evento_luogo_comune: str = ""
    evento_descrizione_danni: str = ""

    # --- Denuncia ---
    denuncia_autorita: str = "Carabinieri"
    denuncia_comando: str = ""  # es. "Stazione Carabinieri di Roma Tomba di Nerone"
    denuncia_data: date | None = None
    denuncia_protocollo: str = ""

    # --- Compagnia destinataria (dall'anagrafica interna) ---
    compagnia_pec: str = ""
    compagnia_indirizzo: str = ""
    compagnia_cap: str = ""
    compagnia_citta: str = ""
    compagnia_provincia: str = ""
    compagnia_ufficio_sinistri: str = ""

    # --- Contatti carrozzeria ---
    carrozzeria_pec: str = ""
    carrozzeria_email: str = ""
    carrozzeria_telefono: str = ""
    carrozzeria_referente: str = ""

    # --- Diagnostica anagrafica (per log/debug) ---
    cf_info: DatiAnagraficiCF | None = field(default=None, repr=False)

    # --- proprietà calcolate ---

    @property
    def sottoscritto_label(self) -> str:
        return "La sottoscritta" if self.assicurato_sesso == "F" else "Il sottoscritto"

    @property
    def assistito_label(self) -> str:
        return "Assistita" if self.assicurato_sesso == "F" else "Assistito"

    @property
    def residenza_compatta(self) -> str:
        parti: list[str] = []
        if self.assicurato_residenza_via:
            parti.append(self.assicurato_residenza_via)
        cap_citta = " ".join(p for p in [
            self.assicurato_residenza_cap, self.assicurato_residenza_comune
        ] if p)
        if cap_citta:
            parti.append(cap_citta)
        if self.assicurato_residenza_provincia:
            parti.append(f"({self.assicurato_residenza_provincia})")
        return ", ".join(parti)

    @property
    def evento_data_formattata(self) -> str:
        return self.evento_data.strftime("%d/%m/%Y") if self.evento_data else ""

    @property
    def denuncia_data_formattata(self) -> str:
        return self.denuncia_data.strftime("%d/%m/%Y") if self.denuncia_data else ""

    @property
    def assicurato_data_nascita_formattata(self) -> str:
        if not self.assicurato_data_nascita:
            return ""
        return self.assicurato_data_nascita.strftime("%d/%m/%Y")

    @property
    def compagnia_indirizzo_compatto(self) -> str:
        parti: list[str] = []
        if self.compagnia_indirizzo:
            parti.append(self.compagnia_indirizzo)
        cap_citta = " ".join(p for p in [self.compagnia_cap, self.compagnia_citta] if p)
        if cap_citta:
            parti.append(cap_citta)
        if self.compagnia_provincia:
            parti.append(f"({self.compagnia_provincia})")
        return ", ".join(parti)

    # --- validazione ---

    def campi_mancanti(self) -> list[str]:
        """Elenca i campi obbligatori vuoti, per il banner di alert."""
        check = {
            "Nominativo assicurato": self.assicurato_nome_completo,
            "Codice fiscale": self.assicurato_codice_fiscale,
            "Residenza (via)": self.assicurato_residenza_via,
            "Residenza (comune)": self.assicurato_residenza_comune,
            "Compagnia (nome)": self.polizza_compagnia_nome,
            "Numero polizza": self.polizza_numero,
            "Targa veicolo": self.veicolo_targa,
            "Marca e modello": self.veicolo_marca_modello,
            "Data evento": self.evento_data,
            "Luogo evento (via)": self.evento_luogo_via,
            "Luogo evento (comune)": self.evento_luogo_comune,
            "Descrizione danni": self.evento_descrizione_danni,
            "Data denuncia": self.denuncia_data,
            "Autorità denuncia": self.denuncia_autorita,
            "Indirizzo PEC compagnia": self.compagnia_pec,
        }
        if self.e_ditta:
            check["Nome ditta"] = self.ditta_nome
            check["Partita IVA"] = self.ditta_partita_iva
        return [name for name, val in check.items() if not val]


# --------------------------------------------------------------------------- #
#  Costruzione da Pratica + Compagnia
# --------------------------------------------------------------------------- #


def from_pratica(
    pratica: Pratica,
    *,
    compagnia: Compagnia | None = None,
    carrozzeria_pec: str = "",
    carrozzeria_email: str = "",
    carrozzeria_telefono: str = "",
    carrozzeria_referente: str = "",
    overrides: dict[str, Any] | None = None,
) -> RichiestaVandalismoData:
    """Costruisce `RichiestaVandalismoData` a partire dai dati WinCar.

    - `compagnia`: record dell'anagrafica interna; se passato precompila PEC e
      indirizzo postale della compagnia destinataria. Se None, i campi compagnia
      restano vuoti e l'operatore deve completarli a mano (oppure prima
      registrare la compagnia in anagrafica).
    - `carrozzeria_*`: contatti della carrozzeria (presi dalle Settings).
    - `overrides`: dizionario da form HTML; ha precedenza su tutti gli altri
      valori derivati. Per le date accetta sia oggetti `date` sia stringhe
      ISO `YYYY-MM-DD`.
    """
    overrides = overrides or {}

    cf_raw = (
        overrides.get("assicurato_codice_fiscale")
        or pratica.cliente.codice_fiscale
        or ""
    ).strip()
    cf_info = parse_codice_fiscale(cf_raw)

    e_ditta = bool(pratica.cliente.partita_iva)
    ditta_nome = pratica.cliente.nominativo if e_ditta else ""
    ditta_piva = pratica.cliente.partita_iva or "" if e_ditta else ""

    veicolo_desc = " ".join(
        v for v in [pratica.veicolo.marca, pratica.veicolo.modello] if v
    ).strip()

    def _take(key: str, default: Any) -> Any:
        if key in overrides and overrides[key] not in (None, ""):
            return overrides[key]
        return default

    return RichiestaVandalismoData(
        numero_pratica=pratica.numero,
        assicurato_nome_completo=_take(
            "assicurato_nome_completo", pratica.cliente.nominativo or ""
        ),
        assicurato_codice_fiscale=_take("assicurato_codice_fiscale", cf_raw),
        assicurato_data_nascita=_take(
            "assicurato_data_nascita", cf_info.data_nascita
        ),
        assicurato_luogo_nascita=_take(
            "assicurato_luogo_nascita", cf_info.luogo_nascita or ""
        ),
        assicurato_sesso=_take("assicurato_sesso", cf_info.sesso or "M"),
        assicurato_residenza_via=_take(
            "assicurato_residenza_via", pratica.cliente.via or ""
        ),
        assicurato_residenza_comune=_take(
            "assicurato_residenza_comune", pratica.cliente.citta or ""
        ),
        assicurato_residenza_cap=_take(
            "assicurato_residenza_cap", pratica.cliente.cap or ""
        ),
        assicurato_residenza_provincia=_take(
            "assicurato_residenza_provincia", pratica.cliente.provincia or ""
        ),
        assicurato_telefono=_take(
            "assicurato_telefono",
            pratica.cliente.cellulare or pratica.cliente.telefono or "",
        ),
        assicurato_email=_take("assicurato_email", pratica.cliente.email or ""),
        e_ditta=_take("e_ditta", e_ditta),
        ditta_nome=_take("ditta_nome", ditta_nome),
        ditta_partita_iva=_take("ditta_partita_iva", ditta_piva),
        polizza_compagnia_nome=_take(
            "polizza_compagnia_nome",
            (compagnia.nome if compagnia else None) or pratica.assicurazione_cliente.nome or "",
        ),
        polizza_numero=_take(
            "polizza_numero", pratica.assicurazione_cliente.numero_polizza or ""
        ),
        polizza_agenzia=_take(
            "polizza_agenzia", pratica.assicurazione_cliente.agenzia or ""
        ),
        veicolo_marca_modello=_take("veicolo_marca_modello", veicolo_desc),
        veicolo_targa=_take("veicolo_targa", pratica.veicolo.targa or ""),
        veicolo_telaio=_take("veicolo_telaio", pratica.veicolo.telaio or ""),
        evento_data=_take("evento_data", pratica.sinistro.data),
        evento_ora=_take("evento_ora", pratica.sinistro.ora or ""),
        evento_luogo_via=_take("evento_luogo_via", pratica.sinistro.via or ""),
        evento_luogo_comune=_take(
            "evento_luogo_comune", pratica.sinistro.comune or ""
        ),
        evento_descrizione_danni=_take(
            "evento_descrizione_danni", pratica.sinistro.dinamica or ""
        ),
        denuncia_autorita=_take("denuncia_autorita", "Carabinieri"),
        denuncia_comando=_take("denuncia_comando", ""),
        denuncia_data=_take("denuncia_data", None),
        denuncia_protocollo=_take("denuncia_protocollo", ""),
        compagnia_pec=_take(
            "compagnia_pec",
            ((compagnia.pec or compagnia.email) if compagnia else "") or "",
        ),
        compagnia_indirizzo=_take(
            "compagnia_indirizzo",
            (compagnia.indirizzo if compagnia else None)
            or pratica.assicurazione_cliente.indirizzo
            or "",
        ),
        compagnia_cap=_take(
            "compagnia_cap",
            (compagnia.cap if compagnia else None)
            or pratica.assicurazione_cliente.cap
            or "",
        ),
        compagnia_citta=_take(
            "compagnia_citta",
            (compagnia.citta if compagnia else None)
            or pratica.assicurazione_cliente.citta
            or "",
        ),
        compagnia_provincia=_take(
            "compagnia_provincia",
            (compagnia.provincia if compagnia else None)
            or pratica.assicurazione_cliente.provincia
            or "",
        ),
        compagnia_ufficio_sinistri=_take(
            "compagnia_ufficio_sinistri",
            (compagnia.ufficio_sinistri if compagnia else "") or "",
        ),
        carrozzeria_pec=carrozzeria_pec,
        carrozzeria_email=carrozzeria_email,
        carrozzeria_telefono=carrozzeria_telefono,
        carrozzeria_referente=carrozzeria_referente,
        cf_info=cf_info,
    )
