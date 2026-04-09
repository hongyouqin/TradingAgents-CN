import sys
import os
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, project_root)

import pandas as pd
import numpy as np
from datetime import datetime
import tushare as ts

# ==============================================
# Tushare 配置（请替换为你的 token）
# ==============================================
TUSHARE_TOKEN = "d55fd8e3f434d49dd06b4b17502b07a983bc850253242fc92fd17e06"  # 在 https://tushare.pro 注册获取
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ==============================================
# 趋势-情绪-时机指标
# ==============================================
class AShareTrendEmotionTiming:
    def __init__(self, stock_code):
        self.stock_code = stock_code
        self.data = None
        self.indicators = None

    def load_data(self, stock_df, hs300_df):
        stock_df = stock_df.sort_values("trade_date").reset_index(drop=True)
        hs300_df = hs300_df.sort_values("trade_date").reset_index(drop=True)
        
        # 重命名列以匹配原有逻辑
        stock_df.rename(columns={
            'trade_date': 'date',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close_stock',
            'vol': 'volume'
        }, inplace=True)
        
        hs300_df.rename(columns={
            'trade_date': 'date',
            'close': 'close_hs300'
        }, inplace=True)
        
        df = pd.merge(stock_df, hs300_df, on="date", how="inner")
        df["ratio_hs"] = df["close_stock"] / df["close_hs300"]
        self.data = df
        return self

    def calculate_trend_score(self):
        df = self.data
        close = df["close_stock"].values
        signals = []

        # ROC 信号
        for p in [15, 20, 25, 30, 40, 50, 60, 80, 120, 150]:
            roc = pd.Series(close).pct_change(periods=p).values * 100
            signals.append(np.where(roc > 0.5, 1, np.where(roc < -0.5, -1, 0)))

        # SMA 信号
        for p in [15, 20, 25, 30, 40, 50, 60, 80, 120, 150]:
            sma = pd.Series(close).rolling(window=p).mean().values
            signals.append(np.where(close > sma * 1.01, 1, np.where(close < sma * 0.99, -1, 0)))

        # 均线交叉信号
        crosses = [(5, 60), (10, 60), (20, 120), (30, 120), (5, 20), (10, 30), (20, 60), (30, 90), (5, 30), (10, 90)]
        for s, l in crosses:
            ma_s = pd.Series(close).rolling(window=s).mean().values
            ma_l = pd.Series(close).rolling(window=l).mean().values
            signals.append(np.where(ma_s > ma_l * 1.01, 1, np.where(ma_s < ma_l * 0.99, -1, 0)))

        df["trend"] = np.array(signals).mean(axis=0)
        df["trend_hs"] = np.sign(df["ratio_hs"].pct_change(20).fillna(0))
        df["trend_score"] = np.where(np.sign(df["trend"]) == np.sign(df["trend_hs"]), df["trend"], 0)
        self.data = df
        return self

    def calculate_emotion_index(self):
        df = self.data
        close = df["close_stock"].values
        emotions = []

        # RSI 变体
        for p in [3, 5, 7, 9, 11, 14]:
            delta = np.diff(close)
            gain = np.maximum(delta, 0)
            loss = -np.minimum(delta, 0)
            avg_gain = pd.Series(gain).rolling(window=p, min_periods=1).mean().values
            avg_loss = pd.Series(loss).rolling(window=p, min_periods=1).mean().values
            rs = avg_gain / (avg_loss + 1e-8)
            rsi = np.concatenate([[50], 100 - (100 / (1 + rs))])
            emotions.append(rsi / 50 - 1)

        # 随机指标变体
        high = df["high"].values
        low = df["low"].values
        for p in [2, 4, 6, 8, 10, 12]:
            hh = pd.Series(high).rolling(window=p).max().values
            ll = pd.Series(low).rolling(window=p).min().values
            rng = (close - ll) / (hh - ll + 1e-8)
            emotions.append(rng * 2 - 1)

        df["emotion_index"] = np.array(emotions).mean(axis=0)
        self.data = df
        return self

    def calculate_anchored_trend(self):
        df = self.data
        anchored = []
        current = 0
        for i in range(len(df)):
            e = df["emotion_index"].iloc[i]
            if abs(e) < 0.15 or (i > 0 and np.sign(e) != np.sign(df["emotion_index"].iloc[i-1])):
                current = df["trend_score"].iloc[i]
            anchored.append(current)
        df["anchored_trend_score"] = anchored
        self.data = df
        return self

    def calculate_timing(self):
        df = self.data
        df["timing_indicator"] = df["anchored_trend_score"] - df["emotion_index"]
        self.indicators = df.iloc[-1]
        return self

    def get_latest(self):
        d = self.indicators
        return {
            "trend_score": round(float(d["trend_score"]), 2),
            "emotion_index": round(float(d["emotion_index"]), 2),
            "anchored_trend_score": round(float(d["anchored_trend_score"]), 2),
            "timing_indicator": round(float(d["timing_indicator"]), 2),
            "action": "BUY" if d["timing_indicator"] > 1.0 else "SELL" if d["timing_indicator"] < -1.0 else "HOLD"
        }

