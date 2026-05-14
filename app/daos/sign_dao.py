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
    """Return True if a sign record exists for the user on sign_date.

    The sign_date is normalized to an ISO date string (YYYY-MM-DD) before querying
    because MongoDB cannot store datetime.date objects directly.
    """
    # normalize sign_date to YYYY-MM-DD string
    if isinstance(sign_date, datetime):
        date_key = sign_date.date().isoformat()
    elif isinstance(sign_date, date):
        date_key = sign_date.isoformat()
    else:
        date_key = str(sign_date)

    doc = await db[COLLECTION_NAME].find_one({"user_id": user_id, "sign_date": date_key})
    return doc is not None

async def insert_sign(db: AsyncIOMotorDatabase, user_id: str, sign_date: date, sign_time: datetime):
    """Insert a sign record. Raises pymongo.errors.DuplicateKeyError if already exists.

    The sign_date will be stored as an ISO date string (YYYY-MM-DD) to avoid
    pymongo encoding issues with datetime.date objects.
    """
    # normalize sign_date
    if isinstance(sign_date, datetime):
        date_key = sign_date.date().isoformat()
    elif isinstance(sign_date, date):
        date_key = sign_date.isoformat()
    else:
        date_key = str(sign_date)

    return await db[COLLECTION_NAME].insert_one({
        "user_id": user_id,
        "sign_date": date_key,
        "sign_time": sign_time,
    })
