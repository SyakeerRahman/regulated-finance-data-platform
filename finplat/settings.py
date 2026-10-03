from pathlib import Path

from pydantic import AliasChoices, Field
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

    # Any API that speaks the OpenAI chat format. DeepSeek is the default because it is the cheapest
    # that calls tools well. The key is optional on purpose: without it the AI layer reports itself
    # off, and every alert still carries its SHAP reason and its policy rule.
    llm_api_key: str | None = Field(default=None, validation_alias=AliasChoices("LLM_API_KEY", "DEEPSEEK_API_KEY"))
    llm_base_url: str = Field(default="https://api.deepseek.com", min_length=1)
    llm_model: str = Field(default="deepseek-chat", min_length=1)
    # Model calls for each UTC day. One question can cost up to 6 calls, so 500 is about 100 questions.
    llm_daily_calls: int = Field(default=500, gt=0)


def get_settings() -> Settings:
    return Settings()
