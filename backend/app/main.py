from fastapi import FastAPI

from app.config import settings

app = FastAPI(title="GEX Trading API")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "provider": settings.PROVIDER, "symbols": settings.symbols}
