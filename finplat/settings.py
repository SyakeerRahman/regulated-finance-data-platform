from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # A local folder or an s3:// URI. No default: writing tables to a guessed place is worse than stopping.
    lake_uri: str = Field(min_length=1)

    rows_per_batch: int = Field(default=20_000, gt=0)
    # The same seed and day always give the same batch, so a rerun is comparable to the first run.
    seed: int = 7


def get_settings() -> Settings:
    return Settings()
