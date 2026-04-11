from typing import Optional, Tuple, Dict
from datetime import datetime

from app.core.database import get_database
from app.services.wechat_qrcode_service import WechatQRCodeService
from app.core.config import settings

from tradingagents.utils.logging_manager import get_logger
logger = get_logger('user_invite_qrcode_service')

'''
    通过公众号二维码推广用户，记录每个用户的专属二维码信息（scene_id、ticket、qr_url）
    适用于代理推广等场景，每个用户只有一个专属二维码，扫码后可以统计推广效果
'''
class UserInviteQRCodeService:
    def __init__(self):
        appid = settings.WECHAT_APP_ID
        appsecret = settings.WECHAT_APP_SECRET
        self.wechat = WechatQRCodeService(appid=appid, appsecret=appsecret)
        
    def init_database(self, db):
        self.db = db
        self.collection = self.db.user_invite_qrcodes

    async def get_or_create_user_qrcode(self, user_id: str) -> Tuple[bool, str, Optional[Dict]]:
        """
        获取或创建用户专属永久推广二维码
        一个用户永远只有一个
        """
        # 1. 查是否已有
        existing = await self.collection.find_one({"user_id": user_id})
        if existing:
            return True, "已获取你的专属推广二维码", existing

        # 2. 生成 scene_id（从 100000 开始，避免冲突）
        max_scene = await self.collection.find_one(sort=[("scene_id", -1)])
        next_scene_id = max_scene["scene_id"] + 1 if max_scene else 100000

        try:
            # 3. 调用微信生成永久二维码
            qr_data = await self.wechat.create_permanent_qrcode(next_scene_id)
            ticket = qr_data["ticket"]
            qr_url = WechatQRCodeService.get_qrcode_image_url(ticket)

            # 4. 保存到数据库
            doc = {
                "user_id": user_id,
                "scene_id": next_scene_id,
                "ticket": ticket,
                "qr_url": qr_url,
                "created_at": int(datetime.now().timestamp() * 1000),
                "total_scanned": 0,
                "total_subscribed": 0
            }
            logger.info(f"生成推广二维码成功: {doc}")
            await self.collection.insert_one(doc)
            return True, "生成推广二维码成功", doc

        except Exception as e:
            return False, f"生成失败: {str(e)}", None

    async def get_inviter_by_scene_id(self, scene_id: int) -> Optional[Dict]:
        """通过 scene_id 找到邀请人 user_id"""
        return self.collection.find_one({"scene_id": scene_id})
    
    
user_qr_service = UserInviteQRCodeService()
    
    