from decimal import Decimal
import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.models.prepare_payment_request import PreparePaymentRequest
from app.models.user import User
from app.routers.auth_db import get_current_user
from app.services.user_service import user_service
from app.services.wechat_pay_service import wechat_pay_service
from app.services.order_service import order_service
from app.services.power_account_service import power_account_service
from app.services.recharge_package_service import recharge_package_service
from app.models.recharge_package import RechargePackageCreate, RechargePackageUpdate
from app.core.config import settings

router = APIRouter(prefix="/api/payment", tags=["支付接口"])
logger = logging.getLogger("payment")

# ==================== 充值套餐管理 ====================
@router.get("/recharge/packages")
async def get_recharge_packages():
    """获取充值套餐列表"""
    packages = await recharge_package_service.get_active_packages()
    result = []
    for pkg in packages:
        result.append({
            "id": pkg.package_id,
            "name": pkg.name,
            "price": float(pkg.price),
            "power": pkg.power,
            "bonus": pkg.bonus,
            "total_power": pkg.total_power,
            "popular": pkg.popular,
            "description": pkg.description,
            "unit_price": float(pkg.unit_price) if pkg.unit_price else 0,
            "sort_order": pkg.sort_order
        })
    return {"code": 0, "message": "success", "data": result}

# ==================== 创建充值订单 ====================
@router.post("/recharge/create")
async def create_recharge_order(
    request: Request,
    payload: PreparePaymentRequest,
    current_user: User = Depends(get_current_user)
):
    """创建充值订单
    请求示例:
    {
        "package_id": "PACK_002",    # 套餐ID
        "payment_scene": "H5"        # JSAPI/NATIVE/H5
    }
    """
    client_ip = request.client.host
    package_id = payload.package_id
    payment_scene = payload.payment_scene

    if not package_id:
        raise HTTPException(400, "请选择充值套餐")
    if payment_scene not in ["JSAPI", "NATIVE", "H5"]:
        raise HTTPException(400, "支付场景仅支持 JSAPI/NATIVE/H5")

    package = await recharge_package_service.get_package_by_id(package_id)
    if not package:
        raise HTTPException(400, "套餐不存在或已下架")

    recharge_data = {
        "order_type": "RECHARGE",
        "package_id": package.package_id,
        "package_name": package.name,
        "price": package.price,
        "power_amount": package.power,
        "bonus_amount": package.bonus,
        "total_power": package.total_power,
        "description": package.description
    }

    order, error = await order_service.create_recharge_order(
        user=current_user,
        recharge_data=recharge_data,
        payment_scene=payment_scene,
        client_ip=client_ip
    )
    if error:
        raise HTTPException(400, error)

    return {
        "code": 0,
        "message": "订单创建成功",
        "data": {
            "order_no": order["order_no"],
            "package_name": package.name,
            "price": float(order["price"]),
            "power_amount": order["power_amount"],
            "bonus_amount": order.get("bonus_amount", 0),
            "total_power": order["total_power"],
            "expired_timestamp": order["expired_timestamp"],
            "expired_at": order["expired_at"],
            "payment_scene": order["payment_scene"]
        }
    }

# ==================== 准备支付 ====================
@router.post("/recharge/{order_no}/prepare")
async def prepare_recharge_payment(
    order_no: str,
    # 改成这样：不强制接收JSON
    body: Optional[dict] = None,
    current_user: User = Depends(get_current_user)
):
    """
    准备充值支付
    """
    # 安全获取 redirect_url
    redirect_url = None
    if body:
        redirect_url = body.get("redirect_url")
    
    if not redirect_url:
        redirect_url = settings.WECHAT_H5_REDIRECT_URL

    # 后面代码不动
    openid = current_user.get("openid") if isinstance(current_user, dict) else getattr(current_user, "openid", None)
    logger.info(f"openid= {openid} current_user= {current_user} redirect_url= {redirect_url}")
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

# ==================== 查询订单状态 ====================
@router.get("/recharge/{order_no}/status")
async def query_recharge_status(
    order_no: str,
    current_user: User = Depends(get_current_user)
):
    result = await order_service.query_recharge_status(current_user, order_no)
    return {"code": 0, "message": "success", "data": result}

# ==================== 订单列表 ====================
@router.get("/recharge/orders")
async def get_recharge_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, le=100),
    current_user: User = Depends(get_current_user)
):
    orders = await order_service.get_user_recharge_orders(current_user, skip, limit)
    return {"code": 0, "message": "success", "data": orders}

# ==================== 消费价格 ====================
@router.get("/consume/price")
async def get_analysis_price():
    return {
        "unit": "⚡",
        "types": {
            "standard": {"name": "standard", "label": "标准分析", "price": 1.5, "description": "快速常规报告"},
            "deep": {"name": "deep", "label": "深度推理", "price": 1.8, "description": "深度复杂分析"}
        }
    }

