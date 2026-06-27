"""
API router for ReportChatAgent

Endpoints:
  POST   /api/report-chat/start          → 创建新会话（需认证，检查算力）
  POST   /api/report-chat/message         → 发送消息（需认证，按 token 扣算力）
  GET    /api/report-chat/state/{id}      → 查询会话状态
  GET    /api/report-chat/conversations    → 列出用户会话（需认证）
  DELETE /api/report-chat/conversation/{id} → 删除会话（需认证）
  GET    /api/report-chat/models           → 列出可用模型

算力说明：
  - 启动会话：可用余额 >= 5⚡ 即可创建（不冻结）
  - 发送消息：发前冻结预估费用 → 发完后从冻结中扣除，冻结不够则从余额补扣
  - 删除会话：直接删除，无冻结押金需解冻

返回格式统一为 {success, data, message, timestamp}
"""

import asyncio
import logging
import uuid
from decimal import Decimal
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.response import ok, fail
from app.agents.report_chat_agent import get_report_chat_agent
from app.models.user import User
from app.routers.auth_db import get_current_user
from app.services.power_account_service import power_account_service
from app.utils.mongodb_report_manager import mongodb_report_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["report-chat"])

# ── 算力常量 ──────────────────────────────────────────────
MIN_START_BALANCE = Decimal("5")       # 启动会话最低可用余额 & 单次消息冻结金额
TOKEN_COST_RATE = Decimal("0.0002")    # 每 token 单价（⚡）


# ── Pydantic 请求模型 ──


class StartRequest(BaseModel):
    analysis_id: str


class MessageRequest(BaseModel):
    conversation_id: str
    message: str


# ── 工具函数 ──


def _make_user_obj(user: dict) -> User:
    """从认证接口返回的 dict 构造 User 对象（供 power_account_service 使用）"""
    d = user.copy()
    d["hashed_password"] = "dummy"
    return User.model_validate(d)


def _calc_cost(total_tokens: int) -> Decimal:
    """根据 token 数计算实际费用，最低 0.01⚡"""
    cost = Decimal(str(total_tokens)) * TOKEN_COST_RATE * 2
    return max(cost, Decimal("0.01"))


# ── 端点 ──


