#!/usr/bin/env python3
"""
A股看板模块API路由
"""
import logging
from fastapi import APIRouter

from app.services.kanban_service import get_kanban_service
from app.core.response import ok

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kanban", tags=["A股看板"])

# ==================== 总接口 ====================
@router.get("/all", summary="获取完整看板数据")
async def get_kanban_all_data():
    try:
        service = await get_kanban_service()
        data = service.get_kanban_all()
        return ok(data=data, message="成功")
    except Exception as e:
        logger.error(f"all 接口异常: {e}")
        return ok(success=False, data={
            "market_sentiment": {"sentiment_score": 0},
            "zt_group": [], "sector_rank": [], "risk_list": [], "fund_rank": []
        }, message="数据服务繁忙")

# ==================== 单独接口 ====================
@router.get("/market-sentiment", summary="大盘情绪")
async def get_market_sentiment():
    try:
        service = await get_kanban_service()
        return ok(data=service.get_market_sentiment(), message="成功")
    except:
        return ok(success=False, data={}, message="获取失败")

@router.get("/zt-group", summary="连板梯队")
async def get_zt_group():
    try:
        service = await get_kanban_service()
        return ok(data=service.get_zt_group(), message="成功")
    except:
        return ok(success=False, data=[], message="获取失败")

@router.get("/sector-rank", summary="板块热度")
async def get_sector_rank():
    try:
        service = await get_kanban_service()
        return ok(data=service.get_sector_rank(), message="成功")
    except:
        return ok(success=False, data=[], message="获取失败")

@router.get("/risk-list", summary="风险榜单")
async def get_risk_list():
    try:
        service = await get_kanban_service()
        return ok(data=service.get_risk_list(), message="成功")
    except:
        return ok(success=False, data=[], message="获取失败")

@router.get("/fund-rank", summary="资金排行")
async def get_fund_rank():
    try:
        service = await get_kanban_service()
        return ok(data=service.get_fund_rank(), message="成功")
    except:
        return ok(success=False, data=[], message="获取失败")

@router.get("/health", summary="健康检查")
async def health():
    return ok(data={"status": "healthy"}, message="正常")