# ==================== 微信支付回调（已修复） ====================
@router.post("/wxpay/notify")
async def wechat_pay_notify(request: Request):
    body = await request.body()
    xml_data = body.decode()

    success, data = wechat_pay_service.verify_notify(xml_data)
    if not success:
        return HTMLResponse('<xml><return_code>FAIL</return_code><return_msg>签名失败</return_msg></xml>')

    logger.info(f"微信支付回调成功: {data}")
    ok, msg = await order_service.handle_recharge_success(
        order_no=data["out_trade_no"],
        transaction_id=data["transaction_id"],
        paid_amount=int(data["total_fee"])
    )

    if ok:
        return HTMLResponse('<xml><return_code>SUCCESS</return_code><return_msg>OK</return_msg></xml>')
    else:
        logger.error(f"充值处理失败: {msg}")
        return HTMLResponse('<xml><return_code>FAIL</return_code><return_msg>处理失败</return_msg></xml>')

# ==================== 微信授权 ====================
@router.get("/wechat/callback")
async def wechat_callback(
    code: str,
    current_user: User = Depends(get_current_user)
):
    try:
        data = await wechat_pay_service.get_openid_by_code(code)
        openid = data["openid"]
        await user_service.update_user_openid(current_user.username, openid)
        return HTMLResponse("<h3>微信授权成功</h3>")
    except Exception as e:
        logger.error(f"授权失败: {e}")
        return HTMLResponse("<h3>授权失败</h3>")

# ==================== 余额 & 流水 ====================
@router.get("/balance")
async def get_balance(current_user: User = Depends(get_current_user)):
    
    temp_dict = current_user.copy()
    temp_dict['hashed_password'] = 'dummy'
    user_obj = User.model_validate(temp_dict)
    balance = await power_account_service.get_balance(user_obj)
    return {
        "code": 0,
        "message": "success",
        "data": {
            "balance": float(balance["balance"]),
            "frozen": float(balance["frozen"]),
            "available": float(balance["available"]),
            "total_recharged": float(balance["total_recharged"]),
            "total_consumed": float(balance["total_consumed"]),
            "symbol": "⚡"
        }
    }

@router.get("/transactions")
async def get_transactions(
    limit: int = Query(50, le=200),
    transaction_type: Optional[str] = Query(None, regex="^(RECHARGE|CONSUME|FREEZE|ALL)?$"),
    status: Optional[str] = Query(None, regex="^(FROZEN|CONFIRMED|CANCELLED|EXPIRED|ALL)?$"),
    current_user: User = Depends(get_current_user)
):
    filter_type = None if transaction_type == "ALL" else transaction_type
    filter_status = None if status == "ALL" else status

    temp_dict = current_user.copy()
    temp_dict['hashed_password'] = 'dummy'
    user_obj = User.model_validate(temp_dict)
    transactions = await power_account_service.get_transactions(
        user_obj, limit, filter_type, filter_status
    )

    type_map = {"RECHARGE": "充值", "CONSUME": "消费", "FREEZE": "预扣款"}
    status_map = {"FROZEN": "已冻结", "CONFIRMED": "已完成", "CANCELLED": "已取消", "EXPIRED": "已过期"}

    res = []
    for t in transactions:
        res.append({
            "order_no": t["order_no"],
            "type": t["transaction_type"],
            "type_name": type_map.get(t["transaction_type"], t["transaction_type"]),
            "status": t.get("status", ""),
            "status_name": status_map.get(t.get("status"), t.get("status")),
            "amount": float(t["amount"]),
            "before_balance": float(t["before_balance"]),
            "after_balance": float(t["after_balance"]) if t.get("after_balance") else None,
            "description": t.get("description", ""),
            "created_at": t["created_at"].isoformat() + "Z" if hasattr(t["created_at"], "isoformat") else t["created_at"],
            "completed_at": t["completed_at"].isoformat() + "Z" if t.get("completed_at") else None
        })

    return {"code": 0, "message": "success", "data": {"total": len(res), "transactions": res}}

# ==================== 管理员接口（补全权限） ====================
@router.post("/admin/packages")
async def create_package(
    package_data: RechargePackageCreate,
    current_user: User = Depends(get_current_user)
):
    if not current_user.is_admin:
        raise HTTPException(403, "无管理员权限")
    pkg = await recharge_package_service.create_package(package_data)
    return {"code": 0, "message": "创建成功", "data": pkg.dict()}

@router.put("/admin/packages/{package_id}")
async def update_package(
    package_id: str,
    update_data: RechargePackageUpdate,
    current_user: User = Depends(get_current_user)
):
    if not current_user.is_admin:
        raise HTTPException(403, "无管理员权限")
    pkg = await recharge_package_service.update_package(package_id, update_data)
    if not pkg:
        raise HTTPException(404, "套餐不存在")
    return {"code": 0, "message": "更新成功", "data": pkg.dict()}

@router.delete("/admin/packages/{package_id}")
async def delete_package(
    package_id: str,
    hard: bool = Query(False),
    current_user: User = Depends(get_current_user)
):
    if not current_user.is_admin:
        raise HTTPException(403, "无管理员权限")
    if hard:
        await recharge_package_service.hard_delete_package(package_id)
    else:
        await recharge_package_service.delete_package(package_id)
    return {"code": 0, "message": "操作成功"}