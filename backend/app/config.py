from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://auction:auction@postgres:5432/auction"
    redis_url: str = "redis://redis:6379/0"
    ea_api_url: str = "https://apiv8.emiratesauction.net"
    ea_site_url: str = "https://www.emiratesauction.com"
    poll_limit: int = 10
    poll_interval_seconds: int = 300
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    admin_token: str = "change-me"
    eur_aed_rate: float = 4.30
    autoscout_refresh_hours: int = 24
    autoscout_batch_size: int = 8
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
