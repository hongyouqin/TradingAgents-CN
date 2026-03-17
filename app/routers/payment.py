from decimal import Decimal
import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.models.user import User
from app.routers.auth_db import get_current_user
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
    request: Request,
    openid: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """
    准备充值支付 - 支持H5支付
    
    H5支付会返回mweb_url，前端跳转至该地址完成支付
    """
    # 获取H5支付回跳地址（前端传入）
    payload = await request.json()
    redirect_url = payload.get("redirect_url", settings.FRONTEND_URL)
    
    # 调用订单服务准备支付（传递回跳地址）
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
    return {"price": 1.8, "unit": "⚡", "desc": "分析一份报告需要消耗的算力"}


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
    transaction_type: Optional[str] = Query(None, regex="^(RECHARGE|CONSUME|ALL)?$"),
    current_user: User = Depends(get_current_user)
):
    """
    获取交易流水（充值和消费）
    - transaction_type: RECHARGE(仅充值), CONSUME(仅消费), ALL(全部)
    """
    temp_dict = current_user.copy()
    temp_dict['hashed_password'] = 'dummy'
    user_obj = User.model_validate(temp_dict)
    
    # 如果 transaction_type 是 ALL，传 None 给服务层（返回全部）
    filter_type = None if transaction_type == 'ALL' else transaction_type
    
    transactions = await power_account_service.get_transactions(
        user_obj, 
        limit,
        transaction_type=filter_type
    )
    
    # 格式化返回
    result = []
    for t in transactions:
        result.append({
            "order_no": t['order_no'],
            "type": t['transaction_type'],
            "type_name": "充值" if t['transaction_type'] == 'RECHARGE' else "消费",
            "amount": float(t['amount']),
            "before_balance": float(t['before_balance']),
            "after_balance": float(t['after_balance']),
            "description": t.get('description', ''),
            "created_at": t['created_at'].isoformat() + 'Z' if hasattr(t['created_at'], 'isoformat') else t['created_at'],
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