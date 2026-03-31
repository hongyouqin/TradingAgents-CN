from decimal import Decimal
import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.models.prepare_payment_request import PreparePaymentRequest
from app.models.user import User
from app.routers.auth_db import get_current_user
from app.services.user_service import user_service
from app.services.wechat_pay_service import wechat_pay_service
from app.services.order_service import order_service
from app.services.power_account_service import power_account_service
from app.services.recharge_package_service import recharge_package_service
from app.models.recharge_package import RechargePackage, RechargePackageCreate, RechargePackageUpdate
from app.core.config import settings


router = APIRouter(prefix="/api/payment", tags=["支付接口"])
logger = logging.getLogger("payment")


# ==================== 充值套餐管理（从MongoDB读取） ====================

@router.get("/recharge/packages")
async def get_recharge_packages():
    """
    获取充值套餐列表（从MongoDB读取）
    """
    packages = await recharge_package_service.get_active_packages()
    
    result = []
    for pkg in packages:
        result.append({
            "id": pkg.package_id,
            "name": pkg.name,
            "price": pkg.price,
            "power": pkg.power,
            "bonus": pkg.bonus,
            "total_power": pkg.total_power,
            "popular": pkg.popular,
            "description": pkg.description,
            "unit_price": pkg.unit_price,
            "sort_order": pkg.sort_order
        })
    
    return {
        "code": 0,
        "message": "success",
        "data": result
    }


# ==================== 充值订单相关（需要微信支付） ====================

@router.post("/recharge/create")
async def create_recharge_order(
    request: Request,
    payload: dict,
    current_user: User = Depends(get_current_user)
):
    """
    创建充值订单 - 支持H5支付场景
    
    请求示例:
    {
        "package_id": "PACK_002",    # 套餐ID
        "payment_scene": "H5"        # JSAPI/NATIVE/H5
    }
    """
    client_ip = request.client.host
    package_id = payload.get("package_id")
    payment_scene = payload.get("payment_scene")
    
    if not package_id:
        raise HTTPException(status_code=400, detail="请选择充值套餐")
    
    if not payment_scene:
        raise HTTPException(status_code=400, detail="缺少 payment_scene 参数")
    
    # 校验支付场景
    if payment_scene not in ["JSAPI", "NATIVE", "H5"]:
        raise HTTPException(status_code=400, detail="payment_scene 仅支持JSAPI/NATIVE/H5")
    
    # 从MongoDB获取套餐信息
    package = await recharge_package_service.get_package_by_id(package_id)
    if not package:
        raise HTTPException(status_code=400, detail="无效的套餐ID或套餐已下架")
    
    recharge_data = {
        "order_type": "RECHARGE",
        "package_id": package.package_id,
        "package_name": package.name,
        "price": package.price,              # 支付金额
        "power_amount": package.power,        # 基础算力
        "bonus_amount": package.bonus,        # 赠送算力
        "total_power": package.total_power,   # 总获得算力
        "description": package.description
    }
    
    # 创建充值订单
    order, error = await order_service.create_recharge_order(
        user=current_user,
        recharge_data=recharge_data,
        payment_scene=payment_scene,
        client_ip=client_ip
    )
    
    if error:
        raise HTTPException(status_code=400, detail=error)
    
    return {
        "code": 0,
        "message": "充值订单创建成功",
        "data": {
            "order_no": order['order_no'],
            "package_name": package.name,
            "price": order['price'],
            "power_amount": order['power_amount'],
            "bonus_amount": order.get('bonus_amount', 0),
            "total_power": order['total_power'],
            "expired_timestamp": order['expired_timestamp'],
            "expired_at": order['expired_at'],
            "payment_scene": order['payment_scene']
        }
    }


@router.post("/recharge/{order_no}/prepare")
async def prepare_recharge_payment(
    order_no: str,
    payment_request: Optional[PreparePaymentRequest] = None,  # 使用模型
    current_user: User = Depends(get_current_user)
):
    """
    准备充值支付 - 支持H5支付
    
    - **order_no**: 订单号
    - **redirect_url**: H5支付回跳地址（可选）
    """
    # 获取redirect_url
    redirect_url = None
    if payment_request:
        redirect_url = payment_request.redirect_url
    
    # 如果没有传则使用默认值
    if not redirect_url:
        redirect_url = settings.WECHAT_H5_REDIRECT_URL
    
    # 调用订单服务准备支付
    openid = current_user.get("openid")   # 从当前用户获取openid
    payment_params, error = await order_service.prepare_recharge_payment(
        user=current_user,
        order_no=order_no,
        openid=openid,
        redirect_url=redirect_url
    )
    
    if error:
        raise HTTPException(status_code=400, detail=error)
    
    return {
        "code": 0,
        "message": "success",
        "data": payment_params
    }

