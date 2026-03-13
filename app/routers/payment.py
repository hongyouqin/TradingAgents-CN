
from decimal import Decimal
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.models.user import User
from app.routers.auth_db import get_current_user
from app.services.wechat_pay_service import wechat_pay_service
from app.services.order_service import order_service
from app.services.power_account_service import power_account_service



router = APIRouter(prefix="/api/payment", tags=["支付接口"])
logger = logging.getLogger("payment")

# ==================== 充值套餐（优惠档次） ====================
RECHARGE_PACKAGES = {
    "PACK_000": {
        "name": "测试包",
        "price": 0.01,      # 支付0.01元
        "power": 100000,         # 获得100000算力
        "bonus": 0,          # 赠送0
        "popular": False,
        "description": "测试包包⚡"
    },
    "PACK_001": {
        "name": "体验包",
        "price": 9.90,      # 支付9.9元
        "power": 10,         # 获得10算力
        "bonus": 0,          # 赠送0
        "popular": False,
        "description": "9.9元充值10⚡"
    },
    "PACK_002": {
        "name": "标准包",
        "price": 19.80,      # 支付19.8元
        "power": 20,          # 获得20算力
        "bonus": 0,
        "popular": True,
        "description": "19.8元充值20⚡"
    },
    "PACK_003": {
        "name": "畅享包",
        "price": 49.00,      # 支付49元
        "power": 50,          # 获得50算力
        "bonus": 2,           # 赠送2算力
        "popular": False,
        "description": "49元充值50⚡+赠送2⚡"
    },
    "PACK_004": {
        "name": "尊享包",
        "price": 98.00,      # 支付98元
        "power": 100,         # 获得100算力
        "bonus": 5,           # 赠送5算力
        "popular": True,
        "description": "98元充值100⚡+赠送5⚡"
    },
    "PACK_005": {
        "name": "企业包",
        "price": 198.00,     # 支付198元
        "power": 200,         # 获得200算力
        "bonus": 15,          # 赠送15算力
        "popular": False,
        "description": "198元充值200⚡+赠送15⚡"
    },
}


@router.get("/recharge/packages")
async def get_recharge_packages():
    """
    获取充值套餐列表（优惠档次）
    """
    packages = []
    for pid, info in RECHARGE_PACKAGES.items():
        total_power = info["power"] + info.get("bonus", 0)
        packages.append({
            "id": pid,
            "name": info["name"],
            "price": info["price"],
            "power": info["power"],
            "bonus": info.get("bonus", 0),
            "total_power": total_power,
            "popular": info.get("popular", False),
            "description": info["description"],
            "unit_price": round(info["price"] / total_power, 2)  # 单价（元/算力）
        })
    
    # 按价格排序
    packages.sort(key=lambda x: x["price"])
    
    return {
        "code": 0,
        "message": "success",
        "data": packages
    }


# ==================== 充值订单相关（需要微信支付） ====================

@router.post("/recharge/create")
async def create_recharge_order(
    request: Request,
    payload: dict,
    current_user: User = Depends(get_current_user)
):
    """
    创建充值订单 - 选择套餐
    
    请求示例:
    {
        "package_id": "PACK_002",    # 套餐ID
        "payment_scene": "NATIVE"     # JSAPI或NATIVE
    }
    """
    client_ip = request.client.host
    package_id = payload.get("package_id")
    payment_scene = payload.get("payment_scene")
    
    if not package_id:
        raise HTTPException(status_code=400, detail="请选择充值套餐")
    
    if not payment_scene:
        raise HTTPException(status_code=400, detail="缺少 payment_scene 参数")
    
    # 获取套餐信息
    package = RECHARGE_PACKAGES.get(package_id)
    if not package:
        raise HTTPException(status_code=400, detail="无效的套餐ID")
    
    total_power = package["power"] + package.get("bonus", 0)
    
    recharge_data = {
        "order_type": "RECHARGE",
        "package_id": package_id,
        "package_name": package["name"],
        "price": package["price"],              # 支付金额
        "power_amount": package["power"],        # 基础算力
        "bonus_amount": package.get("bonus", 0), # 赠送算力
        "total_power": total_power,              # 总获得算力
        "description": package["description"]
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
            "package_name": package["name"],
            "price": order['price'],
            "power_amount": order['power_amount'],
            "bonus_amount": order.get('bonus_amount', 0),
            "total_power": order['total_power'],
            "expired_timestamp": order['expired_timestamp'],
            "expired_at": order['expired_at']
        }
    }


@router.post("/recharge/{order_no}/prepare")
async def prepare_recharge_payment(
    order_no: str,
    openid: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """
    准备充值支付 - 调用微信统一下单
    """
    payment_params, error = await order_service.prepare_recharge_payment(
        user=current_user,
        order_no=order_no,
        openid=openid
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
    return {"price": 1.8, "unit": "⚡", "desc": "每次分析固定扣费"}

# ==================== 微信支付回调 ====================

@router.post("/wxpay/notify")
async def wechat_pay_notify(request: Request):
    """
    微信支付回调接口 - 处理充值成功
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
    temp_dict['hashed_password'] = 'dummy'  # 临时密码
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
        transaction_type=filter_type  # None 表示不筛选
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
