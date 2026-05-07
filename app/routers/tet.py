import os
import logging
from fastapi import APIRouter, Query, HTTPException
import pandas as pd
import tushare as ts

from app.core.response import ok
from tradingagents.utils.trend_emotion_timing import ATrendEmotionTiming  # 你项目里的统一返回封装

router = APIRouter(prefix="/tet", tags=["TET 趋势-情绪-时机指标"])
logger = logging.getLogger(__name__)

# ==============================
# 【接口1】获取最新 TET 指标信号
# ==============================
@router.get("/latest", summary="获取最新TET指标（趋势/情绪/锚定趋势/时机）")
def get_tet_latest(
    stock_code: str = Query(..., description="股票代码，如 002491"),
    start_date: str = Query(..., description="开始日期 2025-01-01"),
    end_date: str = Query(..., description="结束日期 2026-05-08")
):
    try:
        # 1. 初始化 tushare
        env_token = os.getenv('TUSHARE_TOKEN')
        if not env_token:
            raise HTTPException(status_code=400, detail="未配置 TUSHARE_TOKEN")
        
        api_key = env_token.strip().strip('"').strip("'")
        ts.set_token(api_key)
        pro = ts.pro_api()

        # 2. 日期格式化
        start_date_fmt = start_date.replace("-", "")
        end_date_fmt = end_date.replace("-", "")

        # 3. 获取股票数据
        def get_stock_df(stock_code, start, end):
            if '.' not in stock_code:
                ts_code = f"{stock_code}.SH" if stock_code.startswith('6') else f"{stock_code}.SZ"
            else:
                ts_code = stock_code
            df = pro.daily(
                ts_code=ts_code,
                start_date=start,
                end_date=end,
                fields='trade_date,open,high,low,close,vol'
            )
            df = df.sort_values('trade_date').reset_index(drop=True)
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            return df

        # 4. 获取沪深300
        def get_hs300_df(start, end):
            df = pro.index_daily(
                ts_code='000300.SH',
                start_date=start,
                end_date=end,
                fields='trade_date,close'
            )
            df = df.sort_values('trade_date').reset_index(drop=True)
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            return df

        stock_df = get_stock_df(stock_code, start_date_fmt, end_date_fmt)
        hs300_df = get_hs300_df(start_date_fmt, end_date_fmt)

        # 5. 计算 TET 指标
        tet = ATrendEmotionTiming(stock_code)
        tet.load_data(stock_df, hs300_df)
        tet.calculate_trend_score()
        tet.calculate_emotion_index()
        tet.calculate_anchored_trend()
        tet.calculate_timing()

        # 6. 返回最新信号
        latest = tet.get_latest()
        return ok(data=latest, message="获取TET最新指标成功")

    except Exception as e:
        logger.error(f"❌ TET latest 接口异常: {e}")
        raise HTTPException(status_code=500, detail=f"TET指标计算失败: {str(e)}")


# ==============================
# 【接口2】获取全量数据（前端绘图）
# ==============================
@router.get("/chart", summary="获取TET全量历史数据（前端绘图专用）")
def get_tet_chart_data(
    stock_code: str = Query(..., description="股票代码，如 002491"),
    start_date: str = Query(..., description="开始日期 2025-01-01"),
    end_date: str = Query(..., description="结束日期 2026-05-08")
):
    try:
        env_token = os.getenv('TUSHARE_TOKEN')
        if not env_token:
            raise HTTPException(status_code=400, detail="未配置 TUSHARE_TOKEN")
        
        api_key = env_token.strip().strip('"').strip("'")
        ts.set_token(api_key)
        pro = ts.pro_api()

        start_date_fmt = start_date.replace("-", "")
        end_date_fmt = end_date.replace("-", "")

        # 获取股票数据
        def get_stock_df(stock_code, start, end):
            if '.' not in stock_code:
                ts_code = f"{stock_code}.SH" if stock_code.startswith('6') else f"{stock_code}.SZ"
            else:
                ts_code = stock_code
            df = pro.daily(
                ts_code=ts_code, start_date=start, end_date=end,
                fields='trade_date,open,high,low,close,vol'
            )
            df = df.sort_values('trade_date').reset_index(drop=True)
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            return df

        # 获取沪深300
        def get_hs300_df(start, end):
            df = pro.index_daily(
                ts_code='000300.SH', start_date=start, end_date=end,
                fields='trade_date,close'
            )
            df = df.sort_values('trade_date').reset_index(drop=True)
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            return df

        stock_df = get_stock_df(stock_code, start_date_fmt, end_date_fmt)
        hs300_df = get_hs300_df(start_date_fmt, end_date_fmt)

        # 计算 TET
        tet = ATrendEmotionTiming(stock_code)
        tet.load_data(stock_df, hs300_df)
        tet.calculate_trend_score()
        tet.calculate_emotion_index()
        tet.calculate_anchored_trend()
        tet.calculate_timing()

        # ✅ 返回全量数据（前端绘图）
        all_data = tet.get_all_data()
        return ok(data=all_data, message="获取TET绘图数据成功")

    except Exception as e:
        logger.error(f"❌ TET chart 接口异常: {e}")
        raise HTTPException(status_code=500, detail=f"TET绘图数据获取失败: {str(e)}")