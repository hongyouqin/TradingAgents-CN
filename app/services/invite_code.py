from datetime import datetime, timedelta
import logging
from bson import ObjectId
from typing import Optional, List, Tuple
import random
import string

class InviteCode:
    """邀请码模型"""
    
    def __init__(self, 
                 code: str,
                 created_by: str,  # 邀请码创建者ID
                 max_uses: int = 1,  # 最大使用次数，默认为1（一次性邀请码）
                 expire_days: int = 7,  # 有效期天数
                 description: Optional[str] = None):
        self.code = code
        self.created_by = created_by
        self.max_uses = max_uses
        self.used_count = 0
        # 使用时间戳，存储为整数（毫秒级时间戳）
        self.expire_at = int((datetime.utcnow() + timedelta(days=expire_days)).timestamp() * 1000)
        self.created_at = int(datetime.utcnow().timestamp() * 1000)
        self.is_active = True
        self.description = description
        self.used_by = []  # 使用该邀请码的用户ID列表
    
    def to_dict(self) -> dict:
        return {
            "_id": ObjectId(),
            "code": self.code,
            "created_by": self.created_by,
            "max_uses": self.max_uses,
            "used_count": self.used_count,
            "expire_at": self.expire_at,
            "created_at": self.created_at,
            "is_active": self.is_active,
            "description": self.description,
            "used_by": self.used_by
        }
    
    @staticmethod
    def from_dict(data: dict):
        invite_code = InviteCode(
            code=data["code"],
            created_by=data["created_by"],
            max_uses=data.get("max_uses", 1),
            expire_days=0
        )
        invite_code.used_count = data.get("used_count", 0)
        invite_code.expire_at = data.get("expire_at")
        invite_code.created_at = data.get("created_at")
        invite_code.is_active = data.get("is_active", True)
        invite_code.description = data.get("description")
        invite_code.used_by = data.get("used_by", [])
        return invite_code

