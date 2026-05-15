from datetime import date
from motor.motor_asyncio import AsyncIOMotorDatabase

COLLECTION = "sign_stats"

async def ensure_indexes(db: AsyncIOMotorDatabase):
    """Ensure unique index on stat_date."""
    await db[COLLECTION].create_index([("stat_date", 1)], unique=True, name="date_unique")

async def increment_sign_count(db: AsyncIOMotorDatabase, stat_date: date):
    """Increment sign count for the given date (ISO string). Upserts if missing."""
    if hasattr(stat_date, "isoformat"):
        date_key = stat_date.isoformat()
    else:
        date_key = str(stat_date)
    await db[COLLECTION].update_one(
        {"stat_date": date_key},
        {"$inc": {"count": 1}, "$setOnInsert": {"stat_date": date_key}},
        upsert=True,
    )

async def get_sign_count(db: AsyncIOMotorDatabase, stat_date: date) -> int:
    if hasattr(stat_date, "isoformat"):
        date_key = stat_date.isoformat()
    else:
        date_key = str(stat_date)
    doc = await db[COLLECTION].find_one({"stat_date": date_key})
    return int(doc.get("count", 0)) if doc else 0
