from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    health_db_path: str = "data/health.db"
    garmin_webhook_token: str = ""
    garmin_client_id: str = ""
    garmin_client_secret: str = ""


def get_settings() -> Settings:
    return Settings()
