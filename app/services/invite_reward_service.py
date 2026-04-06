# app/services/invite_reward_service.py
from datetime import datetime
from typing import Tuple, Optional
from bson import ObjectId
import logging
import time
import hashlib

from app.services.reward_strategy import RewardStrategy
from app.services.power_account_service import power_account_service

logger = logging.getLogger(__name__)

class InviteRewardService:
    """邀请奖励服务"""
    
    def __init__(self, db):
        self.db = db
        self.users_collection = db.users
        self.invite_codes_collection = db.invite_codes
    
    def _generate_order_no(self, user_id: str, reward_type: str, amount: int) -> str:
        """
        生成订单号
        格式: INVITE_{user_id}_{timestamp}_{random}
        """
        timestamp = int(time.time() * 1000)
        random_str = hashlib.md5(f"{user_id}{timestamp}{reward_type}".encode()).hexdigest()[:8]
        return f"INVITE_{reward_type}_{user_id}_{timestamp}_{random_str}"
    
    async def grant_new_user_reward(self, user) -> Tuple[bool, str]:
        """
        发放新用户注册奖励（给新用户自己）
        
        Args:
            user: 用户对象
            
        Returns:
            (是否成功, 消息)
        """
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
                desc=f"新用户注册奖励获得 {reward_amount} 算力",
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
    
    async def grant_invite_reward(self, inviter_id: str, new_user_id: str, 
                                   new_user_phone: str, invite_code: str) -> Tuple[bool, str]:
        """
        发放邀请奖励（给邀请人）
        
        Args:
            inviter_id: 邀请人ID
            new_user_id: 新用户ID
            new_user_phone: 新用户手机号
            invite_code: 使用的邀请码
            
        Returns:
            (是否成功, 消息)
        """
        try:
            logger.info(f"🎁 开始发放邀请奖励: 邀请人={inviter_id}, 新用户={new_user_id}")
            
            # 1. 获取邀请人信息
            inviter_doc = self.users_collection.find_one({"_id": ObjectId(inviter_id)})
            if not inviter_doc:
                logger.warning(f"邀请人不存在: {inviter_id}")
                return False, "邀请人不存在"
            
            # 创建临时用户对象用于算力服务
            from app.models.user import User
            inviter = User(**inviter_doc)
            
            # 2. 计算阶梯奖励
            total_invited = inviter_doc.get("invite_rewards", {}).get("total_invited", 0)
            reward_amount = RewardStrategy.calculate_invite_reward(total_invited)
            
            logger.info(f"计算邀请奖励: 当前邀请数={total_invited}, 奖励={reward_amount}算力")
            
            # 3. 生成订单号
            order_no = self._generate_order_no(inviter_id, "INVITE", reward_amount)
            
            # 4. 通过算力账户服务充值
            success, msg = await power_account_service.recharge(
                user=inviter,
                order_no=order_no,
                amount=reward_amount,
                description=f"邀请新用户 {new_user_phone} 注册获得 {reward_amount} 算力",
                metadata={
                    "reward_type": "invite_reward",
                    "invite_code": invite_code,
                    "new_user_id": new_user_id,
                    "new_user_phone": new_user_phone
                }
            )
            
            if not success:
                logger.error(f"邀请奖励发放失败: {msg}")
                return False, msg
            
            # 5. 更新邀请人的统计信息
            # 创建邀请记录
            invited_record = {
                "user_id": new_user_id,
                "phone": new_user_phone,
                "invited_at": datetime.utcnow(),
                "reward_granted": reward_amount,
                "first_analysis_at": None
            }
            
            # 更新数据库统计
            await self.users_collection.update_one(
                {"_id": ObjectId(inviter_id)},
                {
                    "$inc": {
                        "invite_rewards.total_invited": 1,
                        "invite_rewards.total_reward_power": reward_amount
                    },
                    "$push": {
                        "invite_rewards.invited_users": invited_record
                    }
                }
            )
            
            # 6. 更新邀请码的奖励记录
            await self.invite_codes_collection.update_one(
                {"code": invite_code},
                {
                    "$inc": {"total_reward_granted": reward_amount},
                    "$push": {
                        "reward_records": {
                            "user_id": new_user_id,
                            "reward_type": "invite",
                            "amount": reward_amount,
                            "granted_at": int(datetime.utcnow().timestamp() * 1000)
                        }
                    }
                }
            )
            
            logger.info(f"✅ 邀请奖励发放成功: 邀请人={inviter_id}, "
                       f"奖励={reward_amount}算力")
            
            return True, f"邀请成功，获得{reward_amount}算力奖励"
            
        except Exception as e:
            logger.error(f"发放邀请奖励失败: {e}", exc_info=True)
            return False, f"发放奖励失败: {str(e)}"
    
    async def get_invite_reward_stats(self, user_id: str) -> dict:
        """
        获取用户邀请奖励统计
        
        Args:
            user_id: 用户ID
            
        Returns:
            统计信息
        """
        try:
            user = await self.users_collection.find_one({"_id": ObjectId(user_id)})
            if not user:
                return {}
            
            # 获取算力余额
            from app.models.user import User
            user_obj = User(**user)
            balance_info = await power_account_service.get_balance(user_obj)
            
            invite_rewards = user.get("invite_rewards", {})
            invited_users = invite_rewards.get("invited_users", [])
            
            # 统计有效邀请（完成首次分析）
            active_invites = 0
            for invited in invited_users:
                if invited.get("first_analysis_at"):
                    active_invites += 1
            
            return {
                "total_invited": invite_rewards.get("total_invited", 0),
                "total_reward_power": invite_rewards.get("total_reward_power", 0),
                "active_invites": active_invites,
                "current_power_balance": float(balance_info.get("balance", 0)),
                "invited_users": invited_users[-10:]  # 最近10条
            }
            
        except Exception as e:
            logger.error(f"获取邀请奖励统计失败: {e}", exc_info=True)
            return {}