#!/usr/bin/env python
"""
TET 指标计算验证测试

用途：验证 Trend-Emotion-Timing 四指标计算是否正确
用法：
  python scripts/test_tet_calculation.py                    # 使用合成数据
  python scripts/test_tet_calculation.py 000066             # 从 MongoDB 获取真实数据
  python scripts/test_tet_calculation.py 000066 2026-01-01 2026-07-07  # 指定日期范围
"""
import sys
import json
import argparse
from datetime import datetime
import pandas as pd
import numpy as np

# ========== 从项目复制修复后的 TET 计算逻辑（不依赖项目导入） ==========

class TETCalculator:
    """趋势-情绪-时机指标计算器（与项目 trend_emotion_timing.py 一致）"""

    def __init__(self, stock_code):
        self.stock_code = stock_code
        self.data = None
        self.indicators = None
        self._debug = {}  # 调试信息

    def load_data(self, stock_df, hs300_df):
        """加载股票和沪深300数据"""
        stock_df = stock_df.sort_values("trade_date").reset_index(drop=True)
        hs300_df = hs300_df.sort_values("trade_date").reset_index(drop=True)

        stock_df.rename(columns={
            'trade_date': 'date', 'open': 'open', 'high': 'high',
            'low': 'low', 'close': 'close_stock', 'vol': 'volume'
        }, inplace=True)
        hs300_df.rename(columns={
            'trade_date': 'date', 'close': 'close_hs300'
        }, inplace=True)

        # left join: 保证股票数据完整（HS300 缺失时用 0 填充）
        df = pd.merge(stock_df, hs300_df, on="date", how="left")
        df["ratio_hs"] = df["close_stock"] / df["close_hs300"].ffill()
        self.data = df
        return self

    def calculate_trend_score(self):
        """计算趋势得分（30个技术信号均值 × 方向校验）"""
        df = self.data
        close = df["close_stock"].values
        signals = []

        # --- ROC 信号（10个）---
        for p in [15, 20, 25, 30, 40, 50, 60, 80, 120, 150]:
            roc = pd.Series(close).pct_change(periods=p).values * 100
            signals.append(np.where(roc > 0.5, 1, np.where(roc < -0.5, -1, 0)))

        # --- SMA 信号（10个）---
        for p in [15, 20, 25, 30, 40, 50, 60, 80, 120, 150]:
            sma = pd.Series(close).rolling(window=p).mean().values
            signals.append(np.where(close > sma * 1.01, 1, np.where(close < sma * 0.99, -1, 0)))

        # --- 均线交叉信号（10个）---
        crosses = [(5, 60), (10, 60), (20, 120), (30, 120),
                   (5, 20), (10, 30), (20, 60), (30, 90), (5, 30), (10, 90)]
        for s, l in crosses:
            ma_s = pd.Series(close).rolling(window=s).mean().values
            ma_l = pd.Series(close).rolling(window=l).mean().values
            signals.append(np.where(ma_s > ma_l * 1.01, 1, np.where(ma_s < ma_l * 0.99, -1, 0)))

        trend = np.array(signals).mean(axis=0)
        df["trend"] = trend

        # 相对沪深300的方向
        trend_hs = np.sign(df["ratio_hs"].pct_change(20).fillna(0))
        df["trend_hs"] = trend_hs

        # 修复：HS300 横盘时不归零
        df["trend_score"] = np.where(
            df["trend_hs"] == 0, df["trend"],
            np.where(np.sign(df["trend"]) == df["trend_hs"], df["trend"], 0)
        )

        # 调试信息
        self._debug["trend"] = {
            "信号总数": len(signals),
            "各信号最新值": {f"sig{i}": float(signals[i][-1]) for i in range(len(signals))},
        }
        self.data = df
        return self

    def calculate_emotion_index(self):
        """计算情绪指数（6个RSI变体 + 6个随机指标变体）"""
        df = self.data
        close = df["close_stock"].values
        emotions = []
        n_rows = len(close)
        rsi_signals = {}
        stoch_signals = {}

        # --- RSI 变体（修复：百分比收益率）---
        for p in [3, 5, 7, 9, 11, 14]:
            rsi = np.zeros(n_rows)
            rets = np.diff(close) / close[:-1] * 100  # 百分比
            if len(rets) < 1:
                emotions.append(rsi)
                continue
            gain = np.maximum(rets, 0)
            loss = -np.minimum(rets, 0)
            avg_gain = pd.Series(gain).rolling(p, min_periods=1).mean().values
            avg_loss = pd.Series(loss).rolling(p, min_periods=1).mean().values
            rs = avg_gain / (avg_loss + 1e-8)
            rsi[1:] = 100 - (100 / (1 + rs))
            normalized = rsi / 50 - 1
            emotions.append(normalized)
            rsi_signals[f"RSI({p})"] = {
                "raw_rsi": round(float(rsi[-1]), 2),
                "signal": round(float(normalized[-1]), 4),
            }

        # --- 随机指标变体 ---
        high = df["high"].values
        low = df["low"].values
        for p in [2, 4, 6, 8, 10, 12]:
            hh = pd.Series(high).rolling(p).max().values
            ll = pd.Series(low).rolling(p).min().values
            rng = (close - ll) / (hh - ll + 1e-8)
            normalized = rng * 2 - 1
            emotions.append(normalized)
            stoch_signals[f"STOCH({p})"] = {
                "raw_pos": round(float(rng[-1]), 4),
                "signal": round(float(normalized[-1]), 4),
            }

        emotion_index = np.array(emotions).mean(axis=0)
        df["emotion_index"] = emotion_index

        self._debug["emotion"] = {
            "rsi_signals": rsi_signals,
            "stoch_signals": stoch_signals,
            "总分信号数": len(emotions),
        }
        self.data = df
        return self

    def calculate_anchored_trend(self):
        """计算锚定趋势得分（情绪指数穿越零轴时重置）"""
        df = self.data
        anchored = []
        current = 0.0
        reset_count = 0
        for i in range(len(df)):
            e = df["emotion_index"].iloc[i]
            if abs(e) < 0.15 or (i > 0 and np.sign(e) != np.sign(df["emotion_index"].iloc[i - 1])):
                current = df["trend_score"].iloc[i]
                reset_count += 1
            anchored.append(current)
        df["anchored_trend_score"] = anchored
        self._debug["anchored_trend"] = {"重置次数": reset_count}
        self.data = df
        return self

    def calculate_timing(self):
        """计算时机指标 = 锚定趋势 - 情绪指数"""
        df = self.data
        df["timing_indicator"] = df["anchored_trend_score"] - df["emotion_index"]
        df = df.replace([np.inf, -np.inf], np.nan)
        df = df.fillna(0)
        self.data = df
        self.indicators = df.iloc[-1]
        return self

    def get_latest(self):
        d = self.indicators
        return {
            "trend_score": round(float(d["trend_score"]), 4),
            "emotion_index": round(float(d["emotion_index"]), 4),
            "anchored_trend_score": round(float(d["anchored_trend_score"]), 4),
            "timing_indicator": round(float(d["timing_indicator"]), 4),
            "action": "BUY" if d["timing_indicator"] > 1.0
                     else "SELL" if d["timing_indicator"] < -1.0
                     else "HOLD"
        }

    def get_debug_info(self):
        return self._debug

    def get_data_tail(self, n=5):
        """获取最后n行数据"""
        cols = ["date", "close_stock", "close_hs300",
                "trend_score", "emotion_index",
                "anchored_trend_score", "timing_indicator"]
        cols = [c for c in cols if c in self.data.columns]
        return self.data[cols].tail(n).to_string(index=False)

    def get_snapshot(self):
        """获取最新行所有中间结果"""
        cols = ["date", "close_stock", "close_hs300", "ratio_hs",
                "trend", "trend_hs", "trend_score",
                "emotion_index", "anchored_trend_score", "timing_indicator"]
        cols = [c for c in cols if c in self.data.columns]
        row = self.data[cols].iloc[-1]
        snapshot = {}
        for c in cols:
            v = row[c]
            if isinstance(v, (np.floating, float)):
                snapshot[c] = round(float(v), 6)
            elif isinstance(v, (np.integer, int)):
                snapshot[c] = int(v)
            else:
                snapshot[c] = str(v)
        return snapshot