@router.post("/start")
async def start_conversation(
    req: StartRequest,
    user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """创建新的报告对话会话。检查可用余额 >= 5⚡（不冻结资金）。"""
    user_obj = _make_user_obj(user)

    # 1. 检查可用余额
    balance_info = await power_account_service.get_balance(user_obj)
    available = balance_info.get("available", Decimal("0"))
    if available < MIN_START_BALANCE:
        raise HTTPException(
            status_code=400,
            detail=f"算力不足，需要至少 {MIN_START_BALANCE}⚡ 才能开始对话（当前可用: {available:.2f}⚡）",
        )

    # 2. 创建会话
    agent = get_report_chat_agent()
    conv_id = await agent.start_conversation(req.analysis_id, str(user["id"]))

    # 3. 从报告获取股票名称/代码并保存到会话
    try:
        report_data = await asyncio.to_thread(
            mongodb_report_manager.get_report_by_id, req.analysis_id
        )
        if report_data:
            session = await agent.session_store.get_session(conv_id)
            if session:
                session["stock_name"] = report_data.get("stock_name", "")
                session["stock_symbol"] = report_data.get("stock_symbol", "")
                await agent.session_store.set_session(conv_id, session)
    except Exception as e:
        logger.warning(f"保存股票信息到会话失败: {e}")

    logger.info(
        f"✅ 报告对话已创建 | conversation_id={conv_id} user={user['id']}"
    )
    return ok({
        "conversation_id": conv_id,
        "analysis_id": req.analysis_id,
    })


@router.post("/message")
async def send_message(
    req: MessageRequest,
    user: dict = Depends(get_current_user),
) -> Any:
    """发送消息并获取 AI 回复。先冻结 5⚡，发完后部分确认消费，剩余自动解冻。"""
    user_obj = _make_user_obj(user)

    # 1. 冻结 MIN_START_BALANCE（余额不足时 freeze 内部会拒绝）
    freeze_amount = MIN_START_BALANCE
    freeze_order_no = f"CHAT_MSG_FREEZE_{req.conversation_id}_{uuid.uuid4().hex[:8]}"
    freeze_ok, freeze_msg = await power_account_service.freeze(
        user=user_obj,
        order_no=freeze_order_no,
        amount=freeze_amount,
        description=f"报告消息预估冻结-{req.conversation_id[:12]}",
        metadata={
            "conversation_id": req.conversation_id,
            "action": "message_freeze",
        },
    )
    if not freeze_ok:
        raise HTTPException(
            status_code=400,
            detail=f"算力冻结失败: {freeze_msg}",
        )

    # 3. 处理消息
    agent = get_report_chat_agent()
    try:
        res = await agent.handle_message(req.conversation_id, req.message)
        if "error" in res:
            raise HTTPException(status_code=404, detail=res["error"])
    except HTTPException:
        raise
    except Exception as e:
        # 处理异常 → 解冻
        await power_account_service.cancel_consume(
            freeze_order_no, reason="消息处理异常"
        )
        logger.error(f"消息处理异常, 已解冻 {freeze_amount}⚡: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"消息处理失败: {e}")

    # 4. 计算实际费用
    tokens = res.get("tokens", {})
    total_tokens = tokens.get("total", 0)
    actual_cost = _calc_cost(total_tokens)

    # 5. 结算：部分确认消费 — 只扣实际消耗，剩余自动解冻
    if actual_cost <= freeze_amount:
        p_ok, p_msg = await power_account_service.partial_confirm_consume(
            freeze_order_no, actual_cost
        )
        if not p_ok:
            logger.warning(f"部分确认消费失败: {p_msg}（fallback 全确认）")
            await power_account_service.confirm_consume(freeze_order_no)
    else:
        # 实际费用超过冻结 → 全确认 + 补扣差额
        await power_account_service.confirm_consume(freeze_order_no)
        remainder = actual_cost - freeze_amount
        extra_order_no = f"CHAT_MSG_EXTRA_{req.conversation_id}_{uuid.uuid4().hex[:8]}"
        extra_ok, extra_msg = await power_account_service.consume(
            user=user_obj,
            order_no=extra_order_no,
            amount=remainder,
            description=f"报告对话补扣-{req.conversation_id[:12]}",
            metadata={
                "conversation_id": req.conversation_id,
                "total_tokens": total_tokens,
                "deduct_type": "remainder",
            },
        )
        if not extra_ok:
            logger.warning(
                f"补扣差额失败: {extra_msg}（remainder={remainder}）"
            )

    # 6. 获取更新后的余额
    balance_info = await power_account_service.get_balance(user_obj)

    logger.info(
        f"✅ 消息处理完成 | conversation_id={req.conversation_id} "
        f"tokens={total_tokens} cost={actual_cost:.4f}⚡ "
        f"frozen={freeze_amount}⚡ "
        f"available={balance_info.get('available', 0):.2f}⚡"
    )

    return ok({
        "reply": res["reply"],
        "contexts": res.get("contexts", []),
        "tokens": tokens,
        "tool_calls": res.get("tool_calls"),
        "conversation_id": req.conversation_id,
        "cost": {
            "amount": float(actual_cost),
            "unit": "⚡",
            "total_tokens": total_tokens,
            "rate": float(TOKEN_COST_RATE),
        },
        "balance": {
            "available": float(balance_info.get("available", 0)),
            "frozen": float(balance_info.get("frozen", 0)),
        },
    })


@router.get("/state/{conversation_id}")
async def get_state(
    conversation_id: str,
    user: dict = Depends(get_current_user),
) -> Any:
    """获取会话状态，包含历史轮次、token 统计等。"""
    agent = get_report_chat_agent()
    session = await agent.get_conversation_state(conversation_id)
    if not session:
        raise HTTPException(status_code=404, detail="conversation_not_found")

    # 验证归属
    if session.get("user_id") != str(user["id"]):
        raise HTTPException(status_code=403, detail="无权访问此会话")

    history = session.get("history", [])
    return ok({
        "conversation_id": conversation_id,
        "analysis_id": session.get("analysis_id"),
        "stock_name": session.get("stock_name", ""),
        "stock_symbol": session.get("stock_symbol", ""),
        "last_user_message": session.get("last_user_message", ""),
        "tokens_used": session.get("tokens_used", 0),
        "rounds": len(history),
        "created_at": session.get("created_at", ""),
        "recent_messages": [
            {"role": m.get("role"), "text_preview": m.get("text", "")[:200]}
            for m in history[-4:]
        ],
    })


@router.get("/conversations")
async def list_conversations(
    user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
) -> Any:
    """列出当前用户的报告对话会话（分页，按创建时间倒序）。"""
    try:
        from app.core.redis_client import get_redis_service

        redis_svc = get_redis_service()
        pattern = "session:*"
        all_keys = await redis_svc.redis.keys(pattern)

        conversations = []
        for key in all_keys:
            data = await redis_svc.get_json(key)
            if data and data.get("user_id") == str(user["id"]):
                conv_id = data.get("conversation_id", "")
                conversations.append({
                    "conversation_id": conv_id,
                    "analysis_id": data.get("analysis_id"),
                    "stock_name": data.get("stock_name", ""),
                    "stock_symbol": data.get("stock_symbol", ""),
                    "last_user_message": data.get("last_user_message", ""),
                    "rounds": len(data.get("history", [])),
                    "tokens_used": data.get("tokens_used", 0),
                    "created_at": data.get("created_at", ""),
                })

        # 按创建时间倒序排（最近的在前）
        conversations.sort(key=lambda c: c.get("created_at", ""), reverse=True)
        total = len(conversations)
        start = (page - 1) * page_size
        paged = conversations[start : start + page_size]
        return ok({
            "conversations": paged,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total > 0 else 0,
        })
    except Exception as e:
        logger.warning(f"列出会话失败: {e}")
        return ok({"conversations": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0})


@router.delete("/conversation/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    user: dict = Depends(get_current_user),
) -> Any:
    """删除指定会话（无冻结押金需处理）。"""
    try:
        from app.core.redis_client import RedisKeys, get_redis_service

        redis_svc = get_redis_service()
        key = RedisKeys.USER_SESSION.format(session_id=conversation_id)
        data = await redis_svc.get_json(key)

        # 验证归属
        if data and data.get("user_id") != str(user["id"]):
            raise HTTPException(status_code=403, detail="无权删除此会话")

        await redis_svc.redis.delete(key)
        logger.info(
            f"🗑️ 会话已删除 | conversation_id={conversation_id} user={user['id']}"
        )
        return ok({"conversation_id": conversation_id, "deleted": True})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除会话失败: {e}")


@router.get("/models")
async def list_models() -> Any:
    """列出系统中可用于对话的模型。"""
    from app.services.llm_client import get_available_models

    models = get_available_models()
    return ok({"models": models})
