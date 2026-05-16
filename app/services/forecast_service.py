from typing import Optional
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.daos import forecast_dao

class ForecastService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def ensure_indexes(self):
        try:
            await forecast_dao.ensure_indexes(self.db)
        except Exception:
            # best-effort, do not raise to avoid blocking startup
            pass

    async def save_daily_market_data(self, date_key: str, data: dict):
        """Save/replace the cleaned daily market data for date_key (ISO YYYY-MM-DD)."""
        await forecast_dao.upsert_daily_market_data(self.db, date_key, data)

    async def get_daily_market_data(self, date_key: str) -> Optional[dict]:
        return await forecast_dao.get_daily_market_data(self.db, date_key)

    async def save_tomorrow_forecast(self, date_key: str, forecast: dict):
        await forecast_dao.upsert_tomorrow_forecast(self.db, date_key, forecast)

    async def get_tomorrow_forecast(self, date_key: str) -> Optional[dict]:
        return await forecast_dao.get_tomorrow_forecast(self.db, date_key)

    async def get_forecast_history(self, limit: int = 30):
        return await forecast_dao.get_forecast_history(self.db, limit)

# convenient singleton-style factory used by routers/services
_forecast_service_instance = None

def get_forecast_service(db: AsyncIOMotorDatabase):
    global _forecast_service_instance
    if _forecast_service_instance is None:
        _forecast_service_instance = ForecastService(db)
    return _forecast_service_instance