@router.get("/recharge/{order_no}/status")
async def query_recharge_status(
    order_no: str,
    current_user: User = Depends(get_current_user)
):
    """
    查询充值订单状态
    """
    result = await order_service.query_recharge_status(current_user, order_no)
    return {
        "code": 0,
        "message": "success",
        "data": result
    }


@router.get("/recharge/orders")
async def get_recharge_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user)
):
    """
    获取用户的充值订单历史
    """
    orders = await order_service.get_user_recharge_orders(current_user, skip, limit)
    return {
        "code": 0,
        "message": "success",
        "data": orders
    }


@router.get("/consume/price")
async def get_analysis_price():
    """获取消费价格"""
    return {
        "unit": "⚡",
        "types": {
            "standard": {
                "name": "standard",
                "label": "标准分析",
                "price": 1.5,
                "description": "快速分析，适合常规报告",
                "unit": "⚡"
            },
            "deep": {
                "name": "deep",
                "label": "深度推理",
                "price": 1.8,
                "description": "深度分析，适合复杂报告",
                "unit": "⚡"
            }
        }
    }


# ==================== 微信支付回调 ====================
@router.post("/wxpay/notify")
async def wechat_pay_notify(request: Request):
    """
    微信支付回调接口
    """
    body = await request.body()
    xml_data = body.decode('utf-8')
    
    # 验证回调
    success, data = wechat_pay_service.verify_notify(xml_data)
    
    if not success:
        return HTMLResponse(
            content='<xml><return_code><![CDATA[FAIL]]></return_code><return_msg><![CDATA[签名失败]]></return_msg></xml>',
            media_type="text/xml"
        )
    
    logger.info(f"==收到微信支付回调: {data}")
    # 处理充值成功
    ok, msg = await order_service.handle_recharge_success(
        order_no=data['out_trade_no'],
        transaction_id=data['transaction_id'],
        paid_amount=int(data['total_fee'])
    )
    
    logger.info(f"==充值回调处理结果: {ok}, {msg}")
    
    if ok:
        return HTMLResponse(
            content='<xml><return_code><![CDATA[SUCCESS]]></return_code><return_msg><![CDATA[OK]]></return_msg></xml>',
            media_type="text/xml"
        )
    else:
        logger.error(f"充值回调处理失败: {msg}")
        return HTMLResponse(
            content='<xml><return_code><![CDATA[FAIL]]></return_code><return_msg><![CDATA[处理失败]]></return_msg></xml>',
            media_type="text/xml"
        )


@router.get("/wechat/callback")
async def wechat_callback(
    code: str,
    current_user: User = Depends(get_current_user)
):
    """
    微信授权回调（最安全版本）
    不需要 state，不需要传参
    直接获取当前登录用户
    """
    username = current_user["username"]
    logger.info(f"微信授权回调，当前用户: {username}, code: {code}")

    try:
        # 1. 获取 openid
        data = await wechat_pay_service.get_openid_by_code(code)
        openid = data["openid"]

        # 2. 直接保存到【当前登录用户】（安全！）
        await user_service.update_user_openid(username, openid)

        logger.info(f"✅ 用户 {username} 绑定 openid 成功: {openid}")

        # 3. 跳回支付页面，完全不返回任何敏感信息
        return HTMLResponse("<h3>微信支付授权成功</h3>")

    except Exception as e:
        logger.error(f"微信授权失败: {e}")
        return HTMLResponse("<h3>微信支付授权失败</h3>")

# ==================== 通用查询接口 ====================

@router.get("/balance")
async def get_balance(current_user: User = Depends(get_current_user)):
    """
    获取用户算力余额
    """
    temp_dict = current_user.copy()
    temp_dict['hashed_password'] = 'dummy'
    user_obj = User.model_validate(temp_dict)
    balance = await power_account_service.get_balance(user_obj)
    return {
        "code": 0,
        "message": "success",
        "data": {
            "balance": float(balance['balance']),
            "frozen": float(balance['frozen']),
            "available": float(balance['available']),
            "total_recharged": float(balance['total_recharged']),
            "total_consumed": float(balance['total_consumed']),
            "symbol": "⚡"
        }
    }


