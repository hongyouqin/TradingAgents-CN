"""
API router for ReportChatAgent

Endpoints:
  POST   /api/report-chat/start          → 创建新会话
  POST   /api/report-chat/message         → 发送消息
  GET    /api/report-chat/state/{id}      → 查询会话状态（含 token 统计）
  GET    /api/report-chat/conversations?user_id=xxx  → 列出用户会话
  DELETE /api/report-chat/conversation/{id}          → 删除会话
  GET    /api/report-chat/models           → 列出可用模型

返回格式统一为 {success, data, message, timestamp}
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.response import ok, fail
from app.agents.report_chat_agent import get_report_chat_agent

logger = logging.getLogger(__name__)

# 注意：路由前缀 "/api/report-chat" 由 main.py 的 include_router 提供
router = APIRouter(tags=["report-chat"])


# ── Pydantic 请求模型 ──


class StartRequest(BaseModel):
    analysis_id: str
    user_id: str


class MessageRequest(BaseModel):
    conversation_id: str
    message: str


# ── 端点 ──


@router.post("/start")
async def start_conversation(req: StartRequest) -> Dict[str, Any]:
    """创建新的报告对话会话。自动在后台构建向量库。"""
    try:
        agent = get_report_chat_agent()
        conv_id = await agent.start_conversation(req.analysis_id, req.user_id)
        return ok({
            "conversation_id": conv_id,
            "analysis_id": req.analysis_id,
            "user_id": req.user_id,
        })
    except Exception as e:
        logger.error(f"创建会话失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建会话失败: {e}")


@router.post("/message")
async def send_message(req: MessageRequest) -> Any:
    """发送消息并获取 AI 回复。

    返回包含：
      - reply: AI 回复文本
      - contexts: 引用的报告片段
      - tokens: {input_tokens, output_tokens, total}
      - tool_calls: 可选，工具调用记录
    """
    agent = get_report_chat_agent()
    res = await agent.handle_message(req.conversation_id, req.message)
    if "error" in res:
        raise HTTPException(status_code=404, detail=res["error"])
    return ok({
        "reply": res["reply"],
        "contexts": res.get("contexts", []),
        "tokens": res.get("tokens", {}),
        "tool_calls": res.get("tool_calls"),
        "conversation_id": req.conversation_id,
    })


@router.get("/state/{conversation_id}")
async def get_state(conversation_id: str) -> Any:
    """获取会话状态，包含历史轮次、token 统计等。"""
    agent = get_report_chat_agent()
    session = await agent.get_conversation_state(conversation_id)
    if not session:
        raise HTTPException(status_code=404, detail="conversation_not_found")

    # 脱敏：不返回完整历史，只返回概要
    history = session.get("history", [])
    return ok({
        "conversation_id": conversation_id,
        "analysis_id": session.get("analysis_id"),
        "user_id": session.get("user_id"),
        "tokens_used": session.get("tokens_used", 0),
        "rounds": len(history),
        "recent_messages": [
            {"role": m.get("role"), "text_preview": m.get("text", "")[:200]}
            for m in history[-4:]
        ],
    })


@router.get("/conversations")
async def list_conversations(
    user_id: str = Query(..., description="用户 ID"),
    limit: int = Query(20, ge=1, le=100),
) -> Any:
    """列出用户的历史会话（最近 N 条）。"""
    # 由于会话存储在 Redis 中，需要遍历匹配的 key
    try:
        from app.core.redis_client import get_redis_service
        redis_svc = get_redis_service()
        pattern = "session:*"
        all_keys = await redis_svc.redis.keys(pattern)

        conversations = []
        for key in all_keys:
            data = await redis_svc.get_json(key)
            if data and data.get("user_id") == user_id:
                conv_id = data.get("conversation_id", "")
                conversations.append({
                    "conversation_id": conv_id,
                    "analysis_id": data.get("analysis_id"),
                    "rounds": len(data.get("history", [])),
                    "tokens_used": data.get("tokens_used", 0),
                    "created_at": data.get("created_at", ""),
                })

        # 按轮次数降序排列（活跃会话优先）
        conversations.sort(key=lambda c: c["rounds"], reverse=True)
        return ok({"conversations": conversations[:limit], "total": len(conversations)})
    except Exception as e:
        logger.warning(f"列出会话失败: {e}")
        # 降级：返回空列表
        return ok({"conversations": [], "total": 0})


@router.delete("/conversation/{conversation_id}")
async def delete_conversation(conversation_id: str) -> Any:
    """删除指定会话。"""
    try:
        from app.core.redis_client import RedisKeys, get_redis_service
        redis_svc = get_redis_service()
        key = RedisKeys.USER_SESSION.format(session_id=conversation_id)
        await redis_svc.redis.delete(key)
        return ok({"conversation_id": conversation_id, "deleted": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除会话失败: {e}")


@router.get("/models")
async def list_models() -> Any:
    """列出系统中可用于对话的模型。"""
    from app.services.llm_client import get_available_models
    models = get_available_models()
    return ok({"models": models})
