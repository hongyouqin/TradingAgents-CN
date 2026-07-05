"""
数据概览服务（预约披露日 + 雪球热度 + 交易排行榜 + 东方财富人气榜）

功能：
1. 通过 AKShare 获取 A 股预约披露日历
2. 通过 AKShare 获取雪球股票热度（最热门 / 本周新增）
3. 通过 AKShare 获取雪球交易排行榜
4. 通过 AKShare 获取东方财富人气榜
5. 全量替换写入 MongoDB collections
6. 提供查询接口和定时同步入口
"""
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import pandas as pd
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.database import get_mongo_db
from app.models.stock_models import DisclosureCalendarItem

logger = logging.getLogger(__name__)

# AKShare 接口中文列名
COL_STOCK_CODE = "股票代码"
COL_STOCK_NAME = "股票简称"
COL_FIRST_SCHEDULE = "首次预约时间"
COL_FIRST_CHANGE = "一次变更日期"
COL_SECOND_CHANGE = "二次变更日期"
COL_THIRD_CHANGE = "三次变更日期"
COL_ACTUAL_DISCLOSURE = "实际披露时间"

# 按优先级排列的日期列（从高到低）
PRIORITY_DATE_COLS = [
    COL_THIRD_CHANGE,
    COL_SECOND_CHANGE,
    COL_FIRST_CHANGE,
    COL_FIRST_SCHEDULE,
]

MONGO_COLLECTION = "disclosure_calendar"


