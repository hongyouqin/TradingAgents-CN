"""
LangChain integration adapter.
- If langchain is installed, provides LangChainAgent which uses our llm_client and vector_store as retriever.
- If langchain not installed, logs and provides a fallback wrapper that delegates to ReportChatAgent.
"""
import logging
import asyncio
from typing import List

logger = logging.getLogger(__name__)

try:
    from langchain_core.language_models.llms import LLM
    LANGCHAIN_AVAILABLE = True
except Exception:
    LANGCHAIN_AVAILABLE = False

from app.services.llm_client import call_llm, embed_texts
from app.services.vector_store.faiss_store import FaissStore
from app.services.tools.news_tool import NewsTool
from app.services.tools.historical_tool import HistoricalTool
from app.services.tools.funds_tool import FundsTool


if LANGCHAIN_AVAILABLE:
    # ------------------------------------------------------------------
    # LangChain 已安装时的实现
    # 使用 LCEL (LangChain Expression Language) 而非已废弃的 RetrievalQA
    # ------------------------------------------------------------------

    class DeepseekLangchainLLM(LLM):
        """LangChain LLM 包装器，委托到项目的 call_llm。"""
        def __init__(self, model_name: str = 'deepseek-v4-flash'):
            super().__init__()
            self.model_name = model_name

        @property
        def _llm_type(self) -> str:
            return "custom_deepseek"

        def _identifying_params(self):
            return {"model_name": self.model_name}

        def _call(self, prompt: str, stop: List[str] = None) -> str:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    return asyncio.run(self._acall(prompt, stop))
                return loop.run_until_complete(self._acall(prompt, stop))
            except Exception as e:
                logger.warning(f"LLM sync fallback failed: {e}")
                return ""

        async def _acall(self, prompt: str, stop: List[str] = None) -> str:
            try:
                reply, _ = await call_llm(prompt, model=self.model_name)
                return reply
            except Exception as e:
                logger.warning(f"LLM async call failed: {e}")
                return ""

    def build_lcel_chain(llm, retriever):
        """使用 LCEL 构建 RAG 链，替代已废弃的 RetrievalQA / ConversationalRetrievalChain。

        Args:
            llm: 实现了 invoke/ainvoke 的 LangChain LLM 实例
            retriever: 实现了 get_relevant_documents 的检索器
        Returns:
            callable: chain.invoke(question) → answer
        """
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser
        from langchain_core.runnables import RunnablePassthrough

        prompt = ChatPromptTemplate.from_template(
            "基于以下上下文回答问题。如果找不到答案，就说不知道。\n\n"
            "上下文：\n{context}\n\n"
            "问题：{question}\n\n"
            "回答："
        )

        def _format_docs(docs):
            return "\n\n".join(d.page_content for d in docs)

        chain = (
            {"context": retriever | _format_docs, "question": RunnablePassthrough()}
            | prompt
            | llm
            | StrOutputParser()
        )
        return chain

    class LangChainAgent:
        """LangChain 集成代理（使用 LCEL 而非已废弃的 chain 类）。"""

        def __init__(self, analysis_id: str, model_name: str = 'deepseek-v4-flash'):
            self.analysis_id = analysis_id
            self.model = DeepseekLangchainLLM(model_name=model_name)
            self.store = FaissStore()
            self.retriever = VectorStoreRetriever(self.store, analysis_id)

            # 构建 LCEL chain
            self.chain = build_lcel_chain(self.model, self.retriever)

        async def arun(self, question: str) -> str:
            try:
                return await self.chain.ainvoke(question)
            except Exception as e:
                logger.warning(f"LCEL chain ainvoke failed: {e}")
                try:
                    reply, _ = await call_llm(question)
                    return reply
                except Exception:
                    return ""

        def run(self, question: str) -> str:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    return asyncio.run(self.arun(question))
                return loop.run_until_complete(self.arun(question))
            except Exception as e:
                logger.warning(f"LangChainAgent.run fallback failed: {e}")
                return ""

    # 为了向后兼容，保留 VectorStoreRetriever
    class VectorStoreRetriever:
        """适配器：将 FaissStore 包装为 LangChain retriever。"""
        def __init__(self, faiss_store: FaissStore, analysis_id: str):
            self.store = faiss_store
            self.analysis_id = analysis_id

        def get_relevant_documents(self, query: str):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    return loop.run_until_complete(self._aget(query))
                return loop.run_until_complete(self._aget(query))
            except Exception:
                return []

        async def _aget(self, query: str):
            try:
                qvec = (await embed_texts([query]))[0]
            except Exception as e:
                logger.warning(f"Embedding failed in retriever: {e}")
                return []
            from langchain_core.documents import Document as LCDoc
            results = self.store.search(self.analysis_id, qvec, top_k=6)
            return [LCDoc(page_content=r['metadata'].get('text', ''), metadata=r.get('metadata', {})) for r in results]

else:
    class LangChainAgent:
        def __init__(self, analysis_id: str, model_name: str = 'deepseek-v4-flash'):
            logger.info("LangChain not available; LangChainAgent will not be used.")
            self.analysis_id = analysis_id

        def run(self, question: str) -> str:
            raise RuntimeError("LangChain not installed")