# ========== 数据获取 ==========

def fetch_from_mongodb(stock_code, start_date, end_date):
    """从 MongoDB 获取真实数据"""
    import sys
    from pathlib import Path
    # 将项目根目录加入 sys.path（scripts/ 的父目录）
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from pymongo import MongoClient
    from app.core.config import settings

    print(f" 从 MongoDB 获取 {stock_code} 数据 ({start_date} ~ {end_date})...")
    client = MongoClient(settings.MONGO_URI)
    db = client[settings.MONGO_DB]

    # 查询股票日线数据（兼容多种集合名）
    for coll_name in ["stock_daily_quotes"]:
        if coll_name in db.list_collection_names():
            break
    else:
        # 尝试从 akshare_data 或 tushare_data 读取
        for coll_name in ["akshare_data", "tushare_data"]:
            if coll_name in db.list_collection_names():
                break
        else:
            print("[WARN] 未找到股票数据集合，使用合成数据")
            client.close()
            return None, None

    print(f"  集合: {coll_name}")
    cursor = db[coll_name].find({
        "symbol": {"$in": [stock_code, f"{stock_code}.SZ", f"{stock_code}.SH"]},
        "trade_date": {"$gte": start_date, "$lte": end_date}
    }).sort("trade_date", 1)

    docs = list(cursor)
    if not docs:
        print("[WARN] 未找到股票数据，使用合成数据")
        client.close()
        return None, None

    stock_df = pd.DataFrame(docs)
    # 标准化列名
    col_map = {}
    for c in stock_df.columns:
        cl = c.lower()
        if cl in ("date", "trade_date", "datetime", "timestamp"):
            col_map[c] = "trade_date"
        elif cl in ("close", "收盘", "close_price"):
            col_map[c] = "close"
        elif cl == "open":
            col_map[c] = "open"
        elif cl == "high":
            col_map[c] = "high"
        elif cl == "low":
            col_map[c] = "low"
        elif cl in ("volume", "vol", "成交量", "amount"):
            col_map[c] = "vol"
    stock_df = stock_df.rename(columns=col_map)
    stock_df = stock_df[["trade_date", "open", "high", "low", "close", "vol"]]
    stock_df["trade_date"] = pd.to_datetime(stock_df["trade_date"])
    print(f"  [OK] 获取到 {len(stock_df)} 条记录")
    print(f"  日期范围: {stock_df['trade_date'].min()} ~ {stock_df['trade_date'].max()}")
    print(f"  最新收盘价: {stock_df['close'].iloc[-1]:.2f}")

    # 获取沪深300数据
    hs300_cursor = db[coll_name].find({
        "symbol": {"$in": ["000300", "000300.SH", "399300", "399300.SZ"]},
        "trade_date": {"$gte": start_date, "$lte": end_date}
    }).sort("trade_date", 1)
    hs300_docs = list(hs300_cursor)
    hs300_df = None
    if hs300_docs:
        hs300_df = pd.DataFrame(hs300_docs)
        hs300_col_map = {}
        for c in hs300_df.columns:
            cl = c.lower()
            if cl in ("date", "trade_date", "datetime", "timestamp"):
                hs300_col_map[c] = "trade_date"
            elif cl in ("close", "收盘", "close_price"):
                hs300_col_map[c] = "close"
        hs300_df = hs300_df.rename(columns=hs300_col_map)
        hs300_df = hs300_df[["trade_date", "close"]]
        hs300_df["trade_date"] = pd.to_datetime(hs300_df["trade_date"])
        print(f"  [OK] 沪深300: {len(hs300_df)} 条记录")

    client.close()

    if hs300_df is None:
        print("[WARN] 未获取到沪深300数据，使用合成替代")
        hs300_df = pd.DataFrame({
            "trade_date": stock_df["trade_date"],
            "close": np.linspace(3500, 3550, len(stock_df)),
        })

    return stock_df, hs300_df


