"""
板块资金流服务
负责从 Tushare 获取个股资金流向数据并聚合到板块级别。

数据源:
    - Tushare moneyflow 接口：沪深A股资金流向
    - stock_basic_info: 获取个股行业归属

主力资金定义:
    - 大单 (buy_lg/sell_lg) + 超大单 (buy_elg/sell_elg)
    - 主力净流入 = (buy_lg_amount + buy_elg_amount) - (sell_lg_amount + sell_elg_amount)
"""
import logging
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from app.core.database import get_mongo_db
from app.core.config import settings

logger = logging.getLogger(__name__)


def _safe_float(value, default: float = 0.0) -> float:
    """安全地转换为 float，处理 None/空字符串/NaN/Infinity"""
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


class SectorMoneyflowService:
    """板块资金流数据服务"""

    # MongoDB 集合名
    MONEYFLOW_COLLECTION = "moneyflow_data"

    def _clean_records(self, records: List[Dict]) -> List[Dict]:
        """清洗 DataFrame 记录，统一字段类型并计算衍生字段

        Args:
            records: to_dict("records") 输出的原始记录列表

        Returns:
            清洗后的记录列表（含主力/散户资金衍生字段）
        """
        cleaned_list = []
        for record in records:
            ts_code = record.get("ts_code", "")
            trade_date_val = record.get("trade_date", "")
            if not ts_code or not trade_date_val:
                continue

            cleaned = {"ts_code": ts_code, "trade_date": trade_date_val}
            for key, val in record.items():
                if key in ("ts_code", "trade_date"):
                    continue
                cleaned[key] = _safe_float(val)

            # 主力资金 = 大单 + 超大单
            cleaned["main_force_buy"] = cleaned.get("buy_lg_amount", 0) + cleaned.get("buy_elg_amount", 0)
            cleaned["main_force_sell"] = cleaned.get("sell_lg_amount", 0) + cleaned.get("sell_elg_amount", 0)
            cleaned["main_force_net"] = cleaned["main_force_buy"] - cleaned["main_force_sell"]

            # 散户资金 = 小单 + 中单
            cleaned["retail_buy"] = cleaned.get("buy_sm_amount", 0) + cleaned.get("buy_md_amount", 0)
            cleaned["retail_sell"] = cleaned.get("sell_sm_amount", 0) + cleaned.get("sell_md_amount", 0)
            cleaned["retail_net"] = cleaned["retail_buy"] - cleaned["retail_sell"]

            cleaned["updated_at"] = datetime.utcnow()
            cleaned_list.append(cleaned)
        return cleaned_list

    MONEYFLOW_FIELDS = [
        "ts_code", "trade_date",
        "buy_sm_vol", "buy_sm_amount",
        "sell_sm_vol", "sell_sm_amount",
        "buy_md_vol", "buy_md_amount",
        "sell_md_vol", "sell_md_amount",
        "buy_lg_vol", "buy_lg_amount",
        "sell_lg_vol", "sell_lg_amount",
        "buy_elg_vol", "buy_elg_amount",
        "sell_elg_vol", "sell_elg_amount",
        "net_mf_vol", "net_mf_amount",
    ]

    async def _fetch_single_day(
        self, date_str: str, force: bool = False
    ) -> int:
        """获取并存储单日资金流向数据

        Args:
            date_str: 交易日期 YYYYMMDD
            force: 是否覆盖已有数据

        Returns:
            存储的记录数
        """
        import tushare as ts

        pro = ts.pro_api()
        df = pro.query("moneyflow", trade_date=date_str, fields=self.MONEYFLOW_FIELDS)

        if df is None or df.empty:
            logger.info(f"交易日 {date_str} 无资金流向数据（可能非交易日）")
            return 0

        logger.info(f"交易日 {date_str}: 从 Tushare 获取到 {len(df)} 条资金流向记录")
        records = df.to_dict("records")
        cleaned_list = self._clean_records(records)

        db = get_mongo_db()
        collection = db[self.MONEYFLOW_COLLECTION]
        saved_count = 0

        for cleaned in cleaned_list:
            ts_code = cleaned["ts_code"]
            trade_date_val = cleaned["trade_date"]

            if force:
                await collection.replace_one(
                    {"ts_code": ts_code, "trade_date": trade_date_val},
                    cleaned,
                    upsert=True,
                )
            else:
                existing = await collection.find_one(
                    {"ts_code": ts_code, "trade_date": trade_date_val}
                )
                if existing:
                    continue
                await collection.insert_one(cleaned)

            saved_count += 1

        if saved_count > 0:
            logger.info(f"交易日 {date_str}: 存入 {saved_count} 条资金流向记录")
        return saved_count

    async def fetch_and_store_moneyflow(
        self,
        trade_date: Optional[str] = None,
        force: bool = False,
        days_back: int = 0,
    ) -> int:
        """从 Tushare 获取个股资金流向数据并存入 MongoDB

        支持单日和批量回填两种模式。

        单日模式 (days_back=0):
            仅获取指定 trade_date 的数据（默认当天）。

        批量模式 (days_back>0):
            从 trade_date（默认当天）开始向前回填 days_back 个交易日的数据。
            自动跳过非交易日（Tushare 返回空数据）。

        Args:
            trade_date: 起始交易日期 (YYYYMMDD)，None 表示使用当前日期
            force: 是否强制覆盖已有数据
            days_back: >0 时回填最近 N 个交易日的数据；0 表示仅同步单日

        Returns:
            存储的总记录数
        """
        try:
            import tushare as ts

            if not settings.TUSHARE_TOKEN:
                logger.warning("TUSHARE_TOKEN 未配置，无法获取资金流向数据")
                return 0

            ts.set_token(settings.TUSHARE_TOKEN)

            if days_back > 0:
                return await self._fetch_and_store_range(
                    trade_date=trade_date,
                    force=force,
                    days_back=days_back,
                )

            # 单日模式
            date_str = trade_date or datetime.now().strftime("%Y%m%d")
            return await self._fetch_single_day(date_str, force=force)

        except ImportError:
            logger.error("tushare 包未安装，无法获取资金流向数据")
            return 0
        except Exception as e:
            logger.exception(f"获取资金流向数据异常: {e}")
            return 0

    async def _fetch_and_store_range(
        self,
        trade_date: Optional[str] = None,
        force: bool = False,
        days_back: int = 5,
    ) -> int:
        """批量回填多个交易日的资金流向数据（区间查询优化版）

        利用 Tushare moneyflow 接口的 start_date/end_date 参数，
        一次请求获取整个区间的数据，避免逐日多次 API 调用，大幅提升同步效率。
        写入时 force=True 使用 bulk_write 批量 upsert，force=False 逐条检查新增。

        Args:
            trade_date: 起始日期 YYYYMMDD，None 表示当天
            force: 是否覆盖已有数据
            days_back: 需要回填的交易日数量

        Returns:
            存储的总记录数
        """
        import tushare as ts
        from pymongo import UpdateOne

        end = datetime.now() if trade_date is None else datetime.strptime(trade_date, "%Y%m%d")
        # 向前推算足够的日历日以覆盖 days_back 个交易日（按 1/3 交易日密度估算）
        max_calendar_days = max(days_back * 3, 10)
        start = end - timedelta(days=max_calendar_days)
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        logger.info(f"批量区间查询资金流向: {start_str} ~ {end_str} (预期 {days_back} 个交易日)")

        pro = ts.pro_api()
        df = pro.query("moneyflow", start_date=start_str, end_date=end_str, fields=self.MONEYFLOW_FIELDS)

        if df is None or df.empty:
            logger.warning(f"区间 {start_str}~{end_str} 无资金流向数据")
            return 0

        logger.info(f"区间 {start_str}~{end_str}: 从 Tushare 获取到 {len(df)} 条资金流向记录")

        records = df.to_dict("records")
        cleaned_list = self._clean_records(records)

        # 统计实际覆盖的交易日
        trade_dates_found = sorted(set(r["trade_date"] for r in cleaned_list))
        logger.info(f"区间内实际交易日数: {len(trade_dates_found)}, 数据来源日期: {trade_dates_found[0]} ~ {trade_dates_found[-1]}")

        db = get_mongo_db()
        collection = db[self.MONEYFLOW_COLLECTION]

        if force:
            # 强制覆盖模式: 使用 bulk_write 批量 upsert，一次网络往返
            operations = [
                UpdateOne(
                    {"ts_code": r["ts_code"], "trade_date": r["trade_date"]},
                    {"$set": r},
                    upsert=True,
                )
                for r in cleaned_list
            ]
            result = await collection.bulk_write(operations, ordered=False)
            saved_count = result.upserted_count + result.modified_count
            logger.info(
                f"区间 {start_str}~{end_str}: bulk_write 完成, "
                f"已覆盖/新增 {saved_count} 条记录 "
                f"(upserted={result.upserted_count}, modified={result.modified_count})"
            )
        else:
            # 增量模式: 逐条检查，只插入不存在的记录
            saved_count = 0
            for r in cleaned_list:
                existing = await collection.find_one(
                    {"ts_code": r["ts_code"], "trade_date": r["trade_date"]}
                )
                if existing:
                    continue
                await collection.insert_one(r)
                saved_count += 1

            logger.info(f"区间 {start_str}~{end_str}: 新增 {saved_count} 条记录（跳过已有数据）")

        mode = "覆盖" if force else "增量"
        logger.info(f"批量回填完成 [{mode}]: 覆盖 {len(trade_dates_found)} 个交易日, 共 {saved_count} 条记录")
        return saved_count

    async def aggregate_sector_moneyflow(
        self,
        trade_date: Optional[str] = None,
        days: int = 1,
    ) -> List[Dict]:
        """按板块聚合资金流数据

        将个股资金流按行业聚合，计算板块级的主力资金流入/流出情况。

        Args:
            trade_date: 交易日期，None 表示最新可用日期
            days: 聚合天数（返回最近 N 天的聚合结果）

        Returns:
            按日期分组的板块资金流聚合列表
        """
        db = get_mongo_db()

        # 获取股票-行业映射
        industry_map = await self._get_stock_industry_map(db)
        if not industry_map:
            logger.warning("未获取到股票行业映射")
            return []

        # 确定日期范围
        if trade_date:
            end_date = trade_date
            start_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=days * 2)).strftime("%Y%m%d")
        else:
            # 查找最新可用日期
            latest = await db[self.MONEYFLOW_COLLECTION].find_one(
                {}, sort=[("trade_date", -1)]
            )
            if not latest:
                logger.warning("资金流数据为空，请先调用 fetch_and_store_moneyflow")
                return []
            end_date = latest["trade_date"]
            start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=days * 2)).strftime("%Y%m%d")

        # 获取资金流数据
        cursor = db[self.MONEYFLOW_COLLECTION].find({
            "trade_date": {"$gte": start_date, "$lte": end_date},
        })
        records = await cursor.to_list(length=None)

        if not records:
            logger.warning(f"日期范围 {start_date}~{end_date} 内无资金流数据")
            return []

        # 按日期 + 行业聚合
        # { date: { industry: { buy, sell, net, count, total_amount } } }
        from collections import defaultdict

        date_sector_agg: Dict[str, Dict] = defaultdict(lambda: defaultdict(lambda: {
            "main_force_buy": 0.0,
            "main_force_sell": 0.0,
            "main_force_net": 0.0,
            "retail_buy": 0.0,
            "retail_sell": 0.0,
            "retail_net": 0.0,
            "stock_count": 0,
            "stock_codes": set(),
        }))

        # 全市场按日期聚合
        market_agg: Dict[str, Dict] = defaultdict(lambda: {
            "main_force_buy": 0.0,
            "main_force_sell": 0.0,
            "stock_count": 0,
        })

        for record in records:
            ts_code = record.get("ts_code", "")
            rec_date = record.get("trade_date", "")

            # 提取 6 位代码用于行业映射
            code_6d = ts_code[:6] if ts_code else ""
            industry = industry_map.get(code_6d)

            mf_buy = _safe_float(record.get("main_force_buy", 0))
            mf_sell = _safe_float(record.get("main_force_sell", 0))
            mf_net = _safe_float(record.get("main_force_net", 0))
            rt_buy = _safe_float(record.get("retail_buy", 0))
            rt_sell = _safe_float(record.get("retail_sell", 0))
            rt_net = _safe_float(record.get("retail_net", 0))

            # 全市场统计
            market_agg[rec_date]["main_force_buy"] += mf_buy
            market_agg[rec_date]["main_force_sell"] += mf_sell
            market_agg[rec_date]["stock_count"] += 1

            if not industry:
                continue

            sa = date_sector_agg[rec_date][industry]
            sa["main_force_buy"] += mf_buy
            sa["main_force_sell"] += mf_sell
            sa["main_force_net"] += mf_net
            sa["retail_buy"] += rt_buy
            sa["retail_sell"] += rt_sell
            sa["retail_net"] += rt_net
            sa["stock_count"] += 1
            sa["stock_codes"].add(code_6d)

        # 格式化输出
        results = []
        sorted_dates = sorted(date_sector_agg.keys(), reverse=True)

        for date_key in sorted_dates[:days]:
            sectors = date_sector_agg[date_key]
            market_mf_buy = market_agg[date_key]["main_force_buy"]
            market_mf_sell = market_agg[date_key]["main_force_sell"]

            for industry, data in sectors.items():
                mf_buy = data["main_force_buy"]
                mf_sell = data["main_force_sell"]
                mf_net = data["main_force_net"]

                # 主力控盘力：(-1 ~ 1), 正数表示多头主导
                total_mf = mf_buy + mf_sell
                dominance = (mf_buy - mf_sell) / total_mf if total_mf > 0 else 0

                # 主力资金强度：板块主力净流入占比
                # 归一化到板块成交额级别
                total_sector_amount = mf_buy + mf_sell
                intensity = mf_net / total_sector_amount * 100 if total_sector_amount > 0 else 0

                results.append({
                    "industry": industry,
                    "trade_date": date_key,
                    "stock_count": data["stock_count"],
                    "total_main_force_buy": _safe_round(mf_buy, 2),
                    "total_main_force_sell": _safe_round(mf_sell, 2),
                    "total_main_force_net": _safe_round(mf_net, 2),
                    "total_retail_buy": _safe_round(data["retail_buy"], 2),
                    "total_retail_sell": _safe_round(data["retail_sell"], 2),
                    "total_retail_net": _safe_round(data["retail_net"], 2),
                    "market_main_force_buy": _safe_round(market_mf_buy, 2),
                    "market_main_force_sell": _safe_round(market_mf_sell, 2),
                    "main_force_ratio": _safe_round(intensity, 4),
                    "main_force_dominance": _safe_round(dominance, 4),
                })

        return results

    async def _get_stock_industry_map(
        self, db, source: str = "tushare"
    ) -> Dict[str, str]:
        """获取股票6位代码到行业的映射"""
        cursor = db["stock_basic_info"].find(
            {"source": source, "industry": {"$ne": None, "$ne": ""}},
            {"symbol": 1, "code": 1, "industry": 1, "_id": 0},
        )
        docs = await cursor.to_list(length=None)

        if not docs:
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

        return industry_map

    async def get_available_dates(self, limit: int = 60) -> List[str]:
        """获取有资金流数据的交易日列表"""
        db = get_mongo_db()
        pipeline = [
            {"$group": {"_id": "$trade_date"}},
            {"$sort": {"_id": -1}},
            {"$limit": limit},
        ]
        cursor = db[self.MONEYFLOW_COLLECTION].aggregate(pipeline)
        results = await cursor.to_list(length=None)
        return [r["_id"] for r in results if r.get("_id")]


# 全局单例
_moneyflow_service: Optional[SectorMoneyflowService] = None


def get_sector_moneyflow_service() -> SectorMoneyflowService:
    """获取板块资金流服务实例"""
    global _moneyflow_service
    if _moneyflow_service is None:
        _moneyflow_service = SectorMoneyflowService()
    return _moneyflow_service
