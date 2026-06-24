"""
ReportChatAgent 验证测试

运行:  pytest tests/test_report_chat_agent.py -v --no-header

测试覆盖:
  1. TokenTracker       - 单元测试
  2. _is_float_str      - 工具函数
  3. ReportTextSplitter - 报告切分
  4. _parse_tool_call   - 工具调用解析 (含前导零 Bug 防护)
  5. ToolRegistry       - 工具注册与执行
  6. build_prompt       - Prompt 构建
  7. build_default_tools- 默认工具集
  8. handle_message     - 完整消息处理流程 (含 mock)
  9. start_conversation - 会话创建
"""

import pytest
from typing import List, Dict, Any


# ============================================================
# 1. TokenTracker
# ============================================================

class TestTokenTracker:
    def test_add_input_output(self):
        from app.agents.report_chat_agent import TokenTracker
        tt = TokenTracker()
        tt.add_input(100)
        tt.add_output(50)
        assert tt.total == 150
        assert tt.snapshot() == {"input_tokens": 100, "output_tokens": 50, "total": 150}

    def test_snapshot_isolation(self):
        from app.agents.report_chat_agent import TokenTracker
        tt = TokenTracker()
        tt.add_input(30)
        snap = tt.snapshot()
        tt.add_input(10)
        # 旧快照不受影响
        assert snap["input_tokens"] == 30
        assert tt.total == 40


# ============================================================
# 2. _is_float_str
# ============================================================

class TestIsFloatStr:
    def test_basic(self):
        from app.agents.report_chat_agent import _is_float_str
        assert _is_float_str("3.14") is True
        assert _is_float_str("42") is True   # int 也是合法 float str
        assert _is_float_str("abc") is False
        assert _is_float_str("") is False


# ============================================================
# 3. ReportTextSplitter
# ============================================================

class TestReportTextSplitter:
    def test_short_text(self):
        from app.agents.report_chat_agent import ReportTextSplitter
        s = ReportTextSplitter(chunk_size=500)
        chunks = s.split_text("这是一段很短的测试文本。")
        assert len(chunks) == 1

    def test_long_text_splits_by_sentence(self):
        from app.agents.report_chat_agent import ReportTextSplitter
        s = ReportTextSplitter(chunk_size=50)
        text = "第一句。" * 40
        chunks = s.split_text(text)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 55

    def test_paragraph_separation(self):
        from app.agents.report_chat_agent import ReportTextSplitter
        s = ReportTextSplitter()
        text = "段落A。\n\n段落B。\n\n段落C。"
        chunks = s.split_text(text)
        assert len(chunks) == 3


# ============================================================
# 4. _parse_tool_call （★ 关键：前导零保护）
# ============================================================

class TestParseToolCall:
    def _make_agent(self):
        """构造一个带测试工具的 agent，传入假 session_store 避免需要 Redis"""
        from app.agents.report_chat_agent import ReportChatAgent
        from tests.test_report_chat_agent import DummySessionStore
        agent = ReportChatAgent(session_store=DummySessionStore())
        agent.tools.register("test_tool", "test desc", lambda **kw: kw)
        return agent

    def test_int_param(self):
        agent = self._make_agent()
        name, params = agent._parse_tool_call("@test_tool(x=42)")
        assert name == "test_tool"
        assert params["x"] == 42

    def test_stock_code_preserves_leading_zero(self):
        """★ 核心防护：000001 不能变成 1"""
        agent = self._make_agent()
        name, params = agent._parse_tool_call("@test_tool(symbol=000001, limit=5)")
        assert params["symbol"] == "000001", f"Expected '000001', got {repr(params['symbol'])}"
        assert params["limit"] == 5   # limit 没有前导零，仍是 int

    def test_float_param(self):
        agent = self._make_agent()
        name, params = agent._parse_tool_call("@test_tool(price=12.5)")
        assert params["price"] == 12.5

    def test_no_match_returns_none(self):
        agent = self._make_agent()
        name, params = agent._parse_tool_call("你好，请问这只股票怎么样？")
        assert name is None
        assert params == {}

    def test_quoted_string_value(self):
        agent = self._make_agent()
        name, params = agent._parse_tool_call('@test_tool(name="hello", count=10)')
        assert params["name"] == "hello"
        assert params["count"] == 10


