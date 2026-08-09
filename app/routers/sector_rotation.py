"""
板块轮动监测 API
提供板块成交额占比排名、趋势曲线、双维度势能模型等数据。

Endpoints:
    基础:
    - GET /api/sector-rotation/industries   -> 获取行业列表
    - GET /api/sector-rotation/ranking      -> 获取板块成交额占比排名
    - GET /api/sector-rotation/trend        -> 获取单个板块的趋势曲线
    - GET /api/sector-rotation/multi-trend  -> 批量获取多个板块的趋势曲线

    双维度势能模型:
    - GET /api/sector-rotation/momentum-ranking  -> 轮动势能排名
    - GET /api/sector-rotation/momentum-trend    -> 单个板块势能趋势曲线
    - GET /api/sector-rotation/moneyflow/sync    -> 手动触发资金流数据同步

统一响应格式: {success, data, message, timestamp}
"""
import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query

from app.routers.auth_db import get_current_user
from app.core.response import ok
from app.services.sector_rotation_service import (
    get_sector_rotation_service,
)
from app.services.sector_moneyflow_service import (
    get_sector_moneyflow_service,
)
from app.services.sector_momentum_service import (
    get_sector_momentum_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sector-rotation", tags=["板块轮动"])


# ==================== 基础接口 ====================


@router.get("/industries")
async def get_industries(
    source: str = Query("tushare", description="数据源"),
    _user=Depends(get_current_user),
):
    """获取所有可用的行业列表"""
    try:
        service = get_sector_rotation_service()
        industries = await service.get_industry_list(source=source)
        return ok(data={
            "industries": industries,
            "total": len(industries),
        })
    except Exception as e:
        logger.exception(f"获取行业列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取行业列表失败: {str(e)}")


@router.get("/stocks")
async def get_sector_stocks(
    industry: str = Query(..., description="行业名称（来自 /industries 接口）"),
    page: int = Query(1, ge=1, description="页码（从1开始）"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    source: str = Query("tushare", description="数据源"),
    _user=Depends(get_current_user),
):
    """获取指定行业下的股票列表（分页）

    行业名称取值来自 GET /api/sector-rotation/industries 接口。
    数据基于 stock_basic_info（优先指定数据源，无数据时自动降级）。

    Args:
        industry: 行业名称（如 "半导体"）
        page: 页码（默认 1）
        page_size: 每页数量（默认 20，最大 100）
        source: 基础信息数据源

    Returns:
        {
            "items": [{"symbol", "name", "industry", "source"}, ...],
            "total": 行业股票总数,
            "page": 当前页码,
            "page_size": 每页数量,
            "total_pages": 总页数,
        }
    """
    try:
        service = get_sector_rotation_service()
        result = await service.get_stocks_by_industry(
            industry=industry,
            source=source,
            page=page,
            page_size=page_size,
        )
        return ok(data=result)
    except Exception as e:
        logger.exception(f"获取行业股票列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取行业股票列表失败: {str(e)}")


@router.get("/ranking")
async def get_sector_ranking(
    top_n: Optional[int] = Query(None, ge=1, le=100, description="返回前N个板块"),
    _user=Depends(get_current_user),
):
    """获取板块成交额占比实时排名

    板块成交额占比 = (该板块内所有个股的当日成交额之和 / 全市场所有股票的当日成交额之和) × 100%

    返回按占比降序排列的板块列表。
    """
    try:
        service = get_sector_rotation_service()
        ranking = await service.get_industry_turnover_ranking(top_n=top_n)
        return ok(data={
            "ranking": ranking,
            "total": len(ranking),
            "trade_date": None,  # 实时数据来自 market_quotes
            "data_source": "market_quotes",
        })
    except Exception as e:
        logger.exception(f"获取板块排名失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取板块排名失败: {str(e)}")


@router.get("/trend")
async def get_sector_trend(
    industry: str = Query(..., description="行业名称"),
    days: int = Query(20, ge=5, le=120, description="回溯天数"),
    source: str = Query("tushare", description="数据源"),
    _user=Depends(get_current_user),
):
    """获取指定板块的历史成交额占比趋势曲线

    基于 stock_daily_quotes 日线数据，计算每日的板块成交额占比历史变化。

    Args:
        industry: 行业名称（如 "半导体", "银行", "医药生物"）
        days: 回溯天数（默认 20 个交易日）
        source: 基础信息数据源
    """
    try:
        service = get_sector_rotation_service()
        trend = await service.get_industry_turnover_trend(
            industry=industry,
            days=days,
            source=source,
        )
        return ok(data={
            "industry": industry,
            "days": days,
            "trend": trend,
            "data_points": len(trend),
        })
    except Exception as e:
        logger.exception(f"获取板块趋势失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取板块趋势失败: {str(e)}")


@router.get("/multi-trend")
async def get_multi_sector_trend(
    industries: str = Query(..., description="行业名称列表，用逗号分隔（如 '半导体,银行,医药生物'）"),
    days: int = Query(20, ge=5, le=120, description="回溯天数"),
    source: str = Query("tushare", description="数据源"),
    _user=Depends(get_current_user),
):
    """批量获取多个板块的历史成交额占比趋势

    用于对比多个板块的成交额占比变化。

    Args:
        industries: 用逗号分隔的行业名称列表
        days: 回溯天数
        source: 数据源
    """
    try:
        industry_list = [ind.strip() for ind in industries.split(",") if ind.strip()]
        if not industry_list:
            return ok(data={
                "industries": [],
                "trends": {},
                "message": "未提供有效的行业名称",
            })

        if len(industry_list) > 20:
            raise HTTPException(status_code=400, detail="一次最多查询 20 个行业")

        service = get_sector_rotation_service()
        trends = await service.get_multi_industry_trend(
            industries=industry_list,
            days=days,
            source=source,
        )
        return ok(data={
            "industries": industry_list,
            "days": days,
            "trends": trends,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"批量获取板块趋势失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量获取板块趋势失败: {str(e)}")


# ==================== 双维度轮动势能模型 ====================

@router.get("/momentum-ranking")
async def get_momentum_ranking(
    top_n: Optional[int] = Query(None, ge=1, le=100, description="返回前N个板块"),
    days: int = Query(10, ge=5, le=60, description="趋势回溯天数"),
    weight_flow: float = Query(0.5, ge=0, le=1, description="资金流维度权重"),
    weight_turnover: float = Query(0.5, ge=0, le=1, description="成交额占比维度权重"),
    refresh: bool = Query(False, description="强制同步重算（默认始终优先返回缓存，过期后台异步刷新）"),
):
    """【双维度板块轮动势能排名】

    结合「主力资金流强度」和「板块成交额占比趋势」两个维度，
    识别板块轮动中的真上涨、假上涨、低位切换、高位出逃信号。

    性能说明:
        - 结果带进程内 TTL 缓存（默认 3 分钟）：**始终优先返回缓存**，即使缓存已过期也立即返回
          旧数据，并在后台异步重算（stale-while-revalidate），请求从不阻塞
        - 数据库聚合从"每个请求一次"降到"每 3 分钟最多一次"，前端秒开
        - refresh=true 可强制同步重算（一般无需使用）

    维度说明:
        - flow_score:     主力资金流强度得分 [0,100]，正值为主力净流入
        - turnover_score: 成交额占比趋势得分 [0,100]，反映市场关注度变化
        - composite_score: 综合势能得分 = weight_flow×flow_score + weight_turnover×turnover_score

    信号分类:
        - 真上涨:   主力资金流入 + 占比提升，趋势健康
        - 假上涨:   主力资金流出 + 占比提升，放量出货信号
        - 低位切换: 低位放量 + 主力建仓，可能为新主线
        - 高位出逃: 高占比回落 + 主力出逃，老主线退潮
        - 观望:     资金流出 + 占比下降，量价齐跌
        - 中性:     无明显信号

    排序规则:
        - 先按信号分类排序：真上涨 > 低位切换 > 假上涨 > 中性 > 观望 > 高位出逃（真上涨排最前）
        - 同类信号内再按 composite_score 综合评分降序
        - rank 为排序后的名次

    数据时效:
        - 每条记录含 data_date（YYYYMMDD）：该行业评分基于的最新交易日
        - 有资金流数据 → 最新资金流交易日；无资金流数据（降级）→ 成交额占比趋势最新交易日
        - 响应顶层含 cache 字段：{hit, stale, cached_at, age_seconds, ttl_seconds}
          stale=true 表示本次返回的是已过期的旧缓存（后台正在异步重算）

    Args:
        top_n: 返回前N个板块（默认全部）
        days: 趋势回溯天数（默认10个交易日）
        weight_flow: 资金流权重 (0-1, 默认0.5)
        weight_turnover: 成交额占比权重 (0-1, 默认0.5)
    """
    try:
        service = get_sector_momentum_service()
        ranking = await service.get_momentum_ranking(
            top_n=top_n,
            days=days,
            weight_flow=weight_flow,
            weight_turnover=weight_turnover,
            refresh=refresh,
        )

        # 统计各信号数量
        signal_counts = {}
        for item in ranking:
            sig = item["signal"]
            signal_counts[sig] = signal_counts.get(sig, 0) + 1

        return ok(data={
            "ranking": ranking,
            "total": len(ranking),
            "signal_summary": signal_counts,
            "params": {
                "days": days,
                "weight_flow": weight_flow,
                "weight_turnover": weight_turnover,
            },
            "cache": service.get_ranking_cache_info(),
        })
    except Exception as e:
        logger.exception(f"获取轮动势能排名失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取轮动势能排名失败: {str(e)}")


@router.get("/momentum-trend")
async def get_momentum_trend(
    industry: str = Query(..., description="行业名称"),
    days: int = Query(20, ge=5, le=120, description="回溯天数"),
    _user=Depends(get_current_user),
):
    """获取指定板块的轮动势能趋势曲线

    返回该板块的历史势能评分变化，包括资金流得分、成交额占比得分、综合得分。

    Args:
        industry: 行业名称（如 "半导体"）
        days: 回溯天数（默认20个交易日）
    """
    try:
        service = get_sector_momentum_service()
        trend = await service.get_momentum_trend(
            industry=industry,
            days=days,
        )
        return ok(data={
            "industry": industry,
            "days": days,
            "trend": trend,
            "data_points": len(trend),
        })
    except Exception as e:
        logger.exception(f"获取轮动势能趋势失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取轮动势能趋势失败: {str(e)}")


@router.post("/moneyflow/sync")
async def sync_moneyflow_data(
    trade_date: Optional[str] = Query(None, description="起始交易日期(YYYYMMDD)，默认最新"),
    force: bool = Query(False, description="是否强制覆盖已有数据"),
    days_back: int = Query(0, ge=0, le=60, description="批量回填最近 N 个交易日的数据（0=仅同步单日）"),
    _user=Depends(get_current_user),
):
    """手动触发 Tushare 资金流向数据同步

    从 Tushare moneyflow 接口获取个股资金流向数据并存入 MongoDB。

    两种模式:
        - 单日模式 (days_back=0): 仅同步指定 trade_date（默认当天）的数据
        - 批量模式 (days_back>0): 从 trade_date 开始向前回填 N 个交易日的数据

    Args:
        trade_date: 交易日期 YYYYMMDD，不指定则使用当前日期
        force: 是否覆盖已有数据
        days_back: 回填最近 N 个交易日的数据（0=仅同步单日）
    """
    try:
        service = get_sector_moneyflow_service()
        count = await service.fetch_and_store_moneyflow(
            trade_date=trade_date,
            force=force,
            days_back=days_back,
        )
        mode = "batch" if days_back > 0 else "single"
        return ok(data={
            "saved_count": count,
            "trade_date": trade_date or "auto",
            "days_back": days_back,
            "mode": mode,
        }, message=f"[{mode}] 成功同步 {count} 条资金流向记录")
    except Exception as e:
        logger.exception(f"同步资金流向数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"同步资金流向数据失败: {str(e)}")


@router.get("/moneyflow/dates")
async def get_moneyflow_dates(
    limit: int = Query(60, ge=1, le=365, description="返回最近N个交易日"),
):
    """获取已有资金流数据的交易日列表"""
    try:
        service = get_sector_moneyflow_service()
        dates = await service.get_available_dates(limit=limit)
        return ok(data={
            "dates": dates,
            "total": len(dates),
        })
    except Exception as e:
        logger.exception(f"获取资金流交易日列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取资金流交易日列表失败: {str(e)}")
