"""
用户数据模型
"""

from datetime import datetime, timezone
from enum import Enum
from app.utils.timezone import now_tz
from typing import Optional, Dict, Any, Annotated, List
from pydantic import BaseModel, Field, BeforeValidator, PlainSerializer, ConfigDict, field_serializer
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema
from bson import ObjectId


def validate_object_id(v: Any) -> ObjectId:
    """验证ObjectId"""
    if isinstance(v, ObjectId):
        return v
    if isinstance(v, str):
        if ObjectId.is_valid(v):
            return ObjectId(v)
    raise ValueError("Invalid ObjectId")


def serialize_object_id(v: ObjectId) -> str:
    """序列化ObjectId为字符串"""
    return str(v)


# 创建自定义ObjectId类型
PyObjectId = Annotated[
    ObjectId,
    BeforeValidator(validate_object_id),
    PlainSerializer(serialize_object_id, return_type=str),
]

class UserPreferences(BaseModel):
    """用户偏好设置"""
    # 分析偏好
    default_market: str = "A股"
    default_depth: str = "3"  # 1-5级，3级为标准分析（推荐）
    default_analysts: List[str] = Field(default_factory=lambda: ["市场分析师", "基本面分析师"])
    auto_refresh: bool = True
    refresh_interval: int = 30  # 秒

    # 外观设置
    ui_theme: str = "light"
    sidebar_width: int = 240

    # 语言和地区
    language: str = "zh-CN"

    # 通知设置
    notifications_enabled: bool = True
    email_notifications: bool = False
    desktop_notifications: bool = True
    analysis_complete_notification: bool = True
    system_maintenance_notification: bool = True


class FavoriteStock(BaseModel):
    """自选股信息"""
    stock_code: str = Field(..., description="股票代码")
    stock_name: str = Field(..., description="股票名称")
    market: str = Field(..., description="市场类型")
    added_at: datetime = Field(default_factory=now_tz, description="添加时间")
    tags: List[str] = Field(default_factory=list, description="用户标签")
    notes: str = Field(default="", description="用户备注")
    alert_price_high: Optional[float] = Field(None, description="价格上限提醒")
    alert_price_low: Optional[float] = Field(None, description="价格下限提醒")


class InvitedUserRecord(BaseModel):
    """邀请的用户记录"""
    user_id: str = Field(..., description="被邀请用户ID")
    phone: str = Field(..., description="被邀请用户手机号")
    invited_at: datetime = Field(default_factory=now_tz, description="邀请时间")
    reward_granted: int = Field(..., description="已发放的基础奖励")
    first_analysis_at: Optional[datetime] = Field(None, description="首次分析时间")
    extra_reward_granted: bool = Field(False, description="是否已发放额外奖励")


class InviteRewards(BaseModel):
    """邀请奖励统计"""
    total_invited: int = Field(0, description="总邀请人数")
    total_reward_power: int = Field(0, description="总获得算力（基础+额外）")
    total_extra_reward: int = Field(0, description="总额外奖励")
    invited_users: List[InvitedUserRecord] = Field(default_factory=list, description="邀请的用户列表")
    pending_extra_rewards: int = Field(0, description="待发放的额外奖励数量")


class PowerHistoryRecord(BaseModel):
    """算力变动记录"""
    type: str = Field(..., description="类型: invite_reward/extra_reward/analysis_used/recharge")
    amount: int = Field(..., description="变动金额（正数为增加，负数为减少）")
    balance_after: int = Field(0, description="变动后余额")
    description: str = Field(..., description="描述")
    created_at: datetime = Field(default_factory=now_tz, description="创建时间")
    related_user: Optional[str] = Field(None, description="关联用户ID")