class DisclosureCalendarService:
    """预约披露日服务"""

    def __init__(self):
        self._db: Optional[AsyncIOMotorDatabase] = None

    async def _get_db(self) -> AsyncIOMotorDatabase:
        if self._db is None:
            self._db = get_mongo_db()
        return self._db

    # ---- 数据获取 ----

    @staticmethod
    def _get_quarter_end_dates() -> list[str]:
        """
        获取最近两个已结束的季度末日期（优先最近已完成季度）

        规则：取当前日期之前最近的两个季度末，
        因为 AKShare 接口需要传入季末日期如 20260630、20261231。

        Returns:
            ["YYYYMMDD", "YYYYMMDD"] 从近到远
        """
        from datetime import date

        today = date.today()
        results = []

        # 季度末: 03-31, 06-30, 09-30, 12-31
        quarter_ends = [
            (3, 31),
            (6, 30),
            (9, 30),
            (12, 31),
        ]

        # 按年月降序排列所有可能的季度末
        candidates = []
        for y in range(today.year, today.year - 2, -1):
            for m, d in reversed(quarter_ends):
                qe = date(y, m, d)
                if qe <= today:
                    candidates.append(qe)

        # 去重并取最近两个
        seen = set()
        for qe in candidates:
            key = qe.isoformat()
            if key not in seen:
                seen.add(key)
                results.append(qe.strftime("%Y%m%d"))
                if len(results) >= 2:
                    break

        return results

    async def fetch_from_akshare(self, data_date: str) -> List[Dict[str, Any]]:
        """
        从 AKShare 获取预约披露日历数据

        Args:
            data_date: 财报数据截止日期，如 "20260630"

        Returns:
            处理后的记录列表（包含最新预约披露日计算）；若获取失败返回空列表
        """
        try:
            # AKShare 接口在非异步线程中阻塞，用 run_in_executor 避免阻塞事件循环
            import asyncio
            import akshare as ak

            def _fetch():
                try:
                    return ak.stock_yysj_em(date=data_date)
                except Exception as inner_e:
                    logger.warning(f"⚠️ AKShare 内部请求失败: {inner_e}")
                    return None

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _fetch)

            # AKShare 在无效日期时可能返回 dict 或 None，统一转 None 处理
            if result is None:
                logger.warning(f"⚠️ AKShare 返回空, data_date={data_date}")
                return []

            # 如果是 dict 且有 result 字段但为 None（AKShare 内部 JSON 解析结果）
            if isinstance(result, dict):
                inner = result.get("result")
                if inner is None:
                    logger.warning(f"⚠️ AKShare 返回 dict 但 result 为 None, data_date={data_date}")
                    return []
                # 尝试从 dict 构造 DataFrame（pd 已在文件顶部导入）
                df = pd.DataFrame(inner)
            else:
                df = result

            if df is None or (hasattr(df, "empty") and df.empty):
                logger.warning(f"⚠️ AKShare 返回空 DataFrame, data_date={data_date}")
                return []

            # 批量解析日期列
            for col in PRIORITY_DATE_COLS:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors="coerce")

            if COL_ACTUAL_DISCLOSURE in df.columns:
                df[COL_ACTUAL_DISCLOSURE] = pd.to_datetime(
                    df[COL_ACTUAL_DISCLOSURE], errors="coerce"
                )

            # 计算最新预约披露日
            def _get_latest_date(row):
                for col in PRIORITY_DATE_COLS:
                    val = row.get(col)
                    if pd.notna(val):
                        return val
                return pd.NaT

            df["_latest_date"] = df.apply(_get_latest_date, axis=1)
            df = df.dropna(subset=["_latest_date"])

            now_utc = datetime.utcnow()

            records = []
            for _, row in df.iterrows():
                first_schedule = row.get(COL_FIRST_SCHEDULE)
                first_change = row.get(COL_FIRST_CHANGE)
                second_change = row.get(COL_SECOND_CHANGE)
                third_change = row.get(COL_THIRD_CHANGE)
                actual = row.get(COL_ACTUAL_DISCLOSURE)
                latest = row["_latest_date"]

                record = {
                    "stock_code": str(row.get(COL_STOCK_CODE, "")),
                    "stock_name": str(row.get(COL_STOCK_NAME, "")),
                    "first_schedule": first_schedule.to_pydatetime() if pd.notna(first_schedule) else None,
                    "first_change": first_change.to_pydatetime() if pd.notna(first_change) else None,
                    "second_change": second_change.to_pydatetime() if pd.notna(second_change) else None,
                    "third_change": third_change.to_pydatetime() if pd.notna(third_change) else None,
                    "actual_disclosure": actual.to_pydatetime() if pd.notna(actual) else None,
                    "latest_date": latest.to_pydatetime(),
                    "data_date": data_date,
                    "updated_at": now_utc,
                }
                records.append(record)

            logger.info(
                f"✅ AKShare 获取预约披露日历成功: "
                f"data_date={data_date}, total={len(df)}, valid={len(records)}"
            )
            return records

        except Exception as e:
            logger.error(f"❌ AKShare 获取预约披露日历失败: {e}", exc_info=True)
            raise

    # ---- 数据库同步 ----

    async def _ensure_indexes(self):
        """确保集合索引存在"""
        db = await self._get_db()
        col = db[MONGO_COLLECTION]

        await col.create_index("stock_code", unique=True, name="dc_stock_code")
        await col.create_index([("latest_date", 1)], name="dc_latest_date")
        await col.create_index([("data_date", 1)], name="dc_data_date")

    async def sync_disclosure_calendar(self, data_date: Optional[str] = None) -> int:
        """
        全量同步预约披露日数据

        策略：先删除该 data_date 的所有旧数据，再批量插入新数据（全量替换）

        Args:
            data_date: 财报数据截止日期，如 "20260630"；默认自动尝试最近两个季末日

        Returns:
            同步的记录数
        """
        db = await self._get_db()
        await self._ensure_indexes()

        # 确定要尝试的 data_date 列表
        data_dates_to_try: list[str] = []
        if data_date is not None:
            data_dates_to_try = [data_date]
        else:
            data_dates_to_try = self._get_quarter_end_dates()
            logger.info(f"📅 自动猜测季末日期: {data_dates_to_try}")

        # 逐个尝试，直到有数据
        records: List[Dict[str, Any]] = []
        used_data_date: Optional[str] = None
        for dd in data_dates_to_try:
            try:
                records = await self.fetch_from_akshare(dd)
                if records:
                    used_data_date = dd
                    break
            except Exception as e:
                logger.warning(f"⚠️ 尝试 data_date={dd} 失败: {e}")
                continue

        if not records or used_data_date is None:
            logger.warning(f"⚠️ 所有季末日期均无数据: {data_dates_to_try}")
            return 0

        # 2. 全量替换：删除该 data_date 的旧数据
        col = db[MONGO_COLLECTION]
        delete_result = await col.delete_many({"data_date": used_data_date})
        logger.info(f"🗑️ 删除旧数据: data_date={used_data_date}, deleted={delete_result.deleted_count}")

        # 3. 批量插入新数据
        if records:
            insert_result = await col.insert_many(records, ordered=False)
            logger.info(
                f"✅ 预约披露日同步完成: data_date={used_data_date}, "
                f"inserted={len(insert_result.inserted_ids)}"
            )
            return len(insert_result.inserted_ids)

        return 0

    # ---- 查询接口 ----

    async def query_by_stock_code(
        self, stock_code: str
    ) -> Optional[DisclosureCalendarItem]:
        """
        按股票代码查询最新披露日记录

        Args:
            stock_code: 6位股票代码

        Returns:
            披露日记录，未找到返回 None
        """
        db = await self._get_db()
        col = db[MONGO_COLLECTION]

        doc = await col.find_one({"stock_code": stock_code})
        if doc is None:
            return None

        doc.pop("_id", None)
        return DisclosureCalendarItem(**doc)

    async def query_list(
        self,
        page: int = 1,
        page_size: int = 20,
        data_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        按最新披露日排序的分页查询（越近越靠前）

        Args:
            page: 页码，从1开始
            page_size: 每页条数
            data_date: 可选，按数据日期筛选

        Returns:
            { "items": [...], "total": int, "page": int, "page_size": int }
        """
        db = await self._get_db()
        col = db[MONGO_COLLECTION]

        # 构建查询条件
        query_filter: Dict[str, Any] = {}
        if data_date:
            query_filter["data_date"] = data_date

        # 只查未实际披露的记录（预约日在今天之后），更贴近用户需求
        from datetime import date
        query_filter["actual_disclosure"] = None

        total = await col.count_documents(query_filter)

        cursor = (
            col.find(query_filter, {"_id": 0})
            .sort("latest_date", 1)  # 升序 = 披露日越近越靠前
            .skip((page - 1) * page_size)
            .limit(page_size)
        )

        items = []
        async for doc in cursor:
            items.append(DisclosureCalendarItem(**doc))

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }


# ===================== 股票热度（雪球）服务 =====================

STOCK_HOT_COLLECTION = "stock_hot_xq"
STOCK_HOT_CATEGORIES = ["最热门", "本周新增"]


class StockHotXueqiuService:
    """雪球股票热度服务（最热门 / 本周新增）"""

    def __init__(self):
        self._db: Optional[AsyncIOMotorDatabase] = None

    async def _get_db(self) -> AsyncIOMotorDatabase:
        if self._db is None:
            self._db = get_mongo_db()
        return self._db

    # ---- 数据获取 ----

    async def fetch_hot_data(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        从 AKShare 获取雪球股票热度数据（两个分类）

        Returns:
            {"最热门": [...], "本周新增": [...]} 若失败返回空 dict
        """
        import asyncio
        import akshare as ak

        result: Dict[str, List[Dict[str, Any]]] = {}

        for category in STOCK_HOT_CATEGORIES:
            try:
                def _fetch(cat=category):
                    return ak.stock_hot_follow_xq(symbol=cat)

                loop = asyncio.get_event_loop()
                df = await loop.run_in_executor(None, _fetch)

                if df is None or (hasattr(df, "empty") and df.empty):
                    logger.warning(f"雪球热度[{category}] 返回空数据")
                    continue

                records = []
                for rank, (_, row) in enumerate(df.iterrows(), start=1):
                    records.append({
                        "stock_code": str(row.iloc[0]) if len(row) > 0 else "",
                        "stock_name": str(row.iloc[1]) if len(row) > 1 else "",
                        "followers": int(row.iloc[2]) if len(row) > 2 and pd.notna(row.iloc[2]) else 0,
                        "current_price": float(row.iloc[3]) if len(row) > 3 and pd.notna(row.iloc[3]) else None,
                        "category": category,
                        "rank": rank,
                    })

                result[category] = records
                logger.info(f"雪球热度[{category}] 获取成功: {len(records)} 条")

            except Exception as e:
                logger.error(f"雪球热度[{category}] 获取失败: {e}", exc_info=True)

        return result

    # ---- 数据库同步 ----

    async def _ensure_indexes(self):
        """确保集合索引存在"""
        db = await self._get_db()
        col = db[STOCK_HOT_COLLECTION]

        await col.create_index(
            [("stock_code", 1), ("category", 1)],
            unique=True,
            name="hot_stock_code_category",
        )
        await col.create_index([("category", 1)], name="hot_category")
        await col.create_index([("rank", 1)], name="hot_rank")
        await col.create_index([("followers", -1)], name="hot_followers_desc")

    async def sync_hot_data(self) -> int:
        """
        全量同步雪球股票热度数据

        Returns:
            总写入记录数
        """
        db = await self._get_db()
        await self._ensure_indexes()

        data = await self.fetch_hot_data()
        if not data:
            logger.warning("雪球热度数据为空，跳过同步")
            return 0

        col = db[STOCK_HOT_COLLECTION]
        total_inserted = 0
        now_utc = datetime.utcnow()

        for category, records in data.items():
            if not records:
                continue

            # 先删除该分类的旧数据
            delete_result = await col.delete_many({"category": category})
            logger.info(
                f"删除旧热度数据: category={category}, deleted={delete_result.deleted_count}"
            )

            # 写入 updated_at
            for rec in records:
                rec["updated_at"] = now_utc

            # 批量插入
            if records:
                insert_result = await col.insert_many(records, ordered=False)
                total_inserted += len(insert_result.inserted_ids)
                logger.info(f"热度[{category}] 写入完成: {len(insert_result.inserted_ids)} 条")

        logger.info(f"雪球热度全量同步完成: 共 {total_inserted} 条")
        return total_inserted

    # ---- 查询接口 ----

    async def query_by_category(
        self, category: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        按分类查询热度排行

        Args:
            category: "最热门" 或 "本周新增"
            limit: 返回条数

        Returns:
            热度排行列表
        """
        db = await self._get_db()
        col = db[STOCK_HOT_COLLECTION]

        cursor = (
            col.find({"category": category}, {"_id": 0})
            .sort("rank", 1)
            .limit(limit)
        )

        items = []
        async for doc in cursor:
            items.append(doc)

        return items

    async def query_all(self, limit: int = 50) -> Dict[str, List[Dict[str, Any]]]:
        """
        查询所有分类的热度数据

        Returns:
            {"最热门": [...], "本周新增": [...]}
        """
        result = {}
        for category in STOCK_HOT_CATEGORIES:
            result[category] = await self.query_by_category(category, limit)
        return result


# ===================== 交易排行榜（雪球）服务 =====================

STOCK_HOT_DEAL_COLLECTION = "stock_hot_deal_xq"


class StockHotDealXueqiuService:
    """雪球交易排行榜服务（最热门）"""

    DEAL_SYMBOL = "最热门"

    def __init__(self):
        self._db: Optional[AsyncIOMotorDatabase] = None

    async def _get_db(self) -> AsyncIOMotorDatabase:
        if self._db is None:
            self._db = get_mongo_db()
        return self._db

    # ---- 数据获取 ----

    async def fetch_deal_data(self) -> List[Dict[str, Any]]:
        """
        从 AKShare 获取雪球交易排行榜

        Returns:
            交易排行记录列表
        """
        import asyncio
        import akshare as ak

        try:
            def _fetch():
                return ak.stock_hot_deal_xq(symbol=self.DEAL_SYMBOL)

            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(None, _fetch)

            if df is None or (hasattr(df, "empty") and df.empty):
                logger.warning("交易排行榜返回空数据")
                return []

            records = []
            for rank, (_, row) in enumerate(df.iterrows(), start=1):
                records.append({
                    "stock_code": str(row.iloc[0]) if len(row) > 0 else "",
                    "stock_name": str(row.iloc[1]) if len(row) > 1 else "",
                    "deal_attention": int(row.iloc[2]) if len(row) > 2 and pd.notna(row.iloc[2]) else 0,
                    "current_price": float(row.iloc[3]) if len(row) > 3 and pd.notna(row.iloc[3]) else None,
                    "rank": rank,
                })

            logger.info(f"交易排行榜获取成功: {len(records)} 条")
            return records

        except Exception as e:
            logger.error(f"交易排行榜获取失败: {e}", exc_info=True)
            return []

    # ---- 数据库同步 ----

    async def _ensure_indexes(self):
        """确保集合索引存在"""
        db = await self._get_db()
        col = db[STOCK_HOT_DEAL_COLLECTION]

        await col.create_index("stock_code", unique=True, name="deal_stock_code")
        await col.create_index([("rank", 1)], name="deal_rank")
        await col.create_index([("deal_attention", -1)], name="deal_attention_desc")

    async def sync_deal_data(self) -> int:
        """
        全量同步雪球交易排行榜

        Returns:
            写入记录数
        """
        db = await self._get_db()
        await self._ensure_indexes()

        records = await self.fetch_deal_data()
        if not records:
            logger.warning("交易排行榜数据为空，跳过同步")
            return 0

        col = db[STOCK_HOT_DEAL_COLLECTION]
        now_utc = datetime.utcnow()

        # 先全量删除旧数据
        delete_result = await col.delete_many({})
        logger.info(f"删除旧交易排行数据: deleted={delete_result.deleted_count}")

        # 写入 updated_at
        for rec in records:
            rec["updated_at"] = now_utc

        # 批量插入
        insert_result = await col.insert_many(records, ordered=False)
        logger.info(f"交易排行榜写入完成: {len(insert_result.inserted_ids)} 条")
        return len(insert_result.inserted_ids)

    # ---- 查询接口 ----

    async def query_all(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        查询交易排行榜

        Args:
            limit: 返回条数

        Returns:
            排行列表
        """
        db = await self._get_db()
        col = db[STOCK_HOT_DEAL_COLLECTION]

        cursor = (
            col.find({}, {"_id": 0})
            .sort("rank", 1)
            .limit(limit)
        )

        items = []
        async for doc in cursor:
            items.append(doc)

        return items


# ===================== 人气榜（东方财富）服务 =====================

STOCK_HOT_RANK_EM_COLLECTION = "stock_hot_rank_em"

# 列名映射：东方财富人气榜列索引
# 当前排名=iloc[0], 代码=iloc[1], 股票名称=iloc[2], 最新价=iloc[3], 涨跌额=iloc[4], 涨跌幅=iloc[5]


class StockHotRankEMService:
    """东方财富人气榜服务"""

    async def _get_db(self) -> AsyncIOMotorDatabase:
        if not hasattr(self, '_db') or self._db is None:
            self._db = get_mongo_db()
        return self._db

    # ---- 数据获取 ----

    async def fetch_rank_data(self) -> List[Dict[str, Any]]:
        """
        从 AKShare 获取东方财富人气榜

        Returns:
            人气排行记录列表
        """
        import asyncio
        import akshare as ak

        try:
            def _fetch():
                return ak.stock_hot_rank_em()

            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(None, _fetch)

            if df is None or (hasattr(df, "empty") and df.empty):
                logger.warning("人气榜返回空数据")
                return []

            records = []
            for _, row in df.iterrows():
                records.append({
                    "rank": int(row.iloc[0]) if len(row) > 0 and pd.notna(row.iloc[0]) else 0,
                    "stock_code": str(row.iloc[1]) if len(row) > 1 else "",
                    "stock_name": str(row.iloc[2]) if len(row) > 2 else "",
                    "current_price": float(row.iloc[3]) if len(row) > 3 and pd.notna(row.iloc[3]) else None,
                    "change_amount": float(row.iloc[4]) if len(row) > 4 and pd.notna(row.iloc[4]) else None,
                    "change_percent": float(row.iloc[5]) if len(row) > 5 and pd.notna(row.iloc[5]) else None,
                })

            logger.info(f"人气榜获取成功: {len(records)} 条")
            return records

        except Exception as e:
            logger.error(f"人气榜获取失败: {e}", exc_info=True)
            return []

    # ---- 数据库同步 ----

    async def _ensure_indexes(self):
        """确保集合索引存在"""
        db = await self._get_db()
        col = db[STOCK_HOT_RANK_EM_COLLECTION]

        await col.create_index("stock_code", unique=True, name="rank_stock_code")
        await col.create_index([("rank", 1)], name="rank_order")

    async def sync_rank_data(self) -> int:
        """
        全量同步东方财富人气榜

        Returns:
            写入记录数
        """
        db = await self._get_db()
        await self._ensure_indexes()

        records = await self.fetch_rank_data()
        if not records:
            logger.warning("人气榜数据为空，跳过同步")
            return 0

        col = db[STOCK_HOT_RANK_EM_COLLECTION]
        now_utc = datetime.utcnow()

        # 全量删除旧数据
        delete_result = await col.delete_many({})
        logger.info(f"删除旧人气榜数据: deleted={delete_result.deleted_count}")

        for rec in records:
            rec["updated_at"] = now_utc

        insert_result = await col.insert_many(records, ordered=False)
        logger.info(f"人气榜写入完成: {len(insert_result.inserted_ids)} 条")
        return len(insert_result.inserted_ids)

    # ---- 查询接口 ----

    async def query_all(self, limit: int = 50) -> List[Dict[str, Any]]:
        """查询人气榜"""
        db = await self._get_db()
        col = db[STOCK_HOT_RANK_EM_COLLECTION]

        cursor = (
            col.find({}, {"_id": 0})
            .sort("rank", 1)
            .limit(limit)
        )
        items = []
        async for doc in cursor:
            items.append(doc)
        return items


# ===================== 全局单例 =====================

_disclosure_calendar_service: Optional[DisclosureCalendarService] = None
_stock_hot_service: Optional[StockHotXueqiuService] = None
_stock_hot_deal_service: Optional[StockHotDealXueqiuService] = None
_stock_hot_rank_em_service: Optional[StockHotRankEMService] = None


def get_disclosure_calendar_service() -> DisclosureCalendarService:
    """获取预约披露日服务单例"""
    global _disclosure_calendar_service
    if _disclosure_calendar_service is None:
        _disclosure_calendar_service = DisclosureCalendarService()
    return _disclosure_calendar_service


def get_stock_hot_xueqiu_service() -> StockHotXueqiuService:
    """获取雪球热度服务单例"""
    global _stock_hot_service
    if _stock_hot_service is None:
        _stock_hot_service = StockHotXueqiuService()
    return _stock_hot_service


def get_stock_hot_deal_xueqiu_service() -> StockHotDealXueqiuService:
    """获取雪球交易排行服务单例"""
    global _stock_hot_deal_service
    if _stock_hot_deal_service is None:
        _stock_hot_deal_service = StockHotDealXueqiuService()
    return _stock_hot_deal_service


def get_stock_hot_rank_em_service() -> StockHotRankEMService:
    """获取东方财富人气榜服务单例"""
    global _stock_hot_rank_em_service
    if _stock_hot_rank_em_service is None:
        _stock_hot_rank_em_service = StockHotRankEMService()
    return _stock_hot_rank_em_service


# ---- 供调度器调用的同步函数（数据概览）----

async def run_disclosure_calendar_sync(data_date: Optional[str] = None) -> int:
    """
    运行预约披露日数据同步（供 APScheduler 调用）

    Args:
        data_date: 可选，默认当天

    Returns:
        同步的记录数
    """
    service = get_disclosure_calendar_service()
    try:
        count = await service.sync_disclosure_calendar(data_date=data_date)
        logger.info(f"预约披露日定时同步完成: {count} 条")
        return count
    except Exception as e:
        logger.error(f"预约披露日定时同步失败: {e}", exc_info=True)
        raise


async def run_stock_hot_sync() -> int:
    """
    运行雪球股票热度数据同步（供 APScheduler 调用）

    Returns:
        同步的记录数
    """
    service = get_stock_hot_xueqiu_service()
    try:
        count = await service.sync_hot_data()
        logger.info(f"雪球热度定时同步完成: {count} 条")
        return count
    except Exception as e:
        logger.error(f"雪球热度定时同步失败: {e}", exc_info=True)
        raise


async def run_stock_hot_deal_sync() -> int:
    """
    运行雪球交易排行榜同步（供 APScheduler 调用）

    Returns:
        同步的记录数
    """
    service = get_stock_hot_deal_xueqiu_service()
    try:
        count = await service.sync_deal_data()
        logger.info(f"交易排行榜定时同步完成: {count} 条")
        return count
    except Exception as e:
        logger.error(f"交易排行榜定时同步失败: {e}", exc_info=True)
        raise


async def run_stock_hot_rank_em_sync() -> int:
    """
    运行东方财富人气榜同步（供 APScheduler 调用）

    Returns:
        同步的记录数
    """
    service = get_stock_hot_rank_em_service()
    try:
        count = await service.sync_rank_data()
        logger.info(f"人气榜定时同步完成: {count} 条")
        return count
    except Exception as e:
        logger.error(f"人气榜定时同步失败: {e}", exc_info=True)
        raise


async def run_data_overview_sync(data_date: Optional[str] = None) -> dict:
    """
    运行数据概览定时同步（预约披露日 + 雪球热度 + 交易排行榜 + 东方财富人气榜）

    将多个数据源聚合在一个定时任务中执行，
    同时保留各自的独立同步函数以支持单独触发。

    Args:
        data_date: 预约披露日数据日期（可选）

    Returns:
        {"disclosure_calendar": int, "stock_hot": int, "stock_hot_deal": int, "stock_hot_rank_em": int}
    """
    dc_count = 0
    hot_count = 0
    deal_count = 0
    rank_count = 0

    # 1. 预约披露日
    try:
        dc_count = await run_disclosure_calendar_sync(data_date=data_date)
    except Exception as e:
        logger.error(f"数据概览-披露日同步失败: {e}", exc_info=True)

    # 2. 雪球热度（关注榜）
    try:
        hot_count = await run_stock_hot_sync()
    except Exception as e:
        logger.error(f"数据概览-热度同步失败: {e}", exc_info=True)

    # 3. 雪球交易排行榜
    try:
        deal_count = await run_stock_hot_deal_sync()
    except Exception as e:
        logger.error(f"数据概览-交易排行同步失败: {e}", exc_info=True)

    # 4. 东方财富人气榜
    try:
        rank_count = await run_stock_hot_rank_em_sync()
    except Exception as e:
        logger.error(f"数据概览-人气榜同步失败: {e}", exc_info=True)

    logger.info(
        f"数据概览定时同步完成: 披露日={dc_count} 条, "
        f"热度={hot_count} 条, 交易排行={deal_count} 条, 人气榜={rank_count} 条"
    )
    return {
        "disclosure_calendar": dc_count,
        "stock_hot": hot_count,
        "stock_hot_deal": deal_count,
        "stock_hot_rank_em": rank_count,
    }
