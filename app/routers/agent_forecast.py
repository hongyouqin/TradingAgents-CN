from datetime import date, datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.routers.auth_db import get_current_user
from app.core.database import get_mongo_db
from app.services.forecast_service import get_forecast_service
from app.core.response import ok

router = APIRouter(prefix="/agent/forecast", tags=["agent-forecast"])


async def get_admin_user(user: dict = Depends(get_current_user)):
    if not user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="权限不足")
    return user


@router.get("/tomorrow")
async def get_tomorrow_forecast(date: Optional[str] = Query(None, description="预测日期，格式 YYYY-MM-DD，默认：明日（本地）")):
    # determine target date (prediction date)
    if date:
        date_key = date
    else:
        tgt = datetime.now().date() + timedelta(days=1)
        date_key = tgt.isoformat()

    db = get_mongo_db()
    service = get_forecast_service(db)
    forecast = await service.get_tomorrow_forecast(date_key)
    if not forecast:
        return ok(data=None, message=f"forecast for {date_key} not found")
    return ok(data=forecast)


@router.get("/history")
async def get_forecast_history(limit: int = Query(30, ge=1, le=365)):
    db = get_mongo_db()
    service = get_forecast_service(db)
    items = await service.get_forecast_history(limit=limit)
    return ok(data=items)


@router.get("/data/source")
async def get_daily_market_data(date: Optional[str] = Query(None, description="日期 YYYY-MM-DD，默认：今日（本地）"), admin=Depends(get_admin_user)):
    if date:
        date_key = date
    else:
        date_key = datetime.now().date().isoformat()

    db = get_mongo_db()
    service = get_forecast_service(db)
    data = await service.get_daily_market_data(date_key)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="daily market data not found")
    return ok(data=data)


@router.post("/trigger")
async def trigger_pipeline_and_agents(
    date: Optional[str] = Query(None, description="目标日期 YYYY-MM-DD（默认：today）"),
    force: bool = Query(False, description="是否强制执行（跳过时段检查）"),
    admin=Depends(get_admin_user)
):
    """管理员手动触发数据拉取 + 智能体推理（生成 tomorrow_forecast）。

    Returns the forecast payload after successful run.
    """
    from app.services.forecast_data_pipeline import get_forecast_pipeline
    from app.services.forecast_agent_service import get_forecast_agent_service

    db = get_mongo_db()
    pipeline = get_forecast_pipeline()
    target_date = date or datetime.now().date().isoformat()

    # run pipeline (best-effort)
    try:
        await pipeline.run_daily_pipeline(db, target_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"pipeline run failed: {e}")

    # run agents to generate forecast (pass admin user for LLM billing context)
    try:
        agent_service = get_forecast_agent_service(db)
        forecast = await agent_service.run_for_date(target_date, save=True, user=admin)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"agent run failed: {e}")

    return ok(data=forecast, message=f"Forecast generated for {target_date}")
