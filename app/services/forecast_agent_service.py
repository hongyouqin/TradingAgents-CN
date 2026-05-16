import logging
import asyncio
from typing import Optional
from datetime import datetime

from app.services.forecast_service import get_forecast_service
from app.agents.forecast_agents import DataAggregatorAgent, EmotionAgent, SectorAgent, OutputAgent

logger = logging.getLogger(__name__)


class ForecastAgentService:
    """Orchestrates the 4 forecast agents to produce tomorrow's forecast."""

    def __init__(self, db):
        self.db = db
        self.aggregator = DataAggregatorAgent()
        self.emotioner = EmotionAgent()
        self.sectorer = SectorAgent()
        self.outputer = OutputAgent()

    async def run_for_date(self, date_key: Optional[str] = None, save: bool = True, user: dict = None) -> dict:
        if date_key is None:
            date_key = datetime.utcnow().date().isoformat()

        svc = get_forecast_service(self.db)
        # ensure indexes exist (best-effort)
        try:
            await svc.ensure_indexes()
        except Exception:
            pass

        # fetch daily cleaned data
        daily = await svc.get_daily_market_data(date_key)
        if not daily:
            raise RuntimeError(f"No daily_market_data for {date_key}, run pipeline first")

        # 1. aggregator
        ctx = await self.aggregator.run(self.db, daily)

        # 2. emotion + sector in parallel
        emotion_task = asyncio.create_task(self.emotioner.run(ctx))
        sector_task = asyncio.create_task(self.sectorer.run(ctx))
        emotion, sector = await asyncio.gather(emotion_task, sector_task)

        # 3. output (pass user for LLM billing context)
        forecast = await self.outputer.run(ctx, emotion, sector, user=user)

        if save:
            try:
                await svc.save_tomorrow_forecast(date_key, forecast)
            except Exception as e:
                logger.exception(f"Failed to save forecast for {date_key}: {e}")

        return forecast


# factory
_instance = None

def get_forecast_agent_service(db):
    global _instance
    if _instance is None:
        _instance = ForecastAgentService(db)
    return _instance
