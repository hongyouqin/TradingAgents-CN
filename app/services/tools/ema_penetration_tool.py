"""
EMA穿透买入策略分析工具

基于均值穿透方法，在趋势向上时计算建议买入价格。
策略原理：
1. 计算快速EMA(13日)和慢速EMA(26日)，确认趋势方向
2. 统计最近N天内收盘价跌破快速EMA的穿透事件
3. 计算平均穿透深度
4. 估算明日EMA值，减去平均穿透深度得到建议买入价

依赖：
- HistoricalDataService（MongoDB日线数据）
- stock_basic_info 集合（股票名称）
"""

import logging
from typing import Dict, Any

import numpy as np
import pandas as pd

from app.services.historical_data_service import get_historical_data_service
from app.core.database import get_mongo_db

logger = logging.getLogger(__name__)


class EmaPenetrationTool:
    """EMA穿透买入策略分析工具"""

    async def analyze(
        self,
        symbol: str,
        fast_ema: int = 13,
        slow_ema: int = 26,
        lookback_period: int = 30,
    ) -> Dict[str, Any]:
        """
        分析EMA穿透买入策略。

        Args:
            symbol: 6位股票代码，如 "000001"
            fast_ema: 快速EMA周期（默认13日）
            slow_ema: 慢速EMA周期（默认26日）
            lookback_period: 计算平均穿透值的回溯周期（默认30天）

        Returns:
            dict: {
                success: bool,
                data: {
                    symbol, stock_name, latest_date, latest_close,
                    current_fast_ema, current_slow_ema,
                    penetration_count, avg_penetration,
                    estimated_tomorrow_ema, suggested_buy_price,
                    trend_status
                },
                message: str
            }
        """
        try:
            # 1. 获取历史日线数据（最近180天）
            service = await get_historical_data_service()
            records = await service.get_historical_data(
                symbol=symbol,
                period="daily",
                limit=180,
            )

            if not records:
                logger.warning(f"[EmaPenetration] {symbol} 无历史数据")
                return {
                    "success": False,
                    "data": None,
                    "message": f"股票 {symbol} 无历史数据",
                }

            # 2. 转换为 DataFrame（日期升序）
            df = pd.DataFrame(records)
            # 标准化列名：MongoDB 中 trade_date 是日期字段
            df["date"] = pd.to_datetime(df["trade_date"])
            df = df.sort_values("date", ascending=True).reset_index(drop=True)

            latest_close = float(df["close"].iloc[-1])
            latest_date = str(df["date"].iloc[-1].strftime("%Y-%m-%d"))

            logger.info(
                f"[EmaPenetration] {symbol} 最新日期: {latest_date} "
                f"最新收盘价: {latest_close}"
            )

            # 3. 计算 EMA
            df[f"EMA_{fast_ema}"] = df["close"].ewm(span=fast_ema, adjust=False).mean()
            df[f"EMA_{slow_ema}"] = df["close"].ewm(span=slow_ema, adjust=False).mean()

            # 4. 计算每日EMA变化
            df["EMA_diff"] = df[f"EMA_{fast_ema}"].diff()

            # 5. 计算穿透深度（仅当收盘价低于快速EMA时）
            df["penetration"] = df["close"] < df[f"EMA_{fast_ema}"]
            df["penetration_depth"] = np.where(
                df["close"] < df[f"EMA_{fast_ema}"],
                df[f"EMA_{fast_ema}"] - df["close"],  # 穿透深度
                np.nan,  # 非穿透日标记为NaN
            )

            # 6. 统计回溯期内的穿透事件
            recent_data = (
                df.iloc[-lookback_period:] if len(df) >= lookback_period else df
            )
            penetrations = recent_data["penetration_depth"].dropna()

            if len(penetrations) == 0:
                return {
                    "success": True,
                    "data": {
                        "symbol": symbol,
                        "stock_name": await self._get_stock_name(symbol),
                        "latest_date": latest_date,
                        "latest_close": latest_close,
                        "current_fast_ema": round(float(df[f"EMA_{fast_ema}"].iloc[-1]), 2),
                        "current_slow_ema": round(float(df[f"EMA_{slow_ema}"].iloc[-1]), 2),
                        "penetration_count": 0,
                        "avg_penetration": None,
                        "estimated_tomorrow_ema": None,
                        "suggested_buy_price": None,
                        "trend_status": self._judge_trend(df, fast_ema, slow_ema),
                    },
                    "message": f"最近{lookback_period}天内无穿透事件，建议等待价格回调至均线附近再观察",
                }

            # 7. 计算平均穿透深度
            avg_penetration = float(penetrations.mean())

            # 8. 估算明日EMA
            last_ema = float(df[f"EMA_{fast_ema}"].iloc[-1])
            ema_diff = float(df["EMA_diff"].iloc[-1])
            estimated_tomorrow_ema = round(last_ema + ema_diff, 2)

            # 9. 计算建议买入价
            buy_price = round(estimated_tomorrow_ema - avg_penetration, 2)

            # 10. 获取股票名称
            stock_name = await self._get_stock_name(symbol)

            result_data = {
                "symbol": symbol,
                "stock_name": stock_name,
                "latest_date": latest_date,
                "latest_close": latest_close,
                "current_fast_ema": round(last_ema, 2),
                "current_slow_ema": round(float(df[f"EMA_{slow_ema}"].iloc[-1]), 2),
                "penetration_count": int(len(penetrations)),
                "avg_penetration": round(avg_penetration, 2),
                "estimated_tomorrow_ema": estimated_tomorrow_ema,
                "suggested_buy_price": buy_price,
                "trend_status": self._judge_trend(df, fast_ema, slow_ema),
            }

            logger.info(
                f"[EmaPenetration] {stock_name}({symbol}) 分析完成: "
                f"建议买入价={buy_price}, "
                f"穿透次数={len(penetrations)}, "
                f"平均穿透深度={avg_penetration:.2f}"
            )

            return {
                "success": True,
                "data": result_data,
                "message": (
                    f"【EMA穿透策略分析】{stock_name}({symbol})\n"
                    f"当前快EMA({fast_ema}日): {last_ema:.2f} | "
                    f"当前慢EMA({slow_ema}日): {result_data['current_slow_ema']:.2f}\n"
                    f"最近{lookback_period}天穿透{int(len(penetrations))}次 | "
                    f"平均穿透深度: {avg_penetration:.2f}\n"
                    f"趋势状态: {result_data['trend_status']}\n"
                    f"估算明日EMA: {estimated_tomorrow_ema:.2f}\n"
                    f"建议买入价: {buy_price:.2f}"
                ),
            }

        except Exception as e:
            logger.error(f"[EmaPenetration] {symbol} 分析异常: {e}", exc_info=True)
            return {
                "success": False,
                "data": None,
                "message": f"EMA穿透策略分析异常: {e}",
            }

    def _judge_trend(
        self, df: pd.DataFrame, fast_ema: int, slow_ema: int
    ) -> str:
        """判断趋势状态：向上、走平、向下"""
        last_fast = df[f"EMA_{fast_ema}"].iloc[-1]
        last_slow = df[f"EMA_{slow_ema}"].iloc[-1]

        # 取最近5个交易日判断趋势方向
        recent_fast = df[f"EMA_{fast_ema}"].iloc[-5:].values
        if len(recent_fast) < 2:
            return "数据不足"

        fast_trend = recent_fast[-1] - recent_fast[0]

        if last_fast > last_slow and fast_trend > 0:
            return "向上"
        elif last_fast < last_slow:
            return "向下"
        else:
            return "走平"

    async def _get_stock_name(self, symbol: str) -> str:
        """从 stock_basic_info 集合查询股票名称"""
        try:
            db = get_mongo_db()
            doc = await db["stock_basic_info"].find_one(
                {"symbol": symbol},
                projection={"name": 1},
            )
            if doc and doc.get("name"):
                return doc["name"]
            return symbol  # 找不到则返回代码本身
        except Exception as e:
            logger.warning(
                f"[EmaPenetration] 查询股票名称失败 {symbol}: {e}"
            )
            return symbol
