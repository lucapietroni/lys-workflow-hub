"""Builder per ScaffoldContext a partire da WinCar + anagrafica compagnie.

E' il "collante" tra il backend M4 (che vuole un ScaffoldContext gia'
popolato) e le altre sorgenti dati della piattaforma:

  * `WinCarRepository` legge la pratica (cliente, veicolo, sinistro,
    compagnia del cliente);
  * `CompagnieRepository` arricchisce con i dati canonici della
    compagnia destinataria (PEC, indirizzo, ufficio sinistri);
  * `Settings` fornisce i dati della carrozzeria mittente (PEC, email,
    telefono, referente).

Tutte le sorgenti sono opzionali: se mancano (es. WinCar non raggiungibile,
compagnia non in anagrafica), il builder produce comunque un context
coerente con i campi disponibili — gli altri restano stringa vuota e lo
scaffold li omette in fase di render.

Idempotente, mai solleva: tutti gli errori vengono loggati a livello
WARNING e il context viene comunque restituito. Cosi' il polling M3 +
hook M4 non si interrompe mai per dati WinCar mancanti.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.compagnie_repository import (
    Compagnia,
    CompagnieRepository,
)
from lys_workflow_hub.core.wincar_repository import (
    Pratica,
    WinCarRepository,
)
from lys_workflow_hub.workflows.risposte.scaffold import ScaffoldContext


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextWithMeta:
    """ScaffoldContext + meta utili al chiamante (es. la route).

    `compagnia` e `pratica` permettono al chiamante di sapere cosa e'
    stato effettivamente recuperato (per UI, dependency injection, ecc.)
    senza dover rifare le query.
    """

    context: ScaffoldContext
    pratica: Pratica | None
    compagnia: Compagnia | None


def _veicolo_marca_modello(pratica: Pratica) -> str:
    """Composizione cortese 'Marca Modello' lasciando posto a None safely."""
    parts = [pratica.veicolo.marca or "", pratica.veicolo.modello or ""]
    return " ".join(p for p in parts if p).strip()


def _assicurato_nome(pratica: Pratica) -> str:
    return (pratica.cliente.nominativo or "").strip()


def _safe_get_pratica(
    wincar_repo: WinCarRepository | None, numero_pratica: int
) -> Pratica | None:
    if wincar_repo is None:
        return None
    try:
        return wincar_repo.get_pratica(numero_pratica)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "WinCar non raggiungibile durante build context (pratica %s): %s",
            numero_pratica, exc,
        )
        return None


def _safe_lookup_compagnia(
    compagnie_repo: CompagnieRepository | None, nome: str
) -> Compagnia | None:
    if compagnie_repo is None or not nome:
        return None
    try:
        return compagnie_repo.lookup_by_name(nome)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Lookup compagnia '%s' fallito: %s", nome, exc,
        )
        return None


def build_scaffold_context(
    *,
    pratica_numero: int | None,
    subject_originale: str = "",
    wincar_repo: WinCarRepository | None = None,
    compagnie_repo: CompagnieRepository | None = None,
    settings: Settings | None = None,
    compagnia_nome_override: str = "",
) -> ContextWithMeta:
    """Costruisce un `ScaffoldContext` arricchito.

    Strategia per la compagnia destinataria, in ordine di preferenza:
      1. `compagnia_nome_override` se passato (es. dall'editor);
      2. `pratica.assicurazione_cliente.nome` letto da WinCar.

    Il nome trovato viene poi fatto lookup nell'anagrafica per ottenere
    PEC, indirizzo e ufficio sinistri.

    Per la pratica:
      * letta read-only da WinCar tramite `WinCarRepository.get_pratica`.
      * Se WinCar non e' raggiungibile, il context resta minimale.
    """
    settings = settings or get_settings()

    # 1) Pratica WinCar.
    pratica: Pratica | None = None
    if pratica_numero is not None:
        pratica = _safe_get_pratica(wincar_repo, int(pratica_numero))

    # 2) Determina il nome compagnia da cercare.
    nome_compagnia = compagnia_nome_override.strip()
    if not nome_compagnia and pratica is not None:
        nome_compagnia = (pratica.assicurazione_cliente.nome or "").strip()

    # 3) Lookup anagrafica.
    compagnia: Compagnia | None = _safe_lookup_compagnia(
        compagnie_repo, nome_compagnia
    )

    # 4) Compone i singoli campi del context.
    compagnia_display_nome = (
        compagnia.nome if compagnia else nome_compagnia or ""
    )
    compagnia_pec = compagnia.pec if compagnia else ""
    compagnia_indirizzo = compagnia.indirizzo_compatto if compagnia else ""
    compagnia_uffsin = compagnia.ufficio_sinistri if compagnia else ""

    pratica_num = pratica_numero
    sinistro = (pratica.sinistro.numero or "") if pratica else ""
    polizza = (pratica.assicurazione_cliente.numero_polizza or "") if pratica else ""
    targa = (pratica.veicolo.targa or "") if pratica else ""
    assicurato = _assicurato_nome(pratica) if pratica else ""

    # Carrozzeria mittente: viene da Settings (.env).
    # NB: il "nome" carrozzeria e' hardcoded in workflows/risarcimento_vandalismo/data.py
    # ma per il subject/firma dello scaffold M4 lo passiamo da settings se presente,
    # altrimenti fallback al default dello scaffold (Carrozzeria LYS Auto srl).
    carrozzeria_referente = settings.carrozzeria_referente or ""
    carrozzeria_pec = settings.carrozzeria_pec or ""
    carrozzeria_email = settings.carrozzeria_email or ""
    carrozzeria_telefono = settings.carrozzeria_telefono or ""
    # Comune: il default e' Roma (sede LYS). Settings non lo modella ancora;
    # in futuro si puo' aggiungere `carrozzeria_comune` come campo dedicato.
    carrozzeria_comune = getattr(settings, "carrozzeria_comune", "") or ""

    ctx = ScaffoldContext(
        compagnia_nome=compagnia_display_nome,
        compagnia_indirizzo_compatto=compagnia_indirizzo,
        compagnia_ufficio_sinistri=compagnia_uffsin,
        compagnia_pec=compagnia_pec,
        pratica_numero=pratica_num,
        sinistro_numero=sinistro,
        polizza_numero=polizza,
        veicolo_targa=targa,
        assicurato_nome=assicurato,
        carrozzeria_referente=carrozzeria_referente,
        carrozzeria_pec=carrozzeria_pec,
        carrozzeria_email=carrozzeria_email,
        carrozzeria_telefono=carrozzeria_telefono,
        carrozzeria_comune=carrozzeria_comune,
        subject_originale=subject_originale or "",
    )

    return ContextWithMeta(
        context=ctx,
        pratica=pratica,
        compagnia=compagnia,
    )


__all__ = [
    "ContextWithMeta",
    "build_scaffold_context",
]
