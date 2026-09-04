from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg://gex:gex@localhost:5432/gex"
    DATA_DIR: str = "./data"
    PROVIDER: str = "cboe"
    SYMBOLS: str = "SPX,SPY,QQQ"
    TZ: str = "America/New_York"

    @property
    def symbols(self) -> list[str]:
        return [s.strip() for s in self.SYMBOLS.split(",") if s.strip()]


settings = Settings()
