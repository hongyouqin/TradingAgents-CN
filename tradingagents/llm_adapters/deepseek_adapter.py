"""
DeepSeek LLM适配器，支持Token使用统计和思考模式
"""

import os
import time
from typing import Any, Dict, List, Optional, Union
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI
from langchain_core.callbacks import CallbackManagerForLLMRun

# 导入统一日志系统
from tradingagents.utils.logging_init import setup_llm_logging

# 导入日志模块
from tradingagents.utils.logging_manager import get_logger, get_logger_manager
logger = get_logger('agents')
logger = setup_llm_logging()

# 导入token跟踪器
try:
    from tradingagents.config.config_manager import token_tracker
    TOKEN_TRACKING_ENABLED = True
    logger.info("✅ Token跟踪功能已启用")
except ImportError:
    TOKEN_TRACKING_ENABLED = False
    logger.warning("⚠️ Token跟踪功能未启用")


class ChatDeepSeek(ChatOpenAI):
    """
    DeepSeek聊天模型适配器，支持Token使用统计
    
    继承自ChatOpenAI，添加了Token使用量统计功能
    """
    
    def __init__(
        self,
        model: str = "deepseek-v4-flash",
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com",
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs
    ):
        """
        初始化DeepSeek适配器
        
        Args:
            model: 模型名称，默认为deepseek-v4-flash
            api_key: API密钥，如果不提供则从环境变量DEEPSEEK_API_KEY获取
            base_url: API基础URL
            temperature: 温度参数
            max_tokens: 最大token数
            **kwargs: 其他参数
        """
        
        # 检查是否使用了即将弃用的模型
        deprecated_models = ["deepseek-chat", "deepseek-reasoner"]
        if model in deprecated_models:
            logger.warning(
                f"⚠️ 模型 {model} 将于2026/07/24弃用，建议切换到 deepseek-v4-flash 或 deepseek-v4-pro"
            )
            if model == "deepseek-chat":
                model = "deepseek-v4-flash"
                logger.info(f"🔄 自动将 deepseek-chat 映射到 {model}")
            elif model == "deepseek-reasoner":
                model = "deepseek-v4-pro"
                logger.info(f"🔄 自动将 deepseek-reasoner 映射到 {model}")
        
        # 获取API密钥
        if api_key is None:
            # 导入 API Key 验证工具
            try:
                from app.utils.api_key_utils import is_valid_api_key
            except ImportError:
                def is_valid_api_key(key):
                    if not key or len(key) <= 10:
                        return False
                    if key.startswith('your_') or key.startswith('your-'):
                        return False
                    if key.endswith('_here') or key.endswith('-here'):
                        return False
                    if '...' in key:
                        return False
                    return True

            # 从环境变量读取 API Key
            env_api_key = os.getenv("DEEPSEEK_API_KEY")

            # 验证环境变量中的 API Key
            if env_api_key and is_valid_api_key(env_api_key):
                api_key = env_api_key
                logger.info("✅ [DeepSeek初始化] 使用环境变量中的有效 API Key")
            elif env_api_key:
                logger.warning("⚠️ [DeepSeek初始化] 环境变量中的 API Key 无效（可能是占位符），将被忽略")
                api_key = None
            else:
                api_key = None

            if not api_key:
                raise ValueError(
                    "DeepSeek API密钥未找到。请在 Web 界面配置 API Key "
                    "(设置 -> 大模型厂家) 或设置 DEEPSEEK_API_KEY 环境变量。"
                )
        
        # 初始化父类
        super().__init__(
            model=model,
            openai_api_key=api_key,
            openai_api_base=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )
        
        # 保存模型名称（父类使用model_name）
        self.model_name = model
        
    def _convert_message_to_dict(self, msg: Any) -> Optional[Dict[str, str]]:
        """
        将各种格式的消息转换为API所需的字典格式
        """
        try:
            # 处理BaseMessage类型
            if isinstance(msg, BaseMessage):
                if isinstance(msg, SystemMessage):
                    return {"role": "system", "content": str(msg.content)}
                elif isinstance(msg, HumanMessage):
                    return {"role": "user", "content": str(msg.content)}
                elif isinstance(msg, AIMessage):
                    return {"role": "assistant", "content": str(msg.content)}
                else:
                    return {"role": "user", "content": str(msg.content)}
            
            # 处理tuple类型（可能是(message, metadata)格式）
            elif isinstance(msg, tuple):
                if len(msg) >= 1:
                    return self._convert_message_to_dict(msg[0])
                return None
            
            # 处理dict类型
            elif isinstance(msg, dict):
                if "role" in msg and "content" in msg:
                    return {"role": msg["role"], "content": str(msg["content"])}
                elif "type" in msg and "content" in msg:
                    role_map = {"human": "user", "ai": "assistant", "system": "system"}
                    role = role_map.get(msg.get("type", "user"), "user")
                    return {"role": role, "content": str(msg["content"])}
                return None
            
            # 处理字符串
            elif isinstance(msg, str):
                return {"role": "user", "content": msg}
            
            # 其他类型，尝试转换为字符串
            else:
                return {"role": "user", "content": str(msg)}
                
        except Exception as e:
            logger.warning(f"⚠️ 消息转换失败: {e}, 消息类型: {type(msg)}")
            return None
    
    def _generate(
        self,
        messages: List[Any],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """
        生成聊天响应，并记录token使用量
        """
        # 记录开始时间
        start_time = time.time()
        
        # 提取并移除自定义参数，避免传递给父类
        session_id = kwargs.pop('session_id', None)
        analysis_type = kwargs.pop('analysis_type', None)
        
        try:
            # 转换消息格式
            api_messages = []
            for msg in messages:
                converted = self._convert_message_to_dict(msg)
                if converted:
                    api_messages.append(converted)
            
            if not api_messages:
                api_messages = [{"role": "user", "content": "请继续分析"}]
                logger.warning("⚠️ 没有有效的消息，使用默认消息")
            
            # 调用父类方法生成响应
            # 注意：需要传递转换后的消息
            result = super()._generate(
                self._convert_to_langchain_messages(api_messages), 
                stop, 
                run_manager, 
                **kwargs
            )
            
            # 提取token使用量
            input_tokens = 0
            output_tokens = 0
            
            # 尝试从响应中提取token使用量
            if hasattr(result, 'llm_output') and result.llm_output:
                token_usage = result.llm_output.get('token_usage', {})
                if token_usage:
                    input_tokens = token_usage.get('prompt_tokens', 0)
                    output_tokens = token_usage.get('completion_tokens', 0)
            
            # 如果没有获取到token使用量，进行估算
            if input_tokens == 0 and output_tokens == 0:
                input_tokens = self._estimate_input_tokens(messages)
                output_tokens = self._estimate_output_tokens(result)
                logger.debug(f"🔍 [DeepSeek] 使用估算token: 输入={input_tokens}, 输出={output_tokens}")
            else:
                logger.info(f"📊 [DeepSeek] 实际token使用: 输入={input_tokens}, 输出={output_tokens}")
            
            # 记录token使用量
            if TOKEN_TRACKING_ENABLED and (input_tokens > 0 or output_tokens > 0):
                try:
                    if session_id is None:
                        session_id = f"deepseek_{hash(str(messages)) % 10000}"
                    if analysis_type is None:
                        analysis_type = 'stock_analysis'
                    
                    usage_record = token_tracker.track_usage(
                        provider="deepseek",
                        model_name=self.model_name,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        session_id=session_id,
                        analysis_type=analysis_type
                    )
                    
                    if usage_record:
                        if usage_record.cost == 0.0:
                            logger.warning(f"⚠️ [DeepSeek] 成本计算为0，可能配置有问题")
                        else:
                            logger.info(f"💰 [DeepSeek] 本次调用成本: ¥{usage_record.cost:.6f}")
                        
                        try:
                            logger_manager = get_logger_manager()
                            logger_manager.log_token_usage(
                                logger, "deepseek", self.model_name,
                                input_tokens, output_tokens, usage_record.cost,
                                session_id
                            )
                        except:
                            pass
                except Exception as track_error:
                    logger.error(f"⚠️ [DeepSeek] Token统计失败: {track_error}")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ [DeepSeek] 调用失败: {e}", exc_info=True)
            raise
    
    def _convert_to_langchain_messages(self, api_messages: List[Dict]) -> List[BaseMessage]:
        """
        将API消息格式转换回LangChain消息格式
        """
        langchain_messages = []
        for msg in api_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            if role == "system":
                langchain_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                langchain_messages.append(AIMessage(content=content))
            else:
                langchain_messages.append(HumanMessage(content=content))
        
        return langchain_messages
    
    def _estimate_input_tokens(self, messages: List[Any]) -> int:
        """
        估算输入token数量
        """
        total_chars = 0
        for msg in messages:
            if hasattr(msg, 'content'):
                total_chars += len(str(msg.content))
            elif isinstance(msg, (str, dict)):
                total_chars += len(str(msg))
        
        # 粗略估算：2字符/token
        estimated_tokens = max(1, total_chars // 2)
        return estimated_tokens
    
    def _estimate_output_tokens(self, result: ChatResult) -> int:
        """
        估算输出token数量
        """
        total_chars = 0
        for generation in result.generations:
            if hasattr(generation, 'message') and hasattr(generation.message, 'content'):
                total_chars += len(str(generation.message.content))
        
        estimated_tokens = max(1, total_chars // 2)
        return estimated_tokens
    
    def invoke(
        self,
        input: Union[str, List[Any], List[BaseMessage]],
        config: Optional[Dict] = None,
        **kwargs: Any,
    ) -> AIMessage:
        """
        调用模型生成响应
        """
        # 处理输入
        if isinstance(input, str):
            messages = [HumanMessage(content=input)]
        elif isinstance(input, list):
            messages = input
        else:
            messages = [HumanMessage(content=str(input))]
        
        # 调用生成方法
        result = self._generate(messages, **kwargs)
        
        # 返回第一个生成结果的消息
        if result.generations:
            return result.generations[0].message
        else:
            return AIMessage(content="")


def create_deepseek_llm(
    model: str = "deepseek-v4-flash",
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
    **kwargs
) -> ChatDeepSeek:
    """
    创建DeepSeek LLM实例的便捷函数
    
    Args:
        model: 模型名称，推荐 deepseek-v4-flash（快速）或 deepseek-v4-pro（强大）
        temperature: 温度参数
        max_tokens: 最大token数
        **kwargs: 其他参数
        
    Returns:
        ChatDeepSeek实例
    """
    return ChatDeepSeek(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs
    )


# 为了向后兼容，提供别名
DeepSeekLLM = ChatDeepSeek