def generate_synthetic_data():
    """生成合成数据用于测试"""
    np.random.seed(42)
    n = 60
    dates = pd.date_range('2026-04-01', periods=n, freq='B')

    # 温和波动 + 最后一天涨停
    p = 18.0 + np.cumsum(np.random.randn(n) * 0.3)
    p = np.maximum(p, 15.0)
    p[-1] = p[-2] * 1.10  # 涨停

    stock_df = pd.DataFrame({
        'trade_date': dates,
        'open': p - np.random.rand(n) * 0.5,
        'high': p + np.random.rand(n) * 0.3,
        'low': p - np.random.rand(n) * 0.3,
        'close': p,
        'vol': np.random.randint(100000, 1000000, n),
    })
    hs300_df = pd.DataFrame({
        'trade_date': dates,
        'close': 3500 + np.cumsum(np.random.randn(n) * 15),
    })
    return stock_df, hs300_df


# ========== 主程序 ==========

def analyze(code, stock_df, hs300_df):
    print(f"\n{'='*70}")
    print(f" TET 指标分析: {code}")
    print(f"{'='*70}")

    tet = TETCalculator(code)
    tet.load_data(stock_df, hs300_df)

    # 1. 趋势得分
    print(f"\n>> 阶段1: 趋势得分 Trend-Score")
    tet.calculate_trend_score()
    debug = tet.get_debug_info()
    sigs = debug["trend"]["各信号最新值"]
    pos = sum(1 for v in sigs.values() if v > 0)
    neg = sum(1 for v in sigs.values() if v < 0)
    neu = sum(1 for v in sigs.values() if v == 0)
    print(f"  信号统计: 看多={pos}, 看空={neg}, 中性={neu} (共{len(sigs)}个)")
    print(f"  原始趋势(trend):         {tet.data['trend'].iloc[-1]:+.4f}")
    print(f"  沪深300方向(trend_hs):   {tet.data['trend_hs'].iloc[-1]:+.0f}")
    print(f"  最终趋势得分:             {tet.data['trend_score'].iloc[-1]:+.4f}")

    # 2. 情绪指数
    print(f"\n>> 阶段2: 情绪指数 Emotion-Index")
    tet.calculate_emotion_index()
    debug = tet.get_debug_info()
    print(f"  RSI 分量明细 (百分比收益率):")
    for name, info in debug["emotion"]["rsi_signals"].items():
        print(f"    {name:8s}  raw={info['raw_rsi']:6.1f}  ->  归一化信号={info['signal']:+.4f}")
    print(f"  STOCH 分量明细:")
    for name, info in debug["emotion"]["stoch_signals"].items():
        print(f"    {name:8s}  pos={info['raw_pos']:.3f}  ->  归一化信号={info['signal']:+.4f}")

    # 计算各分量的均值
    rsi_signals = [v["signal"] for v in debug["emotion"]["rsi_signals"].values()]
    stoch_signals = [v["signal"] for v in debug["emotion"]["stoch_signals"].values()]
    all_signals = rsi_signals + stoch_signals
    print(f"\n  RSI信号均值:   {np.mean(rsi_signals):+.4f}  (n={len(rsi_signals)})")
    print(f"  STOCH信号均值: {np.mean(stoch_signals):+.4f}  (n={len(stoch_signals)})")
    print(f"  综合情绪指数:   {np.mean(all_signals):+.4f}  (n={len(all_signals)})")
    print(f"  数据库中的值:   {tet.data['emotion_index'].iloc[-1]:+.4f}")

    # 3. 锚定趋势
    print(f"\n>> 阶段3: 锚定趋势 Anchored Trend")
    tet.calculate_anchored_trend()
    debug = tet.get_debug_info()
    print(f"  锚定趋势重置次数: {debug['anchored_trend']['重置次数']}")
    print(f"  最新锚定趋势得分: {tet.data['anchored_trend_score'].iloc[-1]:+.4f}")

    # 4. 时机指标
    print(f"\n>> 阶段4: 时机指标 Timing-Indicator")
    tet.calculate_timing()
    timing = tet.data["timing_indicator"].iloc[-1]
    print(f"  时机指标 = 锚定趋势 - 情绪指数")
    print(f"           = {tet.data['anchored_trend_score'].iloc[-1]:+.4f} - ({tet.data['emotion_index'].iloc[-1]:+.4f})")
    print(f"           = {timing:+.4f}")

    # 最终结果
    result = tet.get_latest()
    print(f"\n{'='*70}")
    print(f" 最终 TET 指标")
    print(f"{'='*70}")
    for k, v in result.items():
        print(f"  {k:30s} = {v}")
    print()

    # 拍个快照
    snapshot = tet.get_snapshot()
    print(f"{'='*70}")
    print(f" 最新行数据快照")
    print(f"{'='*70}")
    for k, v in snapshot.items():
        print(f"  {k:30s} = {v}")

    return result


