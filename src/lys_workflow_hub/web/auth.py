"""Autenticazione: sessione cookie, dependency di protezione route, CSRF.

Approccio:
  - `AuthMiddleware` gira su OGNI richiesta, carica l'utente dalla sessione
    (se presente) in `request.state.current_user`, e blocca (redirect a
    `/login`) qualunque percorso non nella allowlist se non c'è un utente
    loggato. Fail-closed: una route nuova aggiunta domani è protetta di
    default, non serve ricordarsi di aggiungere una dependency.
  - `require_admin` è una dependency FastAPI da aggiungere ai router che
    devono restare riservati agli operatori carrozzeria (tutti, per ora:
    il portale per utenti "esterno" arriva nelle fasi successive).
  - CSRF: token random legato alla sessione, verificato su OGNI richiesta
    POST con body `application/x-www-form-urlencoded` (tranne `/login`, che
    ha la propria verifica dedicata con un messaggio d'errore mostrato sulla
    pagina di login stessa invece del 403 generico qui sotto). Prima estesa
    solo al login; da v3.0 fase 5 (parte G — hardening) copre tutti i form
    dell'app: `template_context_processor` inietta `csrf_token` in ogni
    pagina renderizzata, ogni `<form method="post">` lo porta come campo
    hidden.
    I form `multipart/form-data` (upload file: cessione firmata, verbali
    cortesia) sono ESCLUSI da questo controllo a livello di middleware —
    vedi `_is_multipart()` — e verificano il token da soli nella
    route: `Request.form()` per un body multipart consuma lo `stream()`
    della richiesta, e `BaseHTTPMiddleware` (vedi `_CachedRequest` in
    Starlette) rimanda alla app downstream un body VUOTO per qualunque
    consumo basato su `.stream()` invece che su `.body()` — leggere il
    form qui romperebbe silenziosamente l'upload del file (FastAPI riceve
    un body vuoto e risponde 422, nessun file mai arrivato alla route).
"""
from __future__ import annotations

import json
import logging
import secrets

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from lys_workflow_hub.config import get_settings
from lys_workflow_hub.core.utenti_repository import Utente


logger = logging.getLogger(__name__)

CSRF_SESSION_KEY = "csrf_token"

# Percorsi raggiungibili senza sessione autenticata.
PUBLIC_PATHS = {"/login", "/health", "/firebase-messaging-sw.js"}
PUBLIC_PREFIXES = ("/static/",)

# /login ha una verifica CSRF propria in routes_auth.py (mostra l'errore
# sulla pagina di login invece del 403 generico qui sotto) — esclusa per non
# duplicare/confliggere con quella logica.
_CSRF_EXEMPT_PATHS = {"/login"}


def _is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


async def _submitted_csrf_token(request: Request) -> str:
    """Legge `csrf_token` dal body POST senza rompere la route downstream.

    `BaseHTTPMiddleware` (vedi `_CachedRequest` in Starlette) replica il
    body verso l'app downstream SOLO se in `dispatch()` è stato chiamato
    `Request.body()` — se invece si chiama solo `Request.form()` (che usa
    `Request.stream()` internamente, anche per `x-www-form-urlencoded`),
    lo stream risulta "consumato" ma NON cache-ato, e la route sotto
    riceve un body VUOTO (ogni `Form(...)` richiesto sparisce → 422).
    Chiamare `.body()` PRIMA di `.form()` risolve: mette in cache i byte
    grezzi (`request._body`), che `.form()` userà al posto dello stream
    live, e che la route downstream riceverà intatti."""
    try:
        await request.body()
        form = await request.form()
    except Exception:  # noqa: BLE001
        return ""
    value = form.get("csrf_token")
    return str(value) if value is not None else ""


def _is_multipart(request: Request) -> bool:
    return request.headers.get("content-type", "").startswith("multipart/form-data")


