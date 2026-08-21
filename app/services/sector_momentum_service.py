"""
双维度板块轮动势能模型

核心思想：
    结合「主力资金流强度」和「板块成交额占比趋势」两个维度，
    识别板块轮动中的真上涨、假上涨、低位切换、高位出逃信号。

维度一：主力资金流强度 (Flow Strength)
    - 板块主力净流入 / 板块总成交额 * 100
    - 反映主力资金对该板块的参与意愿和方向
    - 使用 5 日移动平均过滤单日噪音

维度二：成交额占比趋势 (Turnover Ratio Trend)
    - 板块成交额 / 全市场成交额 * 100
    - 反映市场资金对该板块的关注度变化
    - 使用 5 日移动平均 vs 前 5 日移动平均计算变化

信号分类：
    - 真上涨:  资金流入↑ + 占比↑   → 主力真金白银买入，趋势健康
    - 假上涨:  资金流出↑ + 占比↑   → 放量出货，主力边拉边出
    - 低位切换: 资金流入↑ + 占比↑(低起点) → 新主线悄悄建仓
    - 高位出逃: 资金流出↓ + 占比↓(高起点) → 老主线被抛弃
    - 观望:     资金流出 + 占比↓   → 量价齐跌，回避
    - 中性:     其他情况

评分方法：
    - 每个维度 Min-Max 归一化到 [0, 100]
    - 综合评分 = 0.5 × flow_score + 0.5 × turnover_score
"""
import asyncio
import logging
import math
import statistics
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from app.core.database import get_mongo_db
from app.services.sector_rotation_service import (
    get_sector_rotation_service,
    SectorRotationService,
)
from app.services.sector_moneyflow_service import (
    get_sector_moneyflow_service,
    SectorMoneyflowService,
)

logger = logging.getLogger(__name__)


def _safe_float(value, default: float = 0.0) -> float:
    """安全地转换为 float"""
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
    """安全地 round"""
    if value is None or math.isnan(value) or math.isinf(value):
        return 0.0
    return round(value, ndigits)


def _min_max_normalize(values: List[float], target_min: float = 0, target_max: float = 100) -> Dict[float, float]:
    """Min-Max 归一化

    Args:
        values: 原始值列表
        target_min: 目标最小值
        target_max: 目标最大值

    Returns:
        { original_value: normalized_score, ... }
    """
    if not values:
        return {}

    valid = [v for v in values if v is not None and not (math.isnan(v) or math.isinf(v))]
    if not valid:
        return {v: target_min for v in values}

    min_v = min(valid)
    max_v = max(valid)

    result = {}
    if max_v == min_v:
        # 所有值相同，给中间分
        mid = (target_min + target_max) / 2
        for v in values:
            result[v] = mid
    else:
        for v in values:
            # 处理无效值
            if v is None or math.isnan(v) or math.isinf(v):
                result[v] = target_min
            else:
                normalized = (v - min_v) / (max_v - min_v)
                score = target_min + normalized * (target_max - target_min)
                result[v] = _safe_round(score, 2)

    return result


def _zscore_normalize(values: List[float]) -> Dict[float, float]:
    """Z-Score 标准化

    Returns:
        { original_value: zscore, ... }
    """
    if not values:
        return {}

    valid = [v for v in values if v is not None and not (math.isnan(v) or math.isinf(v))]
    if not valid:
        return {v: 0.0 for v in values}

    if len(valid) < 2:
        return {v: 0.0 for v in values}

    mean = statistics.mean(valid)
    std = statistics.stdev(valid)

    result = {}
    for v in values:
        if v is None or math.isnan(v) or math.isinf(v):
            result[v] = 0.0
        else:
            result[v] = _safe_round((v - mean) / std if std > 0 else 0.0, 4)

    return result


