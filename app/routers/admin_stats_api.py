from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any

from app.routers.auth_db import get_current_user
from app.services.user_status_service import user_stat_service
from app.core.database import get_database

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
