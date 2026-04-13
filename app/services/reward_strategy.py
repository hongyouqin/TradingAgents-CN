from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class RewardStrategy:
    """奖励策略"""
    
    # 新用户注册奖励（给新用户自己）
    NEW_USER_REWARD = 5  # 新用户注册送5算力
    
    # 邀请奖励（给邀请人）
    INVITE_REWARD = 3    # 每邀请1人送3算力
    
    @classmethod
    def calculate_invite_reward(cls, total_invited: int) -> int:
        """
        计算邀请奖励（固定奖励，与邀请人数无关）
        
        Args:
            total_invited: 当前总邀请人数（保留参数以兼容调用，但实际不使用）
        
        Returns:
            本次邀请应得的奖励（固定3算力）
        """
        logger.info(f"计算邀请奖励: 固定奖励={cls.INVITE_REWARD}")
        return cls.INVITE_REWARD
    
    @classmethod
    def get_strategy_info(cls) -> Dict[str, Any]:
        """获取策略配置信息"""
        return {
            "new_user_reward": cls.NEW_USER_REWARD,
            "invite_reward": cls.INVITE_REWARD,
            "has_tier": False,
            "description": "新用户注册送5算力，每邀请1人送3算力"
        }