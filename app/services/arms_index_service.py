"""
阿姆氏指标 (Arms Index / TRIN) 服务

阿姆氏指标 = (上涨家数 / 下跌家数) / (上涨量 / 下跌量)

含义:
    TRIN = 1.0  中性，资金均匀分布
    TRIN < 1.0  上涨量占比更高，多头主导（看涨信号）
    TRIN > 1.0  下跌量占比更高，空头主导（看跌信号）

数据源:
    - stock_daily_quotes: 历史日线（pct_chg, volume）【收盘固化正式数据，仅预计算时聚合】
    - market_trin_daily:  阿姆氏指标日线预计算表（每日定时任务写入，读取端唯一数据源）
    - market_quotes:      盘中实时行情（用于实时 TRIN，盘中临时指标，轻量聚合）

预计算流程（每日定时任务，默认工作日 18:00，增量式）:
    1. 发现 stock_daily_quotes 中表内尚未计算的交易日（正常每日运行仅当天 1 天）；
    2. 逐日单日聚合（$match trade_date=X 走索引，约5000文档/天，并发8），计算每日 TRIN 与涨跌宽度；
    3. 按 trade_date 幂等 upsert 到 market_trin_daily（表内仅存每日事实，不含均线）。

读取流程（ArmsIndexService.get_arms_index）:
    1. 从 market_trin_daily 读取每日 TRIN 明细（零聚合）；
    2. 表为空时自动触发一次增量回填（同日防抖）；
    3. 内存中动态计算 5/10/21 日均线（严格满窗口，不足为 null）；
    4. 可选合并盘中实时 TRIN（实时记录插入序列头部，仅补算该记录的均值）。

融合规则：
    1. 实时TRIN仅交易日盘中有效；
    2. 如果 market_trin_daily 已存在当日记录（收盘完成），不再叠加实时数据；
    3. 实时与历史统计股票池保持一致，自动剔除退市标的；
    4. 日期唯一：同一trade_date只能有一条记录，正式收盘数据优先级高于盘中临时实时值

优化说明：
    ✅ 增量落表：读取端零聚合；预计算只处理缺失交易日，单日小聚合避免大管道超时
    ✅ 精简入库结构：表内仅存每日 TRIN 明细，5/10/21日均线查询时内存动态计算
    ✅ 返回完整daily序列，不做切片截断
"""
import asyncio
import logging
import math
from datetime import datetime
from typing import Dict, List, Optional, Set

from app.core.database import get_mongo_db

logger = logging.getLogger(__name__)

# 默认周期
DEFAULT_PERIODS = [5, 10, 21]
# 判定股票退市阈值：超过60天无日线数据
DELISTED_DAY_THRESHOLD = 60
# 阿姆氏指标日线预计算表
TRIN_COLLECTION = "market_trin_daily"
# 每次计算的交易日窗口上限（用于首次回填/手动触发，正常每日运行仅增量当天1天）
MAX_TRADING_DAYS = 250


def _safe_float(value, default: float = 0.0) -> float:
    """安全转换为 float，处理 None/NaN/空值"""
    if value is None:
        return default
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (ValueError, TypeError):
        return default


