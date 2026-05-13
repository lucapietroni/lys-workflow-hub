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
    wincar_archivio: Path = Field(default=Path(r"C:\WinCar\Archivio"))
    wincar_odbc_driver: str = Field(default="Microsoft Access Driver (*.mdb, *.accdb)")

    # --- App ---
    app_host: str = Field(default="127.0.0.1")
    app_port: int = Field(default=8000)
    app_env: str = Field(default="development")
    app_archivio_cessioni: Path = Field(default=Path(r"C:\LYSApp\Cessioni_firmate"))

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

    # --- Posta in uscita (M2) ---
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")

    pec_smtp_host: str = Field(default="sendm.cert.legalmail.it")
    pec_smtp_port: int = Field(default=465)
    pec_smtp_user: str = Field(default="")
    pec_smtp_password: str = Field(default="")

    # --- AI ---
    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-sonnet-4-5")
    ai_budget_monthly_eur: float = Field(default=20.0)
    ai_budget_alert_eur: float = Field(default=15.0)

    # --- Notifiche ---
    ntfy_topic: str = Field(default="")
    ntfy_server: str = Field(default="https://ntfy.sh")
    alert_email: str = Field(default="")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Ritorna l'istanza singleton delle impostazioni."""
    return Settings()
