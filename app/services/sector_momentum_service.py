"""
双维度板块轮动势能模型

核心思想：
    结合「主力资金流强度」和「板块成交额占比趋势」两个维度，
    识别板块轮动中的真上涨、假上涨、低位切换、高位出逃信号。

维度一：主力资金流强度 (Flow Strength)
    - 板块主力净流入 / 板块总成交额 * 100
    - 反映主力资金对该板块的参与意愿和方向

维度二：成交额占比趋势 (Turnover Ratio Trend)
    - 板块成交额 / 全市场成交额 * 100
    - 反映市场资金对该板块的关注度变化

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
import logging
import math
import statistics
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
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return round(value, ndigits)


def _min_max_normalize(values: List[float], target_min: float = 0, target_max: float = 100) -> Dict[str, float]:
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


def _zscore_normalize(values: List[float]) -> Dict[str, float]:
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

    # 信号判定阈值
    # 主力资金：Z-Score > 0.5 为流入，< -0.5 为流出
    FLOW_INFLOW_THRESHOLD = 0.5
    FLOW_OUTFLOW_THRESHOLD = -0.5

    # 成交额占比趋势：变化率 > 5% 为上升，< -5% 为下降
    TURNOVER_RISE_THRESHOLD = 0.05  # 5% 相对变化
    TURNOVER_FALL_THRESHOLD = -0.05

    # 占比绝对水平阈值（用于判断低位/高位）
    RATIO_LOW_THRESHOLD = 1.0  # 占比 < 1% 视为低位
    RATIO_HIGH_THRESHOLD = 5.0  # 占比 > 5% 视为高位

    # 信号排序优先级（数值越小越靠前）：真上涨排最前
    SIGNAL_PRIORITY = {
        "真上涨": 0,   # 主力流入 + 占比提升，最健康
        "低位切换": 1,  # 低位放量 + 主力建仓，潜在新主线
        "假上涨": 2,   # 占比升但主力流出，警示信号
        "中性": 3,     # 无明显信号
        "观望": 4,     # 资金流出 + 占比下降，量价齐跌
        "高位出逃": 5,  # 高占比回落 + 主力出逃，退潮信号
    }

    def __init__(self):
        self.rotation_service = get_sector_rotation_service()
        self.moneyflow_service = get_sector_moneyflow_service()

    async def get_momentum_ranking(
        self,
        top_n: Optional[int] = None,
        days: int = 10,
        weight_flow: float = DEFAULT_WEIGHT_FLOW,
        weight_turnover: float = DEFAULT_WEIGHT_TURNOVER,
    ) -> List[Dict]:
        """计算双维度板块轮动势能排名

        Args:
            top_n: 返回前 N 个板块
            days: 回溯天数（用于计算趋势）
            weight_flow: 资金流维度权重
            weight_turnover: 成交额占比维度权重

        Returns:
            按综合评分降序排列的板块势能列表
        """
        db = get_mongo_db()

        # 1. 获取所有板块当日成交额占比排名
        turnover_ranking = await self.rotation_service.get_industry_turnover_ranking(top_n=None)
        if not turnover_ranking:
            logger.warning("无法获取成交额占比排名")
            return []

        # 构建板块名 -> 占比数据的映射
        turnover_map: Dict[str, Dict] = {}
        for item in turnover_ranking:
            turnover_map[item["industry"]] = item

        # 2. 获取板块资金流聚合数据（最新可用日期）
        flow_aggregated = await self.moneyflow_service.aggregate_sector_moneyflow(days=min(days, 5))
        # 按日期分组，取最新日期
        flow_by_date: Dict[str, Dict[str, Dict]] = defaultdict(dict)
        for item in flow_aggregated:
            flow_by_date[item["trade_date"]][item["industry"]] = item

        # 2b. 自动补数：如果资金流数据为空，自动触发批量同步（多取一些历史数据）
        if not flow_by_date:
            logger.warning("资金流聚合数据为空，自动触发批量同步...")
            try:
                auto_days = min(max(days * 2, 20), 60)  # 至少 20 天，最多 60 天
                await self.moneyflow_service.fetch_and_store_moneyflow(
                    days_back=auto_days,
                    force=False,
                )
                # 重新获取（同步了大量数据，这里取足够的回溯天数）
                refetch_days = min(max(days, auto_days), 60)
                flow_aggregated = await self.moneyflow_service.aggregate_sector_moneyflow(days=refetch_days)
                flow_by_date = defaultdict(dict)
                for item in flow_aggregated:
                    flow_by_date[item["trade_date"]][item["industry"]] = item
                logger.info(f"自动同步后，获取到 {len(flow_by_date)} 天的资金流数据")
            except Exception as e:
                logger.error(f"自动同步资金流数据失败: {e}")

        if not flow_by_date:
            logger.warning("资金流聚合数据仍为空，降级为纯成交额占比评分")
            return self._score_by_turnover_only(turnover_ranking, turnover_map, days, top_n)

        # 取最新日期
        latest_date = max(flow_by_date.keys())
        latest_flow = flow_by_date[latest_date]

        logger.info(f"最新资金流数据日期: {latest_date}, 覆盖 {len(latest_flow)} 个板块")

        # 3. 获取板块成交额占比历史趋势（用于计算变化）
        industry_list = list(turnover_map.keys())
        turnover_trends = await self.rotation_service.get_multi_industry_trend(
            industries=industry_list,
            days=days,
        )

        # 4. 计算每个板块的双维度评分
        sector_scores = []

        # 准备归一化数据
        flow_raw_values = []
        turnover_ratio_values = []
        turnover_change_values = []

        sector_data_items = []

        # 统计有多少板块缺少资金流数据
        missing_flow_industries = []
        industries_with_flow = 0

        for industry in industry_list:
            # 资金流数据
            flow_data = latest_flow.get(industry, {})
            has_flow = bool(flow_data)  # 是否有该板块的资金流数据

            if has_flow:
                industries_with_flow += 1
            else:
                missing_flow_industries.append(industry)

            main_force_net = _safe_float(flow_data.get("total_main_force_net", 0))
            # 主力资金强度 = 净流入 / (买入+卖出)
            total_mf = _safe_float(flow_data.get("total_main_force_buy", 0)) + _safe_float(flow_data.get("total_main_force_sell", 0))
            flow_intensity = main_force_net / total_mf * 100 if total_mf > 0 else 0

            # 成交额占比
            ratio = _safe_float(turnover_map.get(industry, {}).get("ratio", 0))

            # 计算占比趋势变化
            trend_data = turnover_trends.get(industry, [])
            ratio_change_5d = 0.0
            ratio_change_10d = 0.0
            if len(trend_data) >= 6:
                latest_ratio = _safe_float(trend_data[-1].get("ratio", 0))
                prev_5d_ratio = _safe_float(trend_data[-6].get("ratio", 0))
                ratio_change_5d = (latest_ratio - prev_5d_ratio) / prev_5d_ratio if prev_5d_ratio > 0 else 0
            if len(trend_data) >= 11:
                prev_10d_ratio = _safe_float(trend_data[-11].get("ratio", 0))
                ratio_change_10d = (latest_ratio - prev_10d_ratio) / prev_10d_ratio if prev_10d_ratio > 0 else 0

            # 计算5日资金流趋势
            flow_trend_5d = None
            flow_trend_10d = None
            if len(flow_by_date) >= 5:
                dates_sorted = sorted(flow_by_date.keys(), reverse=True)
                recent_5_dates = dates_sorted[:5]
                flow_vals = []
                for d in recent_5_dates:
                    fd = flow_by_date[d].get(industry, {})
                    fv = _safe_float(fd.get("total_main_force_net", 0))
                    flow_vals.append(fv)
                if len(flow_vals) >= 2:
                    flow_trend_5d = sum(flow_vals) / len(flow_vals)

            stock_count = flow_data.get("stock_count", 0) or turnover_map.get(industry, {}).get("stock_count", 0)

            item = {
                "industry": industry,
                "has_flow": has_flow,
                "flow_raw": _safe_round(flow_intensity, 4),
                "flow_net": _safe_round(main_force_net, 2),
                "turnover_ratio": _safe_round(ratio, 4),
                "turnover_change_5d": _safe_round(ratio_change_5d, 4),
                "turnover_change_10d": _safe_round(ratio_change_10d, 4),
                "flow_trend_5d": _safe_round(flow_trend_5d, 2) if flow_trend_5d is not None else None,
                "stock_count": stock_count,
            }
            sector_data_items.append(item)
            flow_raw_values.append(flow_intensity)
            turnover_ratio_values.append(ratio)
            turnover_change_values.append(ratio_change_5d)

        # 记录资金流数据覆盖情况
        total_industries = len(industry_list)
        if missing_flow_industries:
            logger.warning(
                f"资金流数据覆盖: {industries_with_flow}/{total_industries} 个板块有数据, "
                f"缺少 {len(missing_flow_industries)} 个板块的流数据 "
                f"(如: {missing_flow_industries[:5]}{'...' if len(missing_flow_industries) > 5 else ''}), "
                f"这些板块将使用 turnover 单维度评分"
            )

        # 5. 归一化评分
        flow_scores = _min_max_normalize(flow_raw_values)
        # 成交额占比直接用原始值归一化（越大越好，代表关注度高）
        # 但我们要考虑变化趋势，所以用变化率
        turnover_change_scores = _min_max_normalize(turnover_change_values)
        # 同时给当前占比一个基础分
        turnover_base_scores = _min_max_normalize(turnover_ratio_values)

        # 6. 综合评分
        for item in sector_data_items:
            industry = item["industry"]

            # 资金流得分
            flow_score = flow_scores.get(item["flow_raw"], 50.0)

            # 成交额占比得分 = 基础占比 * 0.4 + 变化趋势 * 0.6
            turnover_base = turnover_base_scores.get(item["turnover_ratio"], 50.0)
            turnover_change = turnover_change_scores.get(item["turnover_change_5d"], 50.0)
            turnover_score = 0.4 * turnover_base + 0.6 * turnover_change

            # 综合评分
            composite = weight_flow * flow_score + weight_turnover * turnover_score

            # 信号分类
            if not item["has_flow"]:
                # 缺少资金流数据，降级为纯 turnover 信号
                signal = "中性"
                detail = f"无资金流数据，仅基于成交额占比({item['turnover_ratio']:.2f}%)评分"
            else:
                signal, detail = self._classify_signal(
                    flow_raw=item["flow_raw"],
                    flow_trend_5d=item["flow_trend_5d"],
                    turnover_change_5d=item["turnover_change_5d"],
                    turnover_ratio=item["turnover_ratio"],
                )

            # 数据所属交易日（YYYYMMDD）：
            # - 有资金流数据 → 最新资金流交易日
            # - 无资金流数据（降级）→ 成交额占比趋势的最新交易日
            if item["has_flow"]:
                data_date = latest_date
            else:
                trend_data = turnover_trends.get(industry, [])
                data_date = str(trend_data[-1].get("trade_date", "")).replace("-", "") if trend_data else None

            sector_scores.append({
                "industry": industry,
                "rank": 0,
                "data_date": data_date,
                "flow_score": _safe_round(flow_score, 2),
                "flow_raw": item["flow_raw"],
                "flow_net": item["flow_net"],
                "flow_trend_5d": item["flow_trend_5d"],
                "turnover_score": _safe_round(turnover_score, 2),
                "turnover_ratio": item["turnover_ratio"],
                "turnover_trend_5d": item["turnover_change_5d"],
                "turnover_trend_10d": item["turnover_change_10d"],
                "composite_score": _safe_round(composite, 2),
                "signal": signal,
                "signal_detail": detail,
                "stock_count": item["stock_count"],
                "total_main_force_net": item["flow_net"],
            })

        # 按信号分类排序（真上涨排最前），同类内按综合评分降序
        sector_scores = self._sort_by_signal(sector_scores)

        # 填充排名
        for i, item in enumerate(sector_scores):
            item["rank"] = i + 1

        if top_n and top_n > 0:
            sector_scores = sector_scores[:top_n]

        return sector_scores

    @staticmethod
    def _sort_by_signal(sector_scores: List[Dict]) -> List[Dict]:
        """按信号分类排序（真上涨排最前），同类内按综合评分降序

        优先级: 真上涨 > 低位切换 > 假上涨 > 中性 > 观望 > 高位出逃
        """
        priority = SectorRotationMomentumService.SIGNAL_PRIORITY
        sector_scores.sort(key=lambda x: (priority.get(x["signal"], 99), -x["composite_score"]))
        return sector_scores

    async def get_momentum_trend(
        self,
        industry: str,
        days: int = 20,
    ) -> List[Dict]:
        """获取指定板块的势能趋势曲线（历史评分变化）

        Args:
            industry: 行业名称
            days: 回溯天数

        Returns:
            按日期升序的势能趋势列表
        """
        db = get_mongo_db()

        # 1. 获取板块成交额占比趋势
        turnover_trend = await self.rotation_service.get_industry_turnover_trend(
            industry=industry, days=days
        )

        if not turnover_trend:
            logger.warning(f"行业 {industry} 无成交额趋势数据")
            return []

        # 2. 获取板块资金流时间序列
        flow_aggregated = await self.moneyflow_service.aggregate_sector_moneyflow(
            days=days * 2  # 多取一些
        )

        # 按日期索引（YYYYMMDD -> flow_item）
        flow_by_date: Dict[str, Dict] = {}
        for item in flow_aggregated:
            if item["industry"] == industry:
                flow_by_date[item["trade_date"]] = item

        # 2b. 自动补数：如果资金流数据不足，自动批量同步
        trend_dates_compact = sorted(set(
            item["trade_date"].replace("-", "") for item in turnover_trend
        ))
        actual_flow_dates = set(flow_by_date.keys())
        covered_dates = actual_flow_dates & set(trend_dates_compact)
        coverage_ratio = len(covered_dates) / len(trend_dates_compact) if trend_dates_compact else 0

        if coverage_ratio < 0.5 and trend_dates_compact:
            logger.warning(
                f"资金流数据覆盖不足: {len(covered_dates)}/{len(trend_dates_compact)} 个交易日 "
                f"({coverage_ratio:.0%}), 自动同步覆盖趋势日期范围（多取一些历史数据）..."
            )
            try:
                est_days = min(max(len(trend_dates_compact) * 2, 20), 60)  # 至少 20 天，最多 60 天
                await self.moneyflow_service.fetch_and_store_moneyflow(
                    trade_date=trend_dates_compact[-1],
                    days_back=est_days,
                    force=False,
                )
                # 重新获取（同步了大量数据，取足够的回溯天数）
                refetch_days = min(max(days, est_days), 60)
                flow_aggregated = await self.moneyflow_service.aggregate_sector_moneyflow(days=refetch_days)
                flow_by_date = {}
                for item in flow_aggregated:
                    if item["industry"] == industry:
                        flow_by_date[item["trade_date"]] = item
                actual_flow_dates = set(flow_by_date.keys())
                covered_dates = actual_flow_dates & set(trend_dates_compact)
                new_coverage = len(covered_dates) / len(trend_dates_compact) if trend_dates_compact else 0
                logger.info(f"自动同步后资金流覆盖: {len(covered_dates)}/{len(trend_dates_compact)} ({new_coverage:.0%})")
            except Exception as e:
                logger.error(f"自动同步资金流数据失败: {e}")

        # 3. 合并成交额占比数据和资金流数据
        # 成交额数据日期格式 YYYY-MM-DD, 资金流日期格式 YYYYMMDD
        result = []

        missing_dates = 0
        for trend_item in turnover_trend:
            trade_date = trend_item["trade_date"]
            date_compact = trade_date.replace("-", "")

            flow_data = flow_by_date.get(date_compact, {})
            has_flow = bool(flow_data)
            if not has_flow:
                missing_dates += 1

            # 计算资金流强度
            mf_net = _safe_float(flow_data.get("total_main_force_net", 0))
            mf_buy = _safe_float(flow_data.get("total_main_force_buy", 0))
            mf_sell = _safe_float(flow_data.get("total_main_force_sell", 0))
            total_mf = mf_buy + mf_sell
            flow_intensity = mf_net / total_mf * 100 if total_mf > 0 else 0

            ratio = _safe_float(trend_item.get("ratio", 0))

            # 信号分类
            if has_flow:
                signal, _ = self._classify_signal(
                    flow_raw=flow_intensity,
                    flow_trend_5d=None,
                    turnover_change_5d=0,
                    turnover_ratio=ratio,
                )
            else:
                signal = "中性"

            result.append({
                "trade_date": trade_date,
                "has_flow": has_flow,
                "flow_score": _safe_round(max(0, min(100, (flow_intensity + 10) * 5)), 2),  # 映射到 0-100
                "turnover_score": _safe_round(min(100, ratio * 20), 2),  # 映射到 0-100
                "composite_score": 0,  # Placeholder
                "main_force_net": mf_net,
                "turnover_ratio": ratio,
                "signal": signal,
            })

        if missing_dates > 0:
            logger.info(f"趋势中 {missing_dates}/{len(turnover_trend)} 个日期缺少资金流数据，已使用 turnover-only 评分")

        # 计算综合得分（简单归一化）
        if result:
            # 有 flow 数据的日期参与 flow 归一化，无 flow 的用 turnover 综合
            flow_vals = [r["flow_score"] for r in result]
            turnover_vals = [r["turnover_score"] for r in result]

            flow_norm = _min_max_normalize(flow_vals, 0, 100)
            turnover_norm = _min_max_normalize(turnover_vals, 0, 100)

            for r in result:
                flow_s = flow_norm.get(r["flow_score"], 50)
                turn_s = turnover_norm.get(r["turnover_score"], 50)
                r["flow_score"] = _safe_round(flow_s, 2)
                r["turnover_score"] = _safe_round(turn_s, 2)

                if r["has_flow"]:
                    r["composite_score"] = _safe_round(0.5 * flow_s + 0.5 * turn_s, 2)
                else:
                    r["composite_score"] = _safe_round(turn_s, 2)  # 无 flow 数据，纯 turnover 评分

        return result

    async def _score_by_turnover_only(
        self,
        turnover_ranking: List[Dict],
        turnover_map: Dict[str, Dict],
        days: int,
        top_n: Optional[int] = None,
    ) -> List[Dict]:
        """降级方案：仅基于成交额占比进行评分（资金流数据缺失时使用）

        使用成交额占比 + 占比变化趋势作为评分依据，
        所有 flow 相关字段置为 0。

        Args:
            turnover_ranking: 成交额占比排名原始数据
            turnover_map: 板块名 -> 占比数据的映射
            days: 趋势回溯天数
            top_n: 返回前 N 个板块

        Returns:
            与 get_momentum_ranking 格式一致的势能列表
        """
        industry_list = list(turnover_map.keys())
        turnover_trends = await self.rotation_service.get_multi_industry_trend(
            industries=industry_list,
            days=days,
        )

        sector_scores = []
        ratio_values = []
        change_values = []
        sector_items = []

        for industry in industry_list:
            ratio = _safe_float(turnover_map.get(industry, {}).get("ratio", 0))
            stock_count = turnover_map.get(industry, {}).get("stock_count", 0)

            trend_data = turnover_trends.get(industry, [])
            ratio_change_5d = 0.0
            if len(trend_data) >= 6:
                latest_ratio = _safe_float(trend_data[-1].get("ratio", 0))
                prev_5d_ratio = _safe_float(trend_data[-6].get("ratio", 0))
                ratio_change_5d = (latest_ratio - prev_5d_ratio) / prev_5d_ratio if prev_5d_ratio > 0 else 0

            # 数据所属交易日（YYYYMMDD）：成交额占比趋势的最新交易日
            data_date = str(trend_data[-1].get("trade_date", "")).replace("-", "") if trend_data else None

            sector_items.append({
                "industry": industry,
                "turnover_ratio": _safe_round(ratio, 4),
                "turnover_change_5d": _safe_round(ratio_change_5d, 4),
                "stock_count": stock_count,
                "data_date": data_date,
            })
            ratio_values.append(ratio)
            change_values.append(ratio_change_5d)

        # 归一化
        ratio_scores = _min_max_normalize(ratio_values)
        change_scores = _min_max_normalize(change_values)

        for item in sector_items:
            base_score = ratio_scores.get(item["turnover_ratio"], 50.0)
            change_score = change_scores.get(item["turnover_change_5d"], 50.0)
            turnover_score = 0.4 * base_score + 0.6 * change_score

            sector_scores.append({
                "industry": item["industry"],
                "rank": 0,
                "data_date": item["data_date"],
                "flow_score": 0.0,
                "flow_raw": 0.0,
                "flow_net": 0.0,
                "flow_trend_5d": None,
                "turnover_score": _safe_round(turnover_score, 2),
                "turnover_ratio": item["turnover_ratio"],
                "turnover_trend_5d": item["turnover_change_5d"],
                "turnover_trend_10d": 0.0,
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
        flow_raw: float,
        flow_trend_5d: Optional[float],
        turnover_change_5d: float,
        turnover_ratio: float,
    ) -> Tuple[str, str]:
        """信号分类逻辑

        根据资金流强度和成交额占比变化，生成交易信号。

        Args:
            flow_raw: 当日资金流强度
            flow_trend_5d: 5日资金流趋势
            turnover_change_5d: 5日成交额占比变化率
            turnover_ratio: 当前成交额占比(%)

        Returns:
            (signal_label, detail_description)
        """
        is_inflow = flow_raw > self.FLOW_INFLOW_THRESHOLD
        is_outflow = flow_raw < self.FLOW_OUTFLOW_THRESHOLD
        is_rising = turnover_change_5d > self.TURNOVER_RISE_THRESHOLD
        is_falling = turnover_change_5d < self.TURNOVER_FALL_THRESHOLD
        is_low_ratio = turnover_ratio < self.RATIO_LOW_THRESHOLD
        is_high_ratio = turnover_ratio > self.RATIO_HIGH_THRESHOLD

        # 真上涨：主力流入 + 占比提升
        if is_inflow and is_rising:
            if is_low_ratio:
                return "低位切换", f"低位放量+主力建仓，占比{turnover_ratio:.2f}%从低位启动，可能为新主线"
            if flow_raw > 2 * self.FLOW_INFLOW_THRESHOLD:
                return "真上涨", f"主力大幅流入({flow_raw:.2f})+成交额占比提升({turnover_change_5d:+.2%})，强势主升浪"
            return "真上涨", f"主力资金流入+成交额占比提升，趋势健康"

        # 假上涨：主力流出 + 占比提升（放量出货）
        if is_outflow and is_rising:
            return "假上涨", f"成交额占比上升({turnover_change_5d:+.2%})但主力资金流出({flow_raw:.2f})，警惕放量出货"

        # 高位出逃：主力流出 + 占比下降 + 高占比
        if is_outflow and is_falling and is_high_ratio:
            return "高位出逃", f"高占比({turnover_ratio:.2f}%)回落+主力出逃，老主线退潮信号"

        # 观望：主力流出 + 占比下降
        if is_outflow and is_falling:
            return "观望", f"资金流出+占比下降，量价齐跌宜回避"

        # 资金流入但占比下降（可能被其他板块分流）
        if is_inflow and is_falling:
            return "中性", f"有资金流入但占比被分流下降，关注持续性"

        # 默认中性
        return "中性", "资金面无明显信号，维持中性判断"


# 全局单例
_momentum_service: Optional[SectorRotationMomentumService] = None


def get_sector_momentum_service() -> SectorRotationMomentumService:
    """获取板块轮动势能服务实例"""
    global _momentum_service
    if _momentum_service is None:
        _momentum_service = SectorRotationMomentumService()
    return _momentum_service
