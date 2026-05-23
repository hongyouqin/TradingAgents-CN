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

        df = df.replace([np.inf, -np.inf], np.nan).fillna(0)

        for col in df.columns:
            if col!="date":
                df[col]=df[col].astype(float).round(4)
        return df.to_dict(orient="records")

    # ==============================
    # 【新增1】单股票回测（论文策略）
    # ==============================
    def backtest(self, plot=True, return_equity_curve=False):
        """
        论文策略回测：
        - BUY:  timing_indicator > 1.0
        - SELL: timing_indicator < -1.0
        - 持仓 = 信号（次日开盘成交）

        Parameters
        ----------
        plot : bool
            是否绘制回测曲线图（默认 True，仅适用于 Jupyter 环境）
        return_equity_curve : bool
            是否在返回结果中包含每日净值曲线数据

        Returns
        -------
        dict
        """
        df = self.data.copy()
        df["signal"] = 0
        df.loc[df["timing_indicator"] > 1.0, "signal"] = 1
        df.loc[df["timing_indicator"] < -1.0, "signal"] = -1

        # 次日开盘成交（真实回测）
        df["ret"] = df["close_stock"].pct_change().fillna(0)
        df["strategy_ret"] = df["signal"].shift(1) * df["ret"]

        # 累计收益
        df["cum_strategy"] = (1 + df["strategy_ret"]).cumprod()
        df["cum_bench"] = (1 + df["ret"]).cumprod()

        # 指标
        total_ret = df["cum_strategy"].iloc[-1] - 1
        annual_ret = df["strategy_ret"].mean() * 252
        sharpe = np.sqrt(252) * df["strategy_ret"].mean() / (df["strategy_ret"].std() + 1e-8)
        max_dd = (df["cum_strategy"] / df["cum_strategy"].cummax() - 1).min()
        trade_count = int((df["signal"].diff().abs() > 0).sum())

        if plot:
            plt.figure(figsize=(12,5))
            plt.plot(df["date"], df["cum_strategy"], label="策略", linewidth=2)
            plt.plot(df["date"], df["cum_bench"], label="标的", alpha=0.6)
            plt.title(f"{self.stock_code} 回测")
            plt.legend()
            plt.grid(alpha=0.3)
            plt.show()

        result = {
            "code": self.stock_code,
            "total_return": round(total_ret, 4),
            "annual_return": round(annual_ret, 4),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(max_dd, 4),
            "trade_count": trade_count,
            "win_rate": round(self._calc_win_rate(df), 4)
        }

        if return_equity_curve:
            equity_curve = df[["date", "cum_strategy", "cum_bench"]].copy()
            equity_curve["date"] = equity_curve["date"].astype(str)
            result["equity_curve"] = equity_curve.to_dict(orient="records")

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

    def build(self, top_n=10):
        """
        按论文逻辑构建组合：
        1. 计算每只股票指标
        2. 筛选 timing > 0.8 且 anchored_trend > 0.2
        3. 等权 / 按趋势强度加权
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
                latest["code"] = code
                portfolio.append(latest)
            except:
                continue

        df_port = pd.DataFrame(portfolio)
        if df_port.empty:
            self.results = df_port
            return df_port

        # 论文筛选条件：强趋势 + 好时机
        df_port = df_port[
            (df_port["anchored_trend_score"] > 0.2) &
            (df_port["timing_indicator"] > 0.8)
        ].sort_values("timing_indicator", ascending=False)

        # 取前N只
        df_port = df_port.head(top_n)

        if len(df_port) == 0:
            self.results = df_port
            return df_port

        # 等权
        df_port["weight_equal"] = 1 / len(df_port)
        # 按时机强度加权
        df_port["weight_timing"] = df_port["timing_indicator"] / df_port["timing_indicator"].sum()

        self.results = df_port
        return df_port

    def build_summary(self, top_n=10):
        """
        构建组合并以 API 友好格式返回
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
                "action": row.get("action", "HOLD"),
                "weight_equal": round(float(row["weight_equal"]), 4),
                "weight_timing": round(float(row["weight_timing"]), 4)
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
                "date": combine.index.astype(str),
                "cum_return": cum.values
            })
            result["equity_curve"] = curve.to_dict(orient="records")

        return result