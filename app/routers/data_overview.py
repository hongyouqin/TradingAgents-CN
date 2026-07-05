"""
数据概览 API 路由（预约披露日 + 雪球热度 + 交易排行榜 + 东方财富人气榜）

预约披露日接口：
1. GET /api/disclosure-calendar/{stock_code} — 按股票代码查询最新披露日
2. GET /api/disclosure-calendar/list — 按披露日排序的分页列表

雪球热度接口：
1. GET /api/stock-hot/{category} — 按分类查询热度排行
2. GET /api/stock-hot/all — 查询所有分类热度数据

交易排行榜接口：
1. GET /api/stock-hot-deal — 查询交易排行榜

人气榜接口：
1. GET /api/stock-hot-rank — 查询东方财富人气榜
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.routers.auth_db import get_current_user
from app.services.data_overview_service import get_disclosure_calendar_service
from app.services.data_overview_service import get_stock_hot_xueqiu_service
from app.services.data_overview_service import get_stock_hot_deal_xueqiu_service
from app.services.data_overview_service import get_stock_hot_rank_em_service
from app.models.stock_models import (
    DisclosureCalendarResponse,
    DisclosureCalendarListResponse,
    StockHotXueqiuResponse,
    StockHotXueqiuByCategoryResponse,
    StockHotXueqiuItem,
    StockHotDealXueqiuResponse,
    StockHotDealXueqiuItem,
    StockHotRankEMResponse,
    StockHotRankEMItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/disclosure-calendar", tags=["预约披露日"])

# 雪球热度路由（使用独立前缀）
hot_router = APIRouter(prefix="/api/stock-hot", tags=["雪球热度"])

# 雪球交易排行路由
deal_router = APIRouter(prefix="/api/stock-hot-deal", tags=["雪球交易排行"])

# 东方财富人气榜路由
rank_router = APIRouter(prefix="/api/stock-hot-rank", tags=["东方财富人气榜"])


@router.get("/{stock_code}", response_model=DisclosureCalendarResponse)
async def get_disclosure_by_stock(
    stock_code: str,
    current_user: dict = Depends(get_current_user),
):
    """
    按股票代码查询最新预约披露日

    Args:
        stock_code: 6 位股票代码

    Returns:
        DisclosureCalendarResponse: 包含该股票的最新预约披露日信息
    """
    try:
        service = get_disclosure_calendar_service()
        item = await service.query_by_stock_code(stock_code)

        if item is None:
            return DisclosureCalendarResponse(
                success=False,
                data=None,
                message=f"未找到股票代码 {stock_code} 的预约披露日信息",
            )

        return DisclosureCalendarResponse(
            success=True,
            data=item,
            message="获取成功",
        )

    except Exception as e:
        logger.error(f"❌ 查询预约披露日失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.get("", response_model=DisclosureCalendarListResponse)
@router.get("/list", response_model=DisclosureCalendarListResponse)
async def list_disclosure_calendar(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    data_date: Optional[str] = Query(None, description="财报数据截止日期，如 20260630"),
    current_user: dict = Depends(get_current_user),
):
    """
    获取预约披露日分页列表，按披露日升序排列（越近越靠前）

    Args:
        page: 页码，从 1 开始
        page_size: 每页条数 (1-100)
        data_date: 可选，按财报数据截止日期筛选

    Returns:
        DisclosureCalendarListResponse: 分页列表
    """
    try:
        service = get_disclosure_calendar_service()
        result = await service.query_list(
            page=page,
            page_size=page_size,
            data_date=data_date,
        )

        return DisclosureCalendarListResponse(
            success=True,
            data=result["items"],
            total=result["total"],
            page=result["page"],
            page_size=result["page_size"],
            message="获取成功",
        )

    except Exception as e:
        logger.error(f"❌ 查询预约披露日列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


# ===================== 雪球股票热度接口 =====================


@hot_router.get("/{category}", response_model=StockHotXueqiuResponse)
async def get_stock_hot_by_category(
    category: str,
    limit: int = Query(50, ge=1, le=200, description="返回条数"),
    current_user: dict = Depends(get_current_user),
):
    """
    按分类查询雪球股票热度排行

    Args:
        category: "最热门" 或 "本周新增"
        limit: 返回条数 (1-200)

    Returns:
        StockHotXueqiuResponse: 热度排行列表
    """
    if category not in ["最热门", "本周新增"]:
        raise HTTPException(
            status_code=400,
            detail="category 参数必须为 '最热门' 或 '本周新增'",
        )

    try:
        service = get_stock_hot_xueqiu_service()
        items = await service.query_by_category(category, limit=limit)

        return StockHotXueqiuResponse(
            success=True,
            data=[StockHotXueqiuItem(**item) for item in items],
            total=len(items),
            message=f"获取雪球热度[{category}]成功",
        )
    except Exception as e:
        logger.error(f"❌ 查询雪球热度失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@hot_router.get("/all", response_model=StockHotXueqiuByCategoryResponse)
async def get_all_stock_hot(
    limit: int = Query(50, ge=1, le=200, description="每分类返回条数"),
    current_user: dict = Depends(get_current_user),
):
    """
    查询所有分类的雪球股票热度数据

    Args:
        limit: 每分类返回条数 (1-200)

    Returns:
        StockHotXueqiuByCategoryResponse: 按分类组织的热度数据
    """
    try:
        service = get_stock_hot_xueqiu_service()
        data = await service.query_all(limit=limit)

        # 将 dict values 转为 StockHotXueqiuItem 列表
        result = {}
        for category, items in data.items():
            result[category] = [StockHotXueqiuItem(**item) for item in items]

        return StockHotXueqiuByCategoryResponse(
            success=True,
            data=result,
            message="获取所有雪球热度数据成功",
        )
    except Exception as e:
        logger.error(f"❌ 查询雪球热度失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


# ===================== 雪球交易排行榜接口 =====================


@deal_router.get("", response_model=StockHotDealXueqiuResponse)
async def get_stock_hot_deal(
    limit: int = Query(50, ge=1, le=200, description="返回条数"),
    current_user: dict = Depends(get_current_user),
):
    """
    查询雪球交易排行榜

    Args:
        limit: 返回条数 (1-200)

    Returns:
        StockHotDealXueqiuResponse: 交易排行列表
    """
    try:
        service = get_stock_hot_deal_xueqiu_service()
        items = await service.query_all(limit=limit)

        return StockHotDealXueqiuResponse(
            success=True,
            data=[StockHotDealXueqiuItem(**item) for item in items],
            total=len(items),
            message="获取交易排行榜成功",
        )
    except Exception as e:
        logger.error(f"❌ 查询交易排行榜失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


# ===================== 东方财富人气榜接口 =====================


@rank_router.get("", response_model=StockHotRankEMResponse)
async def get_stock_hot_rank(
    limit: int = Query(50, ge=1, le=200, description="返回条数"),
    current_user: dict = Depends(get_current_user),
):
    """
    查询东方财富人气榜

    Args:
        limit: 返回条数 (1-200)

    Returns:
        StockHotRankEMResponse: 人气排行列表
    """
    try:
        service = get_stock_hot_rank_em_service()
        items = await service.query_all(limit=limit)

        return StockHotRankEMResponse(
            success=True,
            data=[StockHotRankEMItem(**item) for item in items],
            total=len(items),
            message="获取人气榜成功",
        )
    except Exception as e:
        logger.error(f"❌ 查询人气榜失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")
