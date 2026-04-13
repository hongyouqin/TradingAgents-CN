# app/services/invite_reward_service.py
from datetime import datetime
from typing import Tuple, Optional
from bson import ObjectId
import logging
import time
import hashlib

from app.models.user import User
from app.services.reward_strategy import RewardStrategy
from app.services.power_account_service import power_account_service

logger = logging.getLogger(__name__)

class InviteRewardService:
    """邀请奖励服务"""
    
    def __init__(self, db):
        self.db = db
        self.users_collection = db.users
    
    def _generate_order_no(self, user_id: str, reward_type: str, amount: int) -> str:
        """
        生成订单号
        格式: INVITE_{user_id}_{timestamp}_{random}
        """
        timestamp = int(time.time() * 1000)
        random_str = hashlib.md5(f"{user_id}{timestamp}{reward_type}".encode()).hexdigest()[:8]
        return f"INVITE_{reward_type}_{user_id}_{timestamp}_{random_str}"
    
    async def grant_new_user_reward(self, user: User) -> Tuple[bool, str]:
        """
        发放新用户注册奖励（给新用户自己）
        
        Args:
            user: 用户对象
            
        Returns:
            (是否成功, 消息)
        """
        
        # 注意 self.users_collection文章在这里用的是monogo同步库
        try:
            logger.info(f"🎁 开始发放新用户注册奖励: 用户={user.id}")
            
            # 1. 检查是否已发放过奖励
            if hasattr(user, 'new_user_reward_granted') and user.new_user_reward_granted:
                logger.info(f"新用户奖励已发放: {user.id}")
                return False, "新用户奖励已发放"
            
            # 2. 发放奖励
            reward_amount = RewardStrategy.NEW_USER_REWARD
            
            # 生成订单号
            order_no = self._generate_order_no(str(user.id), "NEW_USER", reward_amount)
            
            # 3. 通过算力账户服务充值
            success, msg = await power_account_service.recharge(
                user=user,
                order_no=order_no,
                amount=reward_amount,
                description=f"新用户注册奖励获得 {reward_amount} 算力",
                metadata={
                    "reward_type": "new_user_reward",
                    "invite_code": None
                }
            )
            
            if not success:
                logger.error(f"新用户奖励发放失败: {msg}")
                return False, msg
            
            # 4. 更新用户标记
            self.users_collection.update_one(
                {"_id": ObjectId(user.id)},
                {"$set": {"new_user_reward_granted": True}}
            )
            
            logger.info(f"✅ 新用户注册奖励发放成功: 用户={user.id}, 奖励={reward_amount}算力")
            
            return True, f"恭喜！获得{reward_amount}算力注册奖励"
            
        except Exception as e:
            logger.error(f"发放新用户奖励失败: {e}", exc_info=True)
            return False, f"发放奖励失败: {str(e)}"

    # ============================
    # 【新增】二维码扫码绑定奖励
    # 给微信自动关注绑定使用
    # ============================
    async def grant_invite_reward_by_qrcode(self, inviter_id: str, new_user_id: str) -> Tuple[bool, str]:
        """
        扫码关注公众号自动绑定邀请后 → 给邀请人发奖励
        """
        # 注意self.users_collection 这里却是用的异步数据库，要小心调用
        try:
            logger.info(f"🎁 【二维码邀请】发放奖励: 邀请人={inviter_id}, 新用户={new_user_id}")

            # 1. 获取邀请人
            inviter = await self.users_collection.find_one({"_id": ObjectId(inviter_id)})
            if not inviter:
                return False, "邀请人不存在"

            # 2. 获取新用户信息
            new_user = await self.users_collection.find_one({"_id": ObjectId(new_user_id)})
            if not new_user:
                return False, "新用户不存在"

            # 3. 计算奖励（和你原有邀请奖励规则完全一致）
            total_invited = inviter.get("invite_rewards", {}).get("total_invited", 0)
            reward_amount = RewardStrategy.calculate_invite_reward(total_invited)

            # 4. 生成订单号
            order_no = self._generate_order_no(inviter_id, "QRCODE_INVITE", reward_amount)

            # 5. 构造用户对象给 power_account_service
            from app.models.user import User
            inviter_obj = User(**inviter)

            # 6. 发放算力奖励
            username = new_user.get("username")
            success, msg = await power_account_service.recharge(
                user=inviter_obj,
                order_no=order_no,
                amount=reward_amount,
                description=f"【扫码关注邀请】新用户 {username} 注册，获得 {reward_amount} 算力",
                metadata={
                    "reward_type": "qrcode_invite_reward",
                    "new_user_id": new_user_id,
                }
            )

            if not success:
                return False, msg

            # 7. 写入邀请记录（和原有体系统一）
            invited_record = {
                "user_id": new_user_id,
                "username": username,
                "invited_at": datetime.utcnow(),
                "reward_granted": reward_amount,
                "invite_source": "qrcode"  # 标记来自二维码
            }

            self.users_collection.update_one(
                {"_id": ObjectId(inviter_id)},
                {
                    "$inc": {
                        "invite_rewards.total_invited": 1,
                        "invite_rewards.total_reward_power": reward_amount
                    },
                    "$push": {"invite_rewards.invited_users": invited_record}
                }
            )

            logger.info(f"✅ 【二维码邀请】奖励发放成功: {inviter_id} -> {reward_amount} 算力")
            return True, f"二维码邀请成功，获得 {reward_amount} 算力"

        except Exception as e:
            logger.error(f"【二维码邀请】奖励发放失败: {e}", exc_info=True)
            return False, str(e)
