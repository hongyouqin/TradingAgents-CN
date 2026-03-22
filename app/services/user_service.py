"""
用户服务 - 基于数据库的用户管理
"""

from enum import Enum
import hashlib
import time
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from pymongo import MongoClient
from bson import ObjectId

from app.core.config import settings
from app.models.user import RegistrationError, User, UserCreate, UserUpdate, UserResponse
from app.services.invite_code import InviteCodeManager
from app.services.sms_code_service import SMSCodeService

# 尝试导入日志管理器
try:
    from tradingagents.utils.logging_manager import get_logger
except ImportError:
    # 如果导入失败，使用标准日志
    import logging
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

logger = get_logger('user_service')

class UserService:
    """用户服务类"""

    def __init__(self):
        self.client = MongoClient(settings.MONGO_URI)
        self.db = self.client[settings.MONGO_DB]
        self.users_collection = self.db.users
        self.sms_service = SMSCodeService()
        self.invite_manager = InviteCodeManager(self.db)

    def close(self):
        """关闭数据库连接"""
        if hasattr(self, 'client') and self.client:
            self.client.close()
            logger.info("✅ UserService MongoDB 连接已关闭")

    def __del__(self):
        """析构函数，确保连接被关闭"""
        self.close()
    
    @staticmethod
    def hash_password(password: str) -> str:
        """密码哈希"""
        # 使用 bcrypt 会更安全，但为了兼容性先使用 SHA-256
        return hashlib.sha256(password.encode()).hexdigest()
    
    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """验证密码"""
        return UserService.hash_password(plain_password) == hashed_password
    
    async def _check_require_invite_code(self) -> bool:
        """
        检查是否需要邀请码才能注册
        可以从系统配置中读取
        """
        try:
            # 从数据库配置表读取
            config_collection = self.db.system_config
            config = config_collection.find_one({"key": "require_invite_code"})
            if config:
                return config.get("value", False)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 检查邀请码要求失败: {e}")
            return False
        
    async def create_invite_code(self, 
                                 user_id: str, 
                                 max_uses: int = 1,
                                 expire_days: int = 7,
                                 custom_code: Optional[str] = None,
                                 description: Optional[str] = None) -> dict:
        """创建邀请码"""
        # 检查用户是否有权限创建邀请码（例如：VIP用户、管理员等）
        user = await self.get_user_by_id(user_id)
        if not user:
            raise ValueError("用户不存在")

        invite_code = await self.invite_manager.create_invite_code(
            created_by=user_id,
            max_uses=max_uses,
            expire_days=expire_days,
            custom_code=custom_code,
            description=description
        )
        
        return {
            "code": invite_code.code,
            "max_uses": invite_code.max_uses,
            "expire_at": invite_code.expire_at,
            "created_at": invite_code.created_at,
            "description": invite_code.description
        }
    
    async def create_user(self, user_data: UserCreate) -> Optional[User]:
        """创建用户"""
        try:
            # 检查用户名是否已存在
            existing_user = self.users_collection.find_one({"username": user_data.username})
            if existing_user:
                logger.warning(f"用户名已存在: {user_data.username}")
                return None
            
            # 检查邮箱是否已存在
            existing_email = self.users_collection.find_one({"email": user_data.email})
            if existing_email:
                logger.warning(f"邮箱已存在: {user_data.email}")
                return None
            
            # 创建用户文档
            user_doc = {
                "username": user_data.username,
                "email": user_data.email,
                "hashed_password": self.hash_password(user_data.password),
                "is_active": True,
                "is_verified": False,
                "is_admin": False,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "last_login": None,
                "preferences": {
                    # 分析偏好
                    "default_market": "A股",
                    "default_depth": "3",  # 1-5级，3级为标准分析（推荐）
                    "default_analysts": ["市场分析师", "基本面分析师"],
                    "auto_refresh": True,
                    "refresh_interval": 30,
                    # 外观设置
                    "ui_theme": "light",
                    "sidebar_width": 240,
                    # 语言和地区 
                    "language": "zh-CN",
                    # 通知设置
                    "notifications_enabled": True,
                    "email_notifications": False,
                    "desktop_notifications": True,
                    "analysis_complete_notification": True,
                    "system_maintenance_notification": True
                },
                "daily_quota": 1000,
                "concurrent_limit": 3,
                "total_analyses": 0,
                "successful_analyses": 0,
                "failed_analyses": 0,
                "favorite_stocks": []
            }
            
            result = self.users_collection.insert_one(user_doc)
            user_doc["_id"] = result.inserted_id
            
            logger.info(f"✅ 用户创建成功: {user_data.username}")
            return User(**user_doc)
            
        except Exception as e:
            logger.error(f"❌ 创建用户失败: {e}")
            return None
    
    async def create_user_by_phone(self, phone: str, sms_code: str, 
                                password: str, username: str = None,
                                email: str = None,
                                invite_code: str = None) -> Tuple[Optional[User], Optional[str], Optional[str]]:
        """
        通过手机号+验证码+密码方式注册用户（支持邀请码）
        
        Args:
            phone: 手机号
            sms_code: 短信验证码
            password: 密码
            username: 可选用户名
            email: 可选邮箱
            invite_code: 邀请码（可选）
            
        Returns:
            Tuple[user, error_type, error_message]
            - user: 成功时返回User对象，失败时返回None
            - error_type: 错误类型（RegistrationError枚举）
            - error_message: 错误描述（中文）
        """
        try:
            logger.info(f"📱 开始手机号注册流程: {phone} 邀请码：{invite_code}")
            
            
            require_invite = await self._check_require_invite_code()
        
            if require_invite and not invite_code:
                error_msg = "注册需要邀请码"
                logger.warning(f"❌ {error_msg}: {phone}")
                return None, RegistrationError.INVITE_CODE_REQUIRED, error_msg
            
            # ========== 1. 验证邀请码（如果提供了） ==========
            if invite_code:
                logger.info(f"🔍 验证邀请码: {invite_code}")
                
                is_valid, error_msg, invite_doc = await self.invite_manager.validate_invite_code(invite_code)
                
                if not is_valid:
                    logger.warning(f"❌ 邀请码验证失败: {invite_code}, 原因: {error_msg}")
                    return None, RegistrationError.INVITE_CODE_INVALID, error_msg
                
                logger.info(f"✅ 邀请码验证成功: {invite_code}")
            
            # ========== 2. 验证短信验证码 ==========
            logger.info(f"🔍 验证短信验证码: {sms_code}")
            is_code_valid, err, sms_info = await self.sms_service.verify_sms_code(
                phone=phone,
                code=sms_code,
                code_type="register"
            )
            
            if not is_code_valid:
                error_msg = err
                logger.warning(f"❌ {error_msg}: {phone}")
                return None, RegistrationError.SMS_CODE_INVALID, error_msg
            
            logger.info(f"✅ 短信验证码验证成功: {phone}, 信息: {sms_info}")
            
            # ========== 3. 检查手机号是否已存在 ==========
            logger.info(f"🔍 检查手机号是否已注册: {phone}")
            existing_phone = self.users_collection.find_one({"phone": phone})
            if existing_phone:
                error_msg = "该手机号已注册"
                logger.warning(f"❌ {error_msg}: {phone}")
                return None, RegistrationError.PHONE_ALREADY_REGISTERED, error_msg
            
            logger.info(f"✅ 手机号可用: {phone}")
            
            # ========== 4. 检查用户名是否已存在 ==========
            if username:
                logger.info(f"🔍 检查用户名是否已存在: {username}")
                existing_username = self.users_collection.find_one({"username": username})
                if existing_username:
                    error_msg = "用户名已被使用"
                    logger.warning(f"❌ {error_msg}: {username}")
                    return None, RegistrationError.USERNAME_ALREADY_EXISTS, error_msg
                logger.info(f"✅ 用户名可用: {username}")
            
            # ========== 5. 检查邮箱是否已存在 ==========
            if email:
                logger.info(f"🔍 检查邮箱是否已存在: {email}")
                existing_email = self.users_collection.find_one({"email": email})
                if existing_email:
                    error_msg = "邮箱已被使用"
                    logger.warning(f"❌ {error_msg}: {email}")
                    return None, RegistrationError.EMAIL_ALREADY_EXISTS, error_msg
                logger.info(f"✅ 邮箱可用: {email}")
            
            # ========== 6. 自动生成用户名 ==========
            if not username:
                logger.info(f"🔧 自动生成用户名: {phone}")
                username = f"user_{phone[-4:]}_{int(time.time()) % 10000}"
                # 确保用户名唯一
                attempt = 0
                max_attempts = 5
                while self.users_collection.find_one({"username": username}) and attempt < max_attempts:
                    username = f"user_{phone[-4:]}_{int(time.time()) % 10000 + attempt}"
                    attempt += 1
                
                if attempt >= max_attempts:
                    error_msg = "生成用户名失败，请稍后重试"
                    logger.error(f"❌ {error_msg}: {phone}")
                    return None, RegistrationError.DATABASE_ERROR, error_msg
                
                logger.info(f"✅ 用户名生成成功: {username}")
            
            # ========== 7. 创建用户文档 ==========
            logger.info(f"📝 创建用户文档: {phone}")
            user_doc = {
                "username": username,
                "email": email or "",
                "phone": phone,
                "hashed_password": self.hash_password(password),
                "is_active": True,
                "is_verified": True,  # 手机号注册默认证通过
                "is_admin": False,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "last_login": None,
                "phone_verified": True,  # 标记手机号已验证
                "email_verified": False,  # 邮箱未验证
                "invited_code": invite_code if invite_code else None,  # 记录使用的邀请码
                "invited_by": None,  # 记录邀请人ID，稍后填充
                "invite_code_used_success": False,  # 邀请码是否使用成功
                "register_type": "phone",  # 标记为手机号注册
                "preferences": {
                    "default_market": "A股",
                    "default_depth": "3",
                    "default_analysts": ["市场分析师", "基本面分析师"],
                    "auto_refresh": True,
                    "refresh_interval": 30,
                    "ui_theme": "light",
                    "sidebar_width": 240,
                    "language": "zh-CN",
                    "notifications_enabled": True,
                    "email_notifications": False,
                    "desktop_notifications": True,
                    "analysis_complete_notification": True,
                    "system_maintenance_notification": True,
                    "sms_notifications": True  # 开启短信通知
                },
                "daily_quota": 1000,
                "concurrent_limit": 3,
                "total_analyses": 0,
                "successful_analyses": 0,
                "failed_analyses": 0,
                "favorite_stocks": []
            }
            
            # ========== 8. 插入数据库 ==========
            try:
                result = self.users_collection.insert_one(user_doc)
                user_doc["_id"] = result.inserted_id
                user_id_str = str(result.inserted_id)
                
                logger.info(f"✅ 手机号用户创建成功: {phone}, 用户名: {username}")
                logger.info(f"   注册方式: 手机号验证")
                logger.info(f"   用户ID: {user_id_str}")
                
                # ========== 9. 使用邀请码（如果提供了） ==========
                if invite_code:
                    logger.info(f"🔧 使用邀请码: {invite_code}")
                    
                    # 使用邀请码
                    success, error_msg = await self.invite_manager.use_invite_code(
                        invite_code, 
                        user_id_str
                    )
                    
                    if not success:
                        # 邀请码使用失败，记录日志但不影响注册
                        logger.warning(f"⚠️ 邀请码使用失败: {invite_code}, 用户: {user_id_str}, 原因: {error_msg}")
                        # 更新用户文档，标记邀请码使用失败
                        self.users_collection.update_one(
                            {"_id": result.inserted_id},
                            {
                                "$set": {
                                    "invite_code_used_success": False,
                                    "invite_code_error": error_msg
                                }
                            }
                        )
                    else:
                        logger.info(f"✅ 邀请码使用成功: {invite_code}, 用户: {user_id_str}")
                        
                        # 获取邀请码详细信息，记录邀请人ID
                        is_valid, _, invite_doc = await self.invite_manager.validate_invite_code(invite_code)
                        if is_valid and invite_doc:
                            created_by = invite_doc.get("created_by")
                            if created_by:
                                # 更新用户文档，记录邀请人信息
                                self.users_collection.update_one(
                                    {"_id": result.inserted_id},
                                    {
                                        "$set": {
                                            "invite_code_used_success": True,
                                            "invited_by": created_by,
                                            "invited_code": invite_code
                                        }
                                    }
                                )
                                logger.info(f"   邀请人ID: {created_by}")
                
                return User(**user_doc), None, None
                
            except Exception as db_error:
                error_msg = "数据库操作失败"
                logger.error(f"❌ {error_msg}: {db_error}")
                return None, RegistrationError.DATABASE_ERROR, f"{error_msg}: {str(db_error)}"
            
        except Exception as e:
            error_msg = "注册过程中发生未知错误"
            logger.error(f"❌ {error_msg}: {e}", exc_info=True)
            return None, RegistrationError.UNKNOWN_ERROR, f"{error_msg}: {str(e)}"
        
    async def update_last_login(self, username: str) -> bool:
        """更新最后登录时间"""
        try:
            result = self.users_collection.update_one(
                {"username": username},
                {"$set": {"last_login": datetime.utcnow()}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"❌ 更新最后登录时间失败: {e}")
            return False
    
    async def send_register_sms(self, phone: str) -> Tuple[bool, str]:
        """
        发送注册验证码
        
        Args:
            phone: 手机号
            
        Returns:
            (成功状态, 消息)
        """
        try:
            # 1. 验证手机号格式（简单验证）
            if not phone or len(phone) != 11 or not phone.isdigit():
                return False, "手机号格式不正确"
            
            # # 2. 检查手机号是否已注册
            # existing_user = self.users_collection.find_one({"phone": phone})
            # if existing_user:
            #     return False, "该手机号已注册"
            
            # 3. 生成并发送验证码
            code = await self.sms_service.create_sms_code(
                phone=phone,
                code_type="register",
                expires_in=300  # 5分钟有效期
            )
            
            if not code:
                return False, "验证码发送失败，请稍后重试"
            
            # 4. 模拟发送短信（开发环境）
            await self.sms_service.send_sms(phone, code)
            
            return True, "验证码发送成功"
            
        except Exception as e:
            logger.error(f"❌ 发送注册验证码失败: {e}")
            return False, "系统错误，请稍后重试"
        
    async def authenticate_by_phone(self, phone: str, password: str) -> Optional[User]:
        """
        通过手机号+密码认证用户
        
        Args:
            phone: 手机号
            password: 密码
            
        Returns:
            User对象或None
        """
        try:
            logger.info(f"📱 [authenticate_by_phone] 开始手机号认证: {phone}")
            
            # 查找用户
            user_doc = self.users_collection.find_one({"phone": phone})
            
            if not user_doc:
                logger.warning(f"❌ [authenticate_by_phone] 手机号不存在: {phone}")
                return None
            
            # 验证密码
            if not self.verify_password(password, user_doc["hashed_password"]):
                logger.warning(f"❌ [authenticate_by_phone] 密码错误: {phone}")
                return None
            
            # 检查用户是否激活
            if not user_doc.get("is_active", True):
                logger.warning(f"❌ [authenticate_by_phone] 用户已禁用: {phone}")
                return None
            
            # 更新最后登录时间
            self.users_collection.update_one(
                {"_id": user_doc["_id"]},
                {"$set": {"last_login": datetime.utcnow()}}
            )
            
            logger.info(f"✅ [authenticate_by_phone] 手机号认证成功: {phone}")
            return User(**user_doc)
            
        except Exception as e:
            logger.error(f"❌ 手机号认证失败: {e}")
            return None
    
    async def reset_password_by_phone(self, phone: str, sms_code: str, 
                                    new_password: str) -> Tuple[bool, str]:
        """
        通过手机号重置密码
        
        Args:
            phone: 手机号
            sms_code: 短信验证码
            new_password: 新密码
            
        Returns:
            (成功状态, 消息)
        """
        try:
            # 1. 验证短信验证码
            is_code_valid = await self.sms_service.verify_sms_code(
                phone=phone,
                code=sms_code,
                code_type="reset_password"
            )
            
            if not is_code_valid:
                return False, "验证码无效或已过期"
            
            # 2. 查找用户
            user_doc = self.users_collection.find_one({"phone": phone})
            if not user_doc:
                return False, "手机号未注册"
            
            # 3. 更新密码
            new_hashed_password = self.hash_password(new_password)
            result = self.users_collection.update_one(
                {"phone": phone},
                {
                    "$set": {
                        "hashed_password": new_hashed_password,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ 手机号密码重置成功: {phone}")
                return True, "密码重置成功"
            else:
                return False, "密码重置失败"
                
        except Exception as e:
            logger.error(f"❌ 手机号密码重置失败: {e}")
            return False, "系统错误，请稍后重试"
    
    async def send_login_sms(self, phone: str) -> Tuple[bool, str]:
        """
        发送登录验证码
        
        Args:
            phone: 手机号
            
        Returns:
            (成功状态, 消息)
        """
        try:
            # 1. 检查手机号是否已注册
            existing_user = self.users_collection.find_one({"phone": phone})
            if not existing_user:
                return False, "该手机号未注册"
            
            # 2. 生成并发送验证码
            code = await self.sms_service.create_sms_code(
                phone=phone,
                code_type="login",
                expires_in=300  # 5分钟有效期
            )
            
            if not code:
                return False, "验证码发送失败，请稍后重试"
            
            # 3. 模拟发送短信
            await self.sms_service.send_sms(phone, code)
            
            return True, "验证码发送成功"
            
        except Exception as e:
            logger.error(f"❌ 发送登录验证码失败: {e}")
            return False, "系统错误，请稍后重试"
    
    async def send_reset_password_sms(self, phone: str) -> Tuple[bool, str]:
        """
        发送重置密码验证码
        
        Args:
            phone: 手机号
            
        Returns:
            (成功状态, 消息)
        """
        try:
            # 1. 检查手机号是否已注册
            existing_user = self.users_collection.find_one({"phone": phone})
            if not existing_user:
                return False, "该手机号未注册"
            
            # 2. 生成并发送验证码
            code = await self.sms_service.create_sms_code(
                phone=phone,
                code_type="reset_password",
                expires_in=300  # 5分钟有效期
            )
            
            if not code:
                return False, "验证码发送失败，请稍后重试"
            
            # 3. 模拟发送短信
            await self.sms_service.send_sms(phone, code)
            
            return True, "验证码发送成功"
            
        except Exception as e:
            logger.error(f"❌ 发送重置密码验证码失败: {e}")
            return False, "系统错误，请稍后重试"
    
    async def authenticate_user(self, username: str, password: str) -> Optional[User]:
        """用户认证"""
        try:
            logger.info(f"🔍 [authenticate_user] 开始认证用户: {username}")

            # 查找用户
            user_doc = self.users_collection.find_one({"username": username})
            logger.info(f"🔍 [authenticate_user] 数据库查询结果: {'找到用户' if user_doc else '用户不存在'}")

            if not user_doc:
                logger.warning(f"❌ [authenticate_user] 用户不存在: {username}")
                return None

            logger.info(f"🔍 [authenticate_user] 用户信息: username={user_doc.get('username')}, email={user_doc.get('email')}, is_active={user_doc.get('is_active')}")

            # 验证密码
            input_password_hash = self.hash_password(password)
            stored_password_hash = user_doc["hashed_password"]
            logger.info(f"🔍 [authenticate_user] 密码哈希对比:")
            logger.info(f"   输入密码哈希: {input_password_hash[:20]}...")
            logger.info(f"   存储密码哈希: {stored_password_hash[:20]}...")
            logger.info(f"   哈希匹配: {input_password_hash == stored_password_hash}")

            if not self.verify_password(password, user_doc["hashed_password"]):
                logger.warning(f"❌ [authenticate_user] 密码错误: {username}")
                return None

            # 检查用户是否激活
            if not user_doc.get("is_active", True):
                logger.warning(f"❌ [authenticate_user] 用户已禁用: {username}")
                return None

            # 更新最后登录时间
            self.users_collection.update_one(
                {"_id": user_doc["_id"]},
                {"$set": {"last_login": datetime.utcnow()}}
            )

            logger.info(f"✅ [authenticate_user] 用户认证成功: {username}")
            return User(**user_doc)
            
        except Exception as e:
            logger.error(f"❌ 用户认证失败: {e}")
            return None
    
    async def get_user_by_username(self, username: str) -> Optional[User]:
        """根据用户名获取用户"""
        try:
            user_doc = self.users_collection.find_one({"username": username})
            if user_doc:
                return User(**user_doc)
            return None
        except Exception as e:
            logger.error(f"❌ 获取用户失败: {e}")
            return None
    
    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        """根据用户ID获取用户"""
        try:
            if not ObjectId.is_valid(user_id):
                return None
            
            user_doc = self.users_collection.find_one({"_id": ObjectId(user_id)})
            if user_doc:
                return User(**user_doc)
            return None
        except Exception as e:
            logger.error(f"❌ 获取用户失败: {e}")
            return None
        
    async def get_user_by_phone(self, phone: str) -> Optional[User]:
        """根据手机号获取用户"""
        try:
            user_doc = self.users_collection.find_one({"phone": phone})
            if user_doc:
                return User(**user_doc)
            return None
        except Exception as e:
            logger.error(f"❌ 获取用户失败: {e}")
            return None
    
    async def update_user(self, username: str, user_data: UserUpdate) -> Optional[User]:
        """更新用户信息"""
        try:
            update_data = {"updated_at": datetime.utcnow()}
            
            # 只更新提供的字段
            if user_data.email:
                # 检查邮箱是否已被其他用户使用
                existing_email = self.users_collection.find_one({
                    "email": user_data.email,
                    "username": {"$ne": username}
                })
                if existing_email:
                    logger.warning(f"邮箱已被使用: {user_data.email}")
                    return None
                update_data["email"] = user_data.email
            
            if user_data.preferences:
                update_data["preferences"] = user_data.preferences.model_dump()
            
            if user_data.daily_quota is not None:
                update_data["daily_quota"] = user_data.daily_quota
            
            if user_data.concurrent_limit is not None:
                update_data["concurrent_limit"] = user_data.concurrent_limit
            
            result = self.users_collection.update_one(
                {"username": username},
                {"$set": update_data}
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ 用户信息更新成功: {username}")
                return await self.get_user_by_username(username)
            else:
                logger.warning(f"用户不存在或无需更新: {username}")
                return None
                
        except Exception as e:
            logger.error(f"❌ 更新用户信息失败: {e}")
            return None
    
    async def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        """修改密码"""
        try:
            # 验证旧密码
            user = await self.authenticate_user(username, old_password)
            if not user:
                logger.warning(f"旧密码验证失败: {username}")
                return False
            
            # 更新密码
            new_hashed_password = self.hash_password(new_password)
            result = self.users_collection.update_one(
                {"username": username},
                {
                    "$set": {
                        "hashed_password": new_hashed_password,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ 密码修改成功: {username}")
                return True
            else:
                logger.error(f"❌ 密码修改失败: {username}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 修改密码失败: {e}")
            return False
    
    async def reset_password(self, username: str, new_password: str) -> bool:
        """重置密码（管理员操作）"""
        try:
            new_hashed_password = self.hash_password(new_password)
            result = self.users_collection.update_one(
                {"username": username},
                {
                    "$set": {
                        "hashed_password": new_hashed_password,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ 密码重置成功: {username}")
                return True
            else:
                logger.error(f"❌ 密码重置失败: {username}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 重置密码失败: {e}")
            return False
    
    async def create_admin_user(self, username: str = "admin", password: str = "admin123", email: str = "admin@tradingagents.cn") -> Optional[User]:
        """创建管理员用户"""
        try:
            # 检查是否已存在管理员
            existing_admin = self.users_collection.find_one({"username": username})
            if existing_admin:
                logger.info(f"管理员用户已存在: {username}")
                return User(**existing_admin)
            
            # 创建管理员用户文档
            admin_doc = {
                "username": username,
                "email": email,
                "hashed_password": self.hash_password(password),
                "is_active": True,
                "is_verified": True,
                "is_admin": True,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "last_login": None,
                "preferences": {
                    "default_market": "A股",
                    "default_depth": "深度",
                    "ui_theme": "light",
                    "language": "zh-CN",
                    "notifications_enabled": True,
                    "email_notifications": False
                },
                "daily_quota": 10000,  # 管理员更高配额
                "concurrent_limit": 10,
                "total_analyses": 0,
                "successful_analyses": 0,
                "failed_analyses": 0,
                "favorite_stocks": []
            }
            
            result = self.users_collection.insert_one(admin_doc)
            admin_doc["_id"] = result.inserted_id
            
            logger.info(f"✅ 管理员用户创建成功: {username}")
            logger.info(f"   密码: {password}")
            logger.info("   ⚠️  请立即修改默认密码！")
            
            return User(**admin_doc)
            
        except Exception as e:
            logger.error(f"❌ 创建管理员用户失败: {e}")
            return None
    
    async def list_users(self, skip: int = 0, limit: int = 100) -> List[UserResponse]:
        """获取用户列表"""
        try:
            cursor = self.users_collection.find().skip(skip).limit(limit)
            users = []
            
            for user_doc in cursor:
                user = User(**user_doc)
                users.append(UserResponse(
                    id=str(user.id),
                    username=user.username,
                    email=user.email,
                    is_active=user.is_active,
                    is_verified=user.is_verified,
                    created_at=user.created_at,
                    last_login=user.last_login,
                    preferences=user.preferences,
                    daily_quota=user.daily_quota,
                    concurrent_limit=user.concurrent_limit,
                    total_analyses=user.total_analyses,
                    successful_analyses=user.successful_analyses,
                    failed_analyses=user.failed_analyses
                ))
            
            return users
            
        except Exception as e:
            logger.error(f"❌ 获取用户列表失败: {e}")
            return []
    
    async def deactivate_user(self, username: str) -> bool:
        """禁用用户"""
        try:
            result = self.users_collection.update_one(
                {"username": username},
                {
                    "$set": {
                        "is_active": False,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ 用户已禁用: {username}")
                return True
            else:
                logger.warning(f"用户不存在: {username}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 禁用用户失败: {e}")
            return False
    
    async def activate_user(self, username: str) -> bool:
        """激活用户"""
        try:
            result = self.users_collection.update_one(
                {"username": username},
                {
                    "$set": {
                        "is_active": True,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ 用户已激活: {username}")
                return True
            else:
                logger.warning(f"用户不存在: {username}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 激活用户失败: {e}")
            return False


# 全局用户服务实例
user_service = UserService()
