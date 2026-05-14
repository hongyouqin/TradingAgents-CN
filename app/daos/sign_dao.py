from typing import Optional
from datetime import date, datetime
import pymongo
from motor.motor_asyncio import AsyncIOMotorDatabase

COLLECTION_NAME = "sign_records"

async def ensure_indexes(db: AsyncIOMotorDatabase):
    """Create unique index on (user_id, sign_date)."""
    await db[COLLECTION_NAME].create_index(
        [("user_id", pymongo.ASCENDING), ("sign_date", pymongo.ASCENDING)],
        unique=True,
        name="user_date_unique",
    )

async def has_signed(db: AsyncIOMotorDatabase, user_id: str, sign_date: date) -> bool:
    """Return True if a sign record exists for the user on sign_date."""
    doc = await db[COLLECTION_NAME].find_one({"user_id": user_id, "sign_date": sign_date})
    return doc is not None

async def insert_sign(db: AsyncIOMotorDatabase, user_id: str, sign_date: date, sign_time: datetime):
    """Insert a sign record. Raises pymongo.errors.DuplicateKeyError if already exists."""
    return await db[COLLECTION_NAME].insert_one({
        "user_id": user_id,
        "sign_date": sign_date,
        "sign_time": sign_time,
    })