def main():
    parser = argparse.ArgumentParser(description="TET 指标计算验证测试")
    parser.add_argument("stock_code", nargs="?", default="000066",
                        help="股票代码（默认 000066）")
    parser.add_argument("start_date", nargs="?", default="2026-01-01",
                        help="开始日期 YYYY-MM-DD")
    parser.add_argument("end_date", nargs="?", default=None,
                        help="结束日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--online", type=str, default=None,
                        help="线上对比 JSON 文件路径")
    args = parser.parse_args()

    if args.end_date is None:
        args.end_date = datetime.now().strftime("%Y-%m-%d")

    # 获取数据
    stock_df, hs300_df = fetch_from_mongodb(
        args.stock_code, args.start_date, args.end_date
    )

    if stock_df is None:
        print("[WARN] 使用合成数据（仅用于验证计算逻辑，非实盘数据）")
        stock_df, hs300_df = generate_synthetic_data()
        print(f"  合成数据: {len(stock_df)} 条, 最后一天涨幅 "
              f"{(stock_df['close'].iloc[-1]/stock_df['close'].iloc[-2]-1)*100:.1f}%")

    # 数据概览
    print(f"\n 数据概览:")
    print(f"  股票: {args.stock_code}")
    print(f"  期间: {args.start_date} ~ {args.end_date}")
    print(f"  交易日数: {len(stock_df)}")
    print(f"  最新收盘: {stock_df['close'].iloc[-1]:.2f}")
    last_change = (stock_df['close'].iloc[-1] / stock_df['close'].iloc[-2] - 1) * 100
    print(f"  最新日涨跌: {last_change:+.2f}%")
    print(f"  HS300 数据: {len(hs300_df)} 条")

    # 运行分析
    result = analyze(args.stock_code, stock_df, hs300_df)

    # 输出 JSON
    print(f"\n JSON 输出:")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 线上对比
    if args.online:
        try:
            with open(args.online, "r", encoding="utf-8") as f:
                online = json.load(f)
            print(f"\n 与线上数据对比 ({args.online}):")
            for k in result:
                if k in online and k != "action":
                    diff = abs(result[k] - online[k])
                    mark = " [OK]" if diff < 0.05 else " [WARN]" if diff < 0.1 else " [FAIL]"
                    print(f"  {k:25s} 本地={result[k]:+.4f}  线上={online[k]:+.4f}  差值={diff:.4f}{mark}")
                elif k == "action":
                    match = result[k] == online.get(k)
                    print(f"  {k:25s} 本地={result[k]}  线上={online.get(k)}  {'[OK]' if match else '[FAIL]'}")
        except Exception as e:
            print(f"  无法加载线上文件: {e}")


if __name__ == "__main__":
    main()
