# TradingAgents/graph/signal_processing.py

"""
SignalProcessor - 数据驱动的交易信号处理模块

不再依赖 LLM 解析 final_trade_decision 文本，而是使用：
1. TET (Trend-Emotion-Timing) 量化指标 → 计算 action/confidence/risk_score
2. Simplified Report 的结构化内容 → 提供 reasoning
3. 股票市场数据 → 推 target_price

这些数据全部来自 Multi-Agent 图执行后的 state，无需额外 LLM 调用。
"""

import re
import json
import math
from typing import Dict, Any, Optional

from langchain_openai import ChatOpenAI

# 导入统一日志系统
from tradingagents.utils.logging_init import get_logger
from tradingagents.utils.tool_logging import log_graph_module
logger = get_logger("graph.signal_processing")


class SignalProcessor:
    """数据驱动的交易信号处理器，基于 TET 量化指标和 Simplified Report 生成决策结构。"""

    def __init__(self, quick_thinking_llm: Optional[ChatOpenAI] = None):
        """初始化信号处理器。

        Args:
            quick_thinking_llm: 保留参数，不再使用（兼容旧初始化调用）
        """
        self.quick_thinking_llm = quick_thinking_llm
        logger.info("🔧 [SignalProcessor] 已初始化为数据驱动模式（无LLM调用）")

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    @log_graph_module("signal_processing")
    def process_signal(
        self,
        full_signal: str,
        stock_symbol: str = None,
        simplified_report: dict = None,
        market_report: str = None,
        sentiment_report: str = None,
        trade_date: str = None,
    ) -> dict:
        """处理交易信号，生成结构化决策信息。

        Args:
            full_signal: final_trade_decision 文本（备用来源）
            stock_symbol: 股票代码
            simplified_report: Simplified Report 节点输出的结构化 JSON
            market_report: 市场分析报告文本（含 TET 数值）
            sentiment_report: 情绪分析报告文本
            trade_date: 分析日期 (YYYY-MM-DD)，用于数据新鲜度验证

        Returns:
            dict: 包含 action / target_price / confidence / risk_score / reasoning 的决策字典
        """
        # ---- 1. 提取 TET 指标 ----
        tet = self._extract_tet(simplified_report, market_report)
        trend_score = tet.get("trend_score", 0.0)
        emotion_index = tet.get("emotion_index", 0.0)
        anchored_trend = tet.get("anchored_trend_score", 0.0)
        timing_indicator = tet.get("timing_indicator", 0.0)

        logger.info(
            f"📊 [SignalProcessor] TET指标: trend={trend_score:.3f}, emotion={emotion_index:.3f}, "
            f"anchored={anchored_trend:.3f}, timing={timing_indicator:.3f}",
            extra={
                "stock_symbol": stock_symbol,
                "trend_score": trend_score,
                "emotion_index": emotion_index,
                "anchored_trend": anchored_trend,
                "timing_indicator": timing_indicator,
            },
        )

        # ---- 2. 提取数据质量信息 ----
        data_quality = self._extract_data_quality(market_report, trade_date)
        data_sufficiency = data_quality["sufficiency"]
        data_freshness = data_quality["freshness"]
        data_rows = data_quality["rows"]
        logger.info(
            f"📊 [SignalProcessor] 数据质量: sufficiency={data_sufficiency:.2f}, "
            f"freshness={data_freshness:.2f}, rows={data_rows}"
        )

        # ---- 3. 提取布林带等市场技术指标 ----
        bollinger = self._extract_bollinger(market_report)
        current_price = self._extract_current_price(market_report) or bollinger.get("current_price")

        # ---- 4. 计算 action（优先从 simplified_report 解析） ----
        action = self._compute_action(
            simplified_report, timing_indicator, trend_score, full_signal,
        )
        logger.info(f"🎯 [SignalProcessor] 决策动作: {action}")

        # ---- 5. 计算 target_price（优先用布林带，再试 insight 中的价格，最后 TET 推算） ----
        target_price = self._compute_target_price(
            simplified_report, action, current_price, trend_score, bollinger,
        )
        logger.info(
            f"💰 [SignalProcessor] 目标价: {target_price} (当前价: {current_price})"
        )

        # ---- 6. 验证 TET 计算正确性 ----
        tet_correctness = self._verify_tet_correctness(
            tet, trend_score, emotion_index, timing_indicator, anchored_trend,
            simplified_report, market_report,
        )

        # ---- 7. 计算 confidence（三维度：数据充足 + 数据新鲜 + TET正确）----
        confidence = self._compute_confidence(
            data_sufficiency=data_sufficiency,
            data_freshness=data_freshness,
            tet_correctness=tet_correctness,
        )
        logger.info(f"📈 [SignalProcessor] 置信度: {confidence:.3f}")

        # ---- 8. 计算 risk_score ----
        risk_score = self._compute_risk_score(
            simplified_report, trend_score, emotion_index, anchored_trend, confidence,
        )
        logger.info(f"⚠️  [SignalProcessor] 风险评分: {risk_score:.3f}")

        # ---- 9. 获取 reasoning ----
        reasoning = self._build_reasoning(
            simplified_report, tet, action, current_price, target_price,
            confidence, risk_score, data_quality,
        )

        # ---- 7. 获取 reasoning ----
        reasoning = self._build_reasoning(
            simplified_report, tet, action, current_price, target_price,
            confidence, risk_score, data_quality,
        )

        result = {
            "action": action,
            "target_price": target_price,
            "confidence": round(confidence, 4),
            "risk_score": round(risk_score, 4),
            "reasoning": reasoning,
        }
        logger.info(
            f"✅ [SignalProcessor] 处理完成: action={action}, target_price={target_price}, "
            f"confidence={confidence:.2f}, risk_score={risk_score:.2f}",
            extra={
                "action": action,
                "target_price": target_price,
                "confidence": confidence,
                "stock_symbol": stock_symbol,
            },
        )
        return result

    # ------------------------------------------------------------------
    # 数据质量提取
    # ------------------------------------------------------------------

    def _extract_data_quality(self, market_report: str, trade_date: str = None) -> dict:
        """从 market_report 中提取数据质量指标。

        market_report 格式参见 data_source_manager.py:733-735：
          "数据期间: 2024-01-01 至 2026-07-10"
          "数据条数: 1200条 (展示最近5个交易日)"
          "💰 最新价格: ¥XX.XX"

        Returns:
            dict: {"sufficiency": 0-1, "freshness": 0-1, "rows": int,
                   "data_start": str, "data_end": str, "years_span": float}
        """
        rows = 0
        data_start = None
        data_end = None
        years_span = 0.0

        if market_report and isinstance(market_report, str):
            # 解析 "数据期间: START 至 END"
            period_match = re.search(
                r"数据期间:\s*(\d{4}-\d{2}-\d{2})\s*至\s*(\d{4}-\d{2}-\d{2})",
                market_report,
            )
            if period_match:
                data_start = period_match.group(1)
                data_end = period_match.group(2)
                try:
                    from datetime import datetime
                    s = datetime.strptime(data_start, "%Y-%m-%d")
                    e = datetime.strptime(data_end, "%Y-%m-%d")
                    days_span = (e - s).days
                    years_span = days_span / 365.0
                except ValueError:
                    pass

            # 解析 "数据条数: XXX条"
            row_match = re.search(r"数据条数:\s*(\d+)\s*条", market_report)
            if row_match:
                rows = int(row_match.group(1))

        # ---- 数据充足度评分 ----
        # 5年 ≈ 1250个交易日
        # 评分曲线：0行→0.1, 250行→0.3, 500行→0.5, 1250行→1.0
        if rows >= 1250:
            sufficiency = 1.0
        elif rows >= 500:
            sufficiency = 0.5 + 0.5 * (rows - 500) / 750
        elif rows >= 250:
            sufficiency = 0.3 + 0.2 * (rows - 250) / 250
        elif rows > 0:
            sufficiency = 0.1 + 0.2 * rows / 250
        elif years_span >= 5.0:
            # 无行数但日期范围超过5年
            sufficiency = 0.8
        else:
            sufficiency = 0.3  # 无法确定时保守给0.3

        # ---- 数据新鲜度评分 ----
        # 用实际数据截止日期 data_end 与 trade_date 对比
        # 正确处理非交易日场景
        freshness = self._calc_freshness(data_end or trade_date, trade_date)

        logger.info(
            f"📊 [数据质量] rows={rows}, sufficiency={sufficiency:.2f}, "
            f"freshness={freshness:.2f}, period={data_start}~{data_end}, "
            f"years={years_span:.1f}"
        )
        return {
            "sufficiency": sufficiency,
            "freshness": freshness,
            "rows": rows,
            "data_start": data_start,
            "data_end": data_end,
            "years_span": years_span,
        }

    def _calc_freshness(self, latest_data_date: str, trade_date: str = None) -> float:
        """计算数据新鲜度。

        核心逻辑：从 market_report 提取数据实际截止日期（data_end），
        与 trade_date（分析目标日期）比较。如果数据截至日 >= 分析日
        → 数据覆盖了目标日，满分。如果差1~2天→仍新鲜（含周末容差）。

        如果差>5天→数据偏旧，confidence 会降低。
        """
        from datetime import datetime, timezone, timedelta

        china_tz = timezone(timedelta(hours=8))

        target = latest_data_date or trade_date
        if not target:
            return 0.5

        try:
            data_dt = datetime.strptime(target[:10], "%Y-%m-%d")
            data_date = data_dt.replace(tzinfo=china_tz).date()
        except (ValueError, TypeError):
            return 0.5

        # 如果有 trade_date，以 trade_date 为基准判断
        if trade_date:
            try:
                analysis_dt = datetime.strptime(trade_date[:10], "%Y-%m-%d")
                analysis_date = analysis_dt.replace(tzinfo=china_tz).date()
            except (ValueError, TypeError):
                analysis_date = None
        else:
            analysis_date = None

        if analysis_date:
            days_diff = (analysis_date - data_date).days
            if days_diff <= 0:
                return 1.0   # 数据截止 >= 分析日
            elif days_diff <= 1:
                return 0.95  # 差1天
            elif days_diff <= 3:
                return 0.85  # 含周末
            elif days_diff <= 5:
                return 0.70
            elif days_diff <= 10:
                return 0.50
            elif days_diff <= 30:
                return 0.30
            else:
                return 0.15

        # 无 trade_date → 与当前日期对比
        today_date = datetime.now(china_tz).date()
        days_old = (today_date - data_date).days

        if days_old <= 1:
            return 1.0
        elif days_old <= 3:
            return 0.85
        elif days_old <= 5:
            return 0.70
        elif days_old <= 10:
            return 0.50
        elif days_old <= 30:
            return 0.30
        else:
            return 0.15

    # ------------------------------------------------------------------
    # 布林带提取（来自 market_report 文本）
    # ------------------------------------------------------------------

    def _extract_bollinger(self, market_report: str) -> dict:
        """从 market_report 中提取布林带上下轨和均线值。

        market_report 格式（data_source_manager.py:778-790）：
          📊 布林带 (BOLL):
             上轨: ¥45.80
             中轨: ¥42.30
             下轨: ¥38.90
          📊 移动平均线 (MA):
             MA5:  ¥43.20
             MA10: ¥42.50
             MA20: ¥41.80
             MA60: ¥40.10
        """
        result = {}
        if not market_report or not isinstance(market_report, str):
            return result

        # 布林带
        upper = re.search(r"上轨:\s*[¥$￥]?(\d+(?:\.\d+)?)", market_report)
        mid = re.search(r"中轨:\s*[¥$￥]?(\d+(?:\.\d+)?)", market_report)
        lower = re.search(r"下轨:\s*[¥$￥]?(\d+(?:\.\d+)?)", market_report)
        if upper:
            result["upper"] = float(upper.group(1))
        if mid:
            result["middle"] = float(mid.group(1))
        if lower:
            result["lower"] = float(lower.group(1))

        # 均线
        ma5 = re.search(r"MA5:\s*[¥$￥]?(\d+(?:\.\d+)?)", market_report)
        ma10 = re.search(r"MA10:\s*[¥$￥]?(\d+(?:\.\d+)?)", market_report)
        ma20 = re.search(r"MA20:\s*[¥$￥]?(\d+(?:\.\d+)?)", market_report)
        ma60 = re.search(r"MA60:\s*[¥$￥]?(\d+(?:\.\d+)?)", market_report)
        if ma5:
            result["ma5"] = float(ma5.group(1))
        if ma10:
            result["ma10"] = float(ma10.group(1))
        if ma20:
            result["ma20"] = float(ma20.group(1))
        if ma60:
            result["ma60"] = float(ma60.group(1))

        # 最新价格（从前面已提取，这里兼容）
        price = re.search(r"最新价格:\s*[¥$￥]?(\d+(?:\.\d+)?)", market_report)
        if price:
            result["current_price"] = float(price.group(1))

        if result:
            logger.debug(f"📊 [布林带] 提取结果: {result}")
        return result

    # ------------------------------------------------------------------
    # 多空分歧度提取
    # ------------------------------------------------------------------

    def _extract_disagreement_level(self, simplified_report: dict) -> float:
        """从 simplified_report 的 core_disagreement 提取分歧度。

        分歧度 0-1：
        - 0.0 = 高度一致（所有观点方向相同）
        - 0.5 = 中性分歧（各有理由）
        - 1.0 = 严重分歧（完全对立）

        core_disagreement 格式：
        {
          "bullish_arguments": "看涨观点...",
          "bearish_arguments": "看跌观点...",
          "consensus": "共识点...",
          "key_disagreement": "核心分歧..."
        }
        """
        if not simplified_report or not isinstance(simplified_report, dict):
            return 0.5

        cd = simplified_report.get("core_disagreement", {}) or {}
        if not isinstance(cd, dict):
            return 0.5

        bull = (cd.get("bullish_arguments") or "").strip()
        bear = (cd.get("bearish_arguments") or "").strip()
        consensus = (cd.get("consensus") or "").strip()

        # 双方都有充分的论述 → 分歧较大
        has_bull = len(bull) > 20
        has_bear = len(bear) > 20
        has_consensus = len(consensus) > 10

        if has_bull and has_bear and not has_consensus:
            return 0.8   # 多空激烈对峙，无共识
        elif has_bull and has_bear and has_consensus:
            return 0.5   # 有分歧但存在共识
        elif has_bull and not has_bear:
            return 0.2   # 看涨占主导
        elif has_bear and not has_bull:
            return 0.2   # 看跌占主导
        elif not has_bull and not has_bear:
            return 0.6   # 双方都缺乏论据→不确定

        return 0.5

    # ------------------------------------------------------------------
    # TET 提取
    # ------------------------------------------------------------------

    def _extract_tet(
        self, simplified_report: dict, market_report: str
    ) -> Dict[str, float]:
        """从 simplified_report 或 market_report 中提取 TET 指标。"""
        tet = {}

        # 优先从 simplified_report 取（已在图节点中提取好）
        if simplified_report and isinstance(simplified_report, dict):
            tet = simplified_report.get("tet_indicators", {}) or {}
            if tet.get("trend_score") is not None:
                logger.debug(
                    f"📊 [TET] 从 simplified_report 提取: {tet}"
                )
                return tet

        # 备选：从 market_report 文本中用正则提取
        if market_report and isinstance(market_report, str):
            patterns = [
                ("trend_score", r"趋势得分[^0-9+-]*?([-+]?\d+\.?\d*)"),
                ("emotion_index", r"情绪指数[^0-9+-]*?([-+]?\d+\.?\d*)"),
                (
                    "anchored_trend_score",
                    r"锚定趋势[^0-9+-]*?([-+]?\d+\.?\d*)",
                ),
                ("timing_indicator", r"时机指标[^0-9+-]*?([-+]?\d+\.?\d*)"),
            ]
            for key, pattern in patterns:
                match = re.search(pattern, market_report)
                if match:
                    try:
                        tet[key] = float(match.group(1))
                    except ValueError:
                        pass
            if tet:
                logger.debug(f"📊 [TET] 从 market_report 正则提取: {tet}")

        return tet

    # ------------------------------------------------------------------
    # Action 计算（优先从 simplified_report 解析）
    # ------------------------------------------------------------------

    def _compute_action(
        self,
        simplified_report: dict,
        timing_indicator: float,
        trend_score: float,
        full_signal: str,
    ) -> str:
        """确定交易动作，优先级：

        1. 从 simplified_report.insight_and_decision 文本中解析（与简化报告保持一致）
        2. TET timing_indicator 信号（论文验证过）
        3. final_trade_decision 文本正则提取（最后回退）
        """
        # 1. 从 simplified_report 的 insight 文本中解析
        if simplified_report and isinstance(simplified_report, dict):
            insight = simplified_report.get("insight_and_decision", "") or ""
            action_from_insight = self._extract_action_from_text(insight)
            if action_from_insight != "持有":
                logger.info(f"🎯 [Action] 从 insight_and_decision 解析: {action_from_insight}")
                return action_from_insight

            # 也检查 core_disagreement 的共识
            cd = simplified_report.get("core_disagreement", {}) or {}
            if isinstance(cd, dict):
                consensus = cd.get("consensus", "") or ""
                action_from_consensus = self._extract_action_from_text(consensus)
                if action_from_consensus != "持有":
                    logger.info(f"🎯 [Action] 从 consensus 解析: {action_from_consensus}")
                    return action_from_consensus

        # 2. TET 量化信号
        if timing_indicator > 1.0:
            return "买入"
        if timing_indicator < -1.0:
            return "卖出"
        if -1.0 <= timing_indicator <= 1.0 and abs(trend_score) > 0.4:
            return "买入" if trend_score > 0 else "卖出"

        # 3. 文本回退
        if full_signal and isinstance(full_signal, str):
            text_action = self._extract_action_from_text(full_signal)
            if text_action:
                return text_action

        return "持有"

    def _extract_action_from_text(self, text: str) -> str:
        """从文本中提取买入/持有/卖出动作，使用关键词计数投票。

        不采用"先匹配卖出→再匹配买入"的优先级，因为文本可能同时包含
        两种信号（如头部"投资建议：买入"但正文全是看跌减仓）。
        改为统计买卖关键词数量，取多数方。
        """
        if not text:
            return "持有"

        # 卖出/看跌关键词
        sell_keywords = [
            "建议卖出", "推荐卖出", "逢高减仓", "减仓", "减持",
            "离场", "看空", "看跌", "看跌方", "卖出评级", "强烈卖出",
            "管住手", "不操作", "不要买", "不要入场",
            "接飞刀",  # "别急着接飞刀"
            "期望值为负",  # 交易期望为负
        ]
        # 买入/看涨关键词
        buy_keywords = [
            "建议买入", "推荐买入", "逢低买入", "增持", "加仓",
            "入场", "看多", "买入评级", "强烈买入", "抄底",
        ]
        # 持有/中性关键词
        hold_keywords = [
            "建议持有", "继续持有", "观望", "保持当前", "中性", "暂不操作",
            "等企稳", "先观望", "再动手", "别急着", "别急", "再考虑",
        ]

        sell_count = sum(1 for kw in sell_keywords if kw in text)
        buy_count = sum(1 for kw in buy_keywords if kw in text)
        hold_count = sum(1 for kw in hold_keywords if kw in text)

        # 如果卖出信号明显占优（卖>买+1），即使有"投资建议：买入"也以正文为准
        if sell_count > buy_count + 1:
            return "卖出"
        if buy_count > sell_count + 1:
            return "买入"
        if sell_count > buy_count and sell_count >= hold_count:
            return "卖出"
        if buy_count > sell_count and buy_count >= hold_count:
            return "买入"
        if hold_count >= max(sell_count, buy_count):
            return "持有"

        # 英文匹配（最后兜底）
        has_sell = bool(re.search(r"\bSELL\b|\b卖出\b", text, re.IGNORECASE))
        has_buy = bool(re.search(r"\bBUY\b|\b买入\b", text, re.IGNORECASE))
        if has_sell and not has_buy:
            return "卖出"
        if has_buy and not has_sell:
            return "买入"

        return "持有"

    # ------------------------------------------------------------------
    # Target Price 计算
    # ------------------------------------------------------------------

    def _extract_current_price(self, market_report: str) -> Optional[float]:
        """从 market_report 文本中提取当前价格。"""
        if not market_report or not isinstance(market_report, str):
            return None

        patterns = [
            r"当前价[格位]?\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"现价\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"股价\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"价格\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"收盘价?\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"最新价?\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, market_report)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        return None

    def _compute_target_price(
        self,
        simplified_report: dict,
        action: str,
        current_price: Optional[float],
        trend_score: float,
        bollinger: dict,
    ) -> Optional[float]:
        """科学推算目标价，优先级：

        1. 从 simplified_report.insight_and_decision 中解析显式目标价
        2. 基于布林带技术位推算（buy→上轨, sell→下轨, hold→中轨）
        3. TET 趋势推算（最后回退）
        """
        # ---- 1. 解析 insight 中的显式目标价 ----
        if simplified_report and isinstance(simplified_report, dict):
            insight = simplified_report.get("insight_and_decision", "") or ""
            explicit_target = self._extract_price_from_text(insight)
            if explicit_target and explicit_target > 0:
                # 验证价格合理性（不偏离当前价太远）
                if current_price and 0.5 < explicit_target / current_price < 3.0:
                    logger.info(f"💰 [目标价] 从 insight 解析: {explicit_target}")
                    return round(explicit_target, 2)

        # ---- 2. 基于布林带 + 均线技术位 ----
        if current_price and current_price > 0 and bollinger:
            upper = bollinger.get("upper")
            middle = bollinger.get("middle")
            lower = bollinger.get("lower")
            ma60 = bollinger.get("ma60")
            ma20 = bollinger.get("ma20")

            if action == "买入":
                # 趋势看涨时：上轨为目标
                if upper and current_price < upper:
                    return round(upper, 2)
                # 价格已突破上轨：用趋势幅度外推
                if upper and current_price >= upper and trend_score > 0:
                    band_width = upper - (middle or current_price)
                    extrapolated = upper + band_width * 0.3
                    return round(extrapolated, 2)
                # 无布林带时用 MA60 作为中期目标
                if ma60 and current_price < ma60:
                    return round(ma60, 2)
            elif action == "卖出":
                # 趋势看跌时：下轨为目标
                if lower and current_price > lower:
                    return round(lower, 2)
                # 价格已跌破下轨：用趋势幅度外推
                if lower and current_price <= lower and trend_score < 0:
                    band_width = (middle or current_price) - lower
                    extrapolated = lower - band_width * 0.3
                    return round(max(extrapolated, 0.01), 2)
            else:  # 持有
                # 中轨（20日均线）作为合理估值
                if middle:
                    return round(middle, 2)
                # 回退到 MA60
                if ma60:
                    return round(ma60, 2)

        # ---- 3. TET 趋势推算（最终回退） ----
        if current_price and current_price > 0:
            trend_magnitude = min(abs(trend_score), 1.0)
            if action == "买入":
                upside = min(trend_magnitude * 0.15, 0.25)
                return round(current_price * (1 + upside), 2)
            elif action == "卖出":
                downside = min(trend_magnitude * 0.10, 0.15)
                return round(current_price * (1 - downside), 2)
            else:
                return round(current_price, 2)

        return None

    def _extract_price_from_text(self, text: str) -> Optional[float]:
        """从文本中提取明确的目标价格数字。"""
        if not text:
            return None
        patterns = [
            r"目标价[位格]?\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"目标\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"看[到至]\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"上涨[到至]\s*[¥$￥]?(\d+(?:\.\d+)?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    price = float(match.group(1))
                    if price > 1.0:  # 排除年份等误匹配
                        return price
                except ValueError:
                    continue
        return None

    # ------------------------------------------------------------------
    # TET 正确性校验
    # ------------------------------------------------------------------

    def _verify_tet_correctness(
        self,
        tet: dict,
        trend_score: float,
        emotion_index: float,
        timing_indicator: float,
        anchored_trend: float,
        simplified_report: dict,
        market_report: str,
    ) -> float:
        """验证 TET 指标计算的正确性和有效性。

        四个检查维度加权：
        - 完整性  (40%)：4个指标都存在且不为 None
        - 值域    (30%)：各指标在正常数学范围内
        - 非全零  (15%)：不是全部为0（全0说明计算可能失败）
        - 存在性  (15%)：TET 段落在 market_report 中实际存在
        """
        # 1. 完整性：4个指标都算出来了
        tet_keys = ["trend_score", "emotion_index", "anchored_trend_score", "timing_indicator"]
        existing = sum(1 for k in tet_keys if tet.get(k) is not None)
        completeness = existing / 4.0

        # 2. 值域合理性
        range_ok = 0
        if trend_score is not None and -2 <= trend_score <= 2:
            range_ok += 1
        if emotion_index is not None and -2 <= emotion_index <= 2:
            range_ok += 1
        if anchored_trend is not None and -2 <= anchored_trend <= 2:
            range_ok += 1
        if timing_indicator is not None and -5 <= timing_indicator <= 5:
            range_ok += 1

        # 3. 非全零
        abs_sum = sum(
            abs(v) for v in
            [trend_score or 0, emotion_index or 0, anchored_trend or 0, timing_indicator or 0]
        )
        not_all_zero = 1.0 if abs_sum > 0.01 else 0.0

        # 4. TET 段落在 market_report 中存在
        has_tet_section = 0.0
        if market_report and isinstance(market_report, str):
            if re.search(r"趋势得分.*情绪指数|趋势.+情绪.+时机", market_report):
                has_tet_section = 1.0

        result = (
            completeness * 0.40
            + (range_ok / 4.0) * 0.30
            + not_all_zero * 0.15
            + has_tet_section * 0.15
        )
        logger.info(
            f"✅ [TET正确性] completeness={completeness:.2f}, range_ok={range_ok}/4, "
            f"non_zero={not_all_zero:.0f}, section={has_tet_section:.0f} → {result:.3f}"
        )
        return max(0.0, min(1.0, result))

    # ------------------------------------------------------------------
    # Confidence 计算（0-1）
    # ------------------------------------------------------------------

    def _compute_confidence(
        self,
        data_sufficiency: float = 0.5,
        data_freshness: float = 0.5,
        tet_correctness: float = 0.5,
    ) -> float:
        """基于三个核心维度判断模型分析的可信度：

        - 数据充足度  (35%)：历史数据是否足够（至少5年≈1250个交易日）
        - 数据新鲜度  (35%)：是否包含最新交易日数据
        - TET正确性  (30%)：量化指标的计算是否有效、合理
        """
        confidence = (
            data_sufficiency * 0.35
            + data_freshness * 0.35
            + tet_correctness * 0.30
        )
        return max(0.10, min(0.95, confidence))

    # ------------------------------------------------------------------
    # Risk Score 计算（0-1）
    # ------------------------------------------------------------------

    def _compute_risk_score(
        self,
        simplified_report: dict,
        trend_score: float,
        emotion_index: float,
        anchored_trend: float,
        confidence: float,
    ) -> float:
        """基于 TET 指标 + Simplified Report 风险提示量化计算风险评分。

        四个维度：
        - 情绪风险 (35%)：情绪越极端风险越高
        - 置信度风险 (25%)：置信度越低风险越高
        - 趋势分歧风险 (20%)：trend 与 anchored trend 差异越大风险越高
        - 文本风险提示 (20%)：从 simplified_report 解析风险关键词
        """
        emotion_risk = min(abs(emotion_index), 1.0)
        confidence_risk = 1.0 - confidence

        if trend_score != 0 and anchored_trend != 0:
            divergence = abs(trend_score - anchored_trend) / max(abs(trend_score), 0.01)
            trend_risk = min(divergence, 1.0)
        else:
            trend_risk = 0.3

        text_risk = self._calc_text_risk(simplified_report)

        risk_score = (
            emotion_risk * 0.35
            + confidence_risk * 0.25
            + trend_risk * 0.20
            + text_risk * 0.20
        )
        return max(0.05, min(0.95, risk_score))

    def _calc_text_risk(self, simplified_report: dict) -> float:
        """从 simplified_report 的文本中提取风险信号。"""
        if not simplified_report or not isinstance(simplified_report, dict):
            return 0.5
        risk_signals = []
        for field in ["personal_view_and_risk", "insight_and_decision"]:
            text = simplified_report.get(field, "") or ""
            if not text:
                continue
            high_risk = ["高风险", "巨大风险", "重大风险", "暴跌", "崩盘",
                         "极度悲观", "清仓", "止损", "强烈看空"]
            med_risk = ["风险", "谨慎", "不确定性", "波动", "警惕",
                        "回调", "下跌", "看空", "卖出", "减持"]
            low_risk = ["低风险", "稳健", "安全", "看好", "乐观"]
            high_count = sum(1 for kw in high_risk if kw in text)
            med_count = sum(1 for kw in med_risk if kw in text)
            low_count = sum(1 for kw in low_risk if kw in text)
            score = 0.5
            score += min(high_count * 0.15, 0.4)
            score += min(med_count * 0.05, 0.3)
            score -= min(low_count * 0.1, 0.2)
            risk_signals.append(max(0.0, min(1.0, score)))
        return sum(risk_signals) / len(risk_signals) if risk_signals else 0.5

    # ------------------------------------------------------------------
    # Reasoning 构建
    # ------------------------------------------------------------------

    def _build_reasoning(
        self,
        simplified_report: dict,
        tet: dict,
        action: str,
        current_price: Optional[float],
        target_price: Optional[float],
        confidence: float,
        risk_score: float,
        data_quality: dict = None,
    ) -> str:
        """从 Simplified Report 中提取最精炼的决策理由。"""
        parts = []

        # 1. 从 simplified_report 取 insight
        insight = ""
        if simplified_report and isinstance(simplified_report, dict):
            insight = simplified_report.get("insight_and_decision", "")
            if not insight:
                insight = simplified_report.get("executive_summary", "")

        if insight:
            parts.append(insight.strip())

        # 2. 如果 insight 太短，补充多空分歧信息
        if len(insight) < 30 and simplified_report:
            disagreement = simplified_report.get("core_disagreement", {})
            if isinstance(disagreement, dict):
                consensus = disagreement.get("consensus", "")
                if consensus:
                    parts.append(f"多空共识: {consensus}")

        # 3. 追加 TET 信号说明
        tet_parts = []
        ts = tet.get("trend_score")
        if ts is not None:
            direction = "上涨" if ts > 0 else "下跌" if ts < 0 else "震荡"
            tet_parts.append(f"趋势得分{ts:.2f}({direction})")
        ei = tet.get("emotion_index")
        if ei is not None:
            sentiment = "过热" if ei > 0.5 else "低迷" if ei < -0.5 else "中性"
            tet_parts.append(f"情绪指数{ei:.2f}({sentiment})")
        ti = tet.get("timing_indicator")
        if ti is not None:
            timing = "良好" if ti > 0.5 else "较差" if ti < -0.5 else "中性"
            tet_parts.append(f"时机指标{ti:.2f}({timing})")

        if tet_parts:
            parts.append(f"TET量化信号: {' | '.join(tet_parts)}")

        # 4. 目标价说明
        if current_price and target_price:
            change = ((target_price / current_price) - 1) * 100
            if abs(change) >= 0.5:
                parts.append(f"目标价{target_price}（较当前{current_price} {'+' if change >= 0 else ''}{change:.1f}%）")

        # 5. 数据质量提示
        if data_quality:
            rows = data_quality.get("rows", 0)
            dq_parts = []
            ds = data_quality.get("data_start")
            de = data_quality.get("data_end")
            if ds and de:
                dq_parts.append(f"{ds}~{de}")
            elif rows > 0:
                years_est = rows / 250
                if years_est >= 5:
                    dq_parts.append(f"{rows}个交易日(>=5年)")
                else:
                    dq_parts.append(f"{rows}个交易日({years_est:.1f}年)")
            fresh = data_quality.get("freshness", 0)
            if fresh == 1.0:
                dq_parts.append("含当日数据")
            elif fresh >= 0.7:
                dq_parts.append("数据较新")
            elif fresh < 0.5:
                dq_parts.append("数据偏旧")
            if dq_parts:
                parts.append(f"数据质量: {' | '.join(dq_parts)}")

        # 6. 风险提示
        risk_level = "高" if risk_score > 0.6 else "中" if risk_score > 0.3 else "低"
        conf_level = "高" if confidence > 0.7 else "中" if confidence > 0.4 else "低"
        parts.append(f"置信度{confidence:.0%}({conf_level}) | 风险评分{risk_score:.0%}({risk_level})")

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # 保留备用方法（不再使用 LLM，仅作文本正则提取）
    # ------------------------------------------------------------------

    def _extract_simple_decision(self, text: str) -> dict:
        """简单的文本提取方法作为极端情况备用。"""
        action = "持有"
        if re.search(r"买入|BUY", text, re.IGNORECASE):
            action = "买入"
        elif re.search(r"卖出|SELL", text, re.IGNORECASE):
            action = "卖出"

        target_price = None
        price_patterns = [
            r"目标价[位格]?\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"目标\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"价格\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"[¥$￥](\d+(?:\.\d+)?)",
            r"(\d+(?:\.\d+)?)元",
        ]
        for pattern in price_patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    target_price = float(match.group(1))
                    break
                except ValueError:
                    continue

        if not target_price:
            target_price = self._smart_price_estimation(text, action, is_china=True)

        return {
            "action": action,
            "target_price": target_price,
            "confidence": 0.5,
            "risk_score": 0.5,
            "reasoning": "基于文本分析的备用决策（无TET数据）",
        }

    def _smart_price_estimation(self, text: str, action: str, is_china: bool) -> Optional[float]:
        """从文本中智能推算价格（极端备用）。"""
        current_price = None
        price_patterns = [
            r"当前价[格位]?\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"现价\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
            r"股价\s*[：:]?\s*[¥$￥]?(\d+(?:\.\d+)?)",
        ]
        for pattern in price_patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    current_price = float(match.group(1))
                    break
                except ValueError:
                    continue

        if current_price:
            if action == "买入":
                return round(current_price * (1.12 if is_china else 1.10), 2)
            elif action == "卖出":
                return round(current_price * (0.95 if is_china else 0.92), 2)
            return current_price
        return None

    def _get_default_decision(self) -> dict:
        """返回默认决策。"""
        return {
            "action": "持有",
            "target_price": None,
            "confidence": 0.5,
            "risk_score": 0.5,
            "reasoning": "输入数据无效，默认持有建议",
        }
