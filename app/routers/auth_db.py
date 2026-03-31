"""
基于数据库的认证路由 - 改进版
替代原有的基于配置文件的认证机制
"""

from datetime import datetime
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field, validator

from app.services.anti_fraud_service import AntiFraudService
from app.services.auth_service import AuthService
from app.services.user_service import user_service
from app.models.user import RegistrationError, UserCreate, UserUpdate
from app.services.operation_log_service import log_operation, log_login_failure
from app.models.operation_log import ActionType
import re
from typing import Dict, Any

from app.utils.utils import get_real_client_ip



# 尝试导入日志管理器
try:
    from tradingagents.utils.logging_manager import get_logger
except ImportError:
    # 如果导入失败，使用标准日志
    import logging
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

logger = get_logger('auth_db')

security = HTTPBearer(auto_error=False) 

# 统一响应格式
class ApiResponse(BaseModel):
    success: bool = True
    data: dict = {}
    message: str = ""

router = APIRouter()

class LoginRequest(BaseModel):
    """登录请求模型 - 支持多种登录方式"""
    login_type: str = Field("password", description="登录类型: password-密码登录, sms-短信验证码登录")
    identifier: str = Field(..., description="用户标识: 用户名/邮箱/手机号")
    password: Optional[str] = Field(None, description="密码（密码登录时必填）")
    sms_code: Optional[str] = Field(None, description="短信验证码（短信登录时必填）")
    
class LoginOldRequest(BaseModel):
    username: str
    password: str

class SMSRequest(BaseModel):
    phone: str
    sms_type: str = "register"  # register, reset_password, login
    

class PhoneRegisterRequest(BaseModel):
    """手机号注册请求模型"""
    
    phone: str = Field(
        ...,
        min_length=11,
        max_length=11,
        pattern=r'^1[3-9]\d{9}$',
        description="手机号，11位数字，以1开头",
        example="13800138000"
    )
    
    sms_code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r'^\d{6}$',
        description="6位数字短信验证码",
        example="123456"
    )
    
    password: str = Field(
        ...,
        min_length=8,
        max_length=50,
        description="密码，至少8个字符",
        example="StrongPass123!"
    )
    
    username: Optional[str] = Field(
        None,
        min_length=3,
        max_length=20,
        pattern=r'^[a-zA-Z0-9_\u4e00-\u9fa5]+$',
        description="用户名，3-20个字符，支持中文、英文、数字、下划线",
        example="用户_123"
    )
    
    email: Optional[str] = Field(
        None,
        pattern=r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
        description="邮箱地址",
        example="user@example.com"
    )
    
    invite_code: Optional[str] = Field(None, description="邀请码")
    
    @validator('phone')
    def validate_phone_format(cls, v):
        """验证手机号格式"""
        if not re.match(r'^1[3-9]\d{9}$', v):
            raise ValueError('手机号格式不正确，必须是11位数字，以1开头')
        return v
    
    @validator('password')
    def validate_password_strength(cls, v):
        """验证密码强度"""
        if len(v) < 8:
            raise ValueError('密码至少需要8个字符')
        
        # 检查是否包含数字
        if not re.search(r'\d', v):
            raise ValueError('密码必须包含至少一个数字')
        
        # 检查是否包含字母
        if not re.search(r'[a-zA-Z]', v):
            raise ValueError('密码必须包含至少一个字母')
        
        # 可选：检查特殊字符
        # if not re.search(r'[!@#$%^&*(),.?":{}|<>]', v):
        #     raise ValueError('密码必须包含至少一个特殊字符')
        
        return v
    
    @validator('username')
    def validate_username(cls, v):
        """验证用户名"""
        if v is None:
            return v
            
        if len(v) < 3:
            raise ValueError('用户名至少需要3个字符')
        
        if len(v) > 20:
            raise ValueError('用户名不能超过20个字符')
        
        # 允许中文、英文、数字、下划线
        if not re.match(r'^[a-zA-Z0-9_\u4e00-\u9fa5]+$', v):
            raise ValueError('用户名只能包含中文、英文、数字和下划线')
        
        # 检查是否以数字开头
        if re.match(r'^\d', v):
            raise ValueError('用户名不能以数字开头')
        
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "phone": "13800138000",
                "sms_code": "123456",
                "password": "StrongPass123!",
                "username": "trading_user",
                "email": "user@example.com",
                "invite_code": "`INVITE123`"
            }
        }
        
