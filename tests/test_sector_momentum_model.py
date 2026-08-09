"""
双维度板块轮动势能模型 — 单元测试

验证核心算法逻辑：
1. 信号分类逻辑（真上涨/假上涨/低位切换/高位出逃/观望/中性）
2. Min-Max 归一化
3. Z-Score 标准化
4. 综合评分计算
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
import pytest


# ===================== 直接从 sector_momentum_service 复制核心函数进行测试 =====================

def _safe_float(value, default=0.0):
    if value is None:
        return default
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (ValueError, TypeError):
        return default


def _safe_round(value, ndigits=2):
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return round(value, ndigits)


def _min_max_normalize(values, target_min=0, target_max=100):
    if not values:
        return {}
    valid = [v for v in values if v is not None and not (math.isnan(v) or math.isinf(v))]
    if not valid:
        return {v: target_min for v in values}
    min_v = min(valid)
    max_v = max(valid)
    result = {}
    if max_v == min_v:
        mid = (target_min + target_max) / 2
        for v in values:
            result[v] = mid
    else:
        for v in values:
            if v is None or math.isnan(v) or math.isinf(v):
                result[v] = target_min
            else:
                normalized = (v - min_v) / (max_v - min_v)
                score = target_min + normalized * (target_max - target_min)
                result[v] = _safe_round(score, 2)
    return result


def _zscore_normalize(values):
    if not values:
        return {}
    valid = [v for v in values if v is not None and not (math.isnan(v) or math.isinf(v))]
    if not valid:
        return {v: 0.0 for v in values}
    if len(valid) < 2:
        return {v: 0.0 for v in values}
    import statistics
    mean = statistics.mean(valid)
    std = statistics.stdev(valid)
    result = {}
    for v in values:
        if v is None or math.isnan(v) or math.isinf(v):
            result[v] = 0.0
        else:
            result[v] = _safe_round((v - mean) / std if std > 0 else 0.0, 4)
    return result


def _classify_signal(flow_raw, flow_trend_5d, turnover_change_5d, turnover_ratio):
    """信号分类逻辑（从 SectorRotationMomentumService 复制）"""
    FLOW_INFLOW_THRESHOLD = 0.5
    FLOW_OUTFLOW_THRESHOLD = -0.5
    TURNOVER_RISE_THRESHOLD = 0.05
    TURNOVER_FALL_THRESHOLD = -0.05
    RATIO_LOW_THRESHOLD = 1.0
    RATIO_HIGH_THRESHOLD = 5.0

    is_inflow = flow_raw > FLOW_INFLOW_THRESHOLD
    is_outflow = flow_raw < FLOW_OUTFLOW_THRESHOLD
    is_rising = turnover_change_5d > TURNOVER_RISE_THRESHOLD
    is_falling = turnover_change_5d < TURNOVER_FALL_THRESHOLD
    is_low_ratio = turnover_ratio < RATIO_LOW_THRESHOLD
    is_high_ratio = turnover_ratio > RATIO_HIGH_THRESHOLD

    if is_inflow and is_rising:
        if is_low_ratio:
            return "低位切换", f"低位放量+主力建仓"
        if flow_raw > 2 * FLOW_INFLOW_THRESHOLD:
            return "真上涨", f"主力大幅流入+成交额占比提升"
        return "真上涨", f"主力资金流入+成交额占比提升"

    if is_outflow and is_rising:
        return "假上涨", f"成交额占比上升但主力资金流出"

    if is_outflow and is_falling and is_high_ratio:
        return "高位出逃", f"高占比回落+主力出逃"

    if is_outflow and is_falling:
        return "观望", f"资金流出+占比下降"

    if is_inflow and is_falling:
        return "中性", f"有资金流入但占比被分流"

    return "中性", "资金面无明显信号"


# ===================== 测试用例 =====================


class TestSafeFloat:
    def test_none(self):
        assert _safe_float(None) == 0.0

    def test_nan(self):
        assert _safe_float(float("nan")) == 0.0

    def test_inf(self):
        assert _safe_float(float("inf")) == 0.0

    def test_normal(self):
        assert _safe_float(123.45) == 123.45

    def test_string_number(self):
        assert _safe_float("123.45") == 123.45

    def test_empty_string(self):
        assert _safe_float("") == 0.0


class TestMinMaxNormalize:
    def test_empty(self):
        assert _min_max_normalize([]) == {}

    def test_single_value(self):
        result = _min_max_normalize([50.0])
        assert result[50.0] == 50.0  # mid point

    def test_two_values(self):
        result = _min_max_normalize([0.0, 100.0])
        assert result[0.0] == 0.0
        assert result[100.0] == 100.0

    def test_three_values(self):
        result = _min_max_normalize([10.0, 30.0, 50.0])
        assert result[10.0] == 0.0
        assert result[30.0] == 50.0
        assert result[50.0] == 100.0

    def test_with_nan(self):
        nan_val = float("nan")
        result = _min_max_normalize([0.0, nan_val, 100.0])
        assert result[0.0] == 0.0
        # NaN keys use identity-based dict lookup; verify sentinel value is 0
        assert any(v == 0.0 for k, v in result.items() if isinstance(k, float) and math.isnan(k))
        assert result[100.0] == 100.0

    def test_negative_values(self):
        result = _min_max_normalize([-50.0, 0.0, 50.0])
        assert result[-50.0] == 0.0
        assert result[0.0] == 50.0
        assert result[50.0] == 100.0

    def test_custom_range(self):
        result = _min_max_normalize([0.0, 100.0], 10, 90)
        assert result[0.0] == 10.0
        assert result[100.0] == 90.0


class TestZScoreNormalize:
    def test_empty(self):
        assert _zscore_normalize([]) == {}

    def test_single(self):
        result = _zscore_normalize([50.0])
        assert result[50.0] == 0.0

    def test_basic(self):
        result = _zscore_normalize([0.0, 50.0, 100.0])
        # mean = 50, std ~ 50
        assert result[50.0] == pytest.approx(0.0, abs=0.01)
        assert result[0.0] == pytest.approx(-1.0, abs=0.01)
        assert result[100.0] == pytest.approx(1.0, abs=0.01)


class TestClassifySignal:
    """测试信号分类逻辑"""

    def test_true_uptrend(self):
        """真上涨：主力流入 + 占比提升"""
        signal, detail = _classify_signal(
            flow_raw=1.0,
            flow_trend_5d=None,
            turnover_change_5d=0.1,
            turnover_ratio=2.0,
        )
        assert signal == "真上涨"

    def test_true_uptrend_strong(self):
        """真上涨(强势)：主力大幅流入 + 占比提升"""
        signal, detail = _classify_signal(
            flow_raw=2.0,
            flow_trend_5d=None,
            turnover_change_5d=0.1,
            turnover_ratio=2.0,
        )
        assert signal == "真上涨"
        assert "大幅流入" in detail

    def test_fake_uptrend(self):
        """假上涨：主力流出 + 占比提升（放量出货）"""
        signal, detail = _classify_signal(
            flow_raw=-1.0,
            flow_trend_5d=None,
            turnover_change_5d=0.1,
            turnover_ratio=3.0,
        )
        assert signal == "假上涨"

    def test_low_level_rotation(self):
        """低位切换：主力流入 + 占比提升 + 低占比起点"""
        signal, detail = _classify_signal(
            flow_raw=1.0,
            flow_trend_5d=None,
            turnover_change_5d=0.1,
            turnover_ratio=0.5,  # 低于 1% 阈值
        )
        assert signal == "低位切换"

    def test_high_level_flee(self):
        """高位出逃：主力流出 + 占比下降 + 高占比"""
        signal, detail = _classify_signal(
            flow_raw=-1.0,
            flow_trend_5d=None,
            turnover_change_5d=-0.1,
            turnover_ratio=6.0,  # 高于 5% 阈值
        )
        assert signal == "高位出逃"

    def test_wait_and_see(self):
        """观望：主力流出 + 占比下降"""
        signal, detail = _classify_signal(
            flow_raw=-1.0,
            flow_trend_5d=None,
            turnover_change_5d=-0.1,
            turnover_ratio=2.0,  # 不在高位
        )
        assert signal == "观望"

    def test_neutral(self):
        """中性：无明显信号"""
        signal, detail = _classify_signal(
            flow_raw=0.0,
            flow_trend_5d=None,
            turnover_change_5d=0.0,
            turnover_ratio=2.0,
        )
        assert signal == "中性"

    def test_inflow_but_ratio_falling(self):
        """中性：资金流入但占比被分流"""
        signal, detail = _classify_signal(
            flow_raw=1.0,
            flow_trend_5d=None,
            turnover_change_5d=-0.1,
            turnover_ratio=2.0,
        )
        assert signal == "中性"
        assert "分流" in detail


class TestCompositeScoreLogic:
    """测试综合评分计算逻辑"""

    def test_min_max_normalize_integration(self):
        """验证归一化在真实数据场景下的表现"""
        # 模拟 5 个板块的资金流强度
        flow_values = [3.5, 1.2, -0.8, -2.1, 0.5]
        normalized = _min_max_normalize(flow_values)

        # 最强的得满分，最弱的得 0 分
        assert normalized[3.5] == 100.0
        assert normalized[-2.1] == 0.0

        # 中间的线性映射
        assert normalized[0.5] > 40  # 应该在中上位置
        assert normalized[-0.8] < 50  # 应该在中间偏下

    def test_composite_weight(self):
        """验证不同权重下的综合评分变化"""
        # 模拟一个板块的评分
        flow_scores = [80, 20, 50]
        turnover_scores = [20, 80, 50]

        # 权重 0.5/0.5
        c1 = 0.5 * flow_scores[0] + 0.5 * turnover_scores[0]
        c2 = 0.5 * flow_scores[1] + 0.5 * turnover_scores[1]
        c3 = 0.5 * flow_scores[2] + 0.5 * turnover_scores[2]

        # 平衡权重下，两个极端板块评分相同
        assert c1 == 50.0
        assert c2 == 50.0
        assert c3 == 50.0

        # 偏向资金流权重
        c1_flow = 0.7 * flow_scores[0] + 0.3 * turnover_scores[0]
        c2_flow = 0.7 * flow_scores[1] + 0.3 * turnover_scores[1]
        assert c1_flow > c2_flow  # 高资金流得分的板块胜出

        # 偏向成交额权重
        c1_turn = 0.3 * flow_scores[0] + 0.7 * turnover_scores[0]
        c2_turn = 0.3 * flow_scores[1] + 0.7 * turnover_scores[1]
        assert c2_turn > c1_turn  # 高成交额得分的板块胜出


class TestRealWorldScenario:
    """真实场景模拟测试"""

    def test_sector_rotation_scenario(self):
        """模拟板块轮动场景

        场景：5 个板块在不同阶段的资金流和成交额占比表现
        """
        sectors = [
            # (name, flow_raw, turnover_change, ratio)
            ("半导体", 3.2, 0.15, 3.5),    # 真上涨 - 主线
            ("银行", -1.5, 0.08, 6.2),      # 假上涨 - 高位出货
            ("新能源", 0.8, 0.12, 0.8),     # 低位切换 - 新主线
            ("房地产", -2.0, -0.1, 4.5),    # 观望 - 退潮中
            ("医药", 0.3, 0.02, 2.0),       # 中性
        ]

        results = []
        for name, flow, turnover_change, ratio in sectors:
            signal, detail = _classify_signal(flow, None, turnover_change, ratio)
            results.append((name, signal, flow, turnover_change, ratio))

        # 验证信号分类
        signal_map = {r[0]: r[1] for r in results}
        assert signal_map["半导体"] == "真上涨"
        assert signal_map["银行"] == "假上涨"
        assert signal_map["新能源"] == "低位切换"
        assert signal_map["房地产"] == "观望"
        assert signal_map["医药"] == "中性"

        # 验证综合评分排名
        flow_vals = [r[2] for r in results]
        turnover_vals = [r[3] for r in results]

        flow_scores = _min_max_normalize(flow_vals)
        turnover_scores = _min_max_normalize(turnover_vals)

        composites = []
        for i, r in enumerate(results):
            f_score = flow_scores[r[2]]
            t_score = turnover_scores[r[3]]
            composite = 0.5 * f_score + 0.5 * t_score
            composites.append((r[0], composite))

        # 按综合评分降序排列
        composites.sort(key=lambda x: x[1], reverse=True)

        # 半导体（真上涨）应该排名靠前
        top_sectors = [c[0] for c in composites]
        assert top_sectors[0] == "半导体", f"半导体应排名第一，实际: {top_sectors}"
        # 新能源（低位切换）应该比较靠前
        assert top_sectors.index("新能源") < top_sectors.index("房地产")
        # 银行（假上涨）应该比纯观望的房地产靠前（因为有占比提升）
        assert top_sectors.index("银行") < top_sectors.index("房地产")

        print("\n--- 板块轮动评分排名 ---")
        for rank, (name, score) in enumerate(composites, 1):
            print(f"  #{rank} {name}: composite={score:.2f}")
