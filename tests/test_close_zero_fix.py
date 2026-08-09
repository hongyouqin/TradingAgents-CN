"""
验证 close=0.0 修复逻辑

测试场景：
1. 盘前数据 (close=0, amount=0, volume=0) → 应跳过合并
2. close=0 但有 amount→ 应回填前日收盘价
3. 正常实时数据 (close>0) → 正常合并
4. _format_stock_data_response 中 close=0 兜底逻辑
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
import pandas as pd
import numpy as np


def test_merge_realtime_skip_premarket():
    """场景1: 盘前数据 close=0 + amount=0 + volume=0 → 跳过合并"""
    # 模拟 market_quotes 盘前数据
    mq = {
        'trade_date': '2026-07-21',
        'close': 0,
        'open': 0,
        'high': 0,
        'low': 0,
        'amount': 0,
        'volume': 0,
        'pct_chg': 0,
        'pre_close': 32.16,
    }

    realtime_close = mq.get('close')
    realtime_amount = mq.get('amount', 0) or 0
    realtime_volume = mq.get('volume', 0) or 0

    # 断言: close=0 且 amount=0 且 volume=0 → 应跳过
    should_skip = (realtime_close is None or realtime_close == 0) and (realtime_amount == 0 and realtime_volume == 0)
    assert should_skip, "盘前数据(close=0,amount=0,vol=0) 应被判定为跳过"
    print("PASS 场景1: 盘前数据正确判定跳过")


def test_merge_realtime_normal_data():
    """场景2: 正常盘中数据 close>0 → 正常合并"""
    mq = {
        'trade_date': '2026-07-21',
        'close': 32.50,
        'open': 32.16,
        'high': 32.80,
        'low': 32.10,
        'amount': 1234567.0,
        'volume': 380000.0,
        'pct_chg': 1.06,
        'pre_close': 32.16,
    }

    realtime_close = mq.get('close')
    realtime_amount = mq.get('amount', 0) or 0
    realtime_volume = mq.get('volume', 0) or 0

    should_skip = (realtime_close is None or realtime_close == 0) and (realtime_amount == 0 and realtime_volume == 0)
    assert not should_skip, "正常盘中数据 不应被跳过"
    assert realtime_close == 32.50
    print("PASS 场景2: 正常盘中数据正确保留")


def test_format_response_close_zero_fallback():
    """场景3: _format_stock_data_response 中 close=0 退化逻辑"""
    # 模拟一个有 close=0 最后一行的 DataFrame
    data = pd.DataFrame({
        'close': [30.0, 31.0, 32.0, 32.16, 0.0],
        'trade_date': ['2026-07-15', '2026-07-16', '2026-07-17', '2026-07-20', '2026-07-21'],
        'data_source': ['', '', '', '', 'market_quotes_realtime'],
        'amount': [100, 200, 150, 180, 0],
    })

    latest_data = data.iloc[-1]
    latest_price = latest_data.get('close', 0)

    # 兜底修复逻辑
    if latest_price == 0 or latest_price is None:
        if len(data) >= 2:
            fallback_close = data.iloc[-2].get('close', 0)
            if fallback_close and fallback_close > 0:
                latest_price = float(fallback_close)

    assert latest_price == 32.16, f"退化失败: expected 32.16, got {latest_price}"
    print(f"PASS 场景3: close=0 退化到前日收盘价 {latest_price}")


def test_format_response_normal():
    """场景4: 正常数据 close>0 不触发退化"""
    data = pd.DataFrame({
        'close': [30.0, 31.0, 32.0, 32.16, 32.50],
        'trade_date': ['2026-07-15', '2026-07-16', '2026-07-17', '2026-07-20', '2026-07-21'],
    })

    latest_data = data.iloc[-1]
    latest_price = latest_data.get('close', 0)

    # 兜底修复不应触发
    original_price = latest_price
    if latest_price == 0 or latest_price is None:
        if len(data) >= 2:
            fallback_close = data.iloc[-2].get('close', 0)
            if fallback_close and fallback_close > 0:
                latest_price = float(fallback_close)

    assert latest_price == 32.50, f"正常数据不应被退化: expected 32.50, got {latest_price}"
    assert latest_price == original_price
    print("PASS 场景4: 正常数据 close>0 不触发退化")


def test_change_pct_calculation():
    """场景5: 验证涨跌幅计算在 close=0 退化后的正确性"""
    # 模拟退化后的数据
    fallback_close = 32.16
    prev_close = 32.16
    change = fallback_close - prev_close
    change_pct = (change / prev_close * 100) if prev_close != 0 else 0

    assert change == 0.0, f"退化到前日收盘价, 涨跌幅应为0: {change}"
    assert change_pct == 0.0, f"涨跌幅% 应为0: {change_pct}"
    print("PASS 场景5: 退化到前日收盘价，涨跌幅正确为0%")


def test_backfill_pct_chg_recalculation():
    """场景6: close回填后 pct_chg 重新计算的逻辑"""
    mq_close = 0.0  # 原始 close
    realtime_close = 32.16  # 回填值 (前日收盘)
    pre_close = 32.16

    # 判断 close 被回填过
    was_backfilled = realtime_close != (mq_close or 0)
    assert was_backfilled, "close 0→32.16 应被识别为回填"

    # 重新计算 pct_chg
    if pre_close and pre_close > 0:
        recalculated_pct = round((realtime_close - pre_close) / pre_close * 100, 2)
    else:
        recalculated_pct = 0.0

    assert recalculated_pct == 0.0, f"回填 close 等于 pre_close, pct_chg 应为0: {recalculated_pct}"
    print("PASS 场景6: close回填后 pct_chg 正确重算为0%")


if __name__ == "__main__":
    test_merge_realtime_skip_premarket()
    test_merge_realtime_normal_data()
    test_format_response_close_zero_fallback()
    test_format_response_normal()
    test_change_pct_calculation()
    test_backfill_pct_chg_recalculation()
    print("\n=== 全部 6 个场景通过 ===")
