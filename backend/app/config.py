from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg://gex:gex@localhost:5432/gex"
    DATA_DIR: str = "./data"
    # Continuous dividend yield used to build the option forward in app/gex/greeks.py
    # (forward = spot * exp((RISK_FREE_RATE - DIVIDEND_YIELD) * T)). One global default,
    # roughly the S&P 500 trailing yield; override per call for a symbol that differs.
    DIVIDEND_YIELD: float = 0.013
    # Empty by default so a user running on the default "cboe" provider is never blocked by a
    # missing credential; app.providers.marketdata.MarketDataProvider raises a clear,
    # setting-named error if it is ever constructed without this populated.
    MARKETDATA_TOKEN: str = ""
    PROVIDER: str = "cboe"
    # Continuously compounded annualized risk-free rate for Greeks. A parameter, never
    # fetched from a rates feed (PLAN.md / TASKS.md T07).
    RISK_FREE_RATE: float = 0.04
    SYMBOLS: str = "SPX,SPY,QQQ,GLD,DIA"
    TZ: str = "America/New_York"

    @property
    def symbols(self) -> list[str]:
        return [s.strip() for s in self.SYMBOLS.split(",") if s.strip()]


settings = Settings()
