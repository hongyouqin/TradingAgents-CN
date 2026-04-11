# app/routers/invite_code.py
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any, Optional
import logging

from app.core.database import get_database
from app.models.user import User
from app.routers.auth_db import get_current_user
from app.services.invite_code import InviteCodeManager

router = APIRouter(prefix="/invite-codes", tags=["传统邀请码"])
logger = logging.getLogger(__name__)


@router.post("/create", response_model=Dict[str, Any])
async def create_invite_code(
    current_user: User = Depends(get_current_user)  # 需要登录
):
    """
    创建邀请码（需要登录）
    
    每个用户同时只能拥有一个有效的未使用邀请码
    自动生成16位随机邀请码，有效期为7天，只能使用1次
    """
    try:
        # 获取数据库连接
        db = get_database()
        invite_manager = InviteCodeManager(db)
        
        # 创建邀请码（使用默认值）
        success, message, invite_code = await invite_manager.create_invite_code(
            created_by=str(current_user["id"]),
            max_uses=1,           # 默认只能使用1次
            expire_days=7,        # 默认7天有效期
            description=f"{current_user.get('username', '用户')}的邀请码",
            allow_multiple=False  # 不允许同时存在多个有效邀请码
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "success": False,
                    "message": message
                }
            )
        
        # 构建返回数据
        return {
            "success": True,
            "message": message,
            "data": {
                "code": invite_code.code,
                "max_uses": invite_code.max_uses,
                "used_count": invite_code.used_count,
                "remaining_uses": invite_code.max_uses - invite_code.used_count,
                "expire_at": invite_code.expire_at,
                "expire_at_datetime": datetime.fromtimestamp(invite_code.expire_at / 1000).isoformat(),
                "created_at": invite_code.created_at,
                "created_at_datetime": datetime.fromtimestamp(invite_code.created_at / 1000).isoformat(),
                "is_active": invite_code.is_active,
                "description": invite_code.description
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建邀请码失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "success": False,
                "message": f"创建邀请码失败: {str(e)}"
            }
        )

@router.get("/my-codes", response_model=Dict[str, Any])
async def get_my_invite_codes(
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,  # valid, expired, used_up, inactive
    current_user: User = Depends(get_current_user)
):
    """
    获取我创建的邀请码列表
    
    status参数:
    - valid: 有效的（未过期、未用完、未失效）
    - expired: 已过期
    - used_up: 已用完
    - inactive: 已手动失效
    """
    try:
        db = get_database()
        invite_manager = InviteCodeManager(db)
        
        result = await invite_manager.get_invite_codes_by_user(
            user_id=str(current_user["id"]),
            page=page,
            page_size=page_size,
            status=status
        )
        
        return {
            "success": True,
            "data": result
        }
        
    except Exception as e:
        logger.error(f"获取邀请码列表失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "success": False,
                "message": f"获取邀请码列表失败: {str(e)}"
            }
        )


@router.get("/stats", response_model=Dict[str, Any])
async def get_invite_code_stats(
    current_user: User = Depends(get_current_user)
):
    """获取邀请码统计信息"""
    try:
        db = get_database()
        invite_manager = InviteCodeManager(db)
        
        stats = await invite_manager.get_invite_code_stats(str(current_user["id"]))
        
        return {
            "success": True,
            "data": stats
        }
        
    except Exception as e:
        logger.error(f"获取邀请码统计失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "success": False,
                "message": f"获取邀请码统计失败: {str(e)}"
            }
        )


@router.post("/{code}/deactivate", response_model=Dict[str, Any])
async def deactivate_invite_code(
    code: str,
    current_user: User = Depends(get_current_user)
):
    """
    手动失效邀请码（只有创建者可以操作）
    
    失效后，该邀请码将无法再被使用
    """
    try:
        db = get_database()
        invite_manager = InviteCodeManager(db)
        
        success, message = await invite_manager.deactivate_invite_code(
            code=code,
            user_id=str(current_user["id"])
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "success": False,
                    "message": message
                }
            )
        
        return {
            "success": True,
            "message": message
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"失效邀请码失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "success": False,
                "message": f"失效邀请码失败: {str(e)}"
            }
        )