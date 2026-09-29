from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # A local folder or an s3:// URI. No default: writing tables to a guessed place is worse than stopping.
    lake_uri: str = Field(min_length=1)

    rows_per_batch: int = Field(default=20_000, gt=0)
    # About 2 transactions for each account each day. Too few accounts and every account's
    # average settles within minutes, which makes amount_vs_account say nothing.
    accounts: int = Field(default=40_000, gt=0)
    # The same seed and day always give the same batch, so a rerun is comparable to the first run.
    seed: int = 7

    # The MLflow server. A file store cannot hold a model registry, so this is always a URL.
    mlflow_tracking_uri: str = Field(default="http://localhost:8096", min_length=1)

    # The Airflow API, for the Pipeline tab. The server stack reaches it by service name.
    airflow_url: str = Field(default="http://localhost:8095", min_length=1)

    # Alerts and analyst decisions. Port 5440 is this project's slot in the workspace port table.
    postgres_dsn: str = Field(default="postgresql://finplat:finplat@localhost:5440/finplat", min_length=1)


def get_settings() -> Settings:
    return Settings()
