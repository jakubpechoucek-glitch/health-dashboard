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
