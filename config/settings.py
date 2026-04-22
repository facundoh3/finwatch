from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Carga .env desde la raíz del proyecto (relativo a este archivo, no al CWD)
_ROOT = Path(__file__).parent.parent
load_dotenv(dotenv_path=_ROOT / ".env", override=False)


class Settings(BaseSettings):
    finnhub_api_key: str = ""
    marketaux_api_key: str = ""
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    alpha_vantage_api_key: str = ""

    cache_ttl_minutes: int = 30
    news_hours_back: int = 24
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=str(_ROOT / ".env"),
        env_file_encoding="utf-8",
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
