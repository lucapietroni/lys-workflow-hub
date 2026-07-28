"""Configurazione dell'applicazione, caricata da variabili d'ambiente / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Tutte le impostazioni dell'app in un unico oggetto immutabile.

    I valori sono presi (in ordine di priorità) da:
    1. variabili d'ambiente del processo;
    2. file `.env` nella radice del progetto;
    3. default qui sotto.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- WinCar ---
    # Nome variabile storico "archivio"; sul disco la cartella si chiama "Archivi" (plurale).
    wincar_archivio: Path = Field(default=Path(r"C:\WinCar\Archivi"))
    wincar_odbc_driver: str = Field(default="Microsoft Access Driver (*.mdb, *.accdb)")

    # --- App ---
    app_host: str = Field(default="127.0.0.1")
    app_port: int = Field(default=8000)
    app_env: str = Field(default="development")
    app_archivio_cessioni: Path = Field(default=Path(r"C:\LYSApp\Cessioni_firmate"))
    # DB interno SQLite per anagrafica compagnie (M2) e in futuro altri metadati.
    app_db_path: Path = Field(default=Path("data/lys_hub.db"))
    # Log su file con rotazione (5 MB x 5 file). La cartella viene creata
    # automaticamente all'avvio. Utile per esecuzione in background con
    # pythonw.exe / Task Scheduler quando non c'è una console attiva.
    app_log_path: Path = Field(default=Path(r"C:\LYSApp\logs\lys-hub.log"))
    app_log_level: str = Field(default="INFO")

    # --- Carrozzeria (per intestazione PEC, contatti) ---
    carrozzeria_pec: str = Field(default="")
    carrozzeria_email: str = Field(default="")
    carrozzeria_telefono: str = Field(default="")
    carrozzeria_referente: str = Field(default="")

    # --- Posta in entrata (M3) ---
    pec_imap_host: str = Field(default="mbox.cert.legalmail.it")
    pec_imap_port: int = Field(default=993)
    pec_user: str = Field(default="")
    pec_password: str = Field(default="")

    email_imap_host: str = Field(default="mail.tophost.it")
    email_imap_port: int = Field(default=993)
    email_user: str = Field(default="")
    email_password: str = Field(default="")

    mail_poll_interval_min: int = Field(default=5)
    # Filtro data per il fetcher IMAP. Se valorizzato (formato YYYY-MM-DD),
    # il fetcher scarica SOLO le email ricevute dal server in quella data
    # in poi. Indispensabile al primo deploy per evitare di processare
    # l'intero archivio storico di una casella PEC.
    # Esempio: "2026-05-15" = solo email dal 15 maggio 2026 in poi.
    # Vuoto = nessun filtro (scarica tutto secondo la logica UID).
    mail_fetch_since: str = Field(default="")

    # --- Posta in uscita (M2) ---
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    # From: address separato dall'utente di autenticazione. Utile se il provider
    # usa un alias o se la casella e' su un dominio diverso da quello di auth.
    # Vuoto = usa smtp_user come From.
    smtp_from: str = Field(default="")
    # Modalita' di cifratura SMTP: "starttls" (default, porta 587), "ssl"
    # (porta 465) o "none" (sconsigliato). Se vuoto, viene scelto in base
    # alla porta: 465 -> ssl, altrimenti starttls.
    smtp_tls: str = Field(default="")

    pec_smtp_host: str = Field(default="sendm.cert.legalmail.it")
    pec_smtp_port: int = Field(default=465)
    pec_smtp_user: str = Field(default="")
    pec_smtp_password: str = Field(default="")

    # Se True, l'app NON apre alcuna connessione SMTP: genera comunque il file
    # .eml e lo archivia, registrando l'invio come "DRY_RUN" nel DB.
    # Usato per testare il workflow di invio in sviluppo senza spammare le
    # compagnie. In produzione lasciare False (default).
    pec_dry_run: bool = Field(default=False)

    # Cartella centrale dove salvare i .eml delle PEC inviate, partizionata per
    # anno (sottocartella creata automaticamente al primo invio).
    app_archivio_pec: Path = Field(default=Path(r"C:\LYSApp\PEC_inviate"))

    # Display-name del mittente nella PEC ("From: <nome> <indirizzo>").
    # Se vuoto, viene usata la ragione sociale della carrozzeria.
    carrozzeria_pec_alias: str = Field(default="")

    # --- AI (M3) ---
    # Se True, il classificatore AI NON viene chiamato e la risposta viene
    # marcata come categoria="altro" con confidence 0. Utile per testare il
    # flusso end-to-end senza consumare budget Anthropic.
    ai_disabled: bool = Field(default=False)
    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-haiku-4-5-20251001")
    # Modello per lettura targhe da foto (v2.1): serve più capacità OCR/vision
    # di Haiku, specie su foto di taglio o angolate. Costo per foto trascurabile.
    anthropic_vision_model: str = Field(default="claude-sonnet-5")
    ai_budget_monthly_eur: float = Field(default=20.0)
    ai_budget_alert_eur: float = Field(default=15.0)

    # --- Foto lavorazioni (v2.1) ---
    # Cartella dove Syncthing deposita le foto dallo smartphone aziendale.
    # Il watcher elabora ogni nuova immagine: estrae targa via Claude Vision,
    # copia in foto_fallback_path/<TARGA>/ e (se trovata pratica) in WinCar.
    # None (default) = watcher disabilitato. Impostare in .env su prod:
    #   FOTO_INBOX_PATH=C:\LYSApp\Inbox Foto
    foto_inbox_path: Path | None = Field(default=None)
    # Archivio permanente per targa, sempre popolato indipendentemente dalla pratica.
    foto_fallback_path: Path = Field(default=Path(r"C:\LYSApp\Foto lavorazioni"))

    # --- SLA pratiche (M5/M6.1) ---
    # Numero di giorni senza risposta dopo i quali scatta l'alert SLA.
    # Impostare a 0 per disabilitare il check SLA nel polling.
    sla_giorni_alert: int = Field(default=15)
    # Escalation automatica (M6.1): soglie per sollecito formale e diffida.
    # 0 = livello disabilitato. I livelli inferiori devono avere soglia minore.
    sla_formale_giorni: int = Field(default=30)
    sla_diffida_giorni: int = Field(default=45)

    # --- PDF extraction (M5.3) ---
    # Estrae il testo dagli allegati PDF delle risposte assicurative quando
    # il corpo della mail è troppo corto per essere classificato dall'AI.
    pdf_extract_enabled: bool = Field(default=True)
    # Soglia in caratteri: l'estrazione scatta solo se body_text < questo valore.
    pdf_extract_min_body_len: int = Field(default=200)
    # Cartella centrale dove archiviare le .eml ricevute, partizionata per anno.
    app_archivio_mail_in: Path = Field(default=Path(r"C:\LYSApp\Mail_in"))

    # --- Notifiche (M3) ---
    # Topic ntfy.sh segreto: l'app pubblica push su https://<server>/<topic>;
    # tu lo aggiungi all'app ntfy sul telefono per riceverli. Senza topic le
    # notifiche push sono disattivate (l'email riassuntiva resta attiva).
    ntfy_topic: str = Field(default="")
    ntfy_server: str = Field(default="https://ntfy.sh")
    # Email a cui inviare il riepilogo a fine ciclo polling (può essere la tua
    # email ordinaria, non serve PEC). Vuoto = niente email riassuntiva.
    alert_email: str = Field(default="")
    # Se True, niente push e niente email riassuntiva (modalità "silenziosa"
    # per il rodaggio o per il dev). Utile soprattutto in pytest.
    notify_disabled: bool = Field(default=False)
    # URL pubblico dell'app usato nei link delle notifiche push/email (es.
    # "https://hub.lysauto.it"). Vuoto = usa http://APP_HOST:APP_PORT, che
    # funziona solo da dentro la LAN — sbagliato per un link su cui l'admin
    # tocca dal telefono fuori casa, o per un'email a un utente esterno.
    public_base_url: str = Field(default="")

    # --- FCM push (app Android Capacitor) ---
    # Path al JSON della service account Firebase (Console Firebase >
    # Project Settings > Service Accounts > Generate new private key).
    # None = FCM disabilitato (nessun tentativo di invio, nessun errore).
    fcm_credentials_path: Path | None = Field(default=None)
    # Project ID Firebase (es. "lys-workflow-hub"), usato per l'URL FCM
    # HTTP v1: https://fcm.googleapis.com/v1/projects/<id>/messages:send
    fcm_project_id: str = Field(default="")

    # --- FCM push (Web, browser desktop/mobile del portale esterno) ---
    # Config pubblica dell'app Web Firebase (Console Firebase > Project
    # Settings > General > Your apps > app Web) — sono valori destinati al
    # browser, non segreti (a differenza di fcm_credentials_path sopra, che
    # resta server-side). Vuoto = notifiche Web push disattivate, nessun
    # errore (lo script client-side non parte).
    fcm_web_api_key: str = Field(default="")
    fcm_web_auth_domain: str = Field(default="")
    fcm_web_project_id: str = Field(default="")
    fcm_web_storage_bucket: str = Field(default="")
    fcm_web_messaging_sender_id: str = Field(default="")
    fcm_web_app_id: str = Field(default="")
    # VAPID public key (Console Firebase > Project Settings > Cloud Messaging
    # > Web configuration > Web Push certificates > Generate key pair).
    fcm_web_vapid_key: str = Field(default="")

    def public_url(self, path: str) -> str:
        """Costruisce un URL assoluto per link in notifiche push/email.

        Se `public_base_url` non è impostato, il fallback usa `app_host` —
        ma `app_host` è tipicamente `0.0.0.0` (bind su tutte le interfacce,
        vedi §10.7 in docs/SETUP_PRODUCTION.md), un indirizzo valido per
        *ascoltare* ma non per *navigare*: un link `http://0.0.0.0:8000/...`
        toccato da un browser reale non porta da nessuna parte. In quel
        caso usiamo `localhost` — comunque utile solo da dentro la LAN
        (come già documentato), ma almeno un link che si apre davvero
        invece di un indirizzo insensato. La soluzione vera resta impostare
        `PUBLIC_BASE_URL` in produzione."""
        if self.public_base_url:
            return f"{self.public_base_url.rstrip('/')}{path}"
        host = "localhost" if self.app_host == "0.0.0.0" else self.app_host
        return f"http://{host}:{self.app_port}{path}"

    # --- Autenticazione (v3.0) ---
    # Chiave usata per firmare il cookie di sessione (SessionMiddleware).
    # OBBLIGATORIA in produzione: l'app si rifiuta di partire se app_env=production
    # e questa è vuota (vedi assert in main.py). Generare con:
    #   python -c "import secrets; print(secrets.token_hex(32))"
    # Vuoto in sviluppo = viene generata una chiave random ad ogni avvio (le
    # sessioni non sopravvivono al riavvio, comodo per testare senza doverla
    # configurare a mano).
    secret_key: str = Field(default="")
    # Durata del cookie di sessione (giorni). Scaduto, l'utente deve rifare login.
    session_max_age_days: int = Field(default=14)
    # Tentativi di login falliti consecutivi prima del blocco temporaneo account.
    login_max_attempts: int = Field(default=5)
    # Durata del blocco account dopo troppi tentativi falliti (minuti).
    login_lockout_minutes: int = Field(default=15)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Ritorna l'istanza singleton delle impostazioni."""
    return Settings()