class AuthMiddleware(BaseHTTPMiddleware):
    """Carica l'utente corrente dalla sessione; redirect a /login se assente;
    verifica il token CSRF su ogni richiesta POST (tranne /login).

    Legge il repository da `request.app.state.utenti_repo` (singleton creato
    in `main.py`, stesso pattern di `app.state.foto_repo`) invece di
    costruirselo da sé: cosi' i test possono sostituirlo con un DB temporaneo
    senza dover toccare `.env` / la cache di `get_settings()`.
    """

    async def dispatch(self, request: Request, call_next):
        repo = request.app.state.utenti_repo

        user: Utente | None = None
        user_id = request.session.get("user_id")
        if user_id is not None:
            try:
                candidate = repo.get(int(user_id))
            except (TypeError, ValueError):
                candidate = None
            if candidate is not None and candidate.attivo:
                user = candidate
            else:
                # Utente cancellato/disattivato dopo il login: sessione morta.
                request.session.clear()

        request.state.current_user = user

        path = request.url.path
        if user is None and not _is_public(path):
            next_qs = f"?next={path}" if path != "/" else ""
            return RedirectResponse(url=f"/login{next_qs}", status_code=303)

        # "/" è admin-only (require_admin a livello di router in routes.py),
        # ma è anche l'URL che chiunque digita/salva come preferito
        # (hub.lysauto.it "nudo"). Un esterno già loggato che la apre
        # otterrebbe altrimenti il 403 JSON grezzo di require_admin invece
        # del redirect amichevole a /portale che già riceve da /login
        # (_default_landing in routes_auth.py) — bug reale segnalato in
        # produzione. Solo "/" riceve questo trattamento: le altre route
        # admin-only devono continuare a rispondere 403 a un esterno, è il
        # segnale di sicurezza corretto lì.
        if user is not None and not user.is_admin and path == "/":
            return RedirectResponse(
                url="/operatore" if user.is_operatore else "/portale", status_code=303
            )

        if (
            request.method == "POST"
            and path not in _CSRF_EXEMPT_PATHS
            and not _is_multipart(request)
        ):
            submitted = await _submitted_csrf_token(request)
            if not verify_csrf(request, submitted):
                return JSONResponse(
                    {"detail": "Token di sicurezza mancante o scaduto. Ricarica la pagina e riprova."},
                    status_code=403,
                )

        return await call_next(request)


def get_current_user(request: Request) -> Utente | None:
    """Dependency: utente loggato (o None). Popolato da `AuthMiddleware`."""
    return getattr(request.state, "current_user", None)


def require_admin(request: Request) -> Utente:
    """Dependency: 403 se l'utente corrente non è admin.

    `AuthMiddleware` garantisce già che ci sia un utente loggato (altrimenti
    la richiesta non arriva qui: è stata reindirizzata a /login prima).
    """
    user = getattr(request.state, "current_user", None)
    if user is None or not user.is_admin:
        raise HTTPException(status_code=403, detail="Accesso riservato agli amministratori.")
    return user


def template_context_processor(request: Request) -> dict:
    """Iniettato in ogni `Jinja2Templates(context_processors=[...])`.

    Rende `current_user` e `csrf_token` disponibili in tutti i template
    senza dover modificare ogni singola vista — `csrf_token` è quello che
    ogni `<form method="post">` deve portare come campo hidden (verificato
    da `AuthMiddleware` su ogni POST, vedi sopra).

    `fcm_web_*` sono la config pubblica (non segreta) dell'app Web Firebase,
    servita a `base.html` per il Web Push del portale in browser — stesso
    motivo per cui vive qui invece che in ogni singola vista: `get_settings()`
    è cachata (`lru_cache`), costo trascurabile su ogni render. Il config
    Firebase è preserializzato in JSON qui (non con un filtro `tojson`, non
    garantito disponibile su Jinja2 puro come lo usa questo progetto) e
    inserito nel template con `| safe`.
    """
    settings = get_settings()
    fcm_web_configured = bool(settings.fcm_web_api_key and settings.fcm_web_vapid_key)
    return {
        "current_user": getattr(request.state, "current_user", None),
        "csrf_token": new_csrf_token(request),
        "fcm_web_configured": fcm_web_configured,
        "fcm_web_config_json": _json_per_script(
            {
                "apiKey": settings.fcm_web_api_key,
                "authDomain": settings.fcm_web_auth_domain,
                "projectId": settings.fcm_web_project_id,
                "storageBucket": settings.fcm_web_storage_bucket,
                "messagingSenderId": settings.fcm_web_messaging_sender_id,
                "appId": settings.fcm_web_app_id,
            }
        ),
        "fcm_web_vapid_key_json": _json_per_script(settings.fcm_web_vapid_key),
    }


def _json_per_script(value: object) -> str:
    """`json.dumps` normale non fa escape di `<`/`>`/`&` — una stringa che
    contenesse `</script>` romperebbe l'HTML in cui viene inserita con
    `| safe`. Improbabile per i valori Firebase attuali (alfa-numerici),
    ma è un pattern riusabile: stesso trucco del filtro `json_script` di
    Django, difesa in profondità senza costo pratico."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def new_csrf_token(request: Request) -> str:
    """Genera (o riusa) il token CSRF per la sessione corrente."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def verify_csrf(request: Request, submitted_token: str) -> bool:
    expected = request.session.get(CSRF_SESSION_KEY)
    return bool(expected) and secrets.compare_digest(expected, submitted_token or "")