# ==============================================
# Tushare 数据获取函数
# ==============================================
def get_stock_df(stock_code, start_date, end_date):
    """
    使用 Tushare 获取股票日线数据
    stock_code: 如 '600000' 或 '600000.SH'
    start_date: 'YYYYMMDD' 格式
    end_date: 'YYYYMMDD' 格式
    """
    # 处理股票代码格式
    if '.' not in stock_code:
        if stock_code.startswith('6'):
            ts_code = f"{stock_code}.SH"
        elif stock_code.startswith('0') or stock_code.startswith('3'):
            ts_code = f"{stock_code}.SZ"
        else:
            ts_code = stock_code
    else:
        ts_code = stock_code
    
    # 获取日线数据（前复权）
    df = pro.daily(
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        fields='trade_date,open,high,low,close,vol'
    )
    
    if df is None or df.empty:
        raise ValueError(f"未获取到股票数据: {ts_code}")
    
    # Tushare 返回的数据默认按 trade_date 倒序，需要反转
    df = df.sort_values('trade_date').reset_index(drop=True)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    
    return df

def get_hs300_df(start_date, end_date):
    """
    使用 Tushare 获取沪深300指数日线数据
    """
    # 沪深300指数代码
    df = pro.index_daily(
        ts_code='000300.SH',
        start_date=start_date,
        end_date=end_date,
        fields='trade_date,close'
    )
    
    if df is None or df.empty:
        raise ValueError("未获取到沪深300指数数据")
    
    df = df.sort_values('trade_date').reset_index(drop=True)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    
    return df

# ==============================================
# 主程序
# ==============================================
if __name__ == "__main__":
    # 配置参数
    stock_code = "600207"  # 浦发银行
    end_date = datetime.today().strftime("%Y%m%d")
    start_date = "20150101"
    
    # 检查 Token 配置
    if TUSHARE_TOKEN == "你的Tushare Token":
        print("⚠️  警告: 请先配置 TUSHARE_TOKEN")
        print("   1. 访问 https://tushare.pro 注册账号")
        print("   2. 在个人中心获取 Token")
        print("   3. 将代码中的 TUSHARE_TOKEN 替换为你的 Token")
        sys.exit(1)
    
    print("=" * 60)
    print("📊 趋势-情绪-时机指标分析系统 (Tushare版本)")
    print("=" * 60)
    print(f"股票代码: {stock_code}")
    print(f"时间范围: {start_date} - {end_date}")
    print("-" * 60)
    
    print("正在获取股票数据...")
    stock_df = get_stock_df(stock_code, start_date, end_date)
    print(f"✓ 获取到 {len(stock_df)} 条股票日线数据")
    
    print("正在获取沪深300指数数据...")
    hs300_df = get_hs300_df(start_date, end_date)
    print(f"✓ 获取到 {len(hs300_df)} 条指数数据")
    
    # 计算指标
    print("正在计算技术指标...")
    tet = AShareTrendEmotionTiming(stock_code)
    tet.load_data(stock_df, hs300_df)
    tet.calculate_trend_score()
    tet.calculate_emotion_index()
    tet.calculate_anchored_trend()
    tet.calculate_timing()
    
    res = tet.get_latest()
    
    print("\n" + "=" * 60)
    print(" 📊 趋势-情绪-时机 指标")
    print("=" * 60)
    print(f"数据日期范围: {stock_df['trade_date'].min().strftime('%Y-%m-%d')} 至 {stock_df['trade_date'].max().strftime('%Y-%m-%d')}")
    print(f"最新日期: {stock_df['trade_date'].max().strftime('%Y-%m-%d')}")
    print("-" * 60)
    print(f"趋势得分 Trend-Score        : {res['trend_score']}")
    print(f"情绪指数 Emotion-Index      : {res['emotion_index']}")
    print(f"锚定趋势 Anchored-Trend     : {res['anchored_trend_score']}")
    print(f"时机指标 Timing-Indicator   : {res['timing_indicator']}")
    print(f"操作建议                   : {res['action']}")
    print("-" * 60)
    print("指标说明:")
    print("• 趋势得分 > 0: 上升趋势, < 0: 下降趋势")
    print("• 情绪指数 > 0: 乐观, < 0: 悲观")
    print("• 时机指标 > 1: 买入信号, < -1: 卖出信号")
    print("=" * 60)