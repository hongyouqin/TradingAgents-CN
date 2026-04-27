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
from app.core.database import get_database
from app.models.user import RegistrationError, User, UserCreate, UserUpdate, UserResponse
from app.services.anti_fraud_service import AntiFraudService
from app.services.sms_code_service import SMSCodeService
from app.services.wechat_pay_service import wechat_pay_service

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
        self.anti_fraud = AntiFraudService(self.db)
        
        self._create_indexes()
        
        
    def _create_indexes(self):
        self.users_collection.create_index(
            "openid",
            unique=True,
            sparse=True  # 👈 关键！只索引存在 openid 字段的数据
        )

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
    
    
    async def bind_user_to_inviter(self, user: User):
        """用户登录后绑定邀请关系,针对微信扫码关注的用户"""
        try:
            openid = user.openid
            user_id = str(user.id) 

            # 1. 查询是否有预绑定
            db = get_database()
            prebind = await db["user_invite_prebind"].find_one({
                "wechat_openid": openid,
                "status": "waiting"
            })

            if prebind and not user.invited_by:
                inviter_id = prebind["inviter_id"]

                # ==========================================
                # 🔥 禁止自己邀请自己
                # ==========================================
                if inviter_id == user_id:
                    logger.warning(f"❌ 用户 {user_id} 尝试自己邀请自己，已拒绝")
                    # 标记为已处理，避免重复判断
                    await db["user_invite_prebind"].update_one(
                        {"_id": prebind["_id"]},
                        {"$set": {"status": "invalid"}}
                    )
                    return

                # 2. 绑定邀请关系
                logger.info(f"🔗 绑定邀请关系: 用户 {user.id} 被邀请人 {inviter_id}")
                await db.users.update_one(
                    {"_id": user.id},
                    {"$set": {"invited_by": inviter_id}}
                )

                # 3. 发放二维码邀请奖励
                logger.info(f"🎁 发放二维码邀请奖励: 邀请人 {inviter_id} 获得奖励，邀请新用户 {user.id}")
                from app.services.invite_reward_service import InviteRewardService
                reward_service = InviteRewardService(db)
                #这里db用的是异步的
                await reward_service.grant_invite_reward_by_qrcode(
                    inviter_id=inviter_id,
                    new_user_id=str(user.id)
                )

                # 4. 标记已绑定
                await db["user_invite_prebind"].update_one(
                    {"_id": prebind["_id"]},
                    {"$set": {"status": "bound"}}
                )


        except Exception as e:
            pass  # 不影响登录
    
    async def _check_require_invite_code(self) -> bool:
        """
        检查是否需要邀请码才能注册
        可以从系统配置中读取
        """
        # 不需要强制用户填写邀请码，如果有话，可以奖励
        return False        
        
        # try:
        #     # 从数据库配置表读取
        #     config_collection = self.db.system_config
        #     config = config_collection.find_one({"key": "require_invite_code"})
        #     if config:
        #         return config.get("value", False)
            
        #     return True
            
        # except Exception as e:
        #     logger.error(f"❌ 检查邀请码要求失败: {e}")
        #     return False
        
 
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
    
    async def grant_new_user_reward(self, user_obj: User) -> Tuple[bool, str]:
        # 发放新用户奖励
        try:
            from app.services.invite_reward_service import InviteRewardService
            reward_service = InviteRewardService(self.db)
            logger.info(f"🎁 新用户注册用户信息: {user_obj}")
            reward_success, reward_msg = await reward_service.grant_new_user_reward(user_obj)
            if reward_success:
                self.users_collection.update_one(
                    {"_id": user_obj.id},
                    {"$set": {"new_user_reward_granted": True}}
                )
                logger.info(f"🎁 新用户注册奖励发放成功: {reward_msg}")
            
            return reward_success, reward_msg
        except Exception as e:
            logger.warning(f"⚠️ 新用户注册奖励发放失败: {e}")
        
        return False, "发放奖励失败"
    
    async def wechat_qr_login(self, openid: str) -> User:
        '''微信扫码登录/注册'''
        db = get_database()
        user = await db.users.find_one({"openid": openid})

        if not user:
            # 生成用户名
            username = f"wx_{openid[-8:]}"
            while await db.users.find_one({"username": username}):
                username = f"wx_{openid[-8:]}_{int(datetime.utcnow().timestamp() % 10000)}"

            user_doc = {
                "username": username,
                "nickname": f"微信用户_{openid[-8:]}",
                "avatar": "",
                "sex": 0,
                "city": "",
                "province": "",
                "email": None,
                "phone": None,
                "openid": openid,
                "hashed_password": "",
                "is_active": True,
                "is_verified": True,
                "is_admin": False,
                "created_at": datetime.utcnow(),
                "last_login": datetime.utcnow(),
                "register_type": "wechat",
                "invited_by": None,
                "new_user_reward_granted": False,
                "preferences": {},
                "daily_quota": 1000,
                "concurrent_limit": 3,
                "power_balance": 0,
            }

            result = await db.users.insert_one(user_doc)
            user = await db.users.find_one({"_id": result.inserted_id})
        else:
            user.last_login = datetime.utcnow()
            cur_user = User(**user)
            if not getattr(cur_user, 'new_user_reward_granted', False):
                # 发放新人奖励
                logger.info("微信扫码登录发放新人奖励")
                await self.grant_new_user_reward(user_obj=cur_user)

        return User(**user)
    
    async def wechat_auth_login(self, code: str) -> Tuple[Optional[User], Optional[str], Optional[str]]:
        try:
            # 1. 通过 code 获取 openid + access_token
            wx_res = await wechat_pay_service.get_openid_by_code(code)
            openid = wx_res.get("openid")
            access_token = wx_res.get("access_token")

            if not openid or not access_token:
                errmsg = wx_res.get("errmsg", "获取微信信息失败")
                return None, RegistrationError.INVALID_PARAMS, errmsg

            # 2. 【核心】获取真实微信用户信息：昵称、头像、性别、地区
            user_info = await wechat_pay_service.get_wechat_user_info(access_token, openid)
            nickname = user_info.get("nickname", f"微信用户_{openid[-8:]}")
            avatar = user_info.get("headimgurl", "")
            sex = user_info.get("sex", 0)
            city = user_info.get("city", "")
            province = user_info.get("province", "")

            logger.info(f"✅ 获取微信用户信息成功: {nickname}, openid={openid}")

            # 3. 查询用户是否已存在
            user_doc = self.users_collection.find_one({"openid": openid})
            if user_doc:
                self.users_collection.update_one(
                    {"_id": user_doc["_id"]},
                    {"$set": {"last_login": datetime.utcnow()}}
                )
                logger.info(f"✅ 微信用户登录成功: {nickname}, openid={openid}")
                
                cur_user = User(**user_doc)
                if not getattr(cur_user, 'new_user_reward_granted', False):
                    # 发放新人奖励
                    logger.info("微信登录发放新人奖励")
                    await self.grant_new_user_reward(user_obj=cur_user)
                    
                
                return User(**user_doc), None, None

            # 4. 自动注册（写入真实微信资料）
            username = f"wx_{openid[-8:]}"
            while self.users_collection.find_one({"username": username}):
                username = f"wx_{openid[-8:]}_{int(time.time() % 10000)}"

            logger.info(f"微信用户注册 username={username}")
            user_doc = {
                "username": username,
                "nickname": nickname,
                "avatar": avatar,
                "sex": sex,
                "city": city,
                "province": province,
                "email": None,
                "phone": None,
                "openid": openid,
                "hashed_password": "",
                "is_active": True,
                "is_verified": True,
                "is_admin": False,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "last_login": datetime.utcnow(),
                "phone_verified": False,
                "email_verified": False,
                "register_type": "wechat",
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
                    "sms_notifications": True
                },
                "daily_quota": 1000,
                "concurrent_limit": 3,
                "total_analyses": 0,
                "successful_analyses": 0,
                "failed_analyses": 0,
                "favorite_stocks": [],
                "invited_by": None,
                "invited_code": None,
                "invited_at": None,
                "is_invite_reward_granted": False,
                "invite_rewards": {},
                "power_balance": 0,
                "power_history": [],
                "register_ip": None,
                "register_device_id": None,
                "daily_invite_count": 0,
                "last_invite_date": None,
                "new_user_reward_granted": False
            }

            # ==============================================
            # ✅ 先校验，通过再入库（修复报错还插库）
            # ==============================================
            try:
                user_obj = User(**user_doc)
            except Exception as e:
                logger.error(f"❌ 用户模型校验失败: {e}")
                return None, RegistrationError.UNKNOWN_ERROR, "用户数据格式错误"

            # 校验通过才插入数据库
            result = self.users_collection.insert_one(user_doc)
            user_doc["_id"] = result.inserted_id
            user_obj.id = result.inserted_id

            if not getattr(user_obj, 'new_user_reward_granted', False):
                # 发放新人奖励
                logger.info("微信注册登录发放新人奖励")
                await self.grant_new_user_reward(user_obj=user_obj)
                    


            return user_obj, None, None

        except Exception as e:
            logger.error(f"❌ 公众号登录失败: {e}", exc_info=True)
            return None, RegistrationError.UNKNOWN_ERROR, "公众号登录异常"


    async def create_user_by_phone(self, phone: str, sms_code: str, 
                                password: str, username: str = None,
                                email: str = None,
                                invite_code: str = None,
                                register_ip: str = None,
                                user_agent: str = None,
                                device_id: str = None) -> Tuple[Optional[User], Optional[str], Optional[str]]:
        """
        通过手机号+验证码+密码方式注册用户（支持邀请码和防刷）
        
        Args:
            phone: 手机号
            sms_code: 短信验证码
            password: 密码
            username: 可选用户名
            email: 可选邮箱
            invite_code: 邀请码
            register_ip: 注册IP（可选）
            user_agent: User-Agent（可选）
            device_id: 设备ID（可选）
            
        Returns:
            Tuple[user, error_type, error_message]
            - user: 成功时返回User对象，失败时返回None
            - error_type: 错误类型（RegistrationError枚举）
            - error_message: 错误描述（中文）
        """
        try:
            logger.info(f"📱 开始手机号注册流程: {phone}, 邀请码: {invite_code}")
            if register_ip:
                logger.info(f"   客户端信息: IP={register_ip}, Device={device_id[:8] if device_id else 'N/A'}...")
            
            # ========== 0. 检查是否需要邀请码 ==========
            require_invite = await self._check_require_invite_code()
        
            if require_invite and not invite_code:
                error_msg = "注册需要邀请码"
                logger.warning(f"❌ {error_msg}: {phone}")
                return None, RegistrationError.INVITE_CODE_REQUIRED, error_msg
            
            # ========== 1. 验证邀请码（如果提供了） ==========
            inviter_id = None
            if invite_code:
                logger.info(f"🔍 验证邀请码: {invite_code}")
                
                is_valid, error_msg, invite_doc = self.invite_manager.validate_invite_code_sync(invite_code)
                
                if not is_valid:
                    logger.warning(f"❌ 邀请码验证失败: {invite_code}, 原因: {error_msg}")
                    return None, RegistrationError.INVITE_CODE_INVALID, error_msg
                
                inviter_id = invite_doc.get("created_by")
                logger.info(f"✅ 邀请码验证成功: {invite_code}, 邀请人: {inviter_id}")
            
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
            
            # # ========== 5. 检查邮箱是否已存在 ==========
            # if email:
            #     logger.info(f"🔍 检查邮箱是否已存在: {email}")
            #     existing_email = self.users_collection.find_one({"email": email})
            #     if existing_email:
            #         error_msg = "邮箱已被使用"
            #         logger.warning(f"❌ {error_msg}: {email}")
            #         return None, RegistrationError.EMAIL_ALREADY_EXISTS, error_msg
            #     logger.info(f"✅ 邮箱可用: {email}")
            
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
                "is_verified": True,
                "is_admin": False,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "last_login": None,
                "phone_verified": True,
                "email_verified": False,
                
                # 邀请相关字段
                "invited_code": invite_code if invite_code else None,
                "invited_by": None,  # 稍后填充
                "invited_at": datetime.utcnow() if invite_code else None,
                "invite_code_used_success": False,
                "new_user_reward_granted": False,  # 新用户注册奖励是否已发放
                
                # 邀请奖励统计（作为邀请人）
                "invite_rewards": {
                    "total_invited": 0,
                    "total_reward_power": 0,
                    "total_extra_reward": 0,
                    "invited_users": [],
                    "pending_extra_rewards": 0
                },
                
                # 防刷相关
                "register_ip": register_ip,
                "register_user_agent": user_agent,
                "register_device_id": device_id,
                "daily_invite_count": 0,
                "last_invite_date": None,
                
                # 注册类型
                "register_type": "phone",
                
                # 用户偏好
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
                    "sms_notifications": True
                },
                
                # 配额和统计
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
                
                # 创建 User 对象
                user_obj = User(**user_doc)
                
                # ========== 9. 发放新用户注册奖励 ==========
                from app.services.invite_reward_service import InviteRewardService
                reward_service = InviteRewardService(self.db)
                
                reward_success, reward_msg = await reward_service.grant_new_user_reward(user_obj)
                if reward_success:
                    logger.info(f"🎁 新用户注册奖励发放成功: {reward_msg}")
                    # 更新用户标记
                    self.users_collection.update_one(
                        {"_id": result.inserted_id},
                        {"$set": {"new_user_reward_granted": True}}
                    )
                else:
                    logger.warning(f"⚠️ 新用户注册奖励发放失败: {reward_msg}")
                
                # ========== 10. 使用邀请码并发放邀请奖励 ==========
                if invite_code and inviter_id:
                    logger.info(f"🔧 处理邀请奖励: 邀请码={invite_code}, 邀请人={inviter_id}")
                    
                    # 使用邀请码
                    success, error_msg = self.invite_manager.use_invite_code_sync(
                        invite_code, 
                        user_id_str
                    )
                    
                    if not success:
                        # 邀请码使用失败，记录日志但不影响注册
                        logger.warning(f"⚠️ 邀请码使用失败: {invite_code}, 用户: {user_id_str}, 原因: {error_msg}")
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
                        logger.info(f"✅ 邀请码使用成功: {invite_code}")
                        
                        # 更新新用户的邀请信息
                        self.users_collection.update_one(
                            {"_id": result.inserted_id},
                            {
                                "$set": {
                                    "invite_code_used_success": True,
                                    "invited_by": inviter_id,
                                    "invited_code": invite_code
                                }
                            }
                        )
                        
                        # 发放邀请奖励给邀请人
                        reward_success, reward_msg = await reward_service.grant_invite_reward(
                            inviter_id=inviter_id,
                            new_user_id=user_id_str,
                            new_user_phone=phone,
                            invite_code=invite_code
                        )
                        
                        if reward_success:
                            logger.info(f"✅ 邀请奖励发放成功: {reward_msg}")
                        else:
                            logger.warning(f"⚠️ 邀请奖励发放失败: {reward_msg}")
                
                # 重新获取用户对象（包含最新信息）
                updated_user = await self.get_user_by_id(user_id_str)
                
                # 获取算力余额
                from app.services.power_account_service import power_account_service
                balance_info = await power_account_service.get_balance(updated_user)
                
                logger.info(f"🎉 用户注册完成: {username}, 算力余额: {balance_info.get('balance', 0)}⚡")
                
                return updated_user, None, None
                    
            except Exception as db_error:
                error_msg = "数据库操作失败"
                logger.error(f"❌ {error_msg}: {db_error}", exc_info=True)
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
            code, _ = await self.sms_service.create_sms_code(
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
            code, _ = await self.sms_service.create_sms_code(
                phone=phone,
                code_type="register",
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
            code, _ = await self.sms_service.create_sms_code(
                    phone=phone,
                    code_type="register",
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
    
    async def update_user_openid(self, username: str, openid: str) -> bool:
        """
        专门用于保存微信 openid
        微信授权回调里直接调用！
        """
        try:
            result = self.users_collection.update_one(
                {"username": username},
                {"$set": {
                    "openid": openid,
                    "updated_at": datetime.utcnow()
                }}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"保存 openid 失败: {e}")
            return False
    
    async def update_user(self, username: str, user_data: UserUpdate) -> Optional[User]:
        """更新用户信息"""
        try:
            update_data = {"updated_at": datetime.utcnow()}
            
            if user_data.email:
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

            # ====================== 新增：保存 openid ======================
            if user_data.openid:
                update_data["openid"] = user_data.openid
            # ===============================================================
            
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
