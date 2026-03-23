from datetime import datetime, timedelta
from typing import Tuple, Optional
import hashlib
import logging

logger = logging.getLogger(__name__)

# 这里的db是同步的，如果使用异步数据库（如Motor），需要调整为异步方法
class AntiFraudService:
    """防刷服务"""
    
    def __init__(self, db):
        self.db = db
        self.users_collection = db.users
        self.invite_codes_collection = db.invite_codes
    
    @staticmethod
    def get_device_id(ip: str, user_agent: str) -> str:
        """生成设备ID"""
        device_str = f"{ip}_{user_agent}"
        return hashlib.md5(device_str.encode()).hexdigest()
    
    def check_self_invite(self, inviter_id: str, new_user_id: str) -> Tuple[bool, str]:
        """检查是否自邀"""
        if inviter_id == new_user_id:
            return False, "不能邀请自己"
        return True, ""
    
    def check_daily_invite_limit(self, inviter_id: str, limit: int = 10) -> Tuple[bool, str]:
        """检查每日邀请上限"""
        user = self.users_collection.find_one({"_id": inviter_id})
        if not user:
            return False, "邀请人不存在"
        
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        last_invite_date = user.get("last_invite_date")
        
        # 重置每日计数
        if not last_invite_date or last_invite_date < today:
            self.users_collection.update_one(
                {"_id": inviter_id},
                {"$set": {"daily_invite_count": 0, "last_invite_date": today}}
            )
            daily_count = 0
        else:
            daily_count = user.get("daily_invite_count", 0)
        
        if daily_count >= limit:
            return False, f"今日邀请已达上限（{limit}人），请明天再试"
        
        return True, ""
    
    def check_ip_limit(self, ip: str, limit: int = 3, hours: int = 24) -> Tuple[bool, str]:
        """检查同一IP注册限制"""
        if not ip:
            return True, ""
        
        # 计算时间范围
        since_time = datetime.utcnow() - timedelta(hours=hours)
        
        # 查询该IP注册的用户数量
        count = self.users_collection.count_documents({
            "register_ip": ip,
            "created_at": {"$gte": since_time}
        })
        
        if count >= limit:
            return False, f"该IP在{hours}小时内最多注册{limit}个账号"
        
        return True, ""
    
    def check_device_limit(self, device_id: str, limit: int = 3, hours: int = 24) -> Tuple[bool, str]:
        """检查同一设备注册限制"""
        if not device_id:
            return True, ""
        
        since_time = datetime.utcnow() - timedelta(hours=hours)
        
        count = self.users_collection.count_documents({
            "register_device_id": device_id,
            "created_at": {"$gte": since_time}
        })
        
        if count >= limit:
            return False, f"该设备在{hours}小时内最多注册{limit}个账号"
        
        return True, ""
    
    def record_invite(self, inviter_id: str):
        """记录邀请，更新每日计数"""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        self.users_collection.update_one(
            {"_id": inviter_id},
            {
                "$inc": {"daily_invite_count": 1},
                "$set": {"last_invite_date": today}
            }
        )
    
    # app/services/anti_fraud_service.py

    def is_suspicious_activity(self, user_agent: str) -> bool:
        """
        检测可疑活动（脚本、爬虫等）
        
        Args:
            user_agent: User-Agent字符串
            
        Returns:
            是否可疑
        """
        suspicious_keywords = [
            "curl", "python", "java", "bot", "spider", 
            "scrapy", "requests", "httpclient", "postman",
            "insomnia", "wget", "go-http-client", "okhttp"
        ]
        
        if not user_agent:
            return True  # 没有 User-Agent 也可疑
        
        ua_lower = user_agent.lower()
        for keyword in suspicious_keywords:
            if keyword in ua_lower:
                logger.warning(f"检测到可疑User-Agent: {user_agent}")
                return True
        return False