from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request, logger
from typing import Dict, Any, Optional

from app.routers.auth_db import get_current_user
from app.services.user_status_service import user_stat_service
from app.core.database import get_database, get_mongo_db_sync

router = APIRouter(prefix="/admin/stats", tags=["管理员-统计大盘"])


# ------------------------------
# 鉴权依赖（管理员专用）
# ------------------------------
async def get_admin_user(user: dict = Depends(get_current_user)):
    # 你提供的权限判断逻辑
    if not user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="权限不足")
    return user


# ------------------------------
# 1. 获取今日统计
# ------------------------------
@router.get("/today", response_model=Dict[str, Any])
async def get_today_stats(admin=Depends(get_admin_user)):
    """
    获取今日运营统计大盘
    包含：用户、日活、充值、报告生成次数
    """
    stats = user_stat_service.generate_daily_stats()

    return {
        "success": True,
        "data": stats
    }


# ------------------------------
# 2. 手动生成今日统计
# ------------------------------
@router.post("/generate", response_model=Dict[str, Any])
async def generate_today_stats(admin=Depends(get_admin_user)):
    """手动生成并保存今日统计数据"""
    stats = user_stat_service.generate_daily_stats()
    return {
        "success": True,
        "data": stats,
        "message": "统计已更新完成"
    }


# ------------------------------
# 3. 获取历史统计（图表用）
# ------------------------------
@router.get("/history", response_model=Dict[str, Any])
async def get_history_stats(
    days: int = 30,
    admin=Depends(get_admin_user)
):
    """获取近N天历史统计，用于前端图表"""
    history = user_stat_service.get_history(days=days)
    return {
        "success": True,
        "count": len(history),
        "data": history
    }


# ------------------------------
# 4. 管理员大盘总览
# ------------------------------
@router.get("/dashboard", response_model=Dict[str, Any])
async def get_admin_dashboard(admin=Depends(get_admin_user)):
    """管理员后台大盘数据"""
    today = user_stat_service.generate_daily_stats()

    return {
        "success": True,
        "data": {
            "today": today,
            "summary": {
                "总用户数": today["total_users"],
                "今日注册": today["daily_register"],
                "今日日活": today["dau"],
                "今日充值": today["daily_recharge"],
                "今日报告生成": today["daily_reports"],
                "本月充值": today["monthly_recharge"],
                "本月报告生成": today["monthly_reports"]
            }
        }
    }
    

# ------------------------------
# 🔥 登录埋点接口（前端每次打开APP/登录时调用）
# ------------------------------
@router.post("/track/login")
async def track_user_login(user: dict = Depends(get_current_user)):
    """
    用户登录/打开APP埋点
    作用：更新 last_login 字段，用于统计 DAU 日活
    """
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=400, detail="用户不存在")

    # 🔥 核心：更新 last_login 为当前UTC时间
    await user_stat_service.update_last_login(user_id)

    return {
        "success": True,
        "message": "登录埋点成功",
        "last_login": datetime.utcnow()
    }


@router.get("/sign/today")
async def get_today_sign_count(db = Depends(get_database), admin=Depends(get_admin_user)):
    """
    获取今日签到人数统计（UTC 日期）
    使用 sign_records 表按 sign_date 字符串统计，避免时区/格式问题
    """
    from datetime import datetime as _dt
    stat_date = _dt.utcnow().date()
    date_key = stat_date.isoformat()
    # sign_records 表中的 sign_date 字段存储为 ISO 日期字符串（YYYY-MM-DD）
    count = await db["sign_records"].count_documents({"sign_date": date_key})
    return {"success": True, "data": {"date": date_key, "sign_count": int(count)}}


