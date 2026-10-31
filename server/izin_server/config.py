from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
  model_config = SettingsConfigDict(env_file=".env", env_prefix="IZIN_")

  database_url: str = "postgresql+asyncpg://izin:izin@localhost:5432/izin"
  api_token: str = "dev-token"

  default_approver: str = "approver"

  webhook_url: str | None = None
  webhook_secret: str = "dev-webhook-secret"

  admin_token: str = "dev-admin-token"

  static_dir: str | None = None
  public_url: str = "http://localhost:8000"

  human_channels: list[str] = []

  smtp_host: str | None = None
  smtp_port: int = 507
  smtp_username: str | None = None
  smtp_password: str | None = None
  smtp_starttls: bool = True
  smtp_from: str = "izin <izin@localhost>"
  link_secret: str = "dev-link_secret"

settings = Settings()