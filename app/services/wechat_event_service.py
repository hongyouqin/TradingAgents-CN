import xmltodict
from datetime import datetime
from app.core.database import get_database
from app.services.user_qrcode_service import user_qr_service

class WechatEventService:
    def __init__(self):
        self.qrcode_service = user_qr_service

    def init_database(self, db):
        self.db = db
        self.user_collection = self.db.users
        self.prebind_collection = self.db["user_invite_prebind"]
        self.login_collection = self.db["wechat_login_records"]  # 登录记录表

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
            scene_id = None
            if event == "subscribe" and event_key.startswith("qrscene_"):
                scene_id = int(event_key.replace("qrscene_", ""))
            elif event == "SCAN" and event_key.isdigit():
                scene_id = int(event_key)

            if not scene_id:
                return

            # ==========================================
            # 登录二维码：scene_id < 100000
            # ==========================================
            if scene_id < 100000:
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

            # ==========================================
            # 邀请二维码：scene_id >= 100000（原有逻辑）
            # ==========================================
            inviter = await self.qrcode_service.get_inviter_by_scene_id(scene_id)
            if not inviter:
                return

            inviter_user_id = inviter["user_id"]

            await self.prebind_collection.update_one(
                {"wechat_openid": openid},
                {
                    "$set": {
                        "inviter_id": inviter_user_id,
                        "status": "waiting"
                    }
                },
                upsert=True
            )

        except Exception:
            pass


wechat_event_service = WechatEventService()