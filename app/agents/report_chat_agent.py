"""
ReportChatAgent：对话式报告问答智能体

功能：
1. 通过 analysis_id 从 MongoDB analysis_reports 集合加载报告
2. 自动切分报告并生成 FAISS 向量库（已存在则跳过）
3. 支持多轮对话，维护对话历史（Redis 存储）
4. 集成可扩展工具：查新闻 / 查行情 / 查资金
5. 精确追踪每次对话的 input_tokens / output_tokens 消耗
6. 手工构建 prompt 直接调用 LLM（轻量，不依赖 LangChain chain 类）
   可选通过 langchain_adapter.py 使用 LCEL 方式

架构：
    FastAPI Router → ReportChatAgent.handle_message()
        ├─ ReportVectorStoreBuilder (FAISS 向量库检索)
        ├─ call_llm() (通过 create_llm_by_provider 构建)
        ├─ ToolRegistry (可扩展工具集)
        └─ TokenTracker (输入/输出分开计数)
    备注：不依赖已废弃的 RetrievalQA / ConversationalRetrievalChain
"""

import asyncio
import logging
import os
import time
from typing import List, Dict, Any, Optional, Tuple

from app.core.response import ok, fail
from web.utils.mongodb_report_manager import mongodb_report_manager
from app.services.llm_client import call_llm, embed_texts
from app.services.session_store import get_session_store

logger = logging.getLogger(__name__)


def _is_float_str(s: str) -> bool:
    """判断字符串是否为合法的浮点数格式。"""
    try:
        float(s)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Token 追踪器
# ---------------------------------------------------------------------------


class TokenTracker:
    """按角色追踪 token 消耗（input / output）。"""

    def __init__(self):
        self.input_tokens: int = 0
        self.output_tokens: int = 0

    def add_input(self, n: int):
        self.input_tokens += n

    def add_output(self, n: int):
        self.output_tokens += n

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def snapshot(self) -> Dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "total": self.total}


# ---------------------------------------------------------------------------
# 报告文本切分器
# ---------------------------------------------------------------------------


class ReportTextSplitter:
    """将报告文本切分为适合向量化的块。"""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split_text(self, text: str) -> List[str]:
        """按段落 + 滑动窗口切分。"""
        pieces: List[str] = []
        for para in text.split("\n\n"):
            p = para.strip()
            if not p:
                continue
            if len(p) <= self.chunk_size:
                pieces.append(p)
            else:
                # 按句号切分后滑动拼接
                cur = ""
                for seg in p.split("。"):
                    seg = seg.strip()
                    if not seg:
                        continue
                    seg += "。"
                    if len(cur) + len(seg) <= self.chunk_size:
                        cur += seg
                    else:
                        if cur:
                            pieces.append(cur)
                        cur = seg
                if cur:
                    pieces.append(cur)
        return pieces


# ---------------------------------------------------------------------------
# RAG 向量库构建器
# ---------------------------------------------------------------------------


