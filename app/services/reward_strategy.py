from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class RewardStrategy:
    """奖励策略"""
    
    # 新用户注册奖励（给新用户自己）
    NEW_USER_REWARD = 5  # 新用户注册送5算力
    
    # 邀请奖励（给邀请人）
    INVITE_REWARD = 10  # 邀请人获得10算力
    
    # 阶梯奖励（邀请越多，单次邀请奖励越高）
    TIER_REWARDS = {
        0: 10,    # 0-9人：10算力
        5: 15,    # 5-14人：15算力
        15: 18,   # 15-29人：18算力
        30: 36    # 30人以上：36算力
    }
    
    @classmethod
    def calculate_invite_reward(cls, total_invited: int) -> int:
        """
        根据总邀请人数计算邀请奖励
        
        Args:
            total_invited: 当前总邀请人数（不包括本次）
        
        Returns:
            本次邀请应得的奖励
        """
        reward = cls.INVITE_REWARD
        for threshold, tier_reward in sorted(cls.TIER_REWARDS.items()):
            if total_invited >= threshold:
                reward = tier_reward
        
        logger.info(f"计算邀请奖励: 当前邀请数={total_invited}, 奖励={reward}")
        return reward
    
    @classmethod
    def get_next_tier_info(cls, total_invited: int) -> Dict[str, Any]:
        """获取下一阶梯信息"""
        next_threshold = None
        next_reward = None
        
        for threshold, reward in sorted(cls.TIER_REWARDS.items()):
            if total_invited < threshold:
                next_threshold = threshold
                next_reward = reward
                break
        
        if next_threshold:
            return {
                "next_tier_invites": next_threshold - total_invited,
                "next_tier_reward": next_reward
            }
        else:
            return {
                "next_tier_invites": 0,
                "next_tier_reward": cls.TIER_REWARDS[max(cls.TIER_REWARDS.keys())]
            }