# ==============================================
# 🔥 埋点：用户图表点击统计
# ==============================================
@router.post("/track/chart-click")
async def track_chart_click(
    request: Request,
    payload: Dict[str, Any],
    user: dict = Depends(get_current_user)
):
    """
    埋点记录：用户点击 TET 图表时的行为

    请求体示例:
    {
        "event_type": "tet_chart_click",
        "stock_code": "002491",
        "start_date": "2025-01-01",
        "end_date": "2026-05-08"
    }
    """
    try:
        user_id = user.get("id")
        username = user.get("username", "unknown")
        event_type = payload.get("event_type", "tet_chart_click")
        stock_code = payload.get("stock_code", "")
        start_date = payload.get("start_date", "")
        end_date = payload.get("end_date", "")

        # 获取客户端 IP
        client_ip = request.client.host if request.client else "unknown"
        # 尝试从 X-Forwarded-For 获取真实 IP
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        # 记录到 MongoDB（同步方式）
        db_sync = get_mongo_db_sync()
        db_sync["tracking_events"].insert_one({
            "event_type": event_type,
            "user_id": user_id,
            "username": username,
            "stock_code": stock_code,
            "start_date": start_date,
            "end_date": end_date,
            "ip": client_ip,
            "created_at": datetime.utcnow()
        })

        return {
            "success": True,
            "message": "埋点记录成功"
        }
    except Exception as e:
        logger.error(f"❌ 埋点记录异常: {e}")
        raise HTTPException(status_code=500, detail=f"埋点记录失败: {str(e)}")


# ==============================================
# 📊 埋点统计查询（管理员专用）
# ==============================================
@router.get("/tracking/chart-clicks/summary")
async def get_chart_click_stats(
    days: int = Query(30, description="查询近N天的数据"),
    admin=Depends(get_admin_user)
):
    """
    获取 TET 图表点击统计汇总（管理员专用）

    返回:
    - total_clicks: 总点击数
    - today_clicks: 今日点击数
    - top_stocks: 点击最多的股票 Top 10
    - top_users: 点击最多的用户 Top 10
    - daily_trend: 每日点击趋势
    """
    try:
        db_sync = get_mongo_db_sync()
        collection = db_sync["tracking_events"]
        now = datetime.utcnow()

        # 计算时间范围
        from datetime import timedelta
        since = now - timedelta(days=days)
        today_start = datetime(now.year, now.month, now.day, 0, 0, 0)
        tomorrow_start = today_start + timedelta(days=1)

        # 总点击数
        total_clicks = collection.count_documents({
            "event_type": "tet_chart_click"
        })

        # 今日点击数
        today_clicks = collection.count_documents({
            "event_type": "tet_chart_click",
            "created_at": {"$gte": today_start, "$lt": tomorrow_start}
        })

        # 近N天点击数
        period_clicks = collection.count_documents({
            "event_type": "tet_chart_click",
            "created_at": {"$gte": since}
        })

        # 点击最多的股票 Top 10
        top_stocks_pipeline = [
            {"$match": {"event_type": "tet_chart_click", "stock_code": {"$ne": ""}}},
            {"$group": {"_id": "$stock_code", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        top_stocks = list(collection.aggregate(top_stocks_pipeline))
        top_stocks = [{"stock_code": item["_id"], "count": item["count"]} for item in top_stocks]

        # 点击最多的用户 Top 10
        top_users_pipeline = [
            {"$match": {"event_type": "tet_chart_click", "username": {"$ne": ""}}},
            {"$group": {"_id": {"user_id": "$user_id", "username": "$username"}, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        top_users_raw = list(collection.aggregate(top_users_pipeline))
        top_users = [
            {
                "user_id": item["_id"]["user_id"],
                "username": item["_id"]["username"],
                "count": item["count"]
            }
            for item in top_users_raw
        ]

        # 每日点击趋势（近N天）
        daily_trend_pipeline = [
            {
                "$match": {
                    "event_type": "tet_chart_click",
                    "created_at": {"$gte": since}
                }
            },
            {
                "$group": {
                    "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"_id": 1}}
        ]
        daily_trend = list(collection.aggregate(daily_trend_pipeline))
        daily_trend = [{"date": item["_id"], "count": item["count"]} for item in daily_trend]

        return {
            "success": True,
            "data": {
                "total_clicks": total_clicks,
                "today_clicks": today_clicks,
                "period_clicks": period_clicks,
                "period_days": days,
                "top_stocks": top_stocks,
                "top_users": top_users,
                "daily_trend": daily_trend
            }
        }
    except Exception as e:
        logger.error(f"❌ 查询埋点统计异常: {e}")
        raise HTTPException(status_code=500, detail=f"查询埋点统计失败: {str(e)}")
