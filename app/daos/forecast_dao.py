from typing import Optional
from datetime import date, datetime
from motor.motor_asyncio import AsyncIOMotorDatabase

COLLECTION_DAILY = "daily_market_data"
COLLECTION_FORECAST = "tomorrow_forecast"

async def ensure_indexes(db: AsyncIOMotorDatabase):
    # ensure unique date indexes for both collections
    await db[COLLECTION_DAILY].create_index([("date", 1)], unique=True, name="daily_date_unique")
    await db[COLLECTION_FORECAST].create_index([("date", 1)], unique=True, name="forecast_date_unique")

async def upsert_daily_market_data(db: AsyncIOMotorDatabase, date_key: str, payload: dict):
    """Upsert cleaned daily market data keyed by ISO date string.

    Avoid updating the 'date' field in the $set payload to prevent MongoDB
    "Updating the path 'date' would create a conflict at 'date'" errors
    when $set and $setOnInsert try to modify the same path.
    """
    payload = payload.copy()
    payload.setdefault("create_time", datetime.utcnow())
    # remove date from payload to avoid conflicts with $setOnInsert
    payload.pop("date", None)
    await db[COLLECTION_DAILY].update_one({"date": date_key}, {"$set": payload, "$setOnInsert": {"date": date_key}}, upsert=True)

async def get_daily_market_data(db: AsyncIOMotorDatabase, date_key: str) -> Optional[dict]:
    doc = await db[COLLECTION_DAILY].find_one({"date": date_key})
    return doc

async def upsert_tomorrow_forecast(db: AsyncIOMotorDatabase, date_key: str, payload: dict):
    """Store the forecast for the given date (the prediction for that date)."""
    payload = payload.copy()
    payload.setdefault("create_time", datetime.utcnow())
    await db[COLLECTION_FORECAST].update_one({"date": date_key}, {"$set": payload, "$setOnInsert": {"date": date_key}}, upsert=True)

async def get_tomorrow_forecast(db: AsyncIOMotorDatabase, date_key: str) -> Optional[dict]:
    doc = await db[COLLECTION_FORECAST].find_one({"date": date_key})
    return doc

async def get_forecast_history(db: AsyncIOMotorDatabase, limit: int = 30):
    cursor = db[COLLECTION_FORECAST].find().sort([("date", -1)]).limit(limit)
    return [doc async for doc in cursor]
