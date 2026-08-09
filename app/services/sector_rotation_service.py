"""
板块轮动监测服务
计算板块成交额占比，提供板块排名、趋势曲线等数据。

核心公式:
    板块成交额占比 = (该板块内所有个股的当日成交额之和 / 全市场所有股票的当日成交额之和) × 100%

数据源:
    - stock_basic_info: 获取个股所属行业
    - market_quotes: 获取实时行情（当日成交额）
    - stock_daily_quotes: 获取历史日线数据（历史成交额）
"""
import logging
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from app.core.database import get_mongo_db

logger = logging.getLogger(__name__)


def _safe_float(value, default: float = 0.0) -> float:
    """安全地转换为 float，处理 None/空字符串/NaN/Infinity

    Returns:
        有效的 float 值（不会返回 NaN 或 Infinity）
    """
    if value is None:
        return default
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (ValueError, TypeError):
        return default


def _safe_round(value: float, ndigits: int = 2) -> float:
    """安全地 round，确保不会产生 NaN/Infinity"""
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return round(value, ndigits)

# 行业聚合分析结果
SectorTurnoverItem = Dict[str, object]
"""
{
    "industry": str,          # 行业名称
    "total_amount": float,     # 该行业总成交额（元）
    "market_amount": float,    # 全市场总成交额（元）
    "ratio": float,            # 成交额占比 (%)
    "stock_count": int,        # 该行业股票数量
    "rank": int,               # 排名
    "avg_amount": float,       # 平均成交额（元）
}
"""