@router.get("/transactions")
async def get_transactions(
    limit: int = Query(50, ge=1, le=200),
    transaction_type: Optional[str] = Query(None, regex="^(RECHARGE|CONSUME|FREEZE|ALL)?$"),
    status: Optional[str] = Query(None, regex="^(FROZEN|CONFIRMED|CANCELLED|EXPIRED|ALL)?$"),
    current_user: User = Depends(get_current_user)
):
    """
    获取用户交易流水
    
    - **transaction_type**: 交易类型筛选
        - `RECHARGE`: 仅充值记录
        - `CONSUME`: 仅消费记录（已确认扣款）
        - `FREEZE`: 仅冻结记录（预扣款/待确认）
        - `ALL`: 所有类型（默认）
    
    - **status**: 交易状态筛选
        - `FROZEN`: 已冻结（待确认）
        - `CONFIRMED`: 已完成（已确认扣款/充值成功）
        - `CANCELLED`: 已取消（冻结已取消）
        - `EXPIRED`: 已过期（超时未确认）
        - `ALL`: 所有状态（默认）
    
    - **limit**: 返回记录数量限制，默认50条，最大200条
    
    返回字段说明：
    - `type`: 交易类型 (RECHARGE/CONSUME/FREEZE)
    - `type_name`: 交易类型中文名
    - `status`: 交易状态
    - `status_name`: 交易状态中文名
    - `amount`: 交易金额
    - `before_balance`: 交易前余额
    - `after_balance`: 交易后余额
    - `description`: 交易描述
    - `created_at`: 创建时间
    - `completed_at`: 完成时间（如有）
    - `metadata`: 元数据
    """
    temp_dict = current_user.copy()
    temp_dict['hashed_password'] = 'dummy'
    user_obj = User.model_validate(temp_dict)
    
    # 处理交易类型筛选
    filter_type = None if transaction_type == 'ALL' or not transaction_type else transaction_type
    
    # 处理状态筛选
    filter_status = None if status == 'ALL' or not status else status
    
    transactions = await power_account_service.get_transactions(
        user_obj, 
        limit,
        transaction_type=filter_type,
        status=filter_status
    )
    
    # 状态中文映射
    status_name_map = {
        'FROZEN': '已冻结',
        'CONFIRMED': '已完成',
        'CANCELLED': '已取消',
        'EXPIRED': '已过期'
    }
    
    # 类型中文映射
    type_name_map = {
        'RECHARGE': '充值',
        'CONSUME': '消费',
        'FREEZE': '预扣款'
    }
    
    # 格式化返回
    result = []
    for t in transactions:
        result.append({
            "order_no": t['order_no'],
            "type": t['transaction_type'],
            "type_name": type_name_map.get(t['transaction_type'], t['transaction_type']),
            "status": t.get('status', ''),
            "status_name": status_name_map.get(t.get('status', ''), t.get('status', '')),
            "amount": float(t['amount']),
            "before_balance": float(t['before_balance']),
            "after_balance": float(t['after_balance']) if t.get('after_balance') else None,
            "description": t.get('description', ''),
            "created_at": t['created_at'].isoformat() + 'Z' if hasattr(t['created_at'], 'isoformat') else t['created_at'],
            "completed_at": t['completed_at'].isoformat() + 'Z' if t.get('completed_at') and hasattr(t['completed_at'], 'isoformat') else t.get('completed_at'),
            "metadata": t.get('metadata', {})
        })
    
    return {
        "code": 0,
        "message": "success",
        "data": {
            "total": len(result),
            "transactions": result
        }
    }


# ==================== 后台管理接口（可选） ====================

@router.post("/admin/packages", dependencies=[Depends(get_current_user)])  # 需要管理员权限
async def create_package(
    package_data: RechargePackageCreate,
    current_user: User = Depends(get_current_user)
):
    """
    【管理员】创建新套餐
    """
    # 检查管理员权限（需要根据你的用户模型判断）
    # if not current_user.is_admin:
    #     raise HTTPException(status_code=403, detail="需要管理员权限")
    
    try:
        package = await recharge_package_service.create_package(package_data)
        return {
            "code": 0,
            "message": "套餐创建成功",
            "data": package.dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/admin/packages/{package_id}", dependencies=[Depends(get_current_user)])
async def update_package(
    package_id: str,
    update_data: RechargePackageUpdate,
    current_user: User = Depends(get_current_user)
):
    """
    【管理员】更新套餐
    """
    package = await recharge_package_service.update_package(package_id, update_data)
    if not package:
        raise HTTPException(status_code=404, detail="套餐不存在")
    
    return {
        "code": 0,
        "message": "套餐更新成功",
        "data": package.dict()
    }


@router.delete("/admin/packages/{package_id}", dependencies=[Depends(get_current_user)])
async def delete_package(
    package_id: str,
    hard_delete: bool = Query(False, description="是否硬删除"),
    current_user: User = Depends(get_current_user)
):
    """
    【管理员】删除套餐（默认软删除）
    """
    if hard_delete:
        # 硬删除（谨慎使用）
        success = await recharge_package_service.hard_delete_package(package_id)
        if not success:
            raise HTTPException(status_code=404, detail="套餐不存在")
        message = "套餐已永久删除"
    else:
        # 软删除
        success = await recharge_package_service.delete_package(package_id)
        if not success:
            raise HTTPException(status_code=404, detail="套餐不存在")
        message = "套餐已下架"
    
    return {
        "code": 0,
        "message": message
    }