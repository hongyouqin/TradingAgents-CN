"""
LLM 与 Embedding 客户端适配器

基于项目现有基础设施提供：
- call_llm()：通过 create_llm_by_provider 调用任意配置的 LLM
- embed_texts()：通过 langchain_openai 兼容接口生成文本嵌入向量

用法：
    reply, tokens = await call_llm("你好")
    vectors = await embed_texts(["文本1", "文本2"])
"""

import json
import logging
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

_embedding_model = None


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """生成文本嵌入向量。
    使用 langchain_openai OpenAIEmbeddings 连接兼容的 embedding 服务。
    首次调用时自动初始化（使用系统默认 embedding 模型）。
    """
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = _create_embedder()
    try:
        return _embedding_model.embed_documents(texts)
    except Exception as e:
        logger.warning(f"embed_texts 失败: {e}，降级为确定性向量")
        import math
        dim = 128
        results = []
        for text in texts:
            h = hash(text) % 10000
            vec = [float((h + i * 7) % 100) / 100.0 for i in range(dim)]
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            results.append([v / norm for v in vec])
        return results


def _create_embedder():
    """根据系统配置创建 embedding 模型实例。

    优先使用真实 embedding 服务（DashScope text-embedding-v3），
    失败时返回 None，由调用方降级为确定性向量。
    """
    try:
        import os
        from dotenv import load_dotenv
        load_dotenv()  # 确保 .env 已加载到 os.environ

        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        if not api_key:
            api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            logger.warning("未找到 API Key，将使用确定性向量")
            return None

        from app.core.unified_config import unified_config
        cfg = unified_config.get_system_settings()
        embed_model = cfg.get("embedding_model", "text-embedding-v3")
        embed_base_url = cfg.get("embedding_base_url",
                                 "https://dashscope.aliyuncs.com/compatible-mode/v1")

        # ── 方式 1：尝试 DashScope 原生 SDK ──
        try:
            import dashscope
            dashscope.api_key = api_key
            # dashscope 原生 SDK 的 embed 调用
            _embedder = _DashScopeEmbedder(api_key, embed_model)
            # 做个快速验证
            logger.info(f"使用 DashScope SDK embedding: {embed_model}")
            return _embedder
        except ImportError:
            pass

        # ── 方式 2：用 httpx 直接调用兼容 API ──
        logger.info(f"使用 httpx direct embedding: {embed_model}")
        return _HttpEmbedder(api_key, embed_model, embed_base_url)

    except Exception as e:
        logger.warning(f"创建 embedding 模型失败: {e}，将使用确定性向量")
        return None


class _DashScopeEmbedder:
    """基于 DashScope Python SDK 的 Embedding。"""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def embed_documents(self, texts):
        import dashscope
        from dashscope import TextEmbedding
        dashscope.api_key = self.api_key
        resp = TextEmbedding.call(model=self.model, input=texts)
        if resp.status_code == 200:
            return [item['embedding'] for item in resp.output['embeddings']]
        raise RuntimeError(f"DashScope embedding error: {resp}")


class _HttpEmbedder:
    """通过 HTTP 直接调用 OpenAI 兼容 embedding API。"""

    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def embed_documents(self, texts):
        import httpx
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "input": texts,
        }
        resp = httpx.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return [item["embedding"] for item in data["data"]]
        # 某些服务（如 DashScope）需要 input 为 dict 格式
        payload2 = {
            "model": self.model,
            "input": {"contents": texts},
        }
        resp2 = httpx.post(url, headers=headers, json=payload2, timeout=30)
        if resp2.status_code == 200:
            data = resp2.json()
            return [item["embedding"] for item in data["data"]]
        raise RuntimeError(
            f"Embedding API 调用失败 (status={resp.status_code}): {resp.text}"
        )


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------


async def call_llm(
    prompt: str,
    model: Optional[str] = None,
    temperature: float = 0.5,
    max_tokens: int = 4096,
    timeout: int = 180,
) -> Tuple[str, int]:
    """调用 LLM 生成回复。

    Args:
        prompt: 输入提示词
        model: 模型名称（如 "deepseek-chat", "qwen-max"），None 使用系统默认
        temperature: 温度参数
        max_tokens: 最大输出 token 数
        timeout: 超时秒数

    Returns:
        (reply_text, total_tokens_used)
    """
    llm = _build_llm(model, temperature, max_tokens, timeout)
    if llm is None:
        return _fallback_reply(prompt), _estimate_tokens(prompt) + max_tokens

    try:
        # 估算输入 token 数
        input_tokens = _estimate_tokens(prompt)

        # 异步调用（aiinvoke 需要从同步方法中转）
        import asyncio
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: llm.invoke(prompt))

        reply = response.content if hasattr(response, "content") else str(response)
        output_tokens = _estimate_tokens(reply)
        total = input_tokens + output_tokens

        # 尝试从 response 获取更精确的 token 计数
        try:
            if hasattr(response, "usage_metadata"):
                usage = response.usage_metadata
                total = usage.get("total_tokens", total)
        except Exception:
            pass

        return reply, total
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return _fallback_reply(prompt, error=str(e)), _estimate_tokens(prompt) + max_tokens


def _build_llm(
    model: Optional[str] = None,
    temperature: float = 0.5,
    max_tokens: int = 4096,
    timeout: int = 180,
):
    """根据模型名称构建 LLM 实例。"""
    try:
        from tradingagents.graph.trading_graph import create_llm_by_provider
        from app.services.simple_analysis_service import get_provider_and_url_by_model_sync
        from app.core.unified_config import unified_config

        model_name = model or "deepseek-chat"

        # 获取供应商信息和 API URL
        provider_info = get_provider_and_url_by_model_sync(model_name)
        provider = provider_info.get("provider", "deepseek")
        backend_url = provider_info.get("backend_url", "")
        api_key = provider_info.get("api_key", None)
        
        logger.info(f"_build_llm model_name={model_name}; provider={provider}; backend_url={backend_url} api_key={api_key}")

        return create_llm_by_provider(
            provider=provider,
            model=model_name,
            backend_url=backend_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            api_key=api_key,
        )
    except Exception as e:
        logger.warning(f"构建 LLM 失败: {e}，尝试直连方式")
        return _build_llm_fallback(model)


def _build_llm_fallback(model: Optional[str] = None):
    """使用 langchain_openai ChatOpenAI 直连兜底。"""
    try:
        from langchain_openai import ChatOpenAI
        import os

        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        model_name = model or "qwen-turbo"

        return ChatOpenAI(
            model=model_name,
            openai_api_key=api_key,
            openai_api_base=base_url,
            temperature=0.7,
            max_tokens=4096,
            timeout=180,
        )
    except Exception as e:
        logger.error(f"兜底 LLM 也失败: {e}")
        return None


def _fallback_reply(prompt: str, error: str = "") -> str:
    """当 LLM 不可用时返回降级回复。"""
    return (
        "（系统暂无法连接大模型服务。请检查 LLM 配置后重试。）"
        if not error
        else f"（LLM 调用出错: {error}）"
    )


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（4 字符 ≈ 1 token）。"""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# 便利函数：获取可用模型列表
# ---------------------------------------------------------------------------


def get_available_models() -> List[dict]:
    """获取系统中可用的 LLM 模型列表。"""
    try:
        from app.core.unified_config import unified_config
        configs = unified_config.get_llm_configs()
        return [
            {
                "model_name": c.model_name,
                "provider": c.provider,
                "enabled": c.enabled,
            }
            for c in configs
        ]
    except Exception as e:
        logger.warning(f"获取模型列表失败: {e}")
        return []
