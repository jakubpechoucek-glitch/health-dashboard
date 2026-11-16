import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    health_db_path: str = "data/health.db"
    garmin_api_key: str = ""
    auto_sync_hours: int = 6      # jak často se Garmin stahuje sám (0 = vypnuto)
    auto_sync_days: int = 7       # kolik posledních dní se při každém stažení obnoví (dohání pozdě dopočítané hodnoty)

    # Přihlášení přes Google = stejný Supabase projekt jako Rozcestník (hodnoty jsou veřejné, jako v auth.js).
    supabase_url: str = "https://unoefgbzymlwwehgzusf.supabase.co"
    supabase_key: str = "sb_publishable_0slnWPDezyFgiOmUGDal4Q_B5ZGz7i_"
    # E-maily, které smí do aplikace (čárkou oddělené). Zdravotní data jsou jen moje, ne celé rodiny.
    allowed_emails: str = ""
    enable_docs: bool = False
    seed_json_b64: str = ""   # jednorázový import dat, viz app/seed.py


def get_settings() -> Settings:
    return Settings()


def garmin_key_var(person_id: int) -> str:
    """Název proměnné prostředí s osobním API klíčem Garminu dané osoby (osoba 1 = vlastník má GARMIN_API_KEY)."""
    return "GARMIN_API_KEY" if person_id == 1 else f"GARMIN_API_KEY_{person_id}"


def garmin_key(settings: Settings, person_id: int) -> str:
    return settings.garmin_api_key if person_id == 1 else os.environ.get(garmin_key_var(person_id), "")


def any_garmin_key(settings: Settings) -> bool:
    return bool(settings.garmin_api_key) or any(k.startswith("GARMIN_API_KEY_") and v for k, v in os.environ.items())