class User(BaseModel):
    """用户模型"""
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., pattern=r'^[^@]+@[^@]+\.[^@]+$')
    phone: Optional[str] = Field(None, pattern=r'^\+?[1-9]\d{1,14}$')
    openid :str = Field(None, description="微信openid")
    hashed_password: str
    is_active: bool = True
    is_verified: bool = False
    is_admin: bool = False
    created_at: datetime = Field(default_factory=now_tz)
    updated_at: datetime = Field(default_factory=now_tz)
    last_login: Optional[datetime] = None
    preferences: UserPreferences = Field(default_factory=UserPreferences)
    
    # 配额和限制
    daily_quota: int = 1000
    concurrent_limit: int = 3
    
    # 统计信息
    total_analyses: int = 0
    successful_analyses: int = 0
    failed_analyses: int = 0

    # 自选股
    favorite_stocks: List[FavoriteStock] = Field(default_factory=list, description="用户自选股列表")
    
    # ========== 新增：邀请相关字段 ==========
    # 邀请信息（作为被邀请人）
    invited_by: Optional[str] = Field(None, description="邀请人ID")
    invited_code: Optional[str] = Field(None, description="使用的邀请码")
    invited_at: Optional[datetime] = Field(None, description="被邀请时间")
    is_invite_reward_granted: bool = Field(False, description="基础奖励是否已发放")
    
    # 邀请奖励统计（作为邀请人）
    invite_rewards: InviteRewards = Field(default_factory=InviteRewards, description="邀请奖励统计")
    
    # 算力相关
    power_balance: int = Field(0, description="算力余额")
    power_history: List[PowerHistoryRecord] = Field(default_factory=list, description="算力变动历史")
    
    # 防刷相关
    register_ip: Optional[str] = Field(None, description="注册IP")
    register_device_id: Optional[str] = Field(None, description="注册设备ID")
    daily_invite_count: int = Field(0, description="今日邀请次数")
    last_invite_date: Optional[datetime] = Field(None, description="最后邀请日期")
    
    model_config = ConfigDict(
        populate_by_name=True, 
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )
    

class RegistrationError(str, Enum):
    """注册错误类型枚举"""
    SMS_CODE_INVALID = "sms_code_invalid"
    PHONE_ALREADY_REGISTERED = "phone_already_registered"
    USERNAME_ALREADY_EXISTS = "username_already_exists"
    EMAIL_ALREADY_EXISTS = "email_already_exists"
    PASSWORD_TOO_WEAK = "password_too_weak"
    USERNAME_INVALID = "username_invalid"
    DATABASE_ERROR = "database_error"
    UNKNOWN_ERROR = "unknown_error"
    INVITE_CODE_INVALID = "invite_code_invalid"
    INVITE_CODE_REQUIRED = "invite_code_required"




class UserCreate(BaseModel):
    """创建用户请求模型"""
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., pattern=r'^[^@]+@[^@]+\.[^@]+$')
    password: str = Field(..., min_length=6, max_length=100)


class UserUpdate(BaseModel):
    """更新用户请求模型"""
    email: Optional[str] = Field(None, pattern=r'^[^@]+@[^@]+\.[^@]+$')
    preferences: Optional[UserPreferences] = None
    daily_quota: Optional[int] = None
    concurrent_limit: Optional[int] = None
    openid: Optional[str] = None  # 微信openid，后续可能用于微信登录绑定


class UserResponse(BaseModel):
    """用户响应模型"""
    id: str
    username: str
    email: str
    is_active: bool
    is_verified: bool
    created_at: datetime
    last_login: Optional[datetime]
    preferences: UserPreferences
    daily_quota: int
    concurrent_limit: int
    total_analyses: int
    successful_analyses: int
    failed_analyses: int

    @field_serializer('created_at', 'last_login')
    def serialize_datetime(self, dt: Optional[datetime], _info) -> Optional[str]:
        """序列化 datetime 为 ISO 8601 格式，保留时区信息"""
        if dt:
            return dt.isoformat()
        return None


class UserLogin(BaseModel):
    """用户登录请求模型"""
    username: str
    password: str


class UserSession(BaseModel):
    """用户会话模型"""
    session_id: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    last_activity: datetime
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    @field_serializer('created_at', 'expires_at', 'last_activity')
    def serialize_datetime(self, dt: Optional[datetime], _info) -> Optional[str]:
        """序列化 datetime 为 ISO 8601 格式，保留时区信息"""
        if dt:
            return dt.isoformat()
        return None


class TokenResponse(BaseModel):
    """Token响应模型"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: Optional[str] = None
    user: UserResponse