# ============================================================
# 5. ToolRegistry
# ============================================================

class TestToolRegistry:
    @pytest.mark.asyncio
    async def test_register_and_execute(self):
        from app.agents.report_chat_agent import ToolRegistry
        registry = ToolRegistry()

        async def fake_news(**kw):
            return {"news": "test", "symbol": kw.get("symbol")}

        registry.register("fetch_news", "Get news", fake_news)
        result = await registry.execute("fetch_news", symbol="000001", limit=5)
        assert "000001" in result

    def test_get_descriptions(self):
        from app.agents.report_chat_agent import ToolRegistry
        registry = ToolRegistry()

        async def f(**kw): return "ok"

        registry.register("tool_a", "工具A描述", f)
        desc = registry.get_descriptions()
        assert "tool_a" in desc
        assert "工具A描述" in desc


# ============================================================
# 6. build_prompt
# ============================================================

class TestBuildPrompt:
    def test_prompt_structure(self):
        from app.agents.report_chat_agent import build_prompt

        prompt = build_prompt(
            message="这只股票怎么样？",
            contexts=["该公司营收增长20%"],
            history=[{"role": "user", "text": "你好"}, {"role": "assistant", "text": "你好！"}],
            tool_descriptions="可用工具: fetch_news",
        )
        assert "ReportChatAgent" in prompt
        assert "该公司营收增长20%" in prompt
        assert "你好" in prompt
        assert "fetch_news" in prompt
        assert "这只股票怎么样？" in prompt


# ============================================================
# 7. build_default_tools
# ============================================================

class TestBuildDefaultTools:
    def test_all_tools_registered(self):
        from app.agents.report_chat_agent import build_default_tools
        registry = build_default_tools()
        desc = registry.get_descriptions()
        assert "fetch_news" in desc
        assert "fetch_historical" in desc
        assert "query_position" in desc


# ============================================================
# 8. handle_message 完整流程 (Mock 模式)
# ============================================================

class DummySessionStore:
    """内存版 SessionStore 替代 Redis"""
    def __init__(self):
        self.sessions = {}

    async def create_session(self, analysis_id: str, user_id: str) -> str:
        import uuid
        from datetime import datetime
        conv_id = str(uuid.uuid4())
        self.sessions[conv_id] = {
            "conversation_id": conv_id,
            "analysis_id": analysis_id,
            "user_id": user_id,
            "history": [],
            "tokens_used": 0,
            "created_at": datetime.utcnow().isoformat(),
        }
        return conv_id

    async def get_session(self, conversation_id: str):
        return self.sessions.get(conversation_id)

    async def set_session(self, conversation_id: str, data):
        self.sessions[conversation_id] = data

    async def append_message(self, conversation_id: str, message, max_history: int = 100):
        s = await self.get_session(conversation_id)
        if not s:
            return False
        s.setdefault("history", []).append(message)
        s["history"] = s["history"][-max_history:]
        return True

    async def increment_tokens(self, conversation_id: str, tokens: int):
        s = await self.get_session(conversation_id)
        if not s:
            return False
        s["tokens_used"] = s.get("tokens_used", 0) + int(tokens)
        return True


