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
        try:
            # 查是否已有
            existing = await self.collection.find_one({"user_id": user_id})
            if existing:
                return True, "已获取你的专属推广二维码", existing

            # 永久字符串场景：invite_用户ID
            scene_str = f"invite_{user_id}"

            # 生成永久二维码
            qr_data = await self.wechat.create_permanent_qrcode_str(scene_str)

            ticket = qr_data.get("ticket")
            qr_url = f"https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket={ticket}"

            if not ticket or not qr_url:
                logger.error(f"微信返回异常: {qr_data}")
                return False, "获取二维码失败", None

            doc = {
                "user_id": user_id,
                "scene_id": scene_str,
                "ticket": ticket,
                "qr_url": qr_url,
                "created_at": int(datetime.now().timestamp() * 1000),
                "total_scanned": 0,
                "total_subscribed": 0
            }

            await self.collection.insert_one(doc)
            logger.info(f"推广二维码生成成功: {scene_str}")
            return True, "生成推广二维码成功", doc

        except Exception as e:
            logger.error(f"生成推广二维码失败: {str(e)}", exc_info=True)
            return False, f"生成失败: {str(e)}", None

    async def get_inviter_by_scene_id(self, scene_id: int) -> Optional[Dict]:
        """通过 scene_id 找到邀请人 user_id"""
        return self.collection.find_one({"scene_id": scene_id})
    
    
user_qr_service = UserInviteQRCodeService()
    
    