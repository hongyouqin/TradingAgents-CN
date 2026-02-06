import asyncio
from datetime import datetime, timedelta
import json
import os
import random
import time
from typing import Optional, Tuple
from pymongo import MongoClient
from app.core.config import settings

from tradingagents.utils.logging_manager import get_logger
logger = get_logger('sms_service')

class SMSCodeService:
    """短信验证码服务类"""
    
    def __init__(self):
        self.client = MongoClient(settings.MONGO_URI)
        self.db = self.client[settings.MONGO_DB]
        self.sms_codes_collection = self.db.sms_codes
        self.sms_key = settings.SMS_ACCESS_KEY_ID
        self.sms_secret = settings.SMS_ACCESS_KEY_SECRET
    
    @staticmethod
    def timestamp_to_datetime(timestamp: float) -> datetime:
        """时间戳转 datetime（UTC）"""
        return datetime.utcfromtimestamp(timestamp)
    
    @staticmethod
    def get_current_timestamp() -> float:
        """获取当前时间戳（秒，浮点数）"""
        return time.time()
    
    @staticmethod
    def datetime_to_timestamp(dt: datetime) -> float:
        """datetime 转时间戳"""
        return dt.timestamp()
    
    def generate_code(self, length: int = 6) -> str:
        """生成指定长度的验证码"""
        return ''.join([str(random.randint(0, 9)) for _ in range(length)])
    
    async def create_sms_code(self, phone: str, code_type: str = "register", 
                            expires_in: int = 300) -> Optional[Tuple[str, float]]:
        """
        创建短信验证码
        
        Returns:
            Tuple[验证码, 过期时间戳] 或 None
        """
        try:
            current_timestamp = self.get_current_timestamp()
            
            # 清理过期的验证码（使用时间戳比较）
            self.sms_codes_collection.delete_many({
                "phone": phone,
                "expires_at_timestamp": {"$lt": current_timestamp}  # 使用时间戳
            })
            
            # 检查是否在冷却期内（防止频繁发送）
            one_minute_ago = current_timestamp - 60
            recent_code = self.sms_codes_collection.find_one({
                "phone": phone,
                "created_at_timestamp": {"$gt": one_minute_ago}  # 使用时间戳
            })
            
            if recent_code:
                # 计算还需要等待多久
                wait_seconds = int(recent_code["created_at_timestamp"] + 60 - current_timestamp)
                logger.warning(f"📵 短信验证码发送过于频繁: {phone}, 请等待 {wait_seconds} 秒")
                return None
            
            # 生成验证码
            code = self.generate_code()
            expires_at_timestamp = current_timestamp + expires_in
            
            # 存储验证码（同时存储时间戳和可读时间）
            sms_doc = {
                "phone": phone,
                "code": code,
                "code_type": code_type,
                "is_used": False,
                # 时间戳（用于比较）
                "created_at_timestamp": current_timestamp,
                "expires_at_timestamp": expires_at_timestamp,
                # 可读时间（用于展示）
                "created_at": datetime.utcnow(),
                "expires_at": expires_at_timestamp,
                # 其他字段
                "attempts": 0,
                "timezone": "UTC"
            }
            
            self.sms_codes_collection.insert_one(sms_doc)
            
            # 计算剩余时间（分钟和秒）
            remaining_minutes = expires_in // 60
            remaining_seconds = expires_in % 60
            
            logger.info(f"✅ 短信验证码创建成功: "
                       f"手机: {phone}, "
                       f"类型: {code_type}, "
                       f"验证码: {code}, "
                       f"有效期: {remaining_minutes}分{remaining_seconds}秒, "
                       f"过期时间: {datetime.utcfromtimestamp(expires_at_timestamp)}")
            
            # 发送短信
            await self.send_sms(phone, code)
            
            # 返回验证码和过期时间戳
            return code, expires_at_timestamp
            
        except Exception as e:
            logger.error(f"❌ 创建短信验证码失败: {e}")
            return None
    
    async def verify_sms_code(self, phone: str, code: str, 
                            code_type: str = "register") -> Tuple[bool, str, Optional[dict]]:
        """
        验证短信验证码
        
        Args:
            phone: 手机号
            code: 验证码
            code_type: 验证码类型
            client_timestamp: 客户端时间戳（可选，用于调试）
            
        Returns:
            Tuple[是否成功, 错误信息, 验证码信息]
        """
        try:
            current_timestamp = self.get_current_timestamp()
            # 查找有效的验证码（使用时间戳）
            sms_doc = self.sms_codes_collection.find_one({
                "phone": phone,
                "code": code,
                "code_type": code_type,
                "is_used": False,
                "expires_at_timestamp": {"$gt": current_timestamp},  # 使用时间戳
                "attempts": {"$lt": 5}
            })
            
            if sms_doc:
                # 验证成功
                remaining_seconds = sms_doc["expires_at_timestamp"] - current_timestamp
                
                # 更新状态
                self.sms_codes_collection.update_one(
                    {"_id": sms_doc["_id"]},
                    {
                        "$set": {
                            "is_used": True,
                            "verified_at_timestamp": current_timestamp,
                            "verified_at": datetime.utcnow()
                        }
                    }
                )
                
                logger.info(f"✅ 短信验证码验证成功: "
                           f"手机: {phone}, "
                           f"剩余时间: {remaining_seconds:.1f}秒")
                
                return True, "验证成功", {
                    "phone": phone,
                    "code_type": code_type,
                    "remaining_seconds": remaining_seconds,
                    "verified_at": datetime.utcnow().isoformat()
                }
            
            else:
                return False, "验证码过期", None
            
        except Exception as e:
            logger.error(f"❌ 验证短信验证码失败: {e}")
            return False, f"系统错误: {str(e)}", None
    
    def _analyze_verification_failure(self, sms_doc: dict, 
                                    input_code: str, now: datetime) -> str:
        """分析验证失败原因"""
        
        if sms_doc.get("is_used"):
            return "验证码已使用过"
        
        if sms_doc.get("expires_at") < now:
            # 计算过期多久了
            expired_seconds = (now - sms_doc["expires_at"]).total_seconds()
            if expired_seconds < 60:
                return f"验证码已过期 {int(expired_seconds)} 秒"
            elif expired_seconds < 3600:
                return f"验证码已过期 {int(expired_seconds/60)} 分钟"
            else:
                return "验证码已过期"
        
        if sms_doc.get("attempts", 0) >= 5:
            return "验证码尝试次数过多，请重新获取"
        
        if sms_doc.get("code") != input_code:
            attempts = sms_doc.get("attempts", 0) + 1
            return f"验证码错误（已尝试 {attempts}/5 次）"
        
        return "验证码无效"
    
    def cleanup_expired_codes(self):
        """清理过期的验证码"""
        try:
            result = self.sms_codes_collection.delete_many({
                "expires_at": {"$lt": datetime.utcnow()}
            })
            logger.info(f"清理了 {result.deleted_count} 个过期的短信验证码")
        except Exception as e:
            logger.error(f"清理过期验证码失败: {e}")
    
    async def send_sms(self, phone: str, code: str) -> bool:
        """发送短信验证码 - 集成阿里云短信服务"""
        try:
            logger.info(f"📱 发送短信验证码: {phone}, 验证码: {code}")
            
            # 获取阿里云配置
            access_key_id = self.sms_key
            access_key_secret = self.sms_secret 
            
            if not access_key_id or not access_key_secret:
                logger.warning("⚠️ 阿里云AK未配置，模拟发送短信")
                logger.info(f"📱 [模拟] 向 {phone} 发送验证码: {code}")
                return True
            
            # 导入阿里云SDK
            from alibabacloud_credentials.client import Client as CredentialClient
            from alibabacloud_credentials.models import Config as CredentialConfig
            from alibabacloud_tea_openapi import models as open_api_models
            from alibabacloud_dysmsapi20170525.client import Client as DysmsapiClient
            from alibabacloud_dysmsapi20170525 import models as dysmsapi_models
            from alibabacloud_tea_util import models as util_models
            
            # 1. 配置凭据
            credentials_config = CredentialConfig(
                type='access_key',
                access_key_id=access_key_id,
                access_key_secret=access_key_secret
            )
            credentials_client = CredentialClient(credentials_config)
            
            # 2. 配置短信客户端
            config = open_api_models.Config(
                credential=credentials_client,
                endpoint='dysmsapi.aliyuncs.com'
            )
            
            # 3. 创建客户端
            client = DysmsapiClient(config)
            
            # 4. 准备发送参数
            send_sms_request = dysmsapi_models.SendSmsRequest(
                phone_numbers=phone,
                sign_name='重庆奇趣创意网络信息科技',
                template_code='SMS_501915163',
                template_param=json.dumps({'code': code})
            )
            
            # 5. 设置运行时选项
            runtime = util_models.RuntimeOptions()
            
            # 6. 发送短信
            logger.info("🔄 正在通过阿里云发送短信...")
            resp = await client.send_sms_with_options_async(send_sms_request, runtime)
            
            # 7. 检查响应
            if hasattr(resp, 'body') and resp.body.code == 'OK':
                logger.info(f"✅ 短信发送成功: {phone}, 业务ID: {resp.body.biz_id}")
                return True
            else:
                error_msg = getattr(resp.body, 'message', '未知错误') if hasattr(resp, 'body') else '响应格式错误'
                logger.error(f"❌ 短信发送失败: {phone}, 错误: {error_msg}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 发送短信异常: {phone}, 错误: {e}")
            
            # 开发环境模拟成功
            environment = os.environ.get('ENVIRONMENT', 'development')
            if environment == 'development':
                logger.info(f"📱 [开发环境模拟] 向 {phone} 发送验证码: {code}")
                return True
            
            return False