class ArmsIndexService:
    """全市场阿姆氏指标计算服务"""

    # 同日防抖：自动补算一天只触发一次，避免反复聚合全表
    _last_backfill_date: Optional[str] = None

    def __init__(self):
        # 预计算互斥锁：定时任务与手动触发并发时，避免重复聚合全表
        self._compute_lock = asyncio.Lock()

    # ==================== 对外接口 ====================
    async def get_arms_index(
        self,
        days: int = 21,
        periods: Optional[List[int]] = None,
        include_realtime: bool = True,
    ) -> Dict:
        """获取全市场阿姆氏指标时间序列

        Args:
            days:     回溯的交易日数量
            periods:  均值周期列表（仅用于兼容上层调用；5/10/21日均线为查询时动态计算）
            include_realtime: 是否合并实时行情（今日盘中数据纳入计算）

        Returns:
            {
                "daily": [ { trade_date, advance_count, decline_count, advance_volume, decline_volume,
                             trin, breadth_ratio, trin_ma5, trin_ma10, trin_ma21 }, ... ],
                "periods": [5, 10, 21],
                "latest_date": "20260730",
                "realtime_included": True/False
            }
        """
        if periods is None:
            periods = DEFAULT_PERIODS

        # 1. 读取预计算表（零聚合，表内仅存每日 TRIN 明细）
        daily_records = await self._load_daily_from_db(days)

        # 2. 表为空（首次部署 / 定时任务未运行）时自动补算一次（同日防抖，避免反复聚合）
        today_key = datetime.now().strftime("%Y%m%d")
        if not daily_records and self._last_backfill_date != today_key:
            logger.info("ℹ️ market_trin_daily 表为空，触发一次增量回填")
            try:
                await self.compute_and_store_daily_trin(MAX_TRADING_DAYS)
                self._last_backfill_date = today_key
                daily_records = await self._load_daily_from_db(days)
            except Exception as e:
                logger.error(f"❌ market_trin_daily 自动补算失败: {e}", exc_info=True)

        if not daily_records:
            return {
                "daily": [],
                "periods": periods,
                "latest_date": None,
                "realtime_included": False
            }

        # 3. 查询时动态计算 5/10/21 日均线（内存计算，严格满窗口）
        records_asc = list(reversed(daily_records))
        records_asc = self._with_rolling_means(records_asc)
        daily_records = list(reversed(records_asc))

        # 4. 尝试合并实时数据（严格防重合，仅补算实时记录自身的均值）
        realtime_included = False
        if include_realtime:
            try:
                rt = await self._compute_realtime_trin()
                if rt:
                    rt_date = self._normalize_date(rt["trade_date"])
                    # 提取历史已存在日期集合
                    existing_date_set: Set[str] = {self._normalize_date(r["trade_date"]) for r in daily_records}
                    if rt_date not in existing_date_set:
                        rt["trade_date"] = rt_date
                        # 均值先占位，插入序列头部后统一补算
                        for p in DEFAULT_PERIODS:
                            rt[f"trin_ma{p}"] = None
                        daily_records.insert(0, rt)
                        realtime_included = True
                        self._compute_realtime_means(daily_records)
                        logger.info(f"✅ 实时 TRIN 已合并: {rt_date} trin={rt['trin']:.4f}")
                    else:
                        logger.info(f"ℹ️ 历史数据已存在当日 {rt_date} 收盘记录，跳过实时数据（防止临时值覆盖正式值）")
            except Exception as e:
                logger.warning(f"合并实时 TRIN 失败（非交易日或数据未就绪）: {e}", exc_info=False)

        # 5. 按日期降序排列（新日期在前）
        daily_records.sort(key=lambda r: r["trade_date"], reverse=True)

        return {
            "daily": daily_records,  # ✅ 不再切片截断，返回完整数据
            "periods": periods,
            "latest_date": daily_records[0]["trade_date"],
            "realtime_included": realtime_included,
        }

    # ==================== market_trin_daily 预计算表 ====================
    async def _load_daily_from_db(self, days: int) -> List[Dict]:
        """从 market_trin_daily 读取最近 days 个交易日的 TRIN 明细（降序，最新在前）

        表内仅存每日事实数据，5/10/21 日均线由调用方基于返回序列动态计算。
        """
        db = get_mongo_db()
        coll = db[TRIN_COLLECTION]

        cursor = coll.find({}, {"_id": 0}).sort("trade_date", -1).limit(days)
        records: List[Dict] = []
        async for doc in cursor:
            records.append(doc)

        records.sort(key=lambda r: r["trade_date"], reverse=True)
        return records

    async def compute_and_store_daily_trin(self, days: int = MAX_TRADING_DAYS) -> Dict:
        """定时任务/手动触发入口：增量聚合缺失交易日 → 计算每日 TRIN 明细 → upsert market_trin_daily

        Args:
            days: 本次计算的交易日窗口上限（默认 MAX_TRADING_DAYS）

        Returns:
            {"stored": int, "start_date": str, "end_date": str}

        幂等性：按 trade_date 唯一 upsert，重复执行不会产生重复数据；
        互斥性：并发调用（定时任务 + 手动触发）时串行执行，避免重复聚合全表。
        """
        async with self._compute_lock:
            return await self._do_compute_and_store(days)

    async def _do_compute_and_store(self, days: int) -> Dict:
        """预计算核心逻辑：增量聚合缺失交易日，逐日小聚合避免大管道超时（调用方需持有 _compute_lock）

        正常每日运行：表内已含全部历史日期，缺失的仅当天 1 天 → 单日聚合（约5000文档）；
        首次回填/手动触发：对最近缺失的交易日逐日聚合（并发8，单日走 trade_date 索引）。
        """
        db = get_mongo_db()
        trin_coll = db[TRIN_COLLECTION]

        # 幂等创建唯一索引（日期唯一，正式数据幂等覆盖）
        try:
            await trin_coll.create_index([("trade_date", 1)], unique=True, name="trade_date_unique")
        except Exception as e:
            logger.warning(f"⚠️ 创建 market_trin_daily 索引失败（可能已存在）: {e}")

        # 1. 发现缺失交易日（正常情况仅当天 1 天）
        missing_dates, raw_by_compact = await self._get_missing_dates(limit=days)
        if not missing_dates:
            logger.info("ℹ️ market_trin_daily 已是最新，无需预计算")
            return {"stored": 0, "start_date": None, "end_date": None}

        logger.info(f"🔍 待预计算交易日: {len(missing_dates)} 天 ({missing_dates[0]} ~ {missing_dates[-1]})")

        # 2. 逐日并发聚合（限制并发，避免瞬时打满 Mongo）
        sem = asyncio.Semaphore(8)
        now = datetime.now()

        async def process_one(compact: str) -> bool:
            async with sem:
                record = await self._aggregate_single_day(raw_by_compact[compact])
                if record is None:
                    logger.debug(f"ℹ️ {compact} 无有效日线数据，跳过")
                    return False
                await trin_coll.update_one(
                    {"trade_date": record["trade_date"]},
                    {
                        "$set": {**record, "updated_at": now},
                        "$setOnInsert": {"created_at": now},
                        # 兼容旧表：清理已废弃的均值字段（均线改为查询时动态计算）
                        "$unset": {f"trin_ma{p}": "" for p in DEFAULT_PERIODS},
                    },
                    upsert=True,
                )
                return True

        results = await asyncio.gather(*[process_one(c) for c in missing_dates])
        stored = sum(1 for r in results if r)

        logger.info(f"✅ market_trin_daily 预计算完成: 新增/更新 {stored} 天")
        return {
            "stored": stored,
            "start_date": missing_dates[-1],
            "end_date": missing_dates[0],
        }

    # ==================== 增量日期发现 + 单日聚合（避免大管道超时） ====================
    async def _get_missing_dates(self, limit: int) -> "tuple[list[str], dict[str, str]]":
        """获取 stock_daily_quotes 中表内尚未计算的交易日（compact 格式，降序，最多 limit 个）

        Returns:
            (缺失日期列表, compact→原始格式映射)
            原始格式用于精确 $match（stock_daily_quotes 中日期可能为 YYYY-MM-DD / YYYYMMDD）
        """
        db = get_mongo_db()
        coll = db["stock_daily_quotes"]
        trin_coll = db[TRIN_COLLECTION]

        # 1. 全表交易日列表（distinct 走 trade_date 索引，不读取文档）
        raw_dates = await coll.distinct("trade_date")
        if not raw_dates:
            return [], {}

        # 同一天可能以不同格式存在，统一为 compact 并保留原始值用于精确匹配
        raw_by_compact: Dict[str, str] = {}
        for d in raw_dates:
            c = self._normalize_date(str(d))
            if c:
                raw_by_compact[c] = str(d)

        recent = sorted(raw_by_compact.keys(), reverse=True)[:limit]
        if not recent:
            return [], {}

        # 2. 过滤掉表内已计算过的日期（正常每日运行仅缺失当天，首次回填则缺失全部）
        existing = await trin_coll.distinct("trade_date", {"trade_date": {"$in": recent}})
        existing_set = set(existing)
        missing = [d for d in recent if d not in existing_set]
        return missing, raw_by_compact

    async def _aggregate_single_day(self, raw_trade_date: str) -> Optional[Dict]:
        """单日 TRIN 聚合：$match trade_date=X 走索引，扫描量约5000文档/天，避免大管道超时

        Args:
            raw_trade_date: stock_daily_quotes 中的原始日期（如 2026-08-08）

        Returns:
            计算后的 TRIN 明细（trade_date 为 compact 格式）；该日无有效日线数据返回 None
        """
        db = get_mongo_db()
        coll = db["stock_daily_quotes"]

        pipeline = [
            {
                "$match": {
                    "trade_date": raw_trade_date,
                    "period": {"$in": ["daily", None]},  # 只统计日线，避免 weekly/monthly 混入
                }
            },
            {"$group": {
                "_id": None,
                "advance_count": {"$sum": {"$cond": [{"$gt": ["$pct_chg", 0]}, 1, 0]}},
                "decline_count": {"$sum": {"$cond": [{"$lt": ["$pct_chg", 0]}, 1, 0]}},
                "equal_count": {"$sum": {"$cond": [{"$eq": ["$pct_chg", 0]}, 1, 0]}},
                "advance_volume": {"$sum": {"$cond": [{"$gt": ["$pct_chg", 0]}, {"$ifNull": ["$volume", 0]}, 0]}},
                "decline_volume": {"$sum": {"$cond": [{"$lt": ["$pct_chg", 0]}, {"$ifNull": ["$volume", 0]}, 0]}},
                "total_count": {"$sum": 1},
            }},
        ]

        cursor = coll.aggregate(pipeline)
        results = await cursor.to_list(length=1)
        if not results:
            return None

        row = results[0]
        trade_date = self._normalize_date(raw_trade_date)
        adv_cnt = row["advance_count"]
        dec_cnt = row["decline_count"]
        adv_vol = _safe_float(row.get("advance_volume", 0))
        dec_vol = _safe_float(row.get("decline_volume", 0))

        return {
            "trade_date": trade_date,
            "advance_count": adv_cnt,
            "decline_count": dec_cnt,
            "equal_count": row["equal_count"],
            "total_count": row["total_count"],
            "advance_volume": round(adv_vol, 2),
            "decline_volume": round(dec_vol, 2),
            "trin": self._calc_trin(adv_cnt, dec_cnt, adv_vol, dec_vol),
            "breadth_ratio": self._calc_breadth_ratio(adv_cnt, dec_cnt),
        }

    # ==================== 实时行情聚合（market_quotes） ====================
    async def _compute_realtime_trin(self) -> Optional[Dict]:
        """
        盘中实时TRIN计算
        约束：
        1. 只统计今日成功同步行情的标的 trade_date=YYYY-MM-DD
        2. 自动规避退市股票（后续可扩展is_delisted过滤）
        3. 非交易日直接返回None
        """
        db = get_mongo_db()
        coll = db["market_quotes"]

        now = datetime.now()
        today_std = now.strftime("%Y-%m-%d")
        today_num = now.strftime("%Y%m%d")

        # 【重要】精准匹配今日行情，杜绝混入历史/退市旧快照
        pipeline = [
            {
                "$match": {
                    "trade_date": today_std,
                    "close": {"$ne": 0},
                    "pct_chg": {"$ne": None}
                }
            },
            {
                "$group": {
                    "_id": None,
                    "advance_count": {"$sum": {"$cond": [{"$gt": ["$pct_chg", 0]}, 1, 0]}},
                    "decline_count": {"$sum": {"$cond": [{"$lt": ["$pct_chg", 0]}, 1, 0]}},
                    "equal_count": {"$sum": {"$cond": [{"$eq": ["$pct_chg", 0]}, 1, 0]}},
                    "advance_volume": {"$sum": {"$cond": [{"$gt": ["$pct_chg", 0]}, {"$ifNull": ["$volume", 0]}, 0]}},
                    "decline_volume": {"$sum": {"$cond": [{"$lt": ["$pct_chg", 0]}, {"$ifNull": ["$volume", 0]}, 0]}},
                    "total_count": {"$sum": 1},
                }
            }
        ]

        cursor = coll.aggregate(pipeline)
        results = await cursor.to_list(length=1)

        if not results:
            logger.info(f"market_quotes 无今日 {today_std} 有效行情")
            return None

        row = results[0]
        adv_cnt = row["advance_count"]
        dec_cnt = row["decline_count"]
        adv_vol = _safe_float(row.get("advance_volume"))
        dec_vol = _safe_float(row.get("decline_volume"))
        total_cnt = row["total_count"]

        # 有效股票过少，盘前/数据同步未完成，放弃实时指标
        if total_cnt < 500:
            logger.info(f"ℹ️ 实时有效标的数量不足({total_cnt})，跳过实时TRIN")
            return None

        trin = self._calc_trin(adv_cnt, dec_cnt, adv_vol, dec_vol)
        breadth_ratio = self._calc_breadth_ratio(adv_cnt, dec_cnt)

        logger.info(
            f"📊 实时 TRIN 计算: date={today_num}, adv={adv_cnt}, dec={dec_cnt}, "
            f"adv_vol={adv_vol:.0f}, dec_vol={dec_vol:.0f}, trin={trin:.4f}"
        )

        return {
            "trade_date": today_num,
            "advance_count": adv_cnt,
            "decline_count": dec_cnt,
            "equal_count": row["equal_count"],
            "total_count": total_cnt,
            "advance_volume": round(adv_vol, 2),
            "decline_volume": round(dec_vol, 2),
            "trin": trin,
            "breadth_ratio": breadth_ratio,
        }

    # ==================== 静态工具方法 ====================
    @staticmethod
    def _with_rolling_means(records_asc: List[Dict]) -> List[Dict]:
        """在升序（旧→新）序列上为每条记录补充 trin_ma5/10/21

        严格满窗口：窗口不足时该日均值为 None（禁止 min_periods 伪均值）
        """
        trins = [r["trin"] for r in records_asc]
        for i, r in enumerate(records_asc):
            for p in DEFAULT_PERIODS:
                if i + 1 >= p:
                    window = trins[i + 1 - p: i + 1]
                    r[f"trin_ma{p}"] = round(sum(window) / p, 4)
                else:
                    r[f"trin_ma{p}"] = None
        return records_asc

    @staticmethod
    def _compute_realtime_means(records_desc: List[Dict]) -> None:
        """为序列头部（实时记录）补算滚动均值，历史记录保留表内固化值

        Args:
            records_desc: 降序序列，index 0 为最新（实时）记录
        """
        if not records_desc:
            return
        head = records_desc[0]
        trins = [r["trin"] for r in records_desc]
        for p in DEFAULT_PERIODS:
            if len(trins) >= p:
                head[f"trin_ma{p}"] = round(sum(trins[:p]) / p, 4)
            else:
                head[f"trin_ma{p}"] = None

    @staticmethod
    def _calc_trin(adv_cnt: int, dec_cnt: int, adv_vol: float, dec_vol: float) -> float:
        """TRIN = (上涨家数/下跌家数) / (上涨量/下跌量)"""
        if dec_cnt == 0 or dec_vol == 0:
            return 1.0
        ratio_issues = adv_cnt / dec_cnt
        ratio_volume = adv_vol / dec_vol
        trin = ratio_issues / ratio_volume
        # 限制极端值，防止单日异常干扰滚动均值
        return round(max(0.1, min(10.0, trin)), 4)

    @staticmethod
    def _normalize_date(date_val: str) -> str:
        """统一日期格式 YYYYMMDD"""
        if not date_val:
            return ""
        s = str(date_val).strip()
        if len(s) == 10 and s[4] == "-":
            return s.replace("-", "")
        return s

    @staticmethod
    def _calc_breadth_ratio(adv_cnt: int, dec_cnt: int) -> float:
        """涨跌宽度 = 上涨家数/(上涨+下跌)"""
        total = adv_cnt + dec_cnt
        if total == 0:
            return 0.5
        return round(adv_cnt / total, 4)


# ==================== 全局单例 ====================
_arms_index_service: Optional[ArmsIndexService] = None


def get_arms_index_service() -> ArmsIndexService:
    global _arms_index_service
    if _arms_index_service is None:
        _arms_index_service = ArmsIndexService()
    return _arms_index_service


# ==================== 定时任务入口 ====================
async def run_market_trin_daily_sync() -> Dict:
    """每日定时计算阿姆氏指标并存入 market_trin_daily 表（供 APScheduler 调用）

    默认每个交易日收盘后（工作日 18:00）执行一次，
    每次重算最近 MAX_TRADING_DAYS 个交易日并整体 upsert，幂等且自愈。
    """
    service = get_arms_index_service()
    try:
        result = await service.compute_and_store_daily_trin()
        logger.info(f"✅ 阿姆氏指标定时预计算完成: {result}")
        return result
    except Exception as e:
        logger.error(f"❌ 阿姆氏指标定时预计算失败: {e}", exc_info=True)
        raise