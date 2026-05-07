import pandas as pd
import numpy as np

# ==============================================
# 趋势-情绪-时机指标
# ==============================================
class ATrendEmotionTiming:
    def __init__(self, stock_code):
        self.stock_code = stock_code
        self.data = None
        self.indicators = None

    def load_data(self, stock_df, hs300_df):
        stock_df = stock_df.sort_values("trade_date").reset_index(drop=True)
        hs300_df = hs300_df.sort_values("trade_date").reset_index(drop=True)
        
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
            signals.append(np.where(roc>0.5,1,np.where(roc<-0.5,-1,0)))

        # SMA 信号
        for p in [15, 20, 25, 30, 40, 50, 60, 80, 120, 150]:
            sma = pd.Series(close).rolling(window=p).mean().values
            signals.append(np.where(close>sma*1.01,1,np.where(close<sma*0.99,-1,0)))

        # 均线交叉信号
        crosses = [(5, 60), (10, 60), (20, 120), (30, 120), (5, 20), (10, 30), (20, 60), (30, 90), (5, 30), (10, 90)]
        for s, l in crosses:
            ma_s = pd.Series(close).rolling(window=s).mean().values
            ma_l = pd.Series(close).rolling(window=l).mean().values
            signals.append(np.where(ma_s>ma_l*1.01,1,np.where(ma_s<ma_l*0.99,-1,0)))

        df["trend"] = np.array(signals).mean(axis=0)
        df["trend_hs"] = np.sign(df["ratio_hs"].pct_change(20).fillna(0))
        df["trend_score"] = np.where(np.sign(df["trend"])==np.sign(df["trend_hs"]), df["trend"], 0)
        self.data = df
        return self

    def calculate_emotion_index(self):
        df = self.data
        close = df["close_stock"].values
        emotions = []

        # RSI 变体
        for p in [3, 5, 7, 9, 11, 14]:
            delta = np.diff(close)
            gain = np.maximum(delta,0)
            loss = -np.minimum(delta,0)
            avg_gain = pd.Series(gain).rolling(window=p, min_periods=1).mean().values
            avg_loss = pd.Series(loss).rolling(window=p, min_periods=1).mean().values
            rs = avg_gain/(avg_loss+1e-8)
            rsi = np.concatenate([[50], 100-(100/(1+rs))])
            emotions.append(rsi/50-1)

        # 随机指标变体
        high = df["high"].values
        low = df["low"].values
        for p in [2,4,6,8,10,12]:
            hh = pd.Series(high).rolling(window=p).max().values
            ll = pd.Series(low).rolling(window=p).min().values
            rng = (close-ll)/(hh-ll+1e-8)
            emotions.append(rng*2-1)

        df["emotion_index"] = np.array(emotions).mean(axis=0)
        self.data = df
        return self

    def calculate_anchored_trend(self):
        df = self.data
        anchored = []
        current=0
        for i in range(len(df)):
            e=df["emotion_index"].iloc[i]
            if abs(e)<0.15 or (i>0 and np.sign(e)!=np.sign(df["emotion_index"].iloc[i-1])):
                current=df["trend_score"].iloc[i]
            anchored.append(current)
        df["anchored_trend_score"]=anchored
        self.data=df
        return self

    def calculate_timing(self):
        df=self.data
        df["timing_indicator"]=df["anchored_trend_score"]-df["emotion_index"]

        df = df.replace([np.inf, -np.inf], np.nan)
        df = df.fillna(0)
        self.data = df
        self.indicators = df.iloc[-1]
        return self

    def get_latest(self):
        d=self.indicators
        return {
            "trend_score": round(float(d["trend_score"]),2),
            "emotion_index": round(float(d["emotion_index"]),2),
            "anchored_trend_score": round(float(d["anchored_trend_score"]),2),
            "timing_indicator": round(float(d["timing_indicator"]),2),
            "action": "BUY" if d["timing_indicator"]>1.0 else "SELL" if d["timing_indicator"]<-1.0 else "HOLD"
        }

    def get_all_data(self):
        if self.data is None:
            return []
        df=self.data.copy()
        keep_cols=["date","close_stock","close_hs300", "trend_score","emotion_index","anchored_trend_score","timing_indicator"]
        keep_cols=[c for c in keep_cols if c in df.columns]
        df=df[keep_cols]

        # 修复 JSON 非法值
        df = df.replace([np.inf, -np.inf], np.nan).fillna(0)

        for col in df.columns:
            if col!="date":
                df[col]=df[col].astype(float).round(4)
        return df.to_dict(orient="records")