class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class RefreshTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

class ResetPasswordRequest(BaseModel):
    username: str
    new_password: str
    
class ResetPasswordByPhoneRequest(BaseModel):
    phone: str = Field(
        ...,
        min_length=11,
        max_length=11,
        pattern=r'^1[3-9]\d{9}$',
        description="手机号，11位数字，以1开头",
        example="13800138000"
    )
    
    sms_code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r'^\d{6}$',
        description="6位数字短信验证码",
        example="123456"
    )
    
    new_password: str = Field(
        ...,
        min_length=8,
        max_length=50,
        description="密码，至少8个字符",
        example="StrongPass123!"
    )
    
    @validator('phone')
    def validate_phone_format(cls, v):
        """验证手机号格式"""
        if not re.match(r'^1[3-9]\d{9}$', v):
            raise ValueError('手机号格式不正确，必须是11位数字，以1开头')
        return v
    
    @validator('new_password')
    def validate_password_strength(cls, v):
        """验证密码强度"""
        if len(v) < 8:
            raise ValueError('密码至少需要8个字符')
        
        # 检查是否包含数字
        if not re.search(r'\d', v):
            raise ValueError('密码必须包含至少一个数字')
        
        # 检查是否包含字母
        if not re.search(r'[a-zA-Z]', v):
            raise ValueError('密码必须包含至少一个字母')
        
        # 可选：检查特殊字符
        # if not re.search(r'[!@#$%^&*(),.?":{}|<>]', v):
        #     raise ValueError('密码必须包含至少一个特殊字符')
        
        return v

class CreateUserRequest(BaseModel):
    username: str
    email: str
    password: str
    is_admin: bool = False