class ReportVectorStoreBuilder:
    """从 analysis_reports 文档构建 FAISS 向量库。"""

    def __init__(self):
        self._store = None  # lazy init

    @property
    def store(self):
        if self._store is None:
            from app.services.vector_store.faiss_store import FaissStore
            self._store = FaissStore()
        return self._store

    async def ensure_index(self, analysis_id: str) -> bool:
        """确保向量库存在，不存在则构建。"""
        if self.store.exists(analysis_id):
            logger.info(f"[ReportChatAgent] 向量库已存在: {analysis_id}")
            return True
        logger.info(f"[ReportChatAgent] 构建向量库: {analysis_id}")
        return await self._build_index(analysis_id)

    async def _build_index(self, analysis_id: str) -> bool:
        """从 MongoDB 加载报告 → 切分 → Embedding → 存储。"""
        loop = asyncio.get_event_loop()

        # 1. 加载报告
        doc = await loop.run_in_executor(None, mongodb_report_manager.get_report_by_id, analysis_id)
        if not doc:
            logger.warning(f"[ReportChatAgent] 报告未找到: {analysis_id}")
            return False

        # 2. 提取 reports 字段
        reports = doc.get("reports", {})
        raw_texts: List[str] = []
        if isinstance(reports, dict):
            for k, v in reports.items():
                if isinstance(v, str):
                    raw_texts.append(v)
                elif isinstance(v, list):
                    for item in v:
                        raw_texts.append(str(item))
        elif isinstance(reports, str):
            raw_texts.append(reports)

        # 3. 切分
        splitter = ReportTextSplitter()
        chunks: List[str] = []
        seen: set = set()
        for t in raw_texts:
            for c in splitter.split_text(t):
                key = c.strip()[:200]
                if key and key not in seen:
                    seen.add(key)
                    chunks.append(c)

        if not chunks:
            logger.warning(f"[ReportChatAgent] 报告无有效文本: {analysis_id}")
            return False

        # 4. 生成向量
        vectors = await embed_texts(chunks)
        metadatas = [
            {"text": chunks[i], "source": analysis_id, "chunk_id": i, "stock_symbol": doc.get("stock_symbol", "")}
            for i in range(len(chunks))
        ]

        # 5. 存储
        self.store.create_index(analysis_id, vectors, metadatas)
        logger.info(f"[ReportChatAgent] 向量库构建完成: {analysis_id} ({len(chunks)} chunks)")
        return True


# ---------------------------------------------------------------------------
# 工具包装器（LangChain Tool 格式）
# ---------------------------------------------------------------------------


class ToolRegistry:
    """注册并管理 ReportChatAgent 可使用的工具。支持按名称检索。"""

    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, description: str, func) -> None:
        self._tools[name] = {"name": name, "description": description, "func": func}

    def get_descriptions(self) -> str:
        """生成工具描述文本，注入 system prompt。"""
        if not self._tools:
            return ""
        lines = ["可用工具："]
        for t in self._tools.values():
            lines.append(f"  - {t['name']}: {t['description']}")
        return "\n".join(lines)

    async def execute(self, name: str, **kwargs) -> str:
        """执行指定工具，返回格式化结果。"""
        tool = self._tools.get(name)
        if not tool:
            return f"工具 '{name}' 不存在"
        try:
            result = await tool["func"](**kwargs)
            if isinstance(result, str):
                return result
            import json
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning(f"工具 {name} 执行失败: {e}")
            return f"工具 {name} 执行出错: {e}"


def build_default_tools() -> ToolRegistry:
    """构建默认工具集。"""
    registry = ToolRegistry()

    from app.services.tools.news_tool import NewsTool
    from app.services.tools.historical_tool import HistoricalTool
    from app.services.tools.position_calc_tool import PositionCalcTool
    from app.services.tools.ema_penetration_tool import EmaPenetrationTool

    news = NewsTool()
    hist = HistoricalTool()
    pos_calc = PositionCalcTool()
    ema_pen = EmaPenetrationTool()

    registry.register(
        "fetch_news",
        "获取指定股票的近期新闻。参数: symbol(股票代码), limit(条数,默认5), hours_back(回溯小时,默认1个月的)",
        lambda symbol="", limit=5, hours_back=24*7*4: news.fetch_news(symbol or None, limit=limit, hours_back=hours_back),
    )
    registry.register(
        "fetch_historical",
        "获取指定股票的历史行情数据。参数: symbol(股票代码), period(周期,daily/weekly/monthly,默认daily), limit(条数,默认30)",
        lambda symbol, period="daily", limit=256: hist.fetch_historical(symbol, period=period, limit=limit),
    )
    registry.register(
        "calc_trade_size",
        "资金管理仓位计算工具，根据买入价、止损价、风险系数、总资金计算安全可交易股数。参数: ep(买入价), sp(止损价), rcf(风险系数，0.02代表单笔亏损上限为总资金2%), tc(账户总资金)",
        # 同步计算函数包装异步执行器，适配框架await
        lambda ep, sp, rcf, tc: __import__("asyncio").get_event_loop().run_in_executor(None, pos_calc.calc_trade_size, ep, sp, rcf, tc),
    )
    registry.register(
        "calc_ema_penetration",
        "EMA穿透买入策略分析工具，基于均线穿透方法计算建议买入价。参数: symbol(6位股票代码), fast_ema(快EMA周期,默认13), slow_ema(慢EMA周期,默认26), lookback_period(穿透回溯天数,默认30)",
        lambda symbol, fast_ema=13, slow_ema=26, lookback_period=30: ema_pen.analyze(symbol, fast_ema=fast_ema, slow_ema=slow_ema, lookback_period=lookback_period),
    )
    # ── 可在此处扩展更多工具 ──

    return registry


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_ZH = """你是一个专业的金融报告分析助手 ReportChatAgent。

你基于以下信息回答用户问题：
1. 分析报告上下文（通过向量检索得到的报告片段）
2. 对话历史
3. 你可以使用工具获取最新的新闻、行情和资金数据

回答原则：
- 基于报告内容回答，不要编造信息
- 如果需要最新数据，主动使用工具获取
- 回答简洁专业，用中文回复
- 如果信息不足，明确说明
"""


