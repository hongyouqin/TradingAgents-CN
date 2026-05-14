"""One-off script to create the unique index on sign_records collection.
Run with: python -m scripts.create_sign_index
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.daos.sign_dao import ensure_indexes
from app.core.config import settings

async def main():
    client = AsyncIOMotorClient(settings.MONGO_URI)
    db = client[settings.MONGO_DB_NAME]
    await ensure_indexes(db)
    print('ensure_indexes done')

if __name__ == '__main__':
    asyncio.run(main())
