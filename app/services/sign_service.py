from datetime import datetime, timezone
from typing import Any
import pymongo

from app.daos.sign_dao import has_signed, insert_sign, ensure_indexes
from app.services.power_account_service import PowerAccountService

# 赠送的算力数量
SIGN_REWARD = 1.5

class SignService:
    def __init__(self, db, power_account_service : PowerAccountService):
        """db: AsyncIOMotorDatabase; power_account_service: existing service with async recharge()"""
        self.db = db
        self.power_account_service = power_account_service

    async def init(self):
        await ensure_indexes(self.db)

    async def _today_date(self):
        # Use server UTC date as agreed
        return datetime.now(timezone.utc).date()

    async def has_signed_today(self, user_id: str) -> bool:
        sd = await self._today_date()
        return await has_signed(self.db, user_id, sd)

    async def submit_sign(self, user_id: str) -> dict:
        sd = await self._today_date()
        now = datetime.now(timezone.utc)
        try:
            await insert_sign(self.db, user_id, sd, now)
        except pymongo.errors.DuplicateKeyError:
            # already signed by another concurrent request
            current_power = None
            if hasattr(self.power_account_service, 'get_balance'):
                current_power = await self.power_account_service.get_balance(user_id)
            return {"has_signed": True, "can_sign_today": False, "current_power": current_power}

        # success insert -> give reward
        # 使用 InviteRewardService 中的签到充值方法统一处理充值逻辑
        from app.services.invite_reward_service import InviteRewardService
        reward_service = InviteRewardService(self.db)
        success, msg = await reward_service.recharge_for_sign(user_id=user_id, amount=SIGN_REWARD, sign_date=str(sd))

        current_power = None
        if hasattr(self.power_account_service, 'get_balance'):
            current_power = await self.power_account_service.get_balance(user_id)

        return {"has_signed": True, "can_sign_today": False, "current_power": current_power, "reward_success": success, "reward_msg": msg}