def build_prompt(
    message: str,
    contexts: List[str],
    history: List[Dict[str, Any]],
    tool_descriptions: str,
) -> str:
    """构建完整 prompt。"""
    parts = [SYSTEM_PROMPT_ZH, ""]

    # 工具描述
    if tool_descriptions:
        parts.append(tool_descriptions)
        parts.append("")

    # 报告上下文
    if contexts:
        parts.append("以下是与问题相关的报告片段：")
        for i, c in enumerate(contexts):
            parts.append(f"[片段 {i + 1}] {c}")
        parts.append("")

    # 对话历史
    if history:
        parts.append("对话历史：")
        for turn in history[-8:]:  # 保留最近 8 轮
            role = turn.get("role", "user")
            text = turn.get("text", "")
            parts.append(f"{role}: {text}")
        parts.append("")

    # 当前问题
    parts.append(f"用户: {message}")
    parts.append("助手:")
    return "\n".join(parts)


def build_tool_call_prompt(
    message: str,
    contexts: List[str],
    history: List[Dict[str, Any]],
    tool_descriptions: str,
) -> str:
    """构建含工具调用指令的 prompt。"""
    prompt = build_prompt(message, contexts, history, tool_descriptions)
    prompt += "\n\n（如果需要查询最新数据，请用以下格式调用工具：\n@工具名(参数名=值, ...)\n例如：@fetch_news(symbol=000001, limit=5)\n然后我会帮你执行并返回结果。）"
    return prompt


# ---------------------------------------------------------------------------
# ReportChatAgent 主类
# ---------------------------------------------------------------------------


