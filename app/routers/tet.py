import os
import logging
from datetime import datetime
from typing import List
from fastapi import APIRouter, Query, HTTPException, Request, Body
import pandas as pd
import tushare as ts

from app.core.response import ok
from app.core.database import get_mongo_db_sync
from tradingagents.utils.trend_emotion_timing import ATrendEmotionTiming, PortfolioBuilder

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
    request: Request,
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

        # ✅ 记录埋点（异步写 MongoDB，不阻塞响应）
        try:
            # 获取客户端 IP
            client_ip = request.client.host if request.client else "unknown"
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                client_ip = forwarded.split(",")[0].strip()

            db_sync = get_mongo_db_sync()
            db_sync["tracking_events"].insert_one({
                "event_type": "tet_chart_click",
                "user_id": "",
                "username": "anonymous",
                "stock_code": stock_code,
                "start_date": start_date,
                "end_date": end_date,
                "ip": client_ip,
                "created_at": datetime.utcnow()
            })
        except Exception as track_err:
            # 埋点失败不影响主流程
            logger.warning(f"⚠️ TET chart 埋点记录失败: {track_err}")

        # ✅ 返回全量数据（前端绘图）
        all_data = tet.get_all_data()
        return ok(data=all_data, message="获取TET绘图数据成功")

    except Exception as e:
        logger.error(f"❌ TET chart 接口异常: {e}")
        raise HTTPException(status_code=500, detail=f"TET绘图数据获取失败: {str(e)}")


# ==============================================
# 数据获取辅助函数
# ==============================================
def _init_tushare_pro():
    """初始化 Tushare pro 接口"""
    env_token = os.getenv('TUSHARE_TOKEN')
    if not env_token:
        raise HTTPException(status_code=400, detail="未配置 TUSHARE_TOKEN")
    api_key = env_token.strip().strip('"').strip("'")
    ts.set_token(api_key)
    return ts.pro_api()


def _fetch_stock_df(pro, stock_code: str, start: str, end: str) -> pd.DataFrame:
    """获取个股日线数据"""
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


def _fetch_hs300_df(pro, start: str, end: str) -> pd.DataFrame:
    """获取沪深300指数日线数据"""
    df = pro.index_daily(
        ts_code='000300.SH', start_date=start, end_date=end,
        fields='trade_date,close'
    )
    df = df.sort_values('trade_date').reset_index(drop=True)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df


# ==============================
# 【接口3】单股票回测
# ==============================
@router.get("/backtest", summary="单股票 TET 策略回测")
def get_tet_backtest(
    request: Request,
    stock_code: str = Query(..., description="股票代码，如 002491"),
    start_date: str = Query(..., description="开始日期 2025-01-01"),
    end_date: str = Query(..., description="结束日期 2026-05-08"),
    return_equity_curve: bool = Query(True, description="是否返回每日净值曲线")
):
    """
    对单只股票执行 TET 策略回测

    返回指标:
    - total_return: 总收益率
    - annual_return: 年化收益率
    - sharpe_ratio: 夏普比率
    - max_drawdown: 最大回撤
    - trade_count: 交易次数
    - win_rate: 胜率
    - equity_curve: 每日净值曲线（策略 vs 基准）
    """
    try:
        pro = _init_tushare_pro()
        start_fmt = start_date.replace("-", "")
        end_fmt = end_date.replace("-", "")

        stock_df = _fetch_stock_df(pro, stock_code, start_fmt, end_fmt)
        hs300_df = _fetch_hs300_df(pro, start_fmt, end_fmt)

        # 计算 TET 指标
        tet = ATrendEmotionTiming(stock_code)
        tet.load_data(stock_df, hs300_df)
        tet.calculate_trend_score()
        tet.calculate_emotion_index()
        tet.calculate_anchored_trend()
        tet.calculate_timing()

        # 执行回测
        result = tet.backtest(plot=False, return_equity_curve=return_equity_curve)

        # ✅ 记录埋点（不阻塞响应）
        try:
            client_ip = request.client.host if request.client else "unknown"
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                client_ip = forwarded.split(",")[0].strip()

            db_sync = get_mongo_db_sync()
            db_sync["tracking_events"].insert_one({
                "event_type": "tet_backtest_click",
                "user_id": "",
                "username": "anonymous",
                "stock_code": stock_code,
                "start_date": start_date,
                "end_date": end_date,
                "ip": client_ip,
                "created_at": datetime.utcnow()
            })
        except Exception as track_err:
            logger.warning(f"⚠️ TET backtest 埋点记录失败: {track_err}")

        return ok(data=result, message="TET策略回测完成")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ TET backtest 接口异常: {e}")
        raise HTTPException(status_code=500, detail=f"TET回测失败: {str(e)}")


# ==============================
# 【接口4】多股票投资组合
# ==============================
@router.post("/portfolio", summary="多股票 TET 投资组合分析")
def build_tet_portfolio(
    request: Request,
    stock_codes: List[str] = Body(..., description="股票代码列表，如 [\"002491\", \"000001\", \"600519\"]"),
    start_date: str = Query(..., description="开始日期 2025-01-01"),
    end_date: str = Query(..., description="结束日期 2026-05-08"),
    top_n: int = Query(10, description="组合中最大持仓股票数量"),
    return_equity_curve: bool = Query(True, description="是否返回净值曲线")
):
    """
    对多只股票进行 TET 指标筛选，构建投资组合并回测

    - 筛选条件: anchored_trend_score > 0.2 AND timing_indicator > 0.8
    - 权重: 等权 (weight_equal) / 按 timing 强度加权 (weight_timing)
    """
    try:
        if not stock_codes or len(stock_codes) == 0:
            raise HTTPException(status_code=400, detail="请提供至少一只股票代码")

        pro = _init_tushare_pro()
        start_fmt = start_date.replace("-", "")
        end_fmt = end_date.replace("-", "")

        # 获取所有股票数据和沪深300
        stock_dict = {}
        for code in stock_codes:
            try:
                df = _fetch_stock_df(pro, code, start_fmt, end_fmt)
                if not df.empty:
                    stock_dict[code] = df
            except Exception as fetch_err:
                logger.warning(f"⚠️ 获取 {code} 数据失败: {fetch_err}")
                continue

        if len(stock_dict) == 0:
            raise HTTPException(status_code=400, detail="所有股票数据获取失败")

        hs300_df = _fetch_hs300_df(pro, start_fmt, end_fmt)

        # 构建组合
        builder = PortfolioBuilder(stock_dict, hs300_df)
        portfolio_summary = builder.build_summary(top_n=top_n)

        # 执行组合回测
        backtest_result = builder.backtest_portfolio(return_equity_curve=return_equity_curve)

        result = {
            "portfolio": portfolio_summary,
            "backtest": backtest_result
        }

        return ok(data=result, message="TET投资组合分析完成")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ TET portfolio 接口异常: {e}")
        raise HTTPException(status_code=500, detail=f"TET投资组合分析失败: {str(e)}")