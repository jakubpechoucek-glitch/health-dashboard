from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    health_db_path: str = "data/health.db"
    garmin_webhook_token: str = ""
    garmin_client_id: str = ""
    garmin_client_secret: str = ""
    garmin_api_key: str = ""

    # Přihlášení do aplikace (HTTP Basic přes HTTPS). Prázdné heslo = aplikace se odmítne spustit pro uživatele.
    app_user: str = "jakub"
    app_password: str = ""
    enable_docs: bool = False


def get_settings() -> Settings:
    return Settings()