class SectorRotationMomentumService:
    """双维度板块轮动势能模型"""

    # 默认权重
    DEFAULT_WEIGHT_FLOW = 0.5
    DEFAULT_WEIGHT_TURNOVER = 0.5

    # 信号判定阈值（基于 Z-Score）
    # 主力资金 5 日移动平均：Z-Score > 0.5 为流入，< -0.5 为流出
    FLOW_INFLOW_THRESHOLD = 0.5
    FLOW_OUTFLOW_THRESHOLD = -0.5

    # 成交额占比趋势：5日移动平均变化率 > 5% 为上升，< -5% 为下降
    TURNOVER_RISE_THRESHOLD = 0.05  # 5% 相对变化
    TURNOVER_FALL_THRESHOLD = -0.05

    # 占比绝对水平阈值（用于判断低位/高位）
    RATIO_LOW_THRESHOLD = 1.0  # 占比 < 1% 视为低位
    RATIO_HIGH_THRESHOLD = 5.0  # 占比 > 5% 视为高位

    # 资金流趋势窗口（用于计算移动平均）
    FLOW_MA_WINDOW = 5  # 5 日移动平均
    TURNOVER_MA_WINDOW = 5  # 5 日移动平均

    # 信号排序优先级（数值越小越靠前）：真上涨排最前
    SIGNAL_PRIORITY = {
        "真上涨": 0,   # 主力流入 + 占比提升，最健康
        "低位切换": 1,  # 低位放量 + 主力建仓，潜在新主线
        "假上涨": 2,   # 占比升但主力流出，警示信号
        "中性": 3,     # 无明显信号
        "观望": 4,     # 资金流出 + 占比下降，量价齐跌
        "高位出逃": 5,  # 高占比回落 + 主力出逃，退潮信号
    }

    # 排名结果缓存 TTL（秒）
    CACHE_TTL_SECONDS = 180

    def __init__(self):
        self.rotation_service = get_sector_rotation_service()
        self.moneyflow_service = get_sector_moneyflow_service()

        # 排名结果缓存（进程内 TTL，stale-while-revalidate）
        self._ranking_cache: Optional[List[Dict]] = None
        self._ranking_cache_at: Optional[float] = None
        self._ranking_cache_key: Optional[Tuple] = None
        self._last_cache_hit: bool = False
        self._revalidate_lock = asyncio.Lock()

    async def get_momentum_ranking(
        self,
        top_n: Optional[int] = None,
        days: int = 10,
        weight_flow: float = DEFAULT_WEIGHT_FLOW,
        weight_turnover: float = DEFAULT_WEIGHT_TURNOVER,
        refresh: bool = False,
    ) -> List[Dict]:
        """计算双维度板块轮动势能排名（stale-while-revalidate 缓存）"""
        cache_key = (top_n, days, round(weight_flow, 4), round(weight_turnover, 4))

        if refresh:
            self._last_cache_hit = False
            return await self._compute_and_cache(cache_key, top_n, days, weight_flow, weight_turnover)

        if self._ranking_cache is not None and self._ranking_cache_key == cache_key:
            self._last_cache_hit = True
            if time.time() - self._ranking_cache_at >= self.CACHE_TTL_SECONDS:
                asyncio.create_task(
                    self._revalidate_worker(cache_key, top_n, days, weight_flow, weight_turnover)
                )
                logger.info("⚡ 返回过期缓存，后台异步重算已触发")
            return [{**item} for item in self._ranking_cache]

        self._last_cache_hit = False
        return await self._compute_and_cache(cache_key, top_n, days, weight_flow, weight_turnover)

    async def _compute_and_cache(
        self,
        cache_key: Tuple,
        top_n: Optional[int],
        days: int,
        weight_flow: float,
        weight_turnover: float,
    ) -> List[Dict]:
        """同步计算轮动势能并写入缓存"""
        sector_scores = await self._compute_ranking(top_n, days, weight_flow, weight_turnover)
        self._ranking_cache = sector_scores
        self._ranking_cache_at = time.time()
        self._ranking_cache_key = cache_key
        logger.info(f"🔁 momentum-ranking 计算完成并写入缓存: {len(sector_scores)} 个板块")
        return sector_scores

    async def _revalidate_worker(
        self,
        cache_key: Tuple,
        top_n: Optional[int],
        days: int,
        weight_flow: float,
        weight_turnover: float,
    ) -> None:
        """缓存过期后的后台异步重算"""
        if self._revalidate_lock.locked():
            return
        try:
            async with self._revalidate_lock:
                if (
                    self._ranking_cache is not None
                    and self._ranking_cache_key == cache_key
                    and time.time() - self._ranking_cache_at < self.CACHE_TTL_SECONDS
                ):
                    return
                logger.info("♻️ 缓存过期，后台异步重算 momentum-ranking...")
                await self._compute_and_cache(cache_key, top_n, days, weight_flow, weight_turnover)
        except Exception as e:
            logger.error(f"❌ 后台重算 momentum-ranking 失败: {e}", exc_info=True)

    async def _compute_ranking(
        self,
        top_n: Optional[int],
        days: int,
        weight_flow: float,
        weight_turnover: float,
    ) -> List[Dict]:
        """双维度轮动势能计算核心（不写缓存）

        核心改进：
            1. 资金流强度使用 5 日移动平均，过滤单日噪音
            2. 成交额占比变化使用 5 日移动平均 vs 前 5 日移动平均
            3. 信号分类基于均值趋势，更稳健
        """
        # 1. 获取所有板块当日成交额占比排名
        turnover_ranking = await self.rotation_service.get_industry_turnover_ranking(top_n=None)
        if not turnover_ranking:
            logger.warning("无法获取成交额占比排名")
            return []

        # 构建板块名 -> 占比数据的映射
        turnover_map: Dict[str, Dict] = {}
        for item in turnover_ranking:
            turnover_map[item["industry"]] = item

        # 2. 获取板块资金流聚合数据
        ma_window = self.FLOW_MA_WINDOW
        fetch_days = max(days + ma_window + 5, 20)
        flow_aggregated = await self.moneyflow_service.aggregate_sector_moneyflow(days=fetch_days)

        flow_by_date: Dict[str, Dict[str, Dict]] = defaultdict(dict)
        for item in flow_aggregated:
            flow_by_date[item["trade_date"]][item["industry"]] = item

        # 2b. 自动补数
        if not flow_by_date:
            logger.warning("资金流聚合数据为空，自动触发批量同步...")
            try:
                auto_days = min(max(fetch_days * 2, 30), 90)
                await self.moneyflow_service.fetch_and_store_moneyflow(
                    days_back=auto_days,
                    force=False,
                )
                refetch_days = min(max(fetch_days, auto_days), 90)
                flow_aggregated = await self.moneyflow_service.aggregate_sector_moneyflow(days=refetch_days)
                flow_by_date = defaultdict(dict)
                for item in flow_aggregated:
                    flow_by_date[item["trade_date"]][item["industry"]] = item
                logger.info(f"自动同步后，获取到 {len(flow_by_date)} 天的资金流数据")
            except Exception as e:
                logger.error(f"自动同步资金流数据失败: {e}")

        if not flow_by_date:
            logger.warning("资金流聚合数据仍为空，降级为纯成交额占比评分")
            return await self._score_by_turnover_only(turnover_ranking, turnover_map, days, top_n)

        latest_date = max(flow_by_date.keys())
        latest_flow = flow_by_date[latest_date]
        all_dates = sorted(flow_by_date.keys(), reverse=True)

        logger.info(f"最新资金流数据日期: {latest_date}, 覆盖 {len(latest_flow)} 个板块, 共 {len(all_dates)} 个交易日")

        # 3. 获取板块成交额占比历史趋势
        industry_list = list(turnover_map.keys())
        turnover_trends = await self.rotation_service.get_multi_industry_trend(
            industries=industry_list,
            days=days + self.TURNOVER_MA_WINDOW * 2 + 5,
        )

        # 4. 计算每个板块的双维度评分
        sector_scores = []
        flow_ma_values = []
        turnover_ma_change_values = []
        turnover_ratio_values = []
        sector_data_items = []

        missing_flow_industries = []
        industries_with_flow = 0

        for industry in industry_list:
            # ========== 计算资金流 5 日移动平均 ==========
            industry_flow_intensities = []

            for date in all_dates:
                if date in flow_by_date and industry in flow_by_date[date]:
                    fd = flow_by_date[date][industry]
                    mf_net = _safe_float(fd.get("total_main_force_net", 0))
                    mf_buy = _safe_float(fd.get("total_main_force_buy", 0))
                    mf_sell = _safe_float(fd.get("total_main_force_sell", 0))
                    total_mf = mf_buy + mf_sell
                    intensity = mf_net / total_mf * 100 if total_mf > 0 else 0
                    industry_flow_intensities.append(intensity)

                    if len(industry_flow_intensities) >= self.FLOW_MA_WINDOW:
                        break

            flow_ma = None
            flow_raw_latest = 0.0
            flow_net_latest = 0.0

            if len(industry_flow_intensities) >= self.FLOW_MA_WINDOW:
                flow_ma = sum(industry_flow_intensities[:self.FLOW_MA_WINDOW]) / self.FLOW_MA_WINDOW
                flow_raw_latest = industry_flow_intensities[0] if industry_flow_intensities else 0.0
                latest_flow_data = flow_by_date.get(all_dates[0], {}).get(industry, {})
                flow_net_latest = _safe_float(latest_flow_data.get("total_main_force_net", 0))
                industries_with_flow += 1
            else:
                if industry_flow_intensities:
                    flow_ma = sum(industry_flow_intensities) / len(industry_flow_intensities)
                    flow_raw_latest = industry_flow_intensities[0]
                missing_flow_industries.append(industry)

            # 计算资金流趋势方向
            flow_trend_direction = None
            if len(industry_flow_intensities) >= self.FLOW_MA_WINDOW * 2:
                recent_ma = sum(industry_flow_intensities[:self.FLOW_MA_WINDOW]) / self.FLOW_MA_WINDOW
                prev_ma = sum(industry_flow_intensities[self.FLOW_MA_WINDOW:self.FLOW_MA_WINDOW * 2]) / self.FLOW_MA_WINDOW
                if prev_ma != 0:
                    flow_trend_direction = (recent_ma - prev_ma) / abs(prev_ma)
                else:
                    flow_trend_direction = 0.0

            # ========== 计算成交额占比 5 日移动平均变化 ==========
            ratio = _safe_float(turnover_map.get(industry, {}).get("ratio", 0))
            trend_data = turnover_trends.get(industry, [])

            ratio_ma_change = 0.0
            ratio_latest_ma = 0.0
            ratio_prev_ma = 0.0

            if len(trend_data) >= self.TURNOVER_MA_WINDOW * 2:
                recent_ratios = [_safe_float(item.get("ratio", 0)) for item in trend_data[-self.TURNOVER_MA_WINDOW:]]
                prev_ratios = [_safe_float(item.get("ratio", 0)) for item in trend_data[-self.TURNOVER_MA_WINDOW * 2:-self.TURNOVER_MA_WINDOW]]

                if recent_ratios and prev_ratios:
                    ratio_latest_ma = sum(recent_ratios) / len(recent_ratios)
                    ratio_prev_ma = sum(prev_ratios) / len(prev_ratios)
                    if ratio_prev_ma > 0:
                        ratio_ma_change = (ratio_latest_ma - ratio_prev_ma) / ratio_prev_ma

            # 降级方案
            if ratio_ma_change == 0.0 and len(trend_data) >= self.TURNOVER_MA_WINDOW + 1:
                latest_ratio = _safe_float(trend_data[-1].get("ratio", 0))
                prev_5d_ratio = _safe_float(trend_data[-self.TURNOVER_MA_WINDOW - 1].get("ratio", 0))
                if prev_5d_ratio > 0:
                    ratio_ma_change = (latest_ratio - prev_5d_ratio) / prev_5d_ratio

            # 获取 stock_count - 修复点：从 turnover_map 获取
            stock_count = turnover_map.get(industry, {}).get("stock_count", 0)

            has_flow = len(industry_flow_intensities) >= 3

            item = {
                "industry": industry,
                "has_flow": has_flow,
                "flow_ma": _safe_round(flow_ma, 4) if flow_ma is not None else 0.0,
                "flow_raw_latest": _safe_round(flow_raw_latest, 4),
                "flow_net_latest": _safe_round(flow_net_latest, 2),
                "flow_trend_direction": _safe_round(flow_trend_direction, 4) if flow_trend_direction is not None else 0.0,
                "flow_data_days": len(industry_flow_intensities),
                "turnover_ratio": _safe_round(ratio, 4),
                "turnover_ma_change": _safe_round(ratio_ma_change, 4),
                "turnover_latest_ma": _safe_round(ratio_latest_ma, 4),
                "turnover_prev_ma": _safe_round(ratio_prev_ma, 4),
                "stock_count": stock_count,
                "data_date": latest_date,
            }
            sector_data_items.append(item)
            flow_ma_values.append(flow_ma if flow_ma is not None else 0.0)
            turnover_ma_change_values.append(ratio_ma_change)
            turnover_ratio_values.append(ratio)

        total_industries = len(industry_list)
        if missing_flow_industries:
            logger.warning(
                f"资金流数据覆盖: {industries_with_flow}/{total_industries} 个板块有足够数据, "
                f"缺少 {len(missing_flow_industries)} 个板块 "
                f"(如: {missing_flow_industries[:5]}{'...' if len(missing_flow_industries) > 5 else ''})"
            )

        # 5. 归一化评分
        flow_zscore_map = _zscore_normalize(flow_ma_values)
        turnover_change_zscore_map = _zscore_normalize(turnover_ma_change_values)
        turnover_base_scores = _min_max_normalize(turnover_ratio_values)

        # 6. 综合评分
        for item in sector_data_items:
            industry = item["industry"]

            flow_zscore = flow_zscore_map.get(item["flow_ma"], 0.0)
            flow_score = 50 + flow_zscore * (50 / 3)
            flow_score = max(0, min(100, flow_score))

            turnover_base = turnover_base_scores.get(item["turnover_ratio"], 50.0)
            change_zscore = turnover_change_zscore_map.get(item["turnover_ma_change"], 0.0)
            turnover_change_score = 50 + change_zscore * (50 / 3)
            turnover_change_score = max(0, min(100, turnover_change_score))

            turnover_score = 0.3 * turnover_base + 0.7 * turnover_change_score
            composite = weight_flow * flow_score + weight_turnover * turnover_score

            if not item["has_flow"]:
                signal = "中性"
                detail = f"资金流数据不足({item['flow_data_days']}天)，仅基于成交额占比评分"
            else:
                signal, detail = self._classify_signal(
                    flow_ma=item["flow_ma"],
                    flow_trend_direction=item["flow_trend_direction"],
                    turnover_ma_change=item["turnover_ma_change"],
                    turnover_ratio=item["turnover_ratio"],
                    flow_raw_latest=item["flow_raw_latest"],
                )

            sector_scores.append({
                "industry": industry,
                "rank": 0,
                "data_date": item["data_date"],
                "flow_score": _safe_round(flow_score, 2),
                "flow_ma": item["flow_ma"],
                "flow_raw_latest": item["flow_raw_latest"],
                "flow_net": item["flow_net_latest"],
                "flow_trend_direction": item["flow_trend_direction"],
                "flow_data_days": item["flow_data_days"],
                "turnover_score": _safe_round(turnover_score, 2),
                "turnover_ratio": item["turnover_ratio"],
                "turnover_ma_change": item["turnover_ma_change"],
                "turnover_latest_ma": item["turnover_latest_ma"],
                "turnover_prev_ma": item["turnover_prev_ma"],
                "composite_score": _safe_round(composite, 2),
                "signal": signal,
                "signal_detail": detail,
                "stock_count": item["stock_count"],
                "total_main_force_net": item["flow_net_latest"],
            })

        sector_scores = self._sort_by_signal(sector_scores)

        for i, item in enumerate(sector_scores):
            item["rank"] = i + 1

        if top_n and top_n > 0:
            sector_scores = sector_scores[:top_n]

        return sector_scores

    def get_ranking_cache_info(self) -> Dict:
        """获取排名接口的缓存状态"""
        info: Dict = {
            "hit": self._last_cache_hit,
            "stale": False,
            "ttl_seconds": self.CACHE_TTL_SECONDS,
            "cached_at": None,
            "age_seconds": None,
        }
        if self._ranking_cache_at is not None:
            from datetime import datetime as _dt
            info["cached_at"] = _dt.fromtimestamp(self._ranking_cache_at).strftime("%Y-%m-%d %H:%M:%S")
            info["age_seconds"] = round(time.time() - self._ranking_cache_at, 1)
            info["stale"] = info["age_seconds"] >= self.CACHE_TTL_SECONDS
        return info

    @staticmethod
    def _sort_by_signal(sector_scores: List[Dict]) -> List[Dict]:
        """按信号分类排序"""
        priority = SectorRotationMomentumService.SIGNAL_PRIORITY
        sector_scores.sort(key=lambda x: (priority.get(x["signal"], 99), -x["composite_score"]))
        return sector_scores

    async def get_momentum_trend(
        self,
        industry: str,
        days: int = 20,
    ) -> List[Dict]:
        """获取指定板块的势能趋势曲线"""
        turnover_trend = await self.rotation_service.get_industry_turnover_trend(
            industry=industry, days=days + self.TURNOVER_MA_WINDOW * 2
        )

        if not turnover_trend:
            logger.warning(f"行业 {industry} 无成交额趋势数据")
            return []

        flow_aggregated = await self.moneyflow_service.aggregate_sector_moneyflow(
            days=days * 2 + self.FLOW_MA_WINDOW * 2
        )

        flow_by_date: Dict[str, Dict] = {}
        for item in flow_aggregated:
            if item["industry"] == industry:
                flow_by_date[item["trade_date"]] = item

        trend_dates_compact = sorted(set(
            item["trade_date"].replace("-", "") for item in turnover_trend
        ))
        actual_flow_dates = set(flow_by_date.keys())
        covered_dates = actual_flow_dates & set(trend_dates_compact)
        coverage_ratio = len(covered_dates) / len(trend_dates_compact) if trend_dates_compact else 0

        if coverage_ratio < 0.5 and trend_dates_compact:
            logger.warning(
                f"资金流数据覆盖不足: {len(covered_dates)}/{len(trend_dates_compact)} 个交易日 "
                f"({coverage_ratio:.0%}), 自动同步..."
            )
            try:
                est_days = min(max(len(trend_dates_compact) * 2, 30), 90)
                await self.moneyflow_service.fetch_and_store_moneyflow(
                    trade_date=trend_dates_compact[-1],
                    days_back=est_days,
                    force=False,
                )
                refetch_days = min(max(days, est_days), 90)
                flow_aggregated = await self.moneyflow_service.aggregate_sector_moneyflow(days=refetch_days)
                flow_by_date = {}
                for item in flow_aggregated:
                    if item["industry"] == industry:
                        flow_by_date[item["trade_date"]] = item
            except Exception as e:
                logger.error(f"自动同步资金流数据失败: {e}")

        flow_intensity_by_date: Dict[str, float] = {}
        for date, fd in flow_by_date.items():
            mf_net = _safe_float(fd.get("total_main_force_net", 0))
            mf_buy = _safe_float(fd.get("total_main_force_buy", 0))
            mf_sell = _safe_float(fd.get("total_main_force_sell", 0))
            total_mf = mf_buy + mf_sell
            intensity = mf_net / total_mf * 100 if total_mf > 0 else 0
            flow_intensity_by_date[date] = intensity

        result = []
        missing_dates = 0

        for idx, trend_item in enumerate(turnover_trend):
            trade_date = trend_item["trade_date"]
            date_compact = trade_date.replace("-", "")

            ratio = _safe_float(trend_item.get("ratio", 0))

            lookback_start = max(0, idx - self.TURNOVER_MA_WINDOW + 1)
            lookback_end = idx + 1
            recent_ratios = [_safe_float(t.get("ratio", 0)) for t in turnover_trend[lookback_start:lookback_end]]
            prev_lookback_start = max(0, idx - self.TURNOVER_MA_WINDOW * 2 + 1)
            prev_lookback_end = max(0, idx - self.TURNOVER_MA_WINDOW + 1)
            prev_ratios = [_safe_float(t.get("ratio", 0)) for t in turnover_trend[prev_lookback_start:prev_lookback_end]]

            ratio_latest_ma = sum(recent_ratios) / len(recent_ratios) if recent_ratios else ratio
            ratio_prev_ma = sum(prev_ratios) / len(prev_ratios) if prev_ratios else ratio

            ratio_ma_change = 0.0
            if ratio_prev_ma > 0:
                ratio_ma_change = (ratio_latest_ma - ratio_prev_ma) / ratio_prev_ma

            flow_intensity = flow_intensity_by_date.get(date_compact)
            has_flow = flow_intensity is not None

            if not has_flow:
                missing_dates += 1
                flow_intensity = 0.0

            flow_ma = None
            if has_flow:
                flow_vals = []
                for offset in range(self.FLOW_MA_WINDOW):
                    check_date = self._get_date_offset(date_compact, offset)
                    if check_date in flow_intensity_by_date:
                        flow_vals.append(flow_intensity_by_date[check_date])
                if flow_vals:
                    flow_ma = sum(flow_vals) / len(flow_vals)

            if has_flow and flow_ma is not None:
                signal, _ = self._classify_signal(
                    flow_ma=flow_ma,
                    flow_trend_direction=None,
                    turnover_ma_change=ratio_ma_change,
                    turnover_ratio=ratio,
                    flow_raw_latest=flow_intensity,
                )
            else:
                signal = "中性"

            flow_score = 50 + (flow_ma / 5) if flow_ma is not None else 50
            flow_score = max(0, min(100, flow_score))
            turnover_score = 50 + ratio_ma_change * 100
            turnover_score = max(0, min(100, turnover_score))

            result.append({
                "trade_date": trade_date,
                "has_flow": has_flow,
                "flow_score": _safe_round(flow_score, 2),
                "flow_ma": _safe_round(flow_ma, 4) if flow_ma is not None else None,
                "turnover_score": _safe_round(turnover_score, 2),
                "composite_score": _safe_round(0.5 * flow_score + 0.5 * turnover_score, 2) if has_flow else _safe_round(turnover_score, 2),
                "main_force_net": _safe_round(flow_intensity_by_date.get(date_compact, 0), 2) if has_flow else 0.0,
                "turnover_ratio": ratio,
                "signal": signal,
            })

        if missing_dates > 0:
            logger.info(f"趋势中 {missing_dates}/{len(turnover_trend)} 个日期缺少资金流数据")

        return result

    def _get_date_offset(self, date_compact: str, offset_days: int) -> str:
        """获取指定日期往前 offset_days 天的日期"""
        try:
            dt = datetime.strptime(date_compact, "%Y%m%d")
            dt = dt - timedelta(days=offset_days)
            return dt.strftime("%Y%m%d")
        except (ValueError, TypeError):
            return date_compact

    async def _score_by_turnover_only(
        self,
        turnover_ranking: List[Dict],
        turnover_map: Dict[str, Dict],
        days: int,
        top_n: Optional[int] = None,
    ) -> List[Dict]:
        """降级方案：仅基于成交额占比进行评分"""
        industry_list = list(turnover_map.keys())
        turnover_trends = await self.rotation_service.get_multi_industry_trend(
            industries=industry_list,
            days=days + self.TURNOVER_MA_WINDOW * 2,
        )

        sector_scores = []
        ratio_values = []
        change_values = []
        sector_items = []

        for industry in industry_list:
            ratio = _safe_float(turnover_map.get(industry, {}).get("ratio", 0))
            stock_count = turnover_map.get(industry, {}).get("stock_count", 0)

            trend_data = turnover_trends.get(industry, [])

            ratio_ma_change = 0.0
            if len(trend_data) >= self.TURNOVER_MA_WINDOW * 2:
                recent_ratios = [_safe_float(item.get("ratio", 0)) for item in trend_data[-self.TURNOVER_MA_WINDOW:]]
                prev_ratios = [_safe_float(item.get("ratio", 0)) for item in trend_data[-self.TURNOVER_MA_WINDOW * 2:-self.TURNOVER_MA_WINDOW]]
                if recent_ratios and prev_ratios:
                    latest_ma = sum(recent_ratios) / len(recent_ratios)
                    prev_ma = sum(prev_ratios) / len(prev_ratios)
                    if prev_ma > 0:
                        ratio_ma_change = (latest_ma - prev_ma) / prev_ma

            if ratio_ma_change == 0.0 and len(trend_data) >= 6:
                latest_ratio = _safe_float(trend_data[-1].get("ratio", 0))
                prev_5d_ratio = _safe_float(trend_data[-6].get("ratio", 0))
                if prev_5d_ratio > 0:
                    ratio_ma_change = (latest_ratio - prev_5d_ratio) / prev_5d_ratio

            data_date = str(trend_data[-1].get("trade_date", "")).replace("-", "") if trend_data else None

            sector_items.append({
                "industry": industry,
                "turnover_ratio": _safe_round(ratio, 4),
                "turnover_ma_change": _safe_round(ratio_ma_change, 4),
                "stock_count": stock_count,
                "data_date": data_date,
            })
            ratio_values.append(ratio)
            change_values.append(ratio_ma_change)

        ratio_scores = _min_max_normalize(ratio_values)
        change_scores = _min_max_normalize(change_values)

        for item in sector_items:
            base_score = ratio_scores.get(item["turnover_ratio"], 50.0)
            change_score = change_scores.get(item["turnover_ma_change"], 50.0)
            turnover_score = 0.3 * base_score + 0.7 * change_score

            sector_scores.append({
                "industry": item["industry"],
                "rank": 0,
                "data_date": item["data_date"],
                "flow_score": 0.0,
                "flow_ma": 0.0,
                "flow_raw_latest": 0.0,
                "flow_net": 0.0,
                "flow_trend_direction": 0.0,
                "flow_data_days": 0,
                "turnover_score": _safe_round(turnover_score, 2),
                "turnover_ratio": item["turnover_ratio"],
                "turnover_ma_change": item["turnover_ma_change"],
                "turnover_latest_ma": 0.0,
                "turnover_prev_ma": 0.0,
                "composite_score": _safe_round(turnover_score, 2),
                "signal": "中性",
                "signal_detail": "无资金流数据，仅基于成交额占比评分",
                "stock_count": item["stock_count"],
                "total_main_force_net": 0.0,
            })

        sector_scores.sort(key=lambda x: x["composite_score"], reverse=True)
        for i, item in enumerate(sector_scores):
            item["rank"] = i + 1

        if top_n and top_n > 0:
            sector_scores = sector_scores[:top_n]

        return sector_scores

    def _classify_signal(
        self,
        flow_ma: float,
        flow_trend_direction: Optional[float],
        turnover_ma_change: float,
        turnover_ratio: float,
        flow_raw_latest: Optional[float] = None,
    ) -> Tuple[str, str]:
        """信号分类逻辑（基于移动平均，更稳健）"""
        is_inflow = flow_ma > self.FLOW_INFLOW_THRESHOLD
        is_outflow = flow_ma < self.FLOW_OUTFLOW_THRESHOLD
        is_rising = turnover_ma_change > self.TURNOVER_RISE_THRESHOLD
        is_falling = turnover_ma_change < self.TURNOVER_FALL_THRESHOLD
        is_low_ratio = turnover_ratio < self.RATIO_LOW_THRESHOLD
        is_high_ratio = turnover_ratio > self.RATIO_HIGH_THRESHOLD

        trend_confirmed = False
        if flow_trend_direction is not None:
            if is_inflow and flow_trend_direction > 0.05:
                trend_confirmed = True
            elif is_outflow and flow_trend_direction < -0.05:
                trend_confirmed = True

        if is_inflow and is_rising:
            if is_low_ratio:
                detail = f"低位放量+主力建仓，占比{turnover_ratio:.2f}%从低位启动，可能为新主线"
                if trend_confirmed:
                    detail += "，趋势确认"
                return "低位切换", detail
            if flow_ma > 2 * self.FLOW_INFLOW_THRESHOLD:
                detail = f"主力大幅流入(MA={flow_ma:.2f})+成交额占比提升({turnover_ma_change:+.2%})，强势主升浪"
                if trend_confirmed:
                    detail += "，趋势确认"
                return "真上涨", detail
            detail = f"主力资金流入(MA={flow_ma:.2f})+成交额占比提升({turnover_ma_change:+.2%})，趋势健康"
            if trend_confirmed:
                detail += "，趋势确认"
            return "真上涨", detail

        if is_outflow and is_rising:
            detail = f"成交额占比上升({turnover_ma_change:+.2%})但主力资金流出(MA={flow_ma:.2f})，警惕放量出货"
            if flow_raw_latest is not None and flow_raw_latest < flow_ma * 0.5:
                detail += "，单日流出加剧"
            return "假上涨", detail

        if is_outflow and is_falling and is_high_ratio:
            detail = f"高占比({turnover_ratio:.2f}%)回落+主力出逃(MA={flow_ma:.2f})，老主线退潮信号"
            return "高位出逃", detail

        if is_outflow and is_falling:
            detail = f"资金流出(MA={flow_ma:.2f})+占比下降({turnover_ma_change:+.2%})，量价齐跌宜回避"
            return "观望", detail

        if is_inflow and is_falling:
            detail = f"有资金流入(MA={flow_ma:.2f})但占比被分流下降({turnover_ma_change:+.2%})，关注持续性"
            return "中性", detail

        return "中性", f"资金面无明显信号(MA={flow_ma:.2f}, 占比变化={turnover_ma_change:+.2%})，维持中性判断"


# 全局单例
_momentum_service: Optional[SectorRotationMomentumService] = None


def get_sector_momentum_service() -> SectorRotationMomentumService:
    """获取板块轮动势能服务实例"""
    global _momentum_service
    if _momentum_service is None:
        _momentum_service = SectorRotationMomentumService()
    return _momentum_service