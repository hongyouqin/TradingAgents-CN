
import xmltodict
from app.core.database import get_database
from app.services.user_qrcode_service import user_qr_service

'''
    扫码关注 = 绑定邀请

'''
class WechatEventService:
    def __init__(self):
        self.qrcode_service = user_qr_service
        
    def init_database(self, db):
        self.db = db
        self.user_collection = self.db.users

    async def handle_wechat_message(self, xml_data: str) -> str:
        """处理微信推送的 XML 消息"""
        try:
            data = xmltodict.parse(xml_data)
            msg_type = data["xml"].get("MsgType")
            event = data["xml"].get("Event")
            openid = data["xml"].get("FromUserName")

            # 关注 / 扫码事件
            if msg_type == "event" and event in ("subscribe", "SCAN"):
                event_key = data["xml"].get("EventKey", "")
                await self.handle_subscribe_event(openid, event, event_key)

            return "success"  # 必须返回 success

        except Exception:
            return "success"

    async def handle_subscribe_event(self, openid: str, event: str, event_key: str):
        """处理关注/扫码，自动绑定邀请关系"""
        try:
            scene_id = None

            # 未关注 → 扫码关注
            if event == "subscribe" and event_key.startswith("qrscene_"):
                scene_id = int(event_key.replace("qrscene_", ""))

            # 已关注 → 扫码
            elif event == "SCAN" and event_key.isdigit():
                scene_id = int(event_key)

            if not scene_id:
                return

            # 通过 scene_id 找到邀请人
            inviter = await self.qrcode_service.get_inviter_by_scene_id(scene_id)
            if not inviter:
                return

            inviter_user_id = inviter["user_id"]

            # 找到/注册用户，并绑定邀请关系
            user = await self.user_collection.find_one({"wechat_openid": openid})
            if user and not user.get("invited_by"):
                await self.user_collection.update_one(
                    {"_id": user["_id"]},
                    {"$set": {"invited_by": inviter_user_id}}
                )
                
                # 发放邀请奖励
                from app.services.invite_reward_service import InviteRewardService
                reward_service = InviteRewardService(self.db)
                await reward_service.grant_invite_reward_by_qrcode(
                    inviter_id=inviter_user_id,
                    new_user_id=str(user["_id"])
                )

        except Exception:
            pass
        
        
wechat_event_service = WechatEventService()