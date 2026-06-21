"""
Session store backed by app.core.redis_client.RedisService.
Provides simple conversation session management functions used by ReportChatAgent.
"""

import json
import uuid
from typing import Optional, Dict, Any
import logging

from app.core.redis_client import get_redis_service, RedisKeys

logger = logging.getLogger(__name__)


class SessionStore:
    def __init__(self):
        self._rs = get_redis_service()

    async def create_session(self, analysis_id: str, user_id: str) -> str:
        conversation_id = str(uuid.uuid4())
        from datetime import datetime
        data = {
            "conversation_id": conversation_id,
            "analysis_id": analysis_id,
            "user_id": user_id,
            "history": [],
            "tokens_used": 0,
            "created_at": datetime.utcnow().isoformat(),
        }
        key = RedisKeys.USER_SESSION.format(session_id=conversation_id)
        await self._rs.set_json(key, data)
        return conversation_id

    async def get_session(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        key = RedisKeys.USER_SESSION.format(session_id=conversation_id)
        return await self._rs.get_json(key)

    async def set_session(self, conversation_id: str, data: Dict[str, Any]):
        key = RedisKeys.USER_SESSION.format(session_id=conversation_id)
        await self._rs.set_json(key, data)

    async def append_message(self, conversation_id: str, message: Dict[str, Any], max_history: int = 100):
        session = await self.get_session(conversation_id)
        if not session:
            return False
        hist = session.get("history", [])
        hist.append(message)
        if len(hist) > max_history:
            hist = hist[-max_history:]
        session["history"] = hist
        await self.set_session(conversation_id, session)
        return True

    async def increment_tokens(self, conversation_id: str, tokens: int):
        session = await self.get_session(conversation_id)
        if not session:
            return False
        session["tokens_used"] = session.get("tokens_used", 0) + int(tokens)
        await self.set_session(conversation_id, session)
        return True


_session_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store