class SectorRotationService:
    """板块轮动监测服务"""

    # 板块名称备选字段
    _INDUSTRY_FIELDS = ["industry", "sector", "board"]

    async def get_industry_list(self, source: str = "tushare") -> List[str]:
        """获取所有可用的行业列表

        Args:
            source: 数据源

        Returns:
            去重后的行业列表
        """
        db = get_mongo_db()
        pipeline = [
            {"$match": {"source": source}},
            {"$group": {"_id": "$industry"}},
            {"$match": {"_id": {"$ne": None, "$ne": ""}}},
            {"$sort": {"_id": 1}},
        ]
        cursor = db["stock_basic_info"].aggregate(pipeline)
        results = await cursor.to_list(length=None)
        return [r["_id"] for r in results if r.get("_id")]

    async def get_stocks_by_industry(
        self,
        industry: str,
        source: str = "tushare",
        page: int = 1,
        page_size: int = 20,
    ) -> Dict:
        """获取指定行业下的股票列表（分页）

        Args:
            industry: 行业名称（来自 get_industry_list 接口）
            source: 数据源（优先该数据源，无数据时自动降级）
            page: 页码（从 1 开始）
            page_size: 每页数量

        Returns:
            {
                "items": [{"symbol", "name", "industry", "source"}, ...],
                "total": int,       # 该行业股票总数
                "page": int,
                "page_size": int,
                "total_pages": int,
            }
        """
        db = get_mongo_db()
        coll = db["stock_basic_info"]

        # 优先指定数据源，无数据时降级（不带 source 条件）
        query: Dict = {"industry": industry, "source": source}
        total = await coll.count_documents(query)
        if total == 0:
            query = {"industry": industry}
            total = await coll.count_documents(query)

        skip = (page - 1) * page_size
        cursor = coll.find(
            query,
            {"symbol": 1, "code": 1, "name": 1, "source": 1, "_id": 0},
        ).sort("symbol", 1).skip(skip).limit(page_size)

        items = []
        async for doc in cursor:
            items.append({
                "symbol": doc.get("symbol") or doc.get("code", ""),
                "name": doc.get("name", ""),
                "industry": industry,
                "source": doc.get("source", ""),
            })

        total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
        logger.info(f"行业 {industry} 股票列表: 共 {total} 只, 第 {page}/{total_pages} 页, 返回 {len(items)} 条")
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    async def get_industry_turnover_ranking(
        self, top_n: Optional[int] = None
    ) -> List[SectorTurnoverItem]:
        """计算当前交易日各板块成交额占比排名

        流程:
            1. 从 stock_basic_info 获取所有股票及其所属行业
            2. 从 market_quotes 获取所有股票的当日成交额
            3. 按行业汇总成交额，计算占比
            4. 按占比降序排列

        Args:
            top_n: 限制返回前 N 个板块，None 返回全部

        Returns:
            按占比降序排列的板块成交额列表
        """
        db = get_mongo_db()

        # 1. 获取所有股票-行业映射（优先使用 tushare 数据源）
        industry_map = await self._get_stock_industry_map(db)
        if not industry_map:
            logger.warning("未获取到股票行业映射数据")
            return []

        # 2. 获取所有股票实时行情（成交额）
        quotes_map = await self._get_all_quotes_amount(db)

        if not quotes_map:
            logger.warning("未获取到实时行情数据")
            return []

        # 3. 按行业汇总成交额
        sector_amounts: Dict[str, float] = defaultdict(float)
        sector_stock_count: Dict[str, int] = defaultdict(int)
        market_total_amount = 0.0

        for code, amount in quotes_map.items():
            amt = _safe_float(amount)
            if amt <= 0:
                continue

            industry = industry_map.get(code)
            if not industry:
                continue

            sector_amounts[industry] += amt
            sector_stock_count[industry] += 1
            market_total_amount += amt

        if market_total_amount <= 0:
            logger.warning("全市场总成交额为 0，无法计算占比")
            return []

        # 4. 计算占比并排序
        results = []
        for industry, total_amount in sector_amounts.items():
            ratio = _safe_round((total_amount / market_total_amount) * 100, 4)
            avg_amount = _safe_round(total_amount / sector_stock_count[industry], 2) if sector_stock_count[industry] > 0 else 0
            results.append({
                "industry": industry,
                "total_amount": _safe_round(total_amount, 2),
                "market_amount": _safe_round(market_total_amount, 2),
                "ratio": ratio,
                "stock_count": sector_stock_count[industry],
                "rank": 0,  # 排序后赋值
                "avg_amount": avg_amount,
            })

        # 按占比降序排列
        results.sort(key=lambda x: x["ratio"], reverse=True)

        # 填充排名
        for i, item in enumerate(results):
            item["rank"] = i + 1

        if top_n and top_n > 0:
            results = results[:top_n]

        return results

    async def get_industry_turnover_trend(
        self, industry: str, days: int = 20, source: str = "tushare"
    ) -> List[Dict]:
        """获取指定板块的历史成交额占比趋势

        流程:
            1. 获取该板块内所有股票代码
            2. 从 stock_daily_quotes 获取每只股票的历史日线数据（日期 + 成交额）
            3. 同时获取全市场历史日线成交额
            4. 按日期计算每日占比

        Args:
            industry: 行业名称
            days: 回溯天数
            source: 数据源

        Returns:
            按日期升序的趋势列表:
            [
                {
                    "trade_date": "2026-07-16",
                    "sector_amount": float,   # 该板块当日成交额
                    "market_amount": float,   # 全市场当日成交额
                    "ratio": float,            # 占比 (%)
                },
                ...
            ]
        """
        db = get_mongo_db()

        # 1. 获取该行业所有股票代码
        codes = await self._get_stocks_by_industry(db, industry, source)
        if not codes:
            logger.warning(f"行业 {industry} 下没有找到股票")
            return []

        logger.info(f"行业 {industry} 包含 {len(codes)} 只股票")

        # 2. 计算日期范围
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y-%m-%d")

        # 3. 从 stock_daily_quotes 获取历史日线数据（行业板块）
        # ⚠️ amount 字段在 DB 中是字符串，需安全转换；空/无效值转 0 避免 NaN
        _safe_to_double = {
            "$convert": {
                "input": "$amount",
                "to": "double",
                "onError": 0,
                "onNull": 0,
            }
        }
        # 聚合管道：按日期分组，计算该板块每日总成交额
        sector_pipeline = [
            {"$match": {
                "symbol": {"$in": codes},
                "trade_date": {"$gte": start_date, "$lte": end_date},
                "period": "daily",
            }},
            {"$group": {
                "_id": "$trade_date",
                "sector_amount": {"$sum": _safe_to_double},
            }},
            {"$sort": {"_id": 1}},
        ]
        logger.info(f"板块成交额聚合 pipeline: 股票数={len(codes)}, 日期范围={start_date}~{end_date}")

        # 4. 获取全市场每日总成交额
        market_pipeline = [
            {"$match": {
                "trade_date": {"$gte": start_date, "$lte": end_date},
                "period": "daily",
            }},
            {"$group": {
                "_id": "$trade_date",
                "market_amount": {"$sum": _safe_to_double},
            }},
            {"$sort": {"_id": 1}},
        ]

        # 并行查询
        import asyncio
        sector_cursor = db["stock_daily_quotes"].aggregate(
            sector_pipeline,
            allowDiskUse=True,
        )
        market_cursor = db["stock_daily_quotes"].aggregate(
            market_pipeline,
            allowDiskUse=True,
        )
        sector_results, market_results = await asyncio.gather(
            sector_cursor.to_list(length=None),
            market_cursor.to_list(length=None),
        )

        logger.info(f"板块 {industry} 返回 {len(sector_results)} 天数据，全市场返回 {len(market_results)} 天数据")

        # 5. 构建 market_amount 的查找字典
        market_map: Dict[str, float] = {}
        for item in market_results:
            date_key = item["_id"]
            market_map[date_key] = _safe_float(item["market_amount"])

        # 6. 合并数据，计算占比
        trend = []
        sector_map: Dict[str, float] = {}
        for item in sector_results:
            sector_map[item["_id"]] = _safe_float(item["sector_amount"])

        all_dates = sorted(set(list(sector_map.keys()) + list(market_map.keys())))

        for date_key in all_dates:
            sector_amt = _safe_float(sector_map.get(date_key, 0))
            market_amt = _safe_float(market_map.get(date_key, 0))
            if market_amt > 0:
                ratio = _safe_round((sector_amt / market_amt) * 100, 4)
            else:
                ratio = 0.0

            trend.append({
                "trade_date": date_key,
                "sector_amount": _safe_round(sector_amt, 2),
                "market_amount": _safe_round(market_amt, 2),
                "ratio": ratio,
            })

        # 限制返回天数
        if len(trend) > days:
            trend = trend[-days:]

        return trend

    async def get_multi_industry_trend(
        self, industries: List[str], days: int = 20, source: str = "tushare"
    ) -> Dict[str, List[Dict]]:
        """批量获取多个板块的历史成交额占比趋势

        Args:
            industries: 行业名称列表
            days: 回溯天数
            source: 数据源

        Returns:
            { industry_name: [trend_item, ...], ... }
        """
        import asyncio

        async def fetch_single(ind: str) -> Tuple[str, List[Dict]]:
            try:
                trend = await self.get_industry_turnover_trend(ind, days, source)
                return ind, trend
            except Exception as e:
                logger.error(f"获取行业 {ind} 趋势失败: {e}")
                return ind, []

        tasks = [fetch_single(ind) for ind in industries]
        results = await asyncio.gather(*tasks)

        return {ind: trend for ind, trend in results}

    async def _get_stock_industry_map(
        self, db, source: str = "tushare"
    ) -> Dict[str, str]:
        """获取股票代码到行业的映射

        Returns:
            { "600160": "化工", "000066": "计算机", ... }
        """
        # 优先从 source 数据源获取，如果没有再尝试其他
        cursor = db["stock_basic_info"].find(
            {"source": source, "industry": {"$ne": None, "$ne": ""}},
            {"symbol": 1, "code": 1, "industry": 1, "_id": 0},
        )
        docs = await cursor.to_list(length=None)

        if not docs:
            # 降级：不带 source 条件
            cursor = db["stock_basic_info"].find(
                {"industry": {"$ne": None, "$ne": ""}},
                {"symbol": 1, "code": 1, "industry": 1, "_id": 0},
            )
            docs = await cursor.to_list(length=None)

        industry_map: Dict[str, str] = {}
        for doc in docs:
            code = doc.get("symbol") or doc.get("code", "")
            ind = doc.get("industry", "")
            if code and ind:
                industry_map[code] = ind

        logger.info(f"获取到 {len(industry_map)} 条股票-行业映射")
        return industry_map

    async def _get_all_quotes_amount(self, db) -> Dict[str, Optional[float]]:
        """从 market_quotes 获取所有股票的当日成交额

        Returns:
            { "600160": 2378692380.0, ... }
        """
        cursor = db["market_quotes"].find(
            {},
            {"symbol": 1, "code": 1, "amount": 1, "_id": 0},
        )
        docs = await cursor.to_list(length=None)

        quotes_map: Dict[str, Optional[float]] = {}
        for doc in docs:
            code = doc.get("symbol") or doc.get("code", "")
            amount = doc.get("amount")
            if code:
                val = _safe_float(amount)
                quotes_map[code] = val if val > 0 else None

        logger.info(f"获取到 {len(quotes_map)} 条实时行情成交额")
        return quotes_map

    async def _get_stocks_by_industry(
        self, db, industry: str, source: str = "tushare"
    ) -> List[str]:
        """获取指定行业下的所有股票代码

        Args:
            db: 数据库连接
            industry: 行业名称
            source: 数据源

        Returns:
            股票代码列表
        """
        cursor = db["stock_basic_info"].find(
            {"industry": industry, "source": source},
            {"symbol": 1, "code": 1, "_id": 0},
        )
        docs = await cursor.to_list(length=None)

        if not docs:
            # 降级：不带 source 条件
            cursor = db["stock_basic_info"].find(
                {"industry": industry},
                {"symbol": 1, "code": 1, "_id": 0},
            )
            docs = await cursor.to_list(length=None)

        codes = []
        for doc in docs:
            code = doc.get("symbol") or doc.get("code", "")
            if code:
                codes.append(code)

        return codes


# 全局单例
_sector_rotation_service: Optional[SectorRotationService] = None


def get_sector_rotation_service() -> SectorRotationService:
    """获取板块轮动监测服务实例"""
    global _sector_rotation_service
    if _sector_rotation_service is None:
        _sector_rotation_service = SectorRotationService()
    return _sector_rotation_service
