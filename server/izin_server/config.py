from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
  model_config = SettingsConfigDict(env_file=".env", env_prefix="IZIN_")

  database_url: str = "postgresql+asyncpg://izin:izin@localhost:5432/izin"
  api_token: str = "dev-token"

  default_approver: str = "approver"

  webhook_url: str | None = None
  webhook_secret: str = "dev-webhook-secret"

settings = Settings()