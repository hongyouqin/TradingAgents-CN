import pandas as pd
import numpy as np
from tqdm import tqdm
import matplotlib.pyplot as plt

# ==============================================
# 趋势-情绪-时机指标（原版 + 回测 + 组合功能）
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
        n_rows = len(close)

        # RSI 变体（修复长度对齐）
        for p in [3, 5, 7, 9, 11, 14]:
            # 整根数组对齐，不出现长度错位
            rsi = np.zeros(n_rows)
            delta = np.diff(close)
            if len(delta) < 1:
                emotions.append(rsi)
                continue

            gain = np.maximum(delta, 0)
            loss = -np.minimum(delta, 0)
            avg_gain = pd.Series(gain).rolling(window=p, min_periods=1).mean().values
            avg_loss = pd.Series(loss).rolling(window=p, min_periods=1).mean().values
            rs = avg_gain / (avg_loss + 1e-8)
            rsi[1:] = 100 - (100 / (1 + rs))
            emotions.append(rsi / 50 - 1)

        # 随机指标变体
        high = df["high"].values
        low = df["low"].values
        for p in [2, 4, 6, 8, 10, 12]:
            hh = pd.Series(high).rolling(window=p).max().values
            ll = pd.Series(low).rolling(window=p).min().values
            rng = (close - ll) / (hh - ll + 1e-8)
            emotions.append(rng * 2 - 1)

        # 关键修复：确保所有信号长度一样
        emotions_arr = np.array(emotions)
        df["emotion_index"] = emotions_arr.mean(axis=0)

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

        df = df.replace([np.inf, -np.inf], np.nan).fillna(0)

        for col in df.columns:
            if col!="date":
                df[col]=df[col].astype(float).round(4)
        return df.to_dict(orient="records")

    def backtest(self, plot=False, return_equity_curve=False):
        df = self.data.copy().dropna()

        # ==============================
        # ✅ 论文 100% 原版信号
        # ==============================
        df["signal"] = 0
        df.loc[df["timing_indicator"] > 1.0, "signal"] = 1      # 买
        df.loc[df["timing_indicator"] <= 0, "signal"] = 0     # 卖（论文真正退出）

        # 保证信号是 0/1
        df["signal"] = df["signal"].fillna(0).astype(int)

        # ==============================
        # 计算收益
        # ==============================
        df["ret"] = df["close_stock"].pct_change().fillna(0)
        df["strategy_ret"] = df["signal"].shift(1) * df["ret"]

        df["cum_strategy"] = (1 + df["strategy_ret"]).cumprod()
        df["cum_bench"] = (1 + df["ret"]).cumprod()

        # 清理非法值
        df["cum_strategy"] = df["cum_strategy"].replace([np.inf, -np.inf], 0.0).fillna(1.0)
        df["cum_bench"] = df["cum_bench"].replace([np.inf, -np.inf], 0.0).fillna(1.0)

        # 指标计算
        total_ret = df["cum_strategy"].iloc[-1] - 1
        annual_ret = df["strategy_ret"].mean() * 252
        sharpe = np.sqrt(252) * df["strategy_ret"].mean() / (df["strategy_ret"].std() + 1e-8)
        max_dd = (df["cum_strategy"] / df["cum_strategy"].cummax() - 1).min()
        trade_count = int((df["signal"].diff().abs() > 0).sum())

        # 胜率
        trades = df[df["signal"].diff() != 0]
        win_rate = 0.0
        if len(trades) > 0:
            win_rate = (trades["strategy_ret"] > 0).mean()

        # 防 nan/inf
        total_ret = np.nan_to_num(total_ret, nan=0.0, posinf=0.0, neginf=0.0)
        annual_ret = np.nan_to_num(annual_ret, nan=0.0, posinf=0.0, neginf=0.0)
        sharpe = np.nan_to_num(sharpe, nan=0.0, posinf=0.0, neginf=0.0)
        max_dd = np.nan_to_num(max_dd, nan=0.0, posinf=0.0, neginf=0.0)
        win_rate = np.nan_to_num(win_rate, nan=0.0, posinf=0.0, neginf=0.0)

        result = {
            "total_return": round(float(total_ret), 3),
            "annual_return": round(float(annual_ret), 3),
            "sharpe_ratio": round(float(sharpe), 2),
            "max_drawdown": round(float(max_dd), 3),
            "trade_count": trade_count,
            "win_rate": round(float(win_rate), 2)
        }

        if return_equity_curve:
            result["equity_curve"] = {
                "date": df["date"].astype(str).tolist(),
                "strategy": df["cum_strategy"].round(4).tolist(),
                "benchmark": df["cum_bench"].round(4).tolist()
            }

        return result

    def _calc_win_rate(self, df):
        """计算胜率（盈利交易 / 总交易）"""
        trades = df[df["signal"].diff().abs() > 0].copy()
        if len(trades) < 2:
            return 0.0
        entry_prices = trades["close_stock"].values
        if len(entry_prices) < 2:
            return 0.0
        # 简单判断：入场后下一次出场时的收益
        wins = 0
        total = 0
        for i in range(0, len(entry_prices)-1, 2):
            if i+1 < len(entry_prices):
                ret = (entry_prices[i+1] - entry_prices[i]) / entry_prices[i]
                if ret > 0:
                    wins += 1
                total += 1
        return wins / total if total > 0 else 0.0

