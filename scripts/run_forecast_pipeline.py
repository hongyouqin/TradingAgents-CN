import asyncio
import json
import sys

sys.path.insert(0, r"E:\workspace\TradingAgents-CN")

from app.services.forecast_data_pipeline import get_forecast_pipeline

# Attempt to initialize real DB; if unavailable, fall back to dry-run mock DB
async def main():
    db = None
    db_inited = False
    try:
        from app.core.database import init_database, get_mongo_db, close_db
        print("Initializing MongoDB/Redis from settings...")
        await init_database()
        db = get_mongo_db()
        db_inited = True
        print("MongoDB initialized successfully.")
    except Exception as e:
        print(f"❌ MongoDB init failed: {e}")
        print("Falling back to dry-run mock database (no persistence).")
        try:
            from scripts.run_forecast_pipeline_dry import MockDB, mock_data
            db = MockDB(mock_data)
        except Exception as imp_err:
            print(f"❌ Failed to import dry-run mocks: {imp_err}")
            raise RuntimeError("No database available for pipeline")

    pipeline = get_forecast_pipeline()
    date_key = "2026-05-15"
    print(f"Running pipeline for date: {date_key}")
    res = await pipeline.run_daily_pipeline(db, date_key)
    print(json.dumps(res, ensure_ascii=False, indent=2))

    if db_inited:
        try:
            await close_db()
        except Exception:
            pass

if __name__ == '__main__':
    asyncio.run(main())