class ReportChatAgent:
    """基于 LangChain 理念构建的报告对话智能体。"""

    def __init__(self, session_store=None):
        self.vector_builder = ReportVectorStoreBuilder()
        self.session_store = session_store or get_session_store()
        self.tools = build_default_tools()
        self._splitter = ReportTextSplitter()

    # ── 公开接口 ──

    async def handle_message(
        self,
        conversation_id: str,
        message: str,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """处理用户消息，返回回复。

        Args:
            conversation_id: 会话 ID
            message: 用户输入文本
            top_k: 检索的最相关片段数

        Returns:
            dict: {reply, contexts, tokens: {input_tokens, output_tokens, total}, tool_calls, sources}
        """
        # 1. 获取会话
        session = await self.session_store.get_session(conversation_id)
        if not session:
            return {"error": "conversation_not_found"}
        analysis_id = session.get("analysis_id")
        if not analysis_id:
            return {"error": "analysis_id_missing"}

        # 2. 确保向量库存在
        await self.vector_builder.ensure_index(analysis_id)

        # 3. 检索相关片段
        contexts = await self._retrieve_contexts(analysis_id, message, top_k)

        # 4. 获取历史
        history = session.get("history", [])

        # 5. 检查是否有工具调用意图
        tool_descriptions = self.tools.get_descriptions()
        tool_call, tool_params = self._parse_tool_call(message)

        tracker = TokenTracker() 
        reply = ""
        tool_results = []

        if tool_call:
            # ── 工具调用模式 ──
            result_str = await self.tools.execute(tool_call, **tool_params)
            tool_results.append({"tool": tool_call, "params": tool_params, "result": result_str})
            # 将工具结果作为上下文重新生成回复
            tool_context = f"[工具 {tool_call} 返回]: {result_str}"
            prompt = build_prompt(message, contexts + [tool_context], history, tool_descriptions)
            reply, token_count = await call_llm(prompt)
            tracker.add_input(max(1, len(prompt) // 4))
            tracker.add_output(max(1, len(reply) // 4))
        else:
            # ── 标准 RAG 问答模式 ──
            prompt = build_prompt(message, contexts, history, tool_descriptions)
            reply, token_count = await call_llm(prompt)
            tracker.add_input(max(1, len(prompt) // 4))
            tracker.add_output(max(1, len(reply) // 4))

        # 6. 持久化对话
        await self.session_store.append_message(conversation_id, {"role": "user", "text": message})
        await self.session_store.append_message(conversation_id, {"role": "assistant", "text": reply})
        await self.session_store.increment_tokens(conversation_id, tracker.total)

        return {
            "reply": reply,
            "contexts": contexts[:3],  # 返回最相关的 3 个
            "tokens": tracker.snapshot(),
            "tool_calls": tool_results if tool_results else None,
        }

    async def start_conversation(
        self,
        analysis_id: str,
        user_id: str,
    ) -> str:
        """启动新会话，预构建向量库（后台）。"""
        conversation_id = await self.session_store.create_session(analysis_id, user_id)

        # 后台构建向量库
        async def _build_bg():
            try:
                await self.vector_builder.ensure_index(analysis_id)
            except Exception as e:
                logger.warning(f"[ReportChatAgent] 后台构建向量库失败: {e}")

        asyncio.create_task(_build_bg())
        return conversation_id

    async def get_conversation_state(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """获取会话状态。"""
        return await self.session_store.get_session(conversation_id)

    # ── 内部方法 ──

    async def _retrieve_contexts(self, analysis_id: str, query: str, top_k: int) -> List[str]:
        """向量检索相关片段。"""
        try:
            qvec = (await embed_texts([query]))[0]
            results = self.vector_builder.store.search(analysis_id, qvec, top_k=top_k)
            return [r["metadata"]["text"] for r in results if r.get("metadata", {}).get("text")]
        except Exception as e:
            logger.warning(f"[ReportChatAgent] 检索失败: {e}")
            return []

    def _parse_tool_call(self, message: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """解析用户消息中的工具调用意图。

        格式：@工具名(参数=值, ...)
        例如：@fetch_news(symbol=000001, limit=5)
        """
        import re
        pattern = r"@(\w+)\(([^)]*)\)"
        m = re.search(pattern, message)
        if not m:
            return None, {}
        tool_name = m.group(1)
        params_str = m.group(2)
        params: Dict[str, Any] = {}
        if params_str.strip():
            for pair in params_str.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("\"'")
                    # 安全数值转换：前导零不转 int（避免股票代码 "000001" → 1）
                    if v.isdigit() and not v.startswith("0"):
                        params[k] = int(v)
                    elif _is_float_str(v) and not v.startswith("0"):
                        params[k] = float(v)
                    else:
                        params[k] = v
        if tool_name in self.tools._tools:
            return tool_name, params
        return None, {}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_report_chat_agent: Optional[ReportChatAgent] = None


def get_report_chat_agent() -> ReportChatAgent:
    global _report_chat_agent
    if _report_chat_agent is None:
        _report_chat_agent = ReportChatAgent()
    return _report_chat_agent
