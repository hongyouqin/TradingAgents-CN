import logging
import xmltodict
from datetime import datetime
from app.services.user_qrcode_service import user_qr_service

logger = logging.getLogger('WechatEventService')

class WechatEventService:
    def __init__(self):
        self.qrcode_service = user_qr_service

    def init_database(self, db):
        self.db = db
        self.user_collection = self.db.users
        self.prebind_collection = self.db["user_invite_prebind"]
        self.login_collection = self.db["wechat_login_sessions"]  # 登录记录表

    async def handle_wechat_message(self, xml_data: str) -> str:
        try:
            data = xmltodict.parse(xml_data)
            xml = data["xml"]
            msg_type = xml.get("MsgType")
            event = xml.get("Event")
            openid = xml.get("FromUserName")
            event_key = xml.get("EventKey", "")

            if msg_type == "event" and event in ("subscribe", "SCAN"):
                await self.handle_scan_event(openid, event, event_key)

            return "success"
        except Exception:
            return "success"

    async def handle_scan_event(self, openid: str, event: str, event_key: str):
        try:
            # ==================================================================
            # 1. 优先处理：推广邀请码（字符串永久码）
            # ==================================================================
            if event == "subscribe" and event_key.startswith("qrscene_invite_"):
                scene_str = event_key.replace("qrscene_", "")
                user_id = scene_str.replace("invite_", "")
                if user_id:
                    await self.prebind_collection.update_one(
                        {"wechat_openid": openid},
                        {"$set": {"inviter_id": user_id, "status": "waiting"}},
                        upsert=True
                    )
                    logger.info(f"[推广扫码] openid={openid} 邀请人={user_id}")
                return

            if event == "SCAN" and event_key.startswith("invite_"):
                user_id = event_key.replace("invite_", "")
                if user_id:
                    await self.prebind_collection.update_one(
                        {"wechat_openid": openid},
                        {"$set": {"inviter_id": user_id, "status": "waiting"}},
                        upsert=True
                    )
                    logger.info(f"[已关注推广扫码] openid={openid} 邀请人={user_id}")
                return

            # ==================================================================
            # 2. 处理：登录二维码（scene_id > 10000）
            # ==================================================================
            scene_id = None
            if event == "subscribe" and event_key.startswith("qrscene_"):
                scene_val = event_key.replace("qrscene_", "")
                if scene_val.isdigit():
                    scene_id = int(scene_val)

            elif event == "SCAN" and event_key.isdigit():
                scene_id = int(event_key)

            # 登录：> 10000
            if scene_id and scene_id > 10000:
                logger.info(f"[用户登录] scene={scene_id}, openid={openid}")
                await self.login_collection.update_one(
                    {"scene": scene_id},
                    {
                        "$set": {
                            "openid": openid,
                            "status": "success",
                            "updated_at": datetime.utcnow()
                        }
                    },
                    upsert=True
                )
                return

        except Exception:
            pass


wechat_event_service = WechatEventService()