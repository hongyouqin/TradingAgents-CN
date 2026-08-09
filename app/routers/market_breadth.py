"""
全市场宽度指标 API（阿姆氏指标 TRIN）

Endpoints:
    - GET /api/market-breadth/arms-index  -> 获取阿姆氏指标时间序列及多周期均值

统一响应格式: {success, data, message, timestamp}
"""
import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query

from app.routers.auth_db import get_current_user
from app.core.response import ok
from app.services.arms_index_service import get_arms_index_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market-breadth", tags=["市场宽度"])


@router.get("/arms-index")
async def get_arms_index(
    days: int = Query(21, ge=5, le=120, description="回溯交易日数量"),
    periods: str = Query("5,10,21", description="周期列表（逗号分隔，如 5,10,21）"),
    include_realtime: bool = Query(True, description="是否合并实时行情"),
):
    """获取全市场阿姆氏指标 (Arms Index / TRIN)

    阿姆氏指标 = (上涨家数 / 下跌家数) / (上涨成交量 / 下跌成交量)

    含义:
        - TRIN = 1.0 → 中性，资金均匀分布
        - TRIN < 1.0 → 上涨量占比更高，多头主导（看涨）
        - TRIN > 1.0 → 下跌量占比更高，空头主导（看跌）

    数据说明:
        - 历史数据来自 stock_daily_quotes（日线）
        - 实时数据来自 market_quotes（盘中快照）
        - 返回包含每日明细 + 多周期滚动均值

    Args:
        days: 回溯的交易日数量（默认 21）
        periods: 需要计算的滚动周期，逗号分隔（默认 "5,10,21"）
        include_realtime: 是否合并今日实时行情（默认 True）
    """
    try:
        period_list = [int(p.strip()) for p in periods.split(",") if p.strip()]
        if not period_list:
            period_list = [5, 10, 21]

        service = get_arms_index_service()
        result = await service.get_arms_index(
            days=days,
            periods=period_list,
            include_realtime=include_realtime,
        )
        return ok(data=result)
    except Exception as e:
        logger.exception(f"获取阿姆氏指标失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取阿姆氏指标失败: {str(e)}")


@router.post("/arms-index/refresh")
async def refresh_arms_index(
    days: int = Query(250, ge=21, le=500, description="重算的交易日窗口（默认250天）"),
):
    """手动触发阿姆氏指标（TRIN）预计算并写入 market_trin_daily

    与每日定时任务共用同一幂等写入逻辑：按 trade_date 唯一 upsert，重复触发不会产生重复数据；
    与定时任务互斥执行（同一把锁），避免并发聚合全表造成 MongoDB 压力。

    Returns:
        {"stored": 天数, "start_date": "YYYYMMDD", "end_date": "YYYYMMDD"}
    """
    try:
        service = get_arms_index_service()
        result = await service.compute_and_store_daily_trin(days=days)
        logger.info(f"🔁 手动触发阿姆氏指标预计算完成: {result}")
        return ok(data=result)
    except Exception as e:
        logger.exception(f"手动触发阿姆氏指标预计算失败: {e}")
        raise HTTPException(status_code=500, detail=f"手动触发阿姆氏指标预计算失败: {str(e)}")
