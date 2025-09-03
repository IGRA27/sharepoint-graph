from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # Credenciales AAD (opcionales al arrancar; se validan al usarse)
    AAD_TENANT_ID: str | None = Field(default=None)
    AAD_CLIENT_ID: str | None = Field(default=None)
    AAD_CLIENT_SECRET: str | None = Field(default=None)

    # Sitio de SharePoint (defaults según tu tenant/proyecto)
    SITE_HOSTNAME: str = "atiscodesa.sharepoint.com"
    SITE_PATH: str = "/sites/Loyalty2021"

    # CORS
    ALLOW_ORIGINS: str = "*"  # separa por coma si necesitas múltiples

    # Zona horaria para resolución auto de mes
    TIMEZONE: str = "America/Guayaquil"

    # Descargas temporales
    DOWNLOAD_DIR: str = "/tmp/sharepoint"

    #Revisar a futuro y cambiar
    SSL_VERIFY: bool = Field(default=True)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
