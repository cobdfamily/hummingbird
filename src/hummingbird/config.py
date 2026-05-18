"""Runtime configuration for hummingbird.

pydantic-settings reads HUMMINGBIRD_* environment
variables (and an optional .env file) into a typed
Settings object that the rest of the app consumes via
the module-level `settings` singleton.

One file, one source of truth -- every operator-
controllable knob (TTS engine, KADOS realm, plugin
registration) shows up here; the test suite swaps
env vars rather than reaching into nested dicts.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="HUMMINGBIRD_", extra="ignore"
    )

    # Default-backend credentials (used by /login when no plugin is active).
    username: str = Field(default="")
    password: str = Field(default="")

    # Active plugin entry-point name. Empty = standalone.
    plugin: str = ""

    # Fallback source for /download when cache misses.
    public_content_url: str = ""

    # Storage.
    data_dir: Path = Path("data")
    cache_dir: Path = Path("cache")

    # Server bind.
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.cache_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / "bookshelves").mkdir(parents=True, exist_ok=True)
(settings.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
(settings.data_dir / "bookmarks").mkdir(parents=True, exist_ok=True)


class KadosSettings(BaseSettings):
    """Independent env prefix so Kados keys don't collide with Hummingbird ones."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="KADOS_", extra="ignore")
    api_key: str = ""


kados_settings = KadosSettings()
