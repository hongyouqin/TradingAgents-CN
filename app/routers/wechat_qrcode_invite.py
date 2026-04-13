import hashlib
from fastapi import APIRouter, Depends, HTTPException, Request
import logging
from datetime import datetime

from fastapi.responses import PlainTextResponse

from app.core.database import get_database
from app.routers.auth_db import get_current_user
from app.services.user_qrcode_service import user_qr_service
from app.services.wechat_event_service import wechat_event_service
from app.services.user_service import user_service

router = APIRouter(prefix="/wechat_qrcode", tags=["微信公众号二维码邀请码"])
logger = logging.getLogger(__name__)

#自定以token，微信公众平台设置的 Token，需保持一致
WECHAT_TOKEN = "qrcode123456fdsjaflj"

# ==============================================
# 1. 获取用户专属永久推广二维码
# ==============================================
@router.get("/invite-qrcode")
async def get_user_invite_qrcode(user: dict = Depends(get_current_user)):
    user_id = user["id"]
    
    # ✅ 同步获取异步 DB（你的结构就是这样）
    db = get_database()
    
    # ✅ 初始化 service
    user_qr_service.init_database(db)

    ok, msg, qrcode = await user_qr_service.get_or_create_user_qrcode(user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    
    return {
        "status": "success",
        "qrcode_url": qrcode["qr_url"],
        "scene_id": qrcode["scene_id"],
        "message": "获取专属推广二维码成功"
    }

# ==============================================
# 2. 获取用户邀请统计（总人数 + 今日 + 列表）
# ==============================================
@router.get("/invite-stats")
async def get_invite_stats(user: dict = Depends(get_current_user)):
    """
    获取当前用户邀请统计
    包含：总邀请人数、今日邀请人数、邀请列表
    统计来源：二维码邀请 + 手动邀请码 统一统计
    """
    user_id = user["id"]
    user_collection = user_service.users_collection

    # 1. 总邀请人数
    total_invited = user_collection.count_documents({
        "invited_by": user_id
    })

    # 2. 今日 0 点时间戳
    today_start = int(datetime.combine(
        datetime.now().date(),
        datetime.min.time()
    ).timestamp() * 1000)

    # 3. 今日邀请人数
    today_invited = user_collection.count_documents({
        "invited_by": user_id,
        "created_at": {"$gte": today_start}
    })

    invited_users = []
    cursor = user_collection.find({
        "invited_by": user_id
    }).sort("created_at", -1).limit(50)

    # 同步 mongo → 普通 for，不是 async for
    for u in cursor:
        invited_users.append({
            "user_id": str(u["_id"]),
            "created_at": u.get("created_at"),
            "nickname": u.get("username", "未知用户"),
            "avatar": u.get("avatar", ""),
            "invite_source": u.get("invite_source", "未知")
        })

    return {
        "total_invited": total_invited,
        "today_invited": today_invited,
        "list": invited_users
    }
    

# ======================
# 3. 微信回调（扫码绑定邀请）
# ======================
@router.api_route("/callback", methods=["GET", "POST"])
async def wechat_callback(request: Request):
    q = request.query_params
    signature = q.get("signature")
    timestamp = q.get("timestamp")
    nonce = q.get("nonce")
    echostr = q.get("echostr")

    if request.method == "GET":
        tmp = [WECHAT_TOKEN, timestamp, nonce]
        tmp.sort()
        sign = hashlib.sha1("".join(tmp).encode()).hexdigest()
        return PlainTextResponse(echostr if sign == signature else "invalid")

    logger.info(f"收到微信扫码回调: {q}")
    body = await request.body()
    wechat_event_service.init_database(db= get_database())  # 确保数据库已初始化
    await wechat_event_service.handle_wechat_message(body.decode())
    return PlainTextResponse("success")