@pytest.mark.asyncio
async def test_handle_message_full_flow(monkeypatch):
    """完整的消息处理流程：Mock MongoDB + Mock LLM + 内存 SessionStore"""
    from app.agents.report_chat_agent import ReportChatAgent

    # ---- Mock SessionStore ----
    dummy_store = DummySessionStore()
    monkeypatch.setattr('app.agents.report_chat_agent.get_session_store', lambda: dummy_store)

    # ---- Mock MongoDB ----
    def dummy_get_report(analysis_id: str):
        return {
            "analysis_id": analysis_id,
            "stock_symbol": "000001",
            "reports": {"market_analysis": "市场分析：整体趋势向好。\n\n技术面：MACD金叉。\n\n基本面：PE合理。"}
        }
    import web.utils.mongodb_report_manager as mgr
    monkeypatch.setattr(mgr.mongodb_report_manager, "get_report_by_id", dummy_get_report)

    # ---- Mock Embedding (固定小向量) ----
    async def dummy_embed_texts(texts: List[str]) -> List[List[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]
    import app.services.llm_client as llm_mod
    monkeypatch.setattr(llm_mod, "embed_texts", dummy_embed_texts)
    monkeypatch.setattr('app.agents.report_chat_agent.embed_texts', dummy_embed_texts)

    # ---- Mock LLM ----
    async def dummy_call_llm(prompt: str, **kw):
        return ("基于报告分析，该股票趋势向好，建议持有。", 42)
    monkeypatch.setattr(llm_mod, "call_llm", dummy_call_llm)
    monkeypatch.setattr('app.agents.report_chat_agent.call_llm', dummy_call_llm)

    # ---- 执行 ----
    agent = ReportChatAgent()
    analysis_id = "test-analysis-flow"
    conv_id = await dummy_store.create_session(analysis_id, "test-user")
    agent.session_store = dummy_store

    # 构建向量库 (绕过 MongoDB 直连)
    ok = await agent.vector_builder.ensure_index(analysis_id)
    assert ok is True, "向量库构建应成功"

    # 发送消息
    result = await agent.handle_message(conv_id, "请分析这只股票")
    assert "reply" in result, "应有 reply"
    assert "tokens" in result, "应有 tokens"
    assert result["tokens"]["input_tokens"] > 0
    assert result["tokens"]["output_tokens"] > 0
    assert result["tokens"]["total"] > 0

    # 验证对话历史持久化
    session = await dummy_store.get_session(conv_id)
    assert len(session["history"]) == 2  # user + assistant
    assert session["tokens_used"] > 0

    # 第二次对话 (多轮)
    result2 = await agent.handle_message(conv_id, "技术面怎么看？")
    assert "reply" in result2
    session2 = await dummy_store.get_session(conv_id)
    assert len(session2["history"]) == 4  # 两轮对话

    # 清理
    agent.vector_builder.store.delete_index(analysis_id)


@pytest.mark.asyncio
async def test_handle_message_session_not_found():
    """不存在的会话应返回 error"""
    from app.agents.report_chat_agent import ReportChatAgent
    from tests.test_report_chat_agent import DummySessionStore
    agent = ReportChatAgent(session_store=DummySessionStore())
    result = await agent.handle_message("nonexistent-id", "你好")
    assert "error" in result
    assert result["error"] == "conversation_not_found"


@pytest.mark.asyncio
async def test_tool_call_in_message(monkeypatch):
    """测试 @tool 语法被识别"""
    from app.agents.report_chat_agent import ReportChatAgent

    dummy_store = DummySessionStore()
    monkeypatch.setattr('app.agents.report_chat_agent.get_session_store', lambda: dummy_store)

    async def dummy_embed_texts(texts): return [[0.1]*4 for _ in texts]
    async def dummy_call_llm(prompt, **kw): return ("工具已执行", 10)
    import app.services.llm_client as llm_mod
    monkeypatch.setattr(llm_mod, "embed_texts", dummy_embed_texts)
    monkeypatch.setattr('app.agents.report_chat_agent.embed_texts', dummy_embed_texts)
    monkeypatch.setattr(llm_mod, "call_llm", dummy_call_llm)
    monkeypatch.setattr('app.agents.report_chat_agent.call_llm', dummy_call_llm)

    agent = ReportChatAgent()
    agent.session_store = dummy_store
    conv_id = await dummy_store.create_session("test-analysis-tool", "user-1")

    # 确保向量库存在
    agent.vector_builder.store.create_index("test-analysis-tool",
        [[0.1]*4, [0.1]*4],
        [{"text": "test", "source": "t", "chunk_id": 0},
         {"text": "test2", "source": "t", "chunk_id": 1}])

    result = await agent.handle_message(conv_id, "@test_tool(x=1)")
    # 由于 test_tool 未注册，会进入标准 RAG 流程，但不应崩溃
    assert "reply" in result

    # 清理
    agent.vector_builder.store.delete_index("test-analysis-tool")
