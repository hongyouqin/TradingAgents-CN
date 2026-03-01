
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


# ==================== 分析服务消费接口（直接扣算力） ====================

@router.post("/consume/analysis")
async def run_analysis(
    payload: dict,
    current_user: User = Depends(get_current_user)
):
    """
    运行分析服务 - 按次扣费
    
    分析类型及价格:
    - 基础分析: 5⚡/次
    - 深度分析: 10⚡/次
    - 专业分析: 20⚡/次
    
    请求示例：
    {
        "analysis_type": "stock_analysis",  # stock_analysis, market_analysis, ai_forecast
        "depth": "deep",                      # basic, deep, professional
        "stock_code": "600519",                # 股票代码（可选）
        "description": "茅台深度分析"          # 自定义描述（可选）
    }
    """
    # 分析类型和价格映射
    analysis_prices = {
        "stock_analysis": {"basic": 5, "deep": 10, "professional": 20},
        "market_analysis": {"basic": 8, "deep": 15, "professional": 30},
        "ai_forecast": {"basic": 10, "deep": 20, "professional": 50}
    }
    
    analysis_type = payload.get("analysis_type")
    depth = payload.get("depth", "basic")
    stock_code = payload.get("stock_code")
    custom_desc = payload.get("description", "")
    
    # 验证分析类型
    if analysis_type not in analysis_prices:
        raise HTTPException(
            status_code=400, 
            detail=f"不支持的分析类型: {analysis_type}，可选: {list(analysis_prices.keys())}"
        )
    
    # 验证深度
    if depth not in analysis_prices[analysis_type]:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的深度: {depth}，可选: {list(analysis_prices[analysis_type].keys())}"
        )
    
    # 获取价格
    price = Decimal(str(analysis_prices[analysis_type][depth]))
    
    # 生成消费流水号
    import time
    consume_no = f"ANA{int(time.time() * 1000)}"
    
    # 构建描述
    type_names = {
        "stock_analysis": "股票分析",
        "market_analysis": "市场分析",
        "ai_forecast": "AI预测"
    }
    depth_names = {"basic": "基础", "deep": "深度", "professional": "专业"}
    
    description = custom_desc or f"{type_names.get(analysis_type, analysis_type)}-{depth_names.get(depth, depth)}"
    if stock_code:
        description += f" [{stock_code}]"
    
    # 扣减算力
    success, msg = await power_account_service.consume(
        user=current_user,
        order_no=consume_no,
        amount=price,
        description=description,
        metadata={
            'consume_type': 'ANALYSIS',
            'analysis_type': analysis_type,
            'depth': depth,
            'stock_code': stock_code,
            'price': float(price)
        }
    )
    
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    
    # 获取最新余额
    balance = await power_account_service.get_balance(current_user)
    
    # 这里可以触发实际的分析任务
    # task_id = await analysis_service.start_analysis(payload)
    
    return {
        "code": 0,
        "message": "分析任务已启动",
        "data": {
            "consume_no": consume_no,
            "analysis_type": analysis_type,
            "depth": depth,
            "price": float(price),
            "balance": float(balance['balance']),
            "task_id": f"TASK{int(time.time())}",  # 模拟任务ID
            "estimated_time": "约30秒"
        }
    }


@router.get("/consume/prices")
async def get_analysis_prices():
    """
    获取分析服务价格表
    """
    prices = {
        "stock_analysis": {
            "name": "股票分析",
            "description": "对单只股票进行技术面和基本面分析",
            "prices": {
                "basic": {"price": 5, "name": "基础分析", "description": "基础指标分析"},
                "deep": {"price": 10, "name": "深度分析", "description": "深度技术分析+基本面评分"},
                "professional": {"price": 20, "name": "专业分析", "description": "完整研究报告+预测模型"}
            }
        },
        "market_analysis": {
            "name": "市场分析",
            "description": "对整体市场行情进行分析",
            "prices": {
                "basic": {"price": 8, "name": "基础分析", "description": "市场概览"},
                "deep": {"price": 15, "name": "深度分析", "description": "板块轮动分析"},
                "professional": {"price": 30, "name": "专业分析", "description": "市场预测报告"}
            }
        },
        "ai_forecast": {
            "name": "AI预测",
            "description": "基于AI模型的股价预测",
            "prices": {
                "basic": {"price": 10, "name": "基础预测", "description": "3天预测"},
                "deep": {"price": 20, "name": "深度预测", "description": "7天预测+置信区间"},
                "professional": {"price": 50, "name": "专业预测", "description": "30天趋势预测"}
            }
        }
    }
    
    return {
        "code": 0,
        "message": "success",
        "data": prices
    }


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
    
    # 处理充值成功
    ok, msg = await order_service.handle_recharge_success(
        order_no=data['out_trade_no'],
        transaction_id=data['transaction_id'],
        paid_amount=int(data['total_fee'])
    )
    
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
    balance = await power_account_service.get_balance(current_user)
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
    transactions = await power_account_service.get_transactions(
        current_user, 
        limit,
        transaction_type=transaction_type
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