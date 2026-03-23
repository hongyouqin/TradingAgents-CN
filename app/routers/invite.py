from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any
from datetime import datetime
import logging

from app.models.user import User
from app.routers.auth_db import get_current_user
from app.services.user_service import user_service
from app.services.reward_strategy import RewardStrategy
from app.services.power_account_service import power_account_service

router = APIRouter(prefix="/invite", tags=["邀请奖励"])
logger = logging.getLogger(__name__)


@router.get("/stats", response_model=Dict[str, Any])
async def get_invite_stats(
    current_user: User = Depends(get_current_user)
):
    """
    获取邀请统计信息
    
    包括：
    - 总邀请人数
    - 总获得算力
    - 有效邀请人数（完成首次分析）
    - 待发放额外奖励
    - 当前算力余额
    - 下一阶梯信息
    """
    try:
        user_id = str(current_user["id"])
        user = await user_service.get_user_by_id(user_id)
        
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        
        # 获取算力余额
        balance_info = await power_account_service.get_balance(user)
        current_power_balance = float(balance_info.get("balance", 0))
        
        invite_rewards = user.invite_rewards
        
        # 获取下一阶梯信息
        next_tier = RewardStrategy.get_next_tier_info(invite_rewards.total_invited)
        
        return {
            "success": True,
            "data": {
                "total_invited": invite_rewards.total_invited,
                "total_reward_power": invite_rewards.total_reward_power,
                "total_extra_reward": invite_rewards.total_extra_reward,
                "current_power_balance": current_power_balance,
                "next_tier": next_tier
            }
        }
        
    except Exception as e:
        logger.error(f"获取邀请统计失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/invited-users", response_model=Dict[str, Any])
async def get_invited_users(
    page: int = 1,
    page_size: int = 20,
    current_user: User = Depends(get_current_user)
):
    """
    获取我邀请的用户列表
    
    支持分页，按邀请时间倒序排列
    """
    try:
        user_id = str(current_user["id"])  # current_user 是字典
        user = await user_service.get_user_by_id(user_id)
        
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        
        # invited_users 是 InvitedUserRecord 对象列表
        invited_users = user.invite_rewards.invited_users
        
        # 转换为字典列表以便排序和访问
        invited_list = []
        for inv in invited_users:
            invited_list.append({
                "user_id": inv.user_id,           # 属性访问
                "phone": inv.phone,
                "invited_at": inv.invited_at,
                "reward_granted": inv.reward_granted,
                "first_analysis_at": inv.first_analysis_at,
                "extra_reward_granted": inv.extra_reward_granted
            })
        
        # 按邀请时间倒序排序
        invited_list.sort(key=lambda x: x["invited_at"], reverse=True)
        
        # 分页
        total = len(invited_list)
        start = (page - 1) * page_size
        end = start + page_size
        paginated_users = invited_list[start:end]
        
        # 获取用户详细信息
        user_details = []
        for invited in paginated_users:
            # 获取新用户的详细信息
            new_user = await user_service.get_user_by_id(invited["user_id"])
            if new_user:
                user_details.append({
                    "user_id": invited["user_id"],
                    "phone": invited["phone"],
                    "username": new_user.username,
                    "invited_at": invited["invited_at"],
                    "reward_granted": invited["reward_granted"],
                    "extra_reward_granted": invited["extra_reward_granted"],
                    "first_analysis_at": invited["first_analysis_at"],
                    "status": "active" if new_user.is_active else "inactive",
                    "last_login": new_user.last_login
                })
        
        # invite_rewards 是 InviteRewards 对象，使用属性访问
        return {
            "success": True,
            "data": {
                "items": user_details,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
                "summary": {
                    "total_invited": user.invite_rewards.total_invited,
                    "total_reward_power": user.invite_rewards.total_reward_power
                }
            }
        }
        
    except Exception as e:
        logger.error(f"获取邀请用户列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ranking", response_model=Dict[str, Any])
async def get_invite_ranking(
    limit: int = 10,
    current_user: User = Depends(get_current_user)
):
    """
    获取邀请排行榜
    
    按总邀请人数排序
    """
    try:
        # 查询邀请排行榜
        pipeline = [
            {"$match": {"invite_rewards.total_invited": {"$gt": 0}}},
            {"$sort": {"invite_rewards.total_invited": -1}},
            {"$limit": limit},
            {"$project": {
                "username": 1,
                "phone": 1,
                "total_invited": "$invite_rewards.total_invited",
                "total_reward_power": "$invite_rewards.total_reward_power",
                "active_invites": {
                    "$size": {
                        "$filter": {
                            "input": "$invite_rewards.invited_users",
                            "as": "user",
                            "cond": {"$ne": ["$$user.first_analysis_at", None]}
                        }
                    }
                }
            }}
        ]
        
        # 同步聚合查询，不需要 await
        cursor = user_service.users_collection.aggregate(pipeline)
        ranking = []
        
        # 使用普通 for 循环，不是 async for
        for doc in cursor:
            ranking.append({
                "username": doc["username"],
                "phone": doc["phone"][:3] + "****" + doc["phone"][-4:] if doc.get("phone") else "",
                "total_invited": doc["total_invited"],
                "total_reward_power": doc["total_reward_power"],
                "active_invites": doc.get("active_invites", 0)
            })
        
        # 获取当前用户信息
        user = await user_service.get_user_by_id(str(current_user["id"]))
        
        # 获取当前用户排名
        current_user_rank = None
        if user and user.invite_rewards.total_invited > 0:  # 使用属性访问
            # 计算排名 - 同步查询
            rank_count = user_service.users_collection.count_documents({
                "invite_rewards.total_invited": {"$gt": user.invite_rewards.total_invited}
            })
            current_user_rank = rank_count + 1
        
        # 获取当前用户算力余额
        current_power_balance = 0
        if user:
            balance_info = await power_account_service.get_balance(user)
            current_power_balance = float(balance_info.get("balance", 0))
        
        return {
            "success": True,
            "data": {
                "ranking": ranking,
                "my_rank": current_user_rank,
                "my_stats": {
                    "total_invited": user.invite_rewards.total_invited if user else 0,
                    "total_reward_power": user.invite_rewards.total_reward_power if user else 0,
                    "current_power_balance": current_power_balance
                } if user else None
            }
        }
        
    except Exception as e:
        logger.error(f"获取邀请排行榜失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