# ==============================================
# 【新增2】多股票投资组合（论文7.3）
# ==============================================
class PortfolioBuilder:
    def __init__(self, stock_dict, hs300_df):
        """
        stock_dict: {股票代码: 行情df}
        """
        self.stock_dict = stock_dict
        self.hs300_df = hs300_df
        self.results = []

    # --------------------------
    # 🔥 新增：论文要求的历史波动率
    # --------------------------
    def calculate_historical_volatility(self, close_series, window=20):
        """计算20日历史波动率（年化）= 论文指定用的波动率"""
        ret = close_series.pct_change().fillna(0)
        vol = ret.rolling(window=window).std() * np.sqrt(252)  # 年化
        return vol

    def build(self, top_n=10):
        """
        按论文逻辑构建组合：
        1. 计算每只股票指标
        2. 筛选 timing > 0.8 且 anchored_trend > 0.2
        3. 按论文：等权 / 趋势强度加权 / 【波动率×趋势】加权
        """
        if len(self.stock_dict) == 0:
            self.results = pd.DataFrame()
            return self.results

        portfolio = []

        for code, df in tqdm(self.stock_dict.items()):
            try:
                model = ATrendEmotionTiming(code)
                model.load_data(df, self.hs300_df)
                model.calculate_trend_score()
                model.calculate_emotion_index()
                model.calculate_anchored_trend()
                model.calculate_timing()

                latest = model.get_latest()

                # ======================
                # ✅ 论文新增：波动率 + 预期收益
                # ======================
                hv = self.calculate_historical_volatility(df["close"])
                latest["hist_volatility"] = hv.iloc[-1]  # 最新波动率
                latest["expected_return"] = latest["anchored_trend_score"] * latest["hist_volatility"]  # 论文公式

                latest["code"] = code
                portfolio.append(latest)
            except Exception as e:
                print(f"[Portfolio] {code} 计算失败: {e}")
                continue

        df_port = pd.DataFrame(portfolio)
        if df_port.empty:
            self.results = df_port
            return df_port

        # 论文筛选条件：强趋势 + 好时机
        df_port = df_port[
            (df_port["anchored_trend_score"] > 0.2) &
            (df_port["timing_indicator"] > 0.8)
        ].sort_values("expected_return", ascending=False)  # 按论文预期收益排序

        # 取前N只
        df_port = df_port.head(top_n)

        if len(df_port) == 0:
            self.results = df_port
            return df_port

        # 三种加权方式（论文全覆盖）
        df_port["weight_equal"] = 1 / len(df_port)                           # 等权
        df_port["weight_timing"] = df_port["timing_indicator"] / df_port["timing_indicator"].sum()  # 时机加权
        df_port["weight_vol_trend"] = df_port["expected_return"] / df_port["expected_return"].sum() # 论文官方加权！

        self.results = df_port
        return df_port

    def build_summary(self, top_n=10):
        """
        构建组合并以 API 友好格式返回（带波动率+预期收益）
        """
        df_port = self.build(top_n=top_n)
        if df_port.empty:
            return {"stocks": [], "total_count": 0}

        stocks = []
        for _, row in df_port.iterrows():
            stocks.append({
                "code": row["code"],
                "trend_score": round(float(row["trend_score"]), 2),
                "emotion_index": round(float(row["emotion_index"]), 2),
                "anchored_trend_score": round(float(row["anchored_trend_score"]), 2),
                "timing_indicator": round(float(row["timing_indicator"]), 2),
                "hist_volatility": round(float(row["hist_volatility"]), 3),       # 新增
                "expected_return": round(float(row["expected_return"]), 3),     # 新增（论文核心）
                "action": row.get("action", "HOLD"),
                "weight_equal": round(float(row["weight_equal"]), 4),
                "weight_timing": round(float(row["weight_timing"]), 4),
                "weight_vol_trend": round(float(row["weight_vol_trend"]), 4),   # 论文加权
            })

        return {
            "total_count": len(stocks),
            "top_n": top_n,
            "stocks": stocks
        }

    def backtest_portfolio(self, return_equity_curve=False):
        """组合历史回测（API 友好版）"""
        if self.results is None or (isinstance(self.results, pd.DataFrame) and self.results.empty):
            return {"error": "请先调用 build() 构建组合"}

        dfs = []
        for code in self.results["code"]:
            df = self.stock_dict[code]
            model = ATrendEmotionTiming(code)
            model.load_data(df, self.hs300_df)
            model.calculate_trend_score()
            model.calculate_emotion_index()
            model.calculate_anchored_trend()
            model.calculate_timing()
            dfs.append(model.data[["date", "close_stock"]].set_index("date"))

        combine = pd.concat(dfs, axis=1).dropna()
        combine.columns = list(self.results["code"])
        ret = combine.pct_change().fillna(0)
        weight = self.results["weight_equal"].values
        port_ret = (ret * weight).sum(axis=1)
        cum = (1 + port_ret).cumprod()

        total_ret = cum.iloc[-1] - 1
        annual_ret = port_ret.mean() * 252
        sharpe = np.sqrt(252) * port_ret.mean() / (port_ret.std() + 1e-8)
        max_dd = (cum / cum.cummax() - 1).min()

        result = {
            "total_return": round(total_ret, 4),
            "annual_return": round(annual_ret, 4),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(max_dd, 4),
            "stock_count": len(self.results)
        }

        if return_equity_curve:
            curve = pd.DataFrame({
                "date": cum.index.astype(str),
                "cum_return": cum.round(4).values
            })
            result["equity_curve"] = curve.to_dict(orient="records")

        return result