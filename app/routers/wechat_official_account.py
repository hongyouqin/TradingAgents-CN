from fastapi import APIRouter, Depends, HTTPException, Request
import logging
from app.core.config import settings
from app.services.wechat_qrcode_service import WechatQRCodeService

router = APIRouter(prefix="/wechat_oa", tags=["微信公众号相关操作"])
logger = logging.getLogger(__name__)

@router.post("/wechat/create-menu", summary="创建公众号自定义菜单（AI研股）")
async def create_menu():
    appid = settings.WECHAT_APP_ID
    appsecret = settings.WECHAT_APP_SECRET
    wechat_service = WechatQRCodeService(appid, appsecret)
    success, msg = await wechat_service.create_ai_stock_menu()
    return {"success": success, "msg": msg}