async def get_current_user(
        credentials: HTTPAuthorizationCredentials = Depends(security)
    ) -> dict:
        """获取当前用户信息（使用 FastAPI 安全依赖）"""
        logger.debug(f"🔐 认证检查开始")
        
        # 检查是否有 credentials
        if not credentials:
            logger.warning("❌ 没有Authorization header")
            raise HTTPException(
                status_code=401,
                detail="No authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # credentials 已经包含了 Bearer 前缀的处理
        token = credentials.credentials
        logger.debug(f"🎫 提取的token长度: {len(token)}")
        logger.debug(f"🎫 Token前20位: {token[:20]}...")

        token_data = AuthService.verify_token(token)
        logger.debug(f"🔍 Token验证结果: {token_data is not None}")

        if not token_data:
            logger.warning("❌ Token验证失败")
            raise HTTPException(
                status_code=401,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 从数据库获取用户信息
        user = await user_service.get_user_by_username(token_data.sub)
        if not user:
            logger.warning(f"❌ 用户不存在: {token_data.sub}")
            raise HTTPException(
                status_code=401,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not user.is_active:
            logger.warning(f"❌ 用户已禁用: {token_data.sub}")
            raise HTTPException(
                status_code=401,
                detail="User is inactive",
                headers={"WWW-Authenticate": "Bearer"},
            )

        logger.debug(f"✅ 认证成功，用户: {token_data.sub}")

        # 返回完整的用户信息，包括偏好设置
        return {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "name": user.username,
            "openid":user.openid,
            "is_admin": user.is_admin,
            "roles": ["admin"] if user.is_admin else ["user"],
            "preferences": user.preferences.model_dump() if user.preferences else {}
        }
        

@router.post("/send-sms")
async def send_sms(request: SMSRequest):
    '''
        发送短信验证码
        sms_type: register, reset_password, login
    '''
    
    if request.sms_type == "register":
        success, message = await user_service.send_register_sms(request.phone)
    elif request.sms_type == "reset_password":
        success, message = await user_service.send_reset_password_sms(request.phone)
    elif request.sms_type == 'login':
        success, message = await user_service.send_login_sms(request.phone)
    else:
        raise HTTPException(status_code=400, detail="无效的短信类型")
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return {"message": message, 'success': success}


@router.post("/register-by-phone", 
            response_model=Dict[str, Any],
            responses={
                200: {
                    "description": "注册成功",
                    "content": {
                        "application/json": {
                            "example": {
                                "success": True,
                                "message": "注册成功",
                                "data": {
                                    "user": {
                                        "id": "60d21b4667d0d8992e610c85",
                                        "username": "user_3800_1234",
                                        "phone": "13800138000",
                                        "email": "user@example.com",
                                        "is_verified": True
                                    }
                                }
                            }
                        }
                    }
                },
                400: {
                    "description": "注册失败",
                    "content": {
                        "application/json": {
                            "example": {
                                "success": False,
                                "error_code": "sms_code_invalid",
                                "message": "短信验证码无效或已过期",
                                "detail": "请重新获取验证码"
                            }
                        }
                    }
                },
                429: {
                    "description": "请求过于频繁",
                    "content": {
                        "application/json": {
                            "example": {
                                "success": False,
                                "error_code": "too_many_requests",
                                "message": "该IP在24小时内最多注册3个账号"
                            }
                        }
                    }
                }
            })
async def register_by_phone(
    request: PhoneRegisterRequest,
    http_request: Request
):
    """
    手机号注册
    
    使用手机号+短信验证码+密码的方式注册新用户。
    
    **注意**：
    - 手机号必须是11位有效号码
    - 密码至少8个字符，包含字母和数字
    - 短信验证码有效期为5分钟
    - 支持邀请码注册，邀请人可获得算力奖励
    """
    try:
        # ========== 1. 获取客户端信息 ==========
        # 获取真实 IP
        real_ip = get_real_client_ip(http_request)
        
        # 获取 User-Agent
        user_agent = http_request.headers.get("user-agent", "")
        
        # 初始化防刷服务
        anti_fraud = user_service.anti_fraud
        
        # 生成设备指纹
        device_id = anti_fraud.get_device_id(real_ip, user_agent)
        
        # 记录客户端信息
        logger.info(f"📱 注册请求 - IP: {real_ip}, Device: {device_id[:8]}..., UA: {user_agent[:50]}")
        
        # ========== 2. 防刷检查 ==========
        # IP 限制检查
        is_valid, error_msg = anti_fraud.check_ip_limit(real_ip, limit=5, hours=24)
        if not is_valid:
            logger.warning(f"❌ IP注册超限: {real_ip}")
            raise HTTPException(
                status_code=429,
                detail={
                    "success": False,
                    "error_code": "ip_limit_exceeded",
                    "message": error_msg,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
        
        # 设备限制检查
        is_valid, error_msg = anti_fraud.check_device_limit(device_id, limit=3, hours=24)
        if not is_valid:
            logger.warning(f"❌ 设备注册超限: {device_id}")
            raise HTTPException(
                status_code=429,
                detail={
                    "success": False,
                    "error_code": "device_limit_exceeded",
                    "message": error_msg,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
        
        # 可疑活动检测
        is_suspicious = anti_fraud.is_suspicious_activity(user_agent)
        if is_suspicious:
            logger.warning(f"⚠️ 可疑注册活动: IP={real_ip}, UA={user_agent}")
            # 这里选择放行但记录日志
        
        # ========== 3. 调用服务层注册 ==========
        user, error_type, error_message = await user_service.create_user_by_phone(
            phone=request.phone,
            sms_code=request.sms_code,
            password=request.password,
            username=request.username,
            email=request.email,
            invite_code=request.invite_code,
            register_ip=real_ip,
            user_agent=user_agent,
            device_id=device_id
        )
        
        # ========== 4. 处理注册结果 ==========
        if user:
            # 注册成功
            logger.info(f"✅ 用户注册成功: {user.phone}, ID: {user.id}")
            
            # 构建响应数据
            user_data = {
                "id": str(user.id),
                "username": user.username,
                "phone": user.phone,
                "email": user.email,
                "is_verified": user.is_verified,
                "register_type": getattr(user, 'register_type', 'phone'),
                "created_at": user.created_at.isoformat() if hasattr(user.created_at, 'isoformat') else str(user.created_at),
                "power_balance": getattr(user, 'power_balance', 0)
            }
            
            # 如果使用了邀请码，添加奖励信息
            if request.invite_code:
                user_data["invite_info"] = {
                    "used_invite_code": request.invite_code,
                    "inviter_reward": 10  # 基础奖励
                }
            
            return {
                "success": True,
                "message": "注册成功",
                "data": {
                    "user": user_data,
                    "token_info": {
                        "note": "请调用登录接口获取访问令牌"
                    }
                }
            }
        else:
            # 注册失败，根据错误类型返回相应的HTTP状态码和错误信息
            error_detail_map = {
                RegistrationError.SMS_CODE_INVALID: {
                    "status_code": 400,
                    "detail": "短信验证码无效或已过期，请重新获取",
                    "suggestion": "请检查验证码是否正确，或重新发送验证码"
                },
                RegistrationError.PHONE_ALREADY_REGISTERED: {
                    "status_code": 409,
                    "detail": "该手机号已注册",
                    "suggestion": "请直接登录或使用其他手机号"
                },
                RegistrationError.USERNAME_ALREADY_EXISTS: {
                    "status_code": 409,
                    "detail": "用户名已被使用",
                    "suggestion": "请选择其他用户名"
                },
                RegistrationError.EMAIL_ALREADY_EXISTS: {
                    "status_code": 409,
                    "detail": "邮箱已被使用",
                    "suggestion": "请使用其他邮箱或直接登录"
                },
                RegistrationError.PASSWORD_TOO_WEAK: {
                    "status_code": 400,
                    "detail": "密码强度不足",
                    "suggestion": "密码至少8位，包含字母和数字"
                },
                RegistrationError.USERNAME_INVALID: {
                    "status_code": 400,
                    "detail": "用户名格式不正确",
                    "suggestion": "用户名3-20位，支持中文、英文、数字、下划线，不能以数字开头"
                },
                RegistrationError.INVITE_CODE_INVALID: {
                    "status_code": 400,
                    "detail": "邀请码无效",
                    "suggestion": "请检查邀请码是否正确"
                },
                RegistrationError.INVITE_CODE_REQUIRED: {
                    "status_code": 400,
                    "detail": "注册需要邀请码",
                    "suggestion": "请联系管理员获取邀请码"
                },
                RegistrationError.DATABASE_ERROR: {
                    "status_code": 500,
                    "detail": "系统内部错误",
                    "suggestion": "请稍后重试"
                }
            }
            
            # 获取错误详情
            error_detail = error_detail_map.get(
                error_type, 
                {
                    "status_code": 500,
                    "detail": "注册失败",
                    "suggestion": "请稍后重试"
                }
            )
            
            # 构建详细的错误响应
            error_response = {
                "success": False,
                "error_code": error_type.value if hasattr(error_type, 'value') else str(error_type),
                "message": error_message or error_detail["detail"],
                "detail": error_detail["detail"],
                "suggestion": error_detail["suggestion"],
                "timestamp": datetime.utcnow().isoformat()
            }
            
            # 记录错误日志
            logger.error(f"❌ 手机号注册失败: phone={request.phone}, "
                        f"error_type={error_type}, message={error_message}")
            
            # 抛出HTTP异常
            raise HTTPException(
                status_code=error_detail["status_code"],
                detail=error_response
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 注册过程发生未知错误: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error_code": "internal_error",
                "message": "注册失败，请稍后重试",
                "timestamp": datetime.utcnow().isoformat()
            }
        )

@router.post("/reset-password-by-phone")
async def reset_password_by_phone(request: ResetPasswordByPhoneRequest):
    '''
        通过手机号重置密码
    '''
    success, message = await user_service.reset_password_by_phone(
        phone=request.phone,
        sms_code=request.sms_code,
        new_password=request.new_password
    )
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return {"message": message, 'success': success}

@router.post("/login")
async def login(payload: LoginRequest, request: Request):
    """
        用户登录 - 支持密码登录和短信验证码登录
        1. 密码登录样板：
        {
            "login_type": "password",
            "identifier": "username",
            "password": "user_password"
        }
        2. 短信验证码登录样板：
        {
            "login_type": "sms",
            "identifier": "13800138000",
            "sms_code": "123456"
        }
    """
    
    start_time = time.time()
    
    # 获取客户端信息
    ip_address = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")
    
    logger.info(f"🔐 登录请求 - 类型: {payload.login_type}, 标识: {payload.identifier}, IP: {ip_address}")
    
    try:
        # 验证输入
        if not payload.identifier:
            logger.warning(f"❌ 登录失败 - 用户标识为空")
            await log_login_failure("unknown", payload.identifier, "用户标识不能为空", 
                                   ip_address, user_agent, start_time)
            raise HTTPException(status_code=400, detail="用户标识不能为空")
        
        # 根据登录类型验证必要参数
        if payload.login_type == "password":
            if not payload.password:
                logger.warning(f"❌ 密码登录失败 - 密码为空")
                await log_login_failure("unknown", payload.identifier, "密码不能为空",
                                       ip_address, user_agent, start_time)
                raise HTTPException(status_code=400, detail="密码不能为空")
        
        elif payload.login_type == "sms":
            if not payload.sms_code:
                logger.warning(f"❌ 短信登录失败 - 验证码为空")
                await log_login_failure("unknown", payload.identifier, "验证码不能为空",
                                       ip_address, user_agent, start_time)
                raise HTTPException(status_code=400, detail="验证码不能为空")
        
        else:
            logger.warning(f"❌ 登录失败 - 不支持的登录类型: {payload.login_type}")
            await log_login_failure("unknown", payload.identifier, f"不支持的登录类型: {payload.login_type}",
                                   ip_address, user_agent, start_time)
            raise HTTPException(status_code=400, detail="不支持的登录类型")
        
        # 执行登录认证
        user = None
        if payload.login_type == "password":
            logger.info(f"🔐 开始密码认证: {payload.identifier}")
            user = await authenticate_by_password(payload.identifier, payload.password)
        
        elif payload.login_type == "sms":
            logger.info(f"📱 开始短信验证码认证: {payload.identifier}")
            user = await authenticate_by_sms(payload.identifier, payload.sms_code)
        
        if not user:
            logger.warning(f"❌ {payload.login_type}登录失败 - 认证失败: {payload.identifier}")
            await log_login_failure("unknown", payload.identifier, f"{payload.login_type}认证失败",
                                   ip_address, user_agent, start_time)
            raise HTTPException(status_code=401, detail="认证失败")
        
        # 检查用户状态
        if not user.is_active:
            logger.warning(f"❌ 登录失败 - 用户已禁用: {user.username}")
            await log_login_failure(str(user.id), user.username, "用户已禁用",
                                   ip_address, user_agent, start_time)
            raise HTTPException(status_code=403, detail="用户已被禁用")
        
        # 生成 token
        token = AuthService.create_access_token(sub=user.username)
        refresh_token = AuthService.create_access_token(sub=user.username, expires_delta=60*60*24*7)
        
        # 记录登录成功日志
        await log_operation(
            user_id=str(user.id),
            username=user.username,
            action_type=ActionType.USER_LOGIN,
            action=f"用户登录({payload.login_type})",
            details={
                "login_type": payload.login_type,
                "login_method": payload.login_type,
                "identifier": payload.identifier
            },
            success=True,
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        return {
            "success": True,
            "data": {
                "access_token": token,
                "refresh_token": refresh_token,
                "expires_in": 60 * 60,
                "user": {
                    "id": str(user.id),
                    "username": user.username,
                    "email": user.email,
                    "phone": user.phone if hasattr(user, 'phone') else "",
                    "name": user.username,
                    "is_admin": user.is_admin,
                    "is_verified": user.is_verified
                }
            },
            "message": "登录成功"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 登录异常: {e}", exc_info=True)
        await log_operation(
            user_id="unknown",
            username=payload.identifier if hasattr(payload, 'identifier') else "unknown",
            action_type=ActionType.USER_LOGIN,
            action=f"用户登录({getattr(payload, 'login_type', 'unknown')})",
            details={"error": str(e)},
            success=False,
            error_message=f"系统错误: {str(e)}",
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
            user_agent=user_agent
        )
        raise HTTPException(status_code=500, detail="登录过程中发生系统错误")

async def authenticate_by_password(identifier: str, password: str):
    """密码认证"""
    try:
        # 尝试通过用户名认证
        user = await user_service.authenticate_user(identifier, password)
        if user:
            return user
        
        # 尝试通过邮箱认证
        user_by_email = await user_service.get_user_by_email(identifier)
        if user_by_email:
            # 验证密码
            if user_service.verify_password(password, user_by_email.hashed_password):
                return user_by_email
        
        # 尝试通过手机号认证
        if identifier.isdigit() and len(identifier) == 11:
            user_by_phone = await user_service.get_user_by_phone(identifier)
            if user_by_phone:
                if user_service.verify_password(password, user_by_phone.hashed_password):
                    return user_by_phone
        
        return None
        
    except Exception as e:
        logger.error(f"❌ 密码认证异常: {e}")
        return None

async def authenticate_by_sms(phone: str, sms_code: str):
    """短信验证码认证"""
    try:
        # 验证手机号格式
        if not phone or len(phone) != 11 or not phone.isdigit():
            logger.warning(f"❌ 短信登录 - 手机号格式错误: {phone}")
            return None
        
        # 验证短信验证码
        sms_service = user_service.sms_service
        is_valid = await sms_service.verify_sms_code(phone, sms_code, "login")
        
        if not is_valid:
            logger.warning(f"❌ 短信登录 - 验证码无效: {phone}")
            return None
        
        # 查找用户
        user = await user_service.get_user_by_phone(phone)
        if not user:
            logger.warning(f"❌ 短信登录 - 用户不存在: {phone}")
            return None
        
        # 更新最后登录时间
        await user_service.update_last_login(user.username)
        
        logger.info(f"✅ 短信登录成功: {phone}")
        return user
        
    except Exception as e:
        logger.error(f"❌ 短信认证异常: {e}")
        return None

@router.post("/login-admin")
async def login2(payload: LoginOldRequest, request: Request):
    """用户登录"""
    start_time = time.time()

    # 获取客户端信息
    ip_address = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")

    logger.info(f"🔐 登录请求 - 用户名: {payload.username}, IP: {ip_address}")

    try:
        # 验证输入
        if not payload.username or not payload.password:
            logger.warning(f"❌ 登录失败 - 用户名或密码为空")
            await log_operation(
                user_id="unknown",
                username=payload.username or "unknown",
                action_type=ActionType.USER_LOGIN,
                action="用户登录",
                details={"reason": "用户名和密码不能为空"},
                success=False,
                error_message="用户名和密码不能为空",
                duration_ms=int((time.time() - start_time) * 1000),
                ip_address=ip_address,
                user_agent=user_agent
            )
            raise HTTPException(status_code=400, detail="用户名和密码不能为空")

        logger.info(f"🔍 开始认证用户: {payload.username}")

        # 使用数据库认证
        user = await user_service.authenticate_user(payload.username, payload.password)

        logger.info(f"🔍 认证结果: user={'存在' if user else '不存在'}")

        if not user:
            logger.warning(f"❌ 登录失败 - 用户名或密码错误: {payload.username}")
            await log_operation(
                user_id="unknown",
                username=payload.username,
                action_type=ActionType.USER_LOGIN,
                action="用户登录",
                details={"reason": "用户名或密码错误"},
                success=False,
                error_message="用户名或密码错误",
                duration_ms=int((time.time() - start_time) * 1000),
                ip_address=ip_address,
                user_agent=user_agent
            )
            raise HTTPException(status_code=401, detail="用户名或密码错误")

        # 生成 token
        token = AuthService.create_access_token(sub=user.username)
        refresh_token = AuthService.create_access_token(sub=user.username, expires_delta=60*60*24*7)  # 7天有效期

        # 记录登录成功日志
        await log_operation(
            user_id=str(user.id),
            username=user.username,
            action_type=ActionType.USER_LOGIN,
            action="用户登录",
            details={"login_method": "password"},
            success=True,
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
            user_agent=user_agent
        )

        return {
            "success": True,
            "data": {
                "access_token": token,
                "refresh_token": refresh_token,
                "expires_in": 60 * 60,
                "user": {
                    "id": str(user.id),
                    "username": user.username,
                    "email": user.email,
                    "name": user.username,
                    "is_admin": user.is_admin
                }
            },
            "message": "登录成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 登录异常: {e}")
        await log_operation(
            user_id="unknown",
            username=payload.username or "unknown",
            action_type=ActionType.USER_LOGIN,
            action="用户登录",
            details={"error": str(e)},
            success=False,
            error_message=f"系统错误: {str(e)}",
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
            user_agent=user_agent
        )
        raise HTTPException(status_code=500, detail="登录过程中发生系统错误")

@router.post("/refresh")
async def refresh_token(payload: RefreshTokenRequest):
    """刷新访问令牌"""
    try:
        logger.debug(f"🔄 收到refresh token请求")
        logger.debug(f"📝 Refresh token长度: {len(payload.refresh_token) if payload.refresh_token else 0}")

        if not payload.refresh_token:
            logger.warning("❌ Refresh token为空")
            raise HTTPException(status_code=401, detail="Refresh token is required")

        # 验证refresh token
        token_data = AuthService.verify_token(payload.refresh_token)
        logger.debug(f"🔍 Token验证结果: {token_data is not None}")

        if not token_data:
            logger.warning("❌ Refresh token验证失败")
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        # 验证用户是否仍然存在且激活
        user = await user_service.get_user_by_username(token_data.sub)
        if not user or not user.is_active:
            logger.warning(f"❌ 用户不存在或已禁用: {token_data.sub}")
            raise HTTPException(status_code=401, detail="User not found or inactive")

        logger.debug(f"✅ Token验证成功，用户: {token_data.sub}")

        # 生成新的tokens
        new_token = AuthService.create_access_token(sub=token_data.sub)
        new_refresh_token = AuthService.create_access_token(sub=token_data.sub, expires_delta=60*60*24*7)

        logger.debug(f"🎉 新token生成成功")

        return {
            "success": True,
            "data": {
                "access_token": new_token,
                "refresh_token": new_refresh_token,
                "expires_in": 60 * 60
            },
            "message": "Token刷新成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Refresh token处理异常: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Token refresh failed: {str(e)}")

@router.post("/logout")
async def logout(request: Request, user: dict = Depends(get_current_user)):
    """用户登出"""
    start_time = time.time()

    # 获取客户端信息
    ip_address = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")

    try:
        # 记录登出日志
        await log_operation(
            user_id=user["id"],
            username=user["username"],
            action_type=ActionType.USER_LOGOUT,
            action="用户登出",
            details={"logout_method": "manual"},
            success=True,
            duration_ms=int((time.time() - start_time) * 1000),
            ip_address=ip_address,
            user_agent=user_agent
        )

        return {
            "success": True,
            "data": {},
            "message": "登出成功"
        }
    except Exception as e:
        logger.error(f"记录登出日志失败: {e}")
        return {
            "success": True,
            "data": {},
            "message": "登出成功"
        }

@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    """获取当前用户信息"""
    return {
        "success": True,
        "data": user,
        "message": "获取用户信息成功"
    }

@router.put("/me")
async def update_me(
    payload: dict,
    user: dict = Depends(get_current_user)
):
    """更新当前用户信息"""
    try:
        from app.models.user import UserUpdate, UserPreferences

        # 构建更新数据
        update_data = {}

        # 更新邮箱
        if "email" in payload:
            update_data["email"] = payload["email"]

        # 更新偏好设置（支持部分更新）
        if "preferences" in payload:
            # 获取当前偏好
            current_prefs = user.get("preferences", {})

            # 合并新的偏好设置
            merged_prefs = {**current_prefs, **payload["preferences"]}

            # 创建 UserPreferences 对象
            update_data["preferences"] = UserPreferences(**merged_prefs)

        # 如果有语言设置，更新到偏好中
        if "language" in payload:
            if "preferences" not in update_data:
                # 获取当前偏好
                current_prefs = user.get("preferences", {})
                update_data["preferences"] = UserPreferences(**current_prefs)
            update_data["preferences"].language = payload["language"]

        # 如果有时区设置，更新到偏好中（如果需要）
        # 注意：时区通常是系统级设置，不是用户级设置

        # 调用服务更新用户
        user_update = UserUpdate(**update_data)
        updated_user = await user_service.update_user(user["username"], user_update)

        if not updated_user:
            raise HTTPException(status_code=400, detail="更新失败，邮箱可能已被使用")

        # 返回更新后的用户信息
        return {
            "success": True,
            "data": updated_user.model_dump(by_alias=True),
            "message": "用户信息更新成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新用户信息失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"更新用户信息失败: {str(e)}")

@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: dict = Depends(get_current_user)
):
    """修改密码"""
    try:
        # 使用数据库服务修改密码
        success = await user_service.change_password(
            user["username"], 
            payload.old_password, 
            payload.new_password
        )
        
        if not success:
            raise HTTPException(status_code=400, detail="旧密码错误")

        return {
            "success": True,
            "data": {},
            "message": "密码修改成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"修改密码失败: {e}")
        raise HTTPException(status_code=500, detail=f"修改密码失败: {str(e)}")

@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    user: dict = Depends(get_current_user)
):
    """重置密码（管理员操作）"""
    try:
        # 检查权限
        if not user.get("is_admin", False):
            raise HTTPException(status_code=403, detail="权限不足")

        # 重置密码
        success = await user_service.reset_password(payload.username, payload.new_password)
        
        if not success:
            raise HTTPException(status_code=404, detail="用户不存在")

        return {
            "success": True,
            "data": {},
            "message": f"用户 {payload.username} 的密码已重置"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重置密码失败: {e}")
        raise HTTPException(status_code=500, detail=f"重置密码失败: {str(e)}")

@router.post("/create-user")
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    user: dict = Depends(get_current_user)
):
    """创建用户（管理员操作）"""
    try:
        # 检查权限
        if not user.get("is_admin", False):
            raise HTTPException(status_code=403, detail="权限不足")

        # 创建用户
        user_create = UserCreate(
            username=payload.username,
            email=payload.email,
            password=payload.password
        )
        
        new_user = await user_service.create_user(user_create)
        
        if not new_user:
            raise HTTPException(status_code=400, detail="用户名或邮箱已存在")

        # 如果需要设置为管理员
        if payload.is_admin:
            from pymongo import MongoClient
            from app.core.config import settings
            client = MongoClient(settings.MONGO_URI)
            db = client[settings.MONGO_DB]
            db.users.update_one(
                {"username": payload.username},
                {"$set": {"is_admin": True}}
            )

        return {
            "success": True,
            "data": {
                "id": str(new_user.id),
                "username": new_user.username,
                "email": new_user.email,
                "is_admin": payload.is_admin
            },
            "message": f"用户 {payload.username} 创建成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建用户失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建用户失败: {str(e)}")

@router.get("/users")
async def list_users(
    skip: int = 0,
    limit: int = 100,
    user: dict = Depends(get_current_user)
):
    """获取用户列表（管理员操作）"""
    try:
        # 检查权限
        if not user.get("is_admin", False):
            raise HTTPException(status_code=403, detail="权限不足")

        users = await user_service.list_users(skip=skip, limit=limit)
        
        return {
            "success": True,
            "data": {
                "users": [user.model_dump() for user in users],
                "total": len(users)
            },
            "message": "获取用户列表成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取用户列表失败: {str(e)}")