class InviteCodeManager:
    """邀请码管理器"""
    
    def __init__(self, db):
        self.db = db
        if db is None:
            raise ValueError("数据库连接不能为None")
        self.collection = db.invite_codes
        self.logger = logging.getLogger(__name__)
    
    @staticmethod
    def _get_current_timestamp() -> int:
        """获取当前时间戳（毫秒）"""
        return int(datetime.utcnow().timestamp() * 1000)
    
    @staticmethod
    def generate_invite_code(length: int = 8) -> str:
        """生成随机邀请码"""
        chars = string.ascii_uppercase + string.digits
        # 避免混淆的字符
        exclude_chars = ['0', 'O', 'I', '1']
        chars = ''.join([c for c in chars if c not in exclude_chars])
        return ''.join(random.choices(chars, k=length))

    async def has_active_invite_code(self, user_id: str) -> bool:
        """
        检查用户是否有有效的未使用邀请码
        
        有效邀请码需要同时满足：
        1. is_active = True （未手动失效）
        2. expire_at > now （未过期）
        3. used_count < max_uses （未使用完）
        """
        try:
            current_timestamp = self._get_current_timestamp()
            
            # 查询该用户所有未过期且未失效的邀请码
            cursor = self.collection.find({
                "created_by": user_id,
                "is_active": True,
                "expire_at": {"$gt": current_timestamp}
            })
            
            # 异步遍历 - 使用 async for
            async for invite_code in cursor:
                used_count = invite_code.get("used_count", 0)
                max_uses = invite_code.get("max_uses", 1)
                
                # 只有未使用完的才算有效
                if used_count < max_uses:
                    self.logger.info(f"用户 {user_id} 有有效邀请码: {invite_code['code']}, "
                                    f"已使用: {used_count}/{max_uses}")
                    return True
            
            self.logger.info(f"用户 {user_id} 没有有效邀请码")
            return False
            
        except Exception as e:
            self.logger.error(f"检查有效邀请码失败: {e}", exc_info=True)
            return False
    
    async def create_invite_code(self, 
                                created_by: str, 
                                max_uses: int = 1,
                                expire_days: int = 7,
                                description: Optional[str] = None,
                                allow_multiple: bool = False) -> Tuple[bool, str, Optional[InviteCode]]:
        """
        创建邀请码
        
        Args:
            created_by: 创建者ID
            max_uses: 最大使用次数
            expire_days: 有效期天数
            description: 描述
            allow_multiple: 是否允许同时存在多个有效邀请码（默认False）
            
        Returns:
            Tuple[bool, str, Optional[InviteCode]]: (是否成功, 消息, 邀请码对象)
        """
        try:
            # 如果不允许同时存在多个有效邀请码，检查是否已有有效的未使用邀请码
            if not allow_multiple:
                has_active = await self.has_active_invite_code(created_by)
                if has_active:
                    return False, "您已有有效的未使用邀请码，请先使用完当前邀请码", None
            
            self.logger.info(f"用户 {created_by} 正在创建邀请码，max_uses={max_uses}, expire_days={expire_days}, description={description}")
            # 生成唯一邀请码
            while True:
                code = self.generate_invite_code(length=16)
                existing = await self.collection.find_one({"code": code})
                if not existing:
                    break
            
            self.logger.info(f"生成的邀请码: {code}")
            invite_code = InviteCode(
                code=code,
                created_by=created_by,
                max_uses=max_uses,
                expire_days=expire_days,
                description=description
            )
            
            self.logger.info(f"邀请码对象: {invite_code.to_dict()}")
            
            result = await self.collection.insert_one(invite_code.to_dict())
            # 设置_id属性
            invite_code._id = result.inserted_id
            
            return True, "邀请码创建成功", invite_code
            
        except Exception as e:
            return False, f"创建邀请码失败: {str(e)}", None
    
    async def validate_invite_code(self, code: str) -> Tuple[bool, str, Optional[dict]]:
        """
        验证邀请码是否有效
        返回: (是否有效, 错误信息, 邀请码信息)
        """
        invite_code = await self.collection.find_one({"code": code})
        
        if not invite_code:
            return False, "邀请码不存在", None
        
        # 1. 检查是否手动失效（用户主动操作）
        if not invite_code.get("is_active", True):
            return False, "邀请码已被手动失效", None
        
        # 2. 检查是否自动过期
        expire_at = invite_code.get("expire_at")
        current_timestamp = self._get_current_timestamp()
        
        if expire_at and expire_at < current_timestamp:
            return False, "邀请码已过期", None
        
        # 3. 检查是否已用完
        used_count = invite_code.get("used_count", 0)
        max_uses = invite_code.get("max_uses", 1)
        
        if used_count >= max_uses:
            return False, "邀请码已达到使用上限", None
        
        return True, "", invite_code
    
    def validate_invite_code_sync(self, code: str) -> Tuple[bool, str, Optional[dict]]:
        """
        验证邀请码是否有效,同步版本，适合monogodb驱动同步的情况
        返回: (是否有效, 错误信息, 邀请码信息)
        """
        invite_code = self.collection.find_one({"code": code})
        
        if not invite_code:
            return False, "邀请码不存在", None
        
        # 1. 检查是否手动失效（用户主动操作）
        if not invite_code.get("is_active", True):
            return False, "邀请码已被手动失效", None
        
        # 2. 检查是否自动过期
        expire_at = invite_code.get("expire_at")
        current_timestamp = self._get_current_timestamp()
        
        if expire_at and expire_at < current_timestamp:
            return False, "邀请码已过期", None
        
        # 3. 检查是否已用完
        used_count = invite_code.get("used_count", 0)
        max_uses = invite_code.get("max_uses", 1)
        
        if used_count >= max_uses:
            return False, "邀请码已达到使用上限", None
        
        return True, "", invite_code
    
    def use_invite_code_sync(self, code: str, user_id: str) -> Tuple[bool, str]:
        """
        使用邀请码
        返回: (是否成功, 错误信息)
        """
        try:
            # 验证邀请码
            is_valid, error_msg, invite_code = self.validate_invite_code_sync(code)
            if not is_valid:
                return False, error_msg
            
            # 检查用户是否已使用过该邀请码
            if user_id in invite_code.get("used_by", []):
                return False, "您已使用过该邀请码"
            
            # 更新邀请码使用记录
            result = self.collection.update_one(
                {
                    "code": code,
                    "used_count": {"$lt": invite_code["max_uses"]}
                },
                {
                    "$inc": {"used_count": 1},
                    "$push": {"used_by": user_id},
                    "$set": {"updated_at": self._get_current_timestamp()}
                }
            )
            
            if result.modified_count == 0:
                return False, "邀请码使用失败，请稍后重试"
            
            return True, ""
            
        except Exception as e:
            return False, f"使用邀请码失败: {str(e)}"
    
    async def use_invite_code(self, code: str, user_id: str) -> Tuple[bool, str]:
        """
        使用邀请码
        返回: (是否成功, 错误信息)
        """
        try:
            # 验证邀请码
            is_valid, error_msg, invite_code = await self.validate_invite_code(code)
            if not is_valid:
                return False, error_msg
            
            # 检查用户是否已使用过该邀请码
            if user_id in invite_code.get("used_by", []):
                return False, "您已使用过该邀请码"
            
            # 更新邀请码使用记录
            result = await self.collection.update_one(
                {
                    "code": code,
                    "used_count": {"$lt": invite_code["max_uses"]}
                },
                {
                    "$inc": {"used_count": 1},
                    "$push": {"used_by": user_id},
                    "$set": {"updated_at": self._get_current_timestamp()}
                }
            )
            
            if result.modified_count == 0:
                return False, "邀请码使用失败，请稍后重试"
            
            return True, ""
            
        except Exception as e:
            return False, f"使用邀请码失败: {str(e)}"

    async def get_invite_codes_by_user(self, user_id: str, 
                                   page: int = 1, 
                                   page_size: int = 20,
                                   status: Optional[str] = None) -> dict:
        """
        获取用户创建的邀请码列表
        
        Args:
            user_id: 用户ID
            page: 页码
            page_size: 每页数量
            status: 状态过滤 valid/expired/used_up/inactive
        """
        skip = (page - 1) * page_size
        
        # 构建基础查询条件
        query = {"created_by": user_id}
        
        current_timestamp = self._get_current_timestamp()
        
        # 根据状态添加过滤条件
        if status == "valid":
            # 有效的：未手动失效、未过期、未用完
            query.update({
                "is_active": True,
                "expire_at": {"$gt": current_timestamp},
                "$expr": {"$lt": ["$used_count", "$max_uses"]}
            })
        elif status == "expired":
            # 已过期的
            query["expire_at"] = {"$lte": current_timestamp}
        elif status == "used_up":
            # 已用完的
            query["$expr"] = {"$gte": ["$used_count", "$max_uses"]}
        elif status == "inactive":
            # 手动失效的
            query["is_active"] = False
        
        # 查询用户创建的邀请码
        cursor = self.collection.find(query).sort("created_at", -1)
        total = await self.collection.count_documents(query)
        
        codes = []
        async for doc in cursor.skip(skip).limit(page_size):
            # 判断各种状态
            is_expired = doc.get("expire_at", 0) < current_timestamp
            is_used_up = doc.get("used_count", 0) >= doc.get("max_uses", 1)
            is_active = doc.get("is_active", True)
            
            # 综合状态
            if not is_active:
                status_text = "手动失效"
            elif is_expired:
                status_text = "已过期"
            elif is_used_up:
                status_text = "已用完"
            else:
                status_text = "有效"
            
            codes.append({
                "code": doc["code"],
                "max_uses": doc["max_uses"],
                "used_count": doc["used_count"],
                "remaining_uses": doc.get("max_uses", 1) - doc.get("used_count", 0),
                "used_by_count": len(doc.get("used_by", [])),
                "expire_at": doc["expire_at"],
                "expire_at_datetime": datetime.fromtimestamp(doc["expire_at"] / 1000).isoformat() if doc.get("expire_at") else None,
                "created_at": doc["created_at"],
                "created_at_datetime": datetime.fromtimestamp(doc["created_at"] / 1000).isoformat() if doc.get("created_at") else None,
                "is_active": is_active,
                "is_expired": is_expired,
                "is_used_up": is_used_up,
                "status": status_text,
                "is_valid": is_active and not is_expired and not is_used_up,
                "description": doc.get("description")
            })
        
        return {
            "items": codes,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size
        }    

    async def get_active_invite_code(self, user_id: str) -> Optional[dict]:
        """
        获取用户当前有效的邀请码（未过期、未使用完）
        
        Args:
            user_id: 用户ID
            
        Returns:
            邀请码信息或None
        """
        current_timestamp = self._get_current_timestamp()
        
        invite_code = await self.collection.find_one({
            "created_by": user_id,
            "is_active": True,
            "expire_at": {"$gt": current_timestamp},
            "$expr": {"$lt": ["$used_count", "$max_uses"]}
        })
        
        return invite_code
    
    async def deactivate_invite_code(self, code: str, user_id: str) -> Tuple[bool, str]:
        """
        使邀请码失效
        
        Args:
            code: 邀请码
            user_id: 操作者ID
            
        Returns:
            Tuple[bool, str]: (是否成功, 错误信息)
        """
        try:
            # 确保只有创建者可以失效邀请码
            invite_code = await self.collection.find_one({"code": code})
            if not invite_code:
                return False, "邀请码不存在"
            
            if invite_code["created_by"] != user_id:
                return False, "无权操作此邀请码"
            
            result = await self.collection.update_one(
                {"code": code},
                {
                    "$set": {
                        "is_active": False,
                        "updated_at": self._get_current_timestamp()
                    }
                }
            )
            
            if result.modified_count > 0:
                return True, "邀请码已失效"
            return False, "操作失败"
            
        except Exception as e:
            return False, f"操作失败: {str(e)}"
    
    async def get_invite_code_stats(self, user_id: str) -> dict:
        """
        获取用户的邀请码统计信息
        
        Args:
            user_id: 用户ID
            
        Returns:
            统计信息字典
        """
        current_timestamp = self._get_current_timestamp()
        
        # 总创建的邀请码数量
        total_created = await self.collection.count_documents({"created_by": user_id})
        
        # 有效邀请码数量（未过期、未使用完）
        valid_count = await self.collection.count_documents({
            "created_by": user_id,
            "is_active": True,
            "expire_at": {"$gt": current_timestamp},
            "$expr": {"$lt": ["$used_count", "$max_uses"]}
        })
        
        # 已使用完的邀请码数量
        used_up_count = await self.collection.count_documents({
            "created_by": user_id,
            "$expr": {"$gte": ["$used_count", "$max_uses"]}
        })
        
        # 已过期的邀请码数量
        expired_count = await self.collection.count_documents({
            "created_by": user_id,
            "expire_at": {"$lte": current_timestamp}
        })
        
        # 总邀请人数（所有邀请码的使用次数总和）
        pipeline = [
            {"$match": {"created_by": user_id}},
            {"$group": {"_id": None, "total_invited": {"$sum": "$used_count"}}}
        ]
        result = await self.collection.aggregate(pipeline).to_list(length=1)
        total_invited = result[0]["total_invited"] if result else 0
        
        return {
            "total_created": total_created,
            "valid_count": valid_count,
            "used_up_count": used_up_count,
            "expired_count": expired_count,
            "total_invited": total_invited
        }