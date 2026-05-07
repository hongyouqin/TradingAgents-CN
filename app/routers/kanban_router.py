#!/usr/bin/env python3
"""
A股看板模块API路由
"""
import logging
from fastapi import APIRouter, HTTPException

from app.services.kanban_service import get_kanban_service
from app.core.response import ok

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kanban", tags=["A股看板"])

# ==================== 【核心】总接口：一次性获取所有看板数据 ====================
@router.get("/all", summary="获取完整看板数据（推荐前端使用）")
async def get_kanban_all_data():
    """
    一次性返回：
    1. 大盘情绪分(0-100)
    2. 涨跌家数、炸板率
    3. 连板梯队分组
    4. 题材热度排行
    5. 风险榜单
    6. 资金排行
    """
    try:
        service = await get_kanban_service()
        data = service.get_kanban_all()
        return ok(data=data, message="获取看板数据成功")
    except Exception as e:
        logger.error(f"获取看板失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取看板数据失败: {str(e)}")

# ==================== 可选单独接口（用于局部刷新/调试） ====================
@router.get("/market-sentiment", summary="大盘情绪、涨跌家数、炸板率")
async def get_market_sentiment():
    try:
        service = await get_kanban_service()
        return ok(data=service.get_market_sentiment(), message="成功")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/zt-group", summary="连板梯队分组")
async def get_zt_group():
    try:
        service = await get_kanban_service()
        return ok(data=service.get_zt_group(), message="成功")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sector-rank", summary="板块热度排行")
async def get_sector_rank():
    try:
        service = await get_kanban_service()
        return ok(data=service.get_sector_rank(), message="成功")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/risk-list", summary="风险榜单（跌幅榜）")
async def get_risk_list():
    try:
        service = await get_kanban_service()
        return ok(data=service.get_risk_list(), message="成功")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/fund-rank", summary="资金成交额排行")
async def get_fund_rank():
    try:
        service = await get_kanban_service()
        return ok(data=service.get_fund_rank(), message="成功")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== 服务检查 ====================
@router.get("/health", summary="看板服务健康检查")
async def kanban_health_check():
    try:
        service = await get_kanban_service()
        service.get_a_stock_spot()
        return ok(data={"status": "healthy"}, message="服务正常")
    except Exception as e:
        logger.error(f"服务异常: {e}")
        return ok(success=False, data={"status": "unhealthy"}, message="服务异常")