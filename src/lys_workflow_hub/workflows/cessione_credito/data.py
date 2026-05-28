"""Modello dati per la Cessione del Credito.

`CessioneData` e' l'oggetto immutabile che il generatore di documenti consuma.
Si costruisce a partire da una `Pratica` di WinCar + eventuali override forniti
dall'operatore tramite la pagina di anteprima (es. per completare la dinamica
mancante in DB).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from lys_workflow_hub.core.wincar_repository import Pratica
from lys_workflow_hub.workflows.cessione_credito.cf_parser import (
    DatiAnagraficiCF,
    parse_codice_fiscale,
)


# Dati anagrafici della cessionaria (la Carrozzeria stessa).
# Sono fissi nel testo originale del modulo. Se in futuro cambiano si modificano qui.
CARROZZERIA_NOME = "Carrozzeria LYS Auto srl"
CARROZZERIA_VIA = "Via Antonio Pucci 37"
CARROZZERIA_CAP = "00139"
CARROZZERIA_COMUNE = "Roma"
CARROZZERIA_PIVA = "14521721002"
LUOGO_SOTTOSCRIZIONE = "Roma"


@dataclass(frozen=True)
class CessioneData:
    """Tutti i campi necessari a riempire il modulo di cessione del credito."""

    numero_pratica: int

    # --- Cedente ---
    cedente_nome_completo: str
    cedente_luogo_nascita: str
    cedente_data_nascita: date | None
    cedente_sesso: str  # "M" o "F"
    cedente_residenza_via: str
    cedente_residenza_comune: str
    cedente_codice_fiscale: str

    # --- Eventuale ditta ---
    e_ditta: bool
    ditta_nome: str = ""
    ditta_partita_iva: str = ""

    # --- Tipo sinistro ---
    e_vandalismo: bool = False  # True = atto vandalico (nessuna controparte)

    # --- Sinistro ---
    sinistro_data: date | None = None
    sinistro_ora: str = ""
    sinistro_comune: str = ""
    sinistro_via: str = ""
    sinistro_dinamica: str = ""

    # --- Veicolo del cedente ---
    veicolo_cedente_descrizione: str = ""
    veicolo_cedente_targa: str = ""

    # --- Controparte ---
    controparte_veicolo_descrizione: str = ""
    controparte_veicolo_targa: str = ""
    controparte_proprietario: str = ""
    controparte_conducente: str = ""
    controparte_compagnia: str = ""
    controparte_polizza: str = ""

    # --- Diagnostica anagrafica (per debug/log) ---
    cf_info: DatiAnagraficiCF | None = field(default=None, repr=False)

    # --- Campi calcolati ---

    @property
    def articolo_sottoscritto(self) -> str:
        return "La" if self.cedente_sesso == "F" else "Il"

    @property
    def desinenza_nato(self) -> str:
        return "a" if self.cedente_sesso == "F" else "o"

    @property
    def sottoscritto_label(self) -> str:
        """Forma completa: 'Il sottoscritto' o 'La sottoscritta'."""
        return "La sottoscritta" if self.cedente_sesso == "F" else "Il sottoscritto"

    @property
    def nato_label(self) -> str:
        """Forma completa: 'nato a' o 'nata a'."""
        return "nata a" if self.cedente_sesso == "F" else "nato a"

    @property
    def residenza_compatta(self) -> str:
        parts = [self.cedente_residenza_via, self.cedente_residenza_comune]
        return ", ".join([p for p in parts if p])

    @property
    def sinistro_data_formattata(self) -> str:
        return self.sinistro_data.strftime("%d/%m/%Y") if self.sinistro_data else ""

    @property
    def cedente_data_nascita_formattata(self) -> str:
        return self.cedente_data_nascita.strftime("%d/%m/%Y") if self.cedente_data_nascita else ""

    # --- Validazione ---

    def campi_mancanti(self) -> list[str]:
        """Elenca i campi obbligatori vuoti (validazione minima prima di stampare)."""
        check = {
            "Nome del cedente": self.cedente_nome_completo,
            "Luogo di nascita": self.cedente_luogo_nascita,
            "Data di nascita": self.cedente_data_nascita,
            "Residenza (via)": self.cedente_residenza_via,
            "Residenza (comune)": self.cedente_residenza_comune,
            "Codice fiscale": self.cedente_codice_fiscale,
            "Data sinistro": self.sinistro_data,
            "Comune sinistro": self.sinistro_comune,
            "Via sinistro": self.sinistro_via,
            "Veicolo del cedente": self.veicolo_cedente_descrizione,
            "Targa del cedente": self.veicolo_cedente_targa,
            "Dinamica del sinistro": self.sinistro_dinamica,
        }
        if not self.e_vandalismo:
            check.update({
                "Veicolo controparte": self.controparte_veicolo_descrizione,
                "Targa controparte": self.controparte_veicolo_targa,
                "Proprietario controparte": self.controparte_proprietario,
                "Conducente controparte": self.controparte_conducente,
                "Compagnia assicurativa controparte": self.controparte_compagnia,
                "Numero polizza controparte": self.controparte_polizza,
            })
        if self.e_ditta:
            check["Nome ditta"] = self.ditta_nome
            check["Partita IVA"] = self.ditta_partita_iva
        return [name for name, val in check.items() if not val]


def from_pratica(
    pratica: Pratica,
    overrides: dict[str, Any] | None = None,
) -> CessioneData:
    """Costruisce `CessioneData` da una `Pratica` WinCar + dizionario di override.

    Gli override hanno precedenza sui dati estratti dal DB: vengono usati dalla
    pagina di anteprima quando l'operatore corregge o completa qualche campo.
    """
    overrides = overrides or {}
    cf = (overrides.get("cedente_codice_fiscale") or pratica.cliente.codice_fiscale or "").strip()
    cf_info = parse_codice_fiscale(cf)

    e_ditta = bool(pratica.cliente.partita_iva)
    ditta_nome = ""
    ditta_piva = ""
    if e_ditta:
        ditta_nome = pratica.cliente.nominativo
        ditta_piva = pratica.cliente.partita_iva or ""

    veicolo_cedente_desc = " ".join(
        v for v in [pratica.veicolo.marca, pratica.veicolo.modello] if v
    ).strip()

    def _take(key: str, default: Any) -> Any:
        return overrides[key] if key in overrides else default

    return CessioneData(
        numero_pratica=pratica.numero,
        cedente_nome_completo=_take(
            "cedente_nome_completo", pratica.cliente.nominativo or ""
        ),
        cedente_luogo_nascita=_take(
            "cedente_luogo_nascita", cf_info.luogo_nascita or ""
        ),
        cedente_data_nascita=_take("cedente_data_nascita", cf_info.data_nascita),
        cedente_sesso=_take("cedente_sesso", cf_info.sesso or "M"),
        cedente_residenza_via=_take(
            "cedente_residenza_via", pratica.cliente.via or ""
        ),
        cedente_residenza_comune=_take(
            "cedente_residenza_comune", pratica.cliente.citta or ""
        ),
        cedente_codice_fiscale=_take("cedente_codice_fiscale", cf),
        e_ditta=_take("e_ditta", e_ditta),
        ditta_nome=_take("ditta_nome", ditta_nome),
        ditta_partita_iva=_take("ditta_partita_iva", ditta_piva),
        e_vandalismo=_take("e_vandalismo", False),
        sinistro_data=_take("sinistro_data", pratica.sinistro.data),
        sinistro_ora=_take("sinistro_ora", pratica.sinistro.ora or ""),
        sinistro_comune=_take("sinistro_comune", pratica.sinistro.comune or ""),
        sinistro_via=_take("sinistro_via", pratica.sinistro.via or ""),
        sinistro_dinamica=_take("sinistro_dinamica", pratica.sinistro.dinamica or ""),
        veicolo_cedente_descrizione=_take(
            "veicolo_cedente_descrizione", veicolo_cedente_desc
        ),
        veicolo_cedente_targa=_take(
            "veicolo_cedente_targa", pratica.veicolo.targa or ""
        ),
        controparte_veicolo_descrizione=_take(
            "controparte_veicolo_descrizione",
            pratica.controparte.veicolo_descrizione or "",
        ),
        controparte_veicolo_targa=_take(
            "controparte_veicolo_targa", pratica.controparte.targa or ""
        ),
        controparte_proprietario=_take(
            "controparte_proprietario", pratica.controparte.proprietario or ""
        ),
        controparte_conducente=_take(
            "controparte_conducente", pratica.controparte.conducente or ""
        ),
        controparte_compagnia=_take(
            "controparte_compagnia", pratica.controparte.compagnia or ""
        ),
        controparte_polizza=_take(
            "controparte_polizza", pratica.controparte.numero_polizza or ""
        ),
        cf_info=cf_info,
    )
