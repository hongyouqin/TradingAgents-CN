"""
报告精简服务 - 将详细股票分析报告转化为老板易懂的精简汇报
生成符合"超级大脑"风格的HTML页面
"""

import json
import logging
import asyncio
import os
import sys
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# 直接导入TradingAgents的LLM创建函数
from tradingagents.graph.trading_graph import create_llm_by_provider
from app.core.unified_config import unified_config
from app.core.database import get_mongo_db

logger = logging.getLogger(__name__)


class SimplifiedReport:
    """简化报告数据模型"""
    
    def __init__(
        self,
        analysis_id: str,
        original_summary: str,
        executive_summary: str,
        decision_points: List[str],
        core_review: Dict[str, str],
        risk_analysis: Dict[str, str],
        short_term_outlook: Dict[str, str],
        action_items: List[str],
        golden_quote: str,
        html_content: str,
        compression_ratio: float = 0.0,
        stock_code: str = "未知",
        stock_name: str = "未知",
        llm_raw_response: str = ""  # 新增：保存LLM原始响应
    ):
        self.analysis_id = analysis_id
        self.original_summary = original_summary
        self.executive_summary = executive_summary
        self.decision_points = decision_points
        self.core_review = core_review
        self.risk_analysis = risk_analysis
        self.short_term_outlook = short_term_outlook
        self.action_items = action_items
        self.golden_quote = golden_quote
        self.html_content = html_content
        self.compression_ratio = compression_ratio
        self.stock_code = stock_code  # 新增
        self.stock_name = stock_name  # 新增
        self.llm_raw_response = llm_raw_response  # 新增
        self.created_at = datetime.utcnow()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "analysis_id": self.analysis_id,
            "original_summary": self.original_summary,
            "executive_summary": self.executive_summary,
            "decision_points": self.decision_points,
            "core_review": self.core_review,
            "risk_analysis": self.risk_analysis,
            "short_term_outlook": self.short_term_outlook,
            "action_items": self.action_items,
            "golden_quote": self.golden_quote,
            "html_content": self.html_content,
            "compression_ratio": self.compression_ratio,
            "stock_code": self.stock_code,  # 新增
            "stock_name": self.stock_name,  # 新增
            "llm_raw_response": self.llm_raw_response,  # 新增
            "created_at": self.created_at.isoformat()
        }


class SimplifiedReportRequest:
    """简化报告请求"""
    
    def __init__(
        self,
        analysis_id: str,
        original_content: Dict[str, Any],
        max_length: int = 1500,
        language: str = "zh-CN"
    ):
        self.analysis_id = analysis_id
        self.original_content = original_content
        self.max_length = max_length
        self.language = language


class ReportSimplifier:
    """报告精简服务 - 将详细分析报告转化为老板易懂的汇报"""
    
    def __init__(self):
        self._cache = {}
        self._prompt_template = self._load_prompt_template()
        self._html_generation_prompt = self._load_html_generation_prompt()
        logger.info("✅ ReportSimplifier 初始化完成")
    
    def _load_prompt_template(self) -> str:
        """加载提示词模板"""
        return """你是一个专业的投资顾问“超级大脑”，正在向一位刚开始炒股的小散户汇报一份股票分析报告。

文件里面是一份股票的分析报告。我现在需要你帮我读懂这份报告。

现在我要求你扮演我的私人投资顾问。你的名字叫“超级大脑”，我是一位刚开始炒A股的小散户，你参与了这份股票分析报告的撰写，你现在要向我汇报这份报告，用简单的言语让我这个初入股市的小散户读懂这份报告。

给我汇报你的总结、你的看法、你的分析、你的建议等等。如果股票分析报告中，有一些陷阱、埋的坑、隐患，你要识别出来这些不好的意图，并帮助我避坑。

原始分析报告内容：
{original_content}

### “超级大脑”的角色设定：
- 你的名字叫“超级大脑”。
- 这里你的语气像是朋友间聊天一样，谈笑风生。文风可以有感情色彩一些。
- 你是一位深谙中国A股操作逻辑的“股票专家”。你的任务不是生硬的会议记录，而是为我提供一份清晰、有趣、简洁的“汇报”。
- 你话说得很恳切，很有分量，情意深长。对我真诚地忠告或劝告。
- 对当前局势进行判断，找出我的可利用机会。
- 你的语言要尽量简洁，我没有时间精力听你长篇大论。所以你的每一句话都要十分的“抓人”。才能吸引我耐心的看完你说的东西。
- 你给到我的汇报结果能够给到我想不到事情和认知以外事情，能够提出让人眼前一亮的观点。
- 如果某些事情用文字不太好描述，可以用图示或图表来表达意思。（注意：在最终JSON输出中，如果确实需要图表，请在对应字段中用“[图表：此处为XXX的示意图]”这样的形式占位说明。）

### 汇报要求：
1. 为这份解读报告编一个“标题”（标题就要直观的标题，标题里面不要出现xx报告之类的描述）。
2. 首先用一段简单的文字总结这份报告（字数控制在100字以内）。请直接回答总结是什么，不要说类似“这份报告是...”这样的口水话。
3. 超级大脑的深度洞察和决策结果。这个模块，你要站在战略角度思考问题，你作为我的顶级幕僚给我一条你的最终决策。最后提供一条一句话的针对性破局建议或风险预警。希望你能够提出让人眼前一亮的观点。要精炼，不要长篇大论，这个模块字数控制在300字以内。
4. 报告的核心回顾（解读基本面、新闻面、技术面、市场情绪面）。
5. 你的看法与风险警示。你有哪些想对我这个小散户说的。
6. 请结合新闻面分析和联网分析，来解读这只股票的炒点是什么？这只股票的炒作点是否符合A股最擅长的“故事驱动估值”游戏？
7. 假设未来3天，如果股票出现上涨，请分析这方面的可能性和原因（是否存在短期炒作、庄家控盘、散户情绪、板块爆炒、相关股票题材关联、重大新闻披露、等等原因？如果有，请说明，如果没有，就不要说）。
8. 多空双方核心分歧与底层逻辑。请基于这份完整的分析报告，分别梳理看涨方、看跌方的核心论点与支撑数据，明确双方的共识内容与核心分歧点。
9. 列出3条左右接下来要做的事（接下来的行动是什么）（待办事项）。
10. 最后以一句对我说的“金句”来作为结尾。

请将以上内容组织成JSON格式返回，包含以下字段：
- title: 你为报告编的标题（字符串）
- executive_summary: 一句话总结（字符串）
- insight_and_decision: 深度洞察和决策结果（字符串）
- core_review: 核心回顾对象，包含以下字段：
    - fundamentals: 基本面解读（字符串）
    - news: 新闻面解读（字符串）
    - technical: 技术面解读（字符串）
    - sentiment: 情绪面解读（字符串）
- personal_view_and_risk: 你的看法与风险警示（字符串）
- 炒作点分析: 炒作点分析（字符串）
- short_term_outlook: 短期展望对象，包含以下字段：
    - possibility: 上涨可能性（如"高"、"中"、"低"）
    - reason: 原因分析（字符串）
- core_disagreement: 多空分歧对象，包含以下字段：
    - bullish_arguments: 看涨方论点（字符串）
    - bearish_arguments: 看跌方论点（字符串）
    - consensus: 共识点（字符串）
    - key_disagreement: 核心分歧点（字符串）
- action_items: 待办事项列表（每个事项是一个字符串，最多3条）
- golden_quote: 金句（字符串）

确保返回的是有效的JSON格式，不要包含任何其他文字说明。"""
    
    def _load_html_generation_prompt(self) -> str:
        """加载HTML生成提示词模板 - 包含stock_name和stock_code"""
        return """根据以下股票分析数据，生成一个适合老板查看的HTML报告页面。

    股票代码：{stock_code}
    股票名称：{stock_name}
    分析数据：
    {simplified_data}

    要求：
    1. 页面标题使用"超级大脑 · {stock_name}({stock_code})"
    2. 英雄区使用深蓝色渐变背景，包含标题和executive_summary，英雄区文字使用**白色**
    3. 所有内容用卡片式布局，白色背景，圆角，卡片内文字使用深灰色（如#1e293b）
    4. 核心词汇用**橙色（如#f59e0b）或深蓝色（如#1e40af）加粗**，确保在白色背景上清晰可见
    5. 包含以下所有内容：
    - 深度洞察和决策结果 (insight_and_decision)
    - 核心回顾 (core_review：基本面、新闻面、技术面、情绪面)
    - 个人看法与风险警示 (personal_view_and_risk)
    - 炒作点分析 (炒作点分析)
    - 短期展望 (short_term_outlook)
    - 多空分歧 (core_disagreement)
    - 待办事项 (action_items)
    - 金句 (golden_quote)

    6. 只返回完整的HTML代码，不要任何解释
    7. 确保代码完整，包含<!DOCTYPE html>
    8. 适配手机端，使用viewport
    9. 保持简洁专业，不要过于复杂

    **颜色规范**：
    - 英雄区背景：深蓝色渐变（如 #1e3c72 → #2a5298），文字：白色
    - 卡片背景：纯白色（#ffffff），文字：深灰色（#1e293b）
    - 标题文字：深蓝色（如 #1e40af）
    - 核心词汇加粗：橙色（#f59e0b）或深蓝色（#1e40af）
    - 风险警示：红色（#ef4444）或橙色（#f97316）
    - 金句区域：浅色背景（如 #f8fafc），深蓝色文字

    直接输出HTML代码："""
    
    async def simplify_report(self, request: SimplifiedReportRequest) -> SimplifiedReport:
        """生成简化报告（带缓存）"""
        try:
            # 1. 检查缓存
            cache_key = f"{request.analysis_id}_{request.language}"
            if cache_key in self._cache:
                cached = self._cache[cache_key]
                # 缓存有效期检查（24小时）
                if (datetime.utcnow() - cached.created_at).total_seconds() < 86400:
                    logger.info(f"📦 使用缓存的简化报告: {request.analysis_id}")
                    return cached
            
            # 2. 检查数据库是否已存在
            existing = await self._get_simplified_report_from_db(request.analysis_id)
            if existing:
                logger.info(f"📦 从数据库获取已有的简化报告: {request.analysis_id}")
                simplified_report = self._dict_to_report(existing)
                self._cache[cache_key] = simplified_report
                return simplified_report
            
            # 3. 获取原始分析报告内容
            original_content = request.original_content
            if not original_content:
                raise ValueError(f"无法找到分析报告内容: {request.analysis_id}")
            
            logger.info(f"🔄 开始调用LLM生成简化报告: {request.analysis_id}")
            
            # 4. 调用模型生成简化内容
            simplified_data, llm_raw_response = await self._call_llm_for_simplification(
                original_content, 
                request.max_length,
                request.language
            )
            
            # 5. 计算压缩比例
            original_str = json.dumps(original_content, ensure_ascii=False)
            simplified_str = json.dumps(simplified_data, ensure_ascii=False)
            compression_ratio = 1 - (len(simplified_str) / len(original_str))
            
            # 6. 提取原始摘要
            original_summary = original_content.get("summary", "")
            
            # 7. 获取股票信息
            stock_code = original_content.get("symbol", "未知")
            stock_name = (
                original_content.get("stock_name") or 
                original_content.get("name") or 
                original_content.get("stockName") or 
                original_content.get("company_name") or 
                stock_code
            )
            logger.info(f"📊 股票信息: 代码={stock_code}, 名称={stock_name}")
            
            # 8. 调用LLM生成HTML页面
            html_content, html_raw_response, is_fallback  = await self._generate_html_by_llm(
                stock_code=stock_code,
                stock_name=stock_name,
                simplified_data=simplified_data
            )
            
            # 9. 合并所有原始响应
            all_raw_responses = {
                "simplification_response": llm_raw_response,
                "html_response": html_raw_response,
                "is_fallback": is_fallback
            }
            
            # 10. 创建简化报告对象（使用映射后的数据）
            mapped_data = self._map_to_legacy_format(simplified_data)
            
            simplified_report = SimplifiedReport(
                analysis_id=request.analysis_id,
                original_summary=original_summary,
                executive_summary=mapped_data.get("executive_summary", ""),
                decision_points=mapped_data.get("decision_points", []),
                core_review=mapped_data.get("core_review", {}),
                risk_analysis=mapped_data.get("risk_analysis", {}),
                short_term_outlook=mapped_data.get("short_term_outlook", {}),
                action_items=mapped_data.get("action_items", []),
                golden_quote=mapped_data.get("golden_quote", "谋定而后动，知止而有得"),
                html_content=html_content,
                compression_ratio=compression_ratio,
                stock_code=stock_code,
                stock_name=stock_name,
                llm_raw_response=json.dumps(all_raw_responses, ensure_ascii=False, indent=2)
            )
            
            # 11. 保存到数据库和缓存
            await self._save_simplified_report_to_db(simplified_report)
            self._cache[cache_key] = simplified_report
            
            # 12. 保存HTML到文件
            await self._save_html_to_file(request.analysis_id, stock_code, html_content)
            
            logger.info(f"✅ 简化报告生成完成: {request.analysis_id}, 压缩比例: {compression_ratio:.2%}")
            return simplified_report
            
        except Exception as e:
            logger.error(f"❌ 生成简化报告失败: {e}")
            import traceback
            logger.error(f"❌ 堆栈跟踪: {traceback.format_exc()}")
            return self._create_fallback_report(request.analysis_id, request.original_content)
    
    def _map_to_legacy_format(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """将新模板的数据映射到旧模板格式，兼容SimplifiedReport"""
        mapped = {}
        
        # 基础字段
        mapped["executive_summary"] = data.get("executive_summary", "")
        mapped["core_review"] = data.get("core_review", {})
        mapped["action_items"] = data.get("action_items", [])
        mapped["golden_quote"] = data.get("golden_quote", "谋定而后动，知止而有得")
        
        # 短期展望
        mapped["short_term_outlook"] = data.get("short_term_outlook", {})
        
        # 处理决策要点 - 从insight_and_decision转换
        if "insight_and_decision" in data:
            insight = data["insight_and_decision"]
            points = [p.strip() for p in insight.split('。') if p.strip()]
            mapped["decision_points"] = points[:5]
        elif "decision_points" in data:
            mapped["decision_points"] = data["decision_points"]
        else:
            mapped["decision_points"] = ["等待分析结果"]
        
        # 处理风险分析
        if "personal_view_and_risk" in data:
            risk_text = data["personal_view_and_risk"]
            mapped["risk_analysis"] = {
                "risk": risk_text,
                "strategy": "建议结合个人风险承受能力制定策略"
            }
        elif "risk_analysis" in data:
            mapped["risk_analysis"] = data["risk_analysis"]
        else:
            mapped["risk_analysis"] = {
                "risk": "暂无显著风险",
                "strategy": "保持关注"
            }
        
        return mapped
    
    async def _call_llm_for_simplification(
        self, 
        original_content: Dict[str, Any],
        max_length: int,
        language: str
    ) -> tuple[Dict[str, Any], str]:
        """调用LLM生成简化内容（带重试机制）"""
        max_retries = 3
        retry_delay = 1
        last_response = ""
        
        for attempt in range(max_retries):
            try:
                llm_config = self._get_llm_config()
                
                logger.info(f"🔧 [简化报告] 尝试 {attempt + 1}/{max_retries}")
                logger.info(f"  模型: {llm_config['model_name']}")
                logger.info(f"  供应商: {llm_config['provider']}")
                
                prompt = self._build_optimized_prompt(original_content, max_length, language)
                
                llm = create_llm_by_provider(
                    provider=llm_config["provider"],
                    model=llm_config["model_name"],
                    backend_url=llm_config["backend_url"],
                    temperature=0.4,
                    max_tokens=8000,
                    timeout=120,
                    api_key=llm_config["api_key"]
                )
                
                loop = asyncio.get_event_loop()
                
                if hasattr(llm, 'ainvoke'):
                    response = await llm.ainvoke(prompt)
                else:
                    response = await loop.run_in_executor(None, llm.invoke, prompt)
                
                if hasattr(response, 'content'):
                    content = response.content
                elif isinstance(response, str):
                    content = response
                else:
                    content = str(response)
                
                last_response = content
                logger.info(f"✅ LLM响应长度: {len(content)} 字符")
                
                # 保存原始响应到文件（调试用）
                await self._save_raw_response_to_file(
                    original_content.get("symbol", "unknown"),
                    "simplification",
                    content
                )
                
                parsed_data = self._parse_llm_response(content)
                return parsed_data, content
                
            except Exception as e:
                logger.warning(f"⚠️ LLM调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))
                else:
                    logger.error(f"❌ 所有重试都失败，返回默认结构")
                    return self._get_default_simplified_data(original_content), last_response
        
        return self._get_default_simplified_data(original_content), last_response
    
    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """解析LLM响应，处理各种格式"""
        try:
            cleaned_response = response.strip()
            
            if cleaned_response.startswith('```'):
                import re
                json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', cleaned_response, re.DOTALL | re.IGNORECASE)
                if json_match:
                    cleaned_response = json_match.group(1)
            
            data = json.loads(cleaned_response)
            logger.info(f"✅ 成功解析JSON响应")
            return data
            
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ JSON解析失败: {e}")
            
            try:
                import re
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group())
                    logger.info(f"✅ 从文本中提取JSON成功")
                    return data
            except:
                pass
            
            logger.error(f"❌ 解析LLM响应失败，响应内容前200字符: {response[:200]}...")
            return self._get_default_simplified_data({})
    
    def _build_optimized_prompt(self, content: Dict[str, Any], max_length: int, language: str) -> str:
        """构建优化后的提示词"""
        return self._prompt_template.format(
            original_content=json.dumps(content, ensure_ascii=False, indent=2)
        )
    
    def _get_llm_config(self) -> Dict[str, Any]:
        """获取LLM配置"""
        is_enabled = os.getenv("DEEPSEEK_ENABLED")
        if is_enabled and is_enabled.lower() in ["true", "1", "yes"]:
            base_url = os.getenv("DEEPSEEK_BASE_URL")
            api_key = os.getenv("DEEPSEEK_API_KEY")
            model_name = unified_config.get_quick_analysis_model()
            from app.services.simple_analysis_service import get_provider_and_url_by_model_sync
            provider_info = get_provider_and_url_by_model_sync(model_name)    
 
            logger.info(f"  模型: {model_name}")
            logger.info(f"  提供商: {provider_info['provider']}")
            logger.info(f"  后端URL: {base_url}")
            
            return {
                "model_name": model_name,
                "provider": 'deepseek',
                "backend_url": base_url,
                "api_key": api_key
            }
        else:
            model_name = unified_config.get_quick_analysis_model()
            from app.services.simple_analysis_service import get_provider_and_url_by_model_sync
            provider_info = get_provider_and_url_by_model_sync(model_name)    
            
            logger.info(f"  模型: {model_name}")
            logger.info(f"  提供商: {provider_info['provider']}")
            logger.info(f"  后端URL: {provider_info['backend_url']}")
            
            return {
                "model_name": model_name,
                "provider": provider_info["provider"],
                "backend_url": provider_info["backend_url"],
                "api_key": provider_info.get("api_key")
            }
    
    async def _generate_html_by_llm(self, stock_code: str, stock_name: str, simplified_data: Dict[str, Any]) -> tuple[str, str, bool]:
        """调用LLM生成HTML页面，返回(html_content, raw_response, is_fallback)"""
        max_retries = 2
        retry_delay = 1
        last_response = ""
        is_fallback = False  # 新增：标记是否使用备用模板
        
        logger.info(f"🔍 _generate_html_by_llm 收到参数:")
        logger.info(f"  stock_code: {stock_code}")
        logger.info(f"  stock_name: {stock_name}")
        logger.info(f"  simplified_data 字段: {list(simplified_data.keys())}")
        
        for attempt in range(max_retries):
            try:
                llm_config = self._get_llm_config()
                
                # 准备增强数据
                enhanced_data = {
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "date": datetime.now().strftime("%Y年%m月%d日"),
                    **simplified_data
                }
                
                # 格式化提示词，传入所有需要的变量
                prompt = self._html_generation_prompt.format(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    simplified_data=json.dumps(enhanced_data, ensure_ascii=False, indent=2)
                )
                
                logger.info(f"📝 HTML生成提示词长度: {len(prompt)} 字符")
                
                llm = create_llm_by_provider(
                    provider=llm_config["provider"],
                    model=llm_config["model_name"],
                    backend_url=llm_config["backend_url"],
                    temperature=0.4,
                    max_tokens=4000,
                    timeout=80,
                    api_key=llm_config["api_key"]
                )
                
                loop = asyncio.get_event_loop()
                
                if hasattr(llm, 'ainvoke'):
                    response = await llm.ainvoke(prompt)
                else:
                    response = await loop.run_in_executor(None, llm.invoke, prompt)
                
                if hasattr(response, 'content'):
                    content = response.content
                elif isinstance(response, str):
                    content = response
                else:
                    content = str(response)
                
                last_response = content
                logger.info(f"✅ HTML生成响应长度: {len(content)} 字符")
                
                # 保存原始HTML响应到文件
                await self._save_raw_response_to_file(
                    stock_code,
                    "html",
                    content
                )
                
                # 记录部分内容用于调试
                logger.info(f"🔍 HTML响应内容前200字符: {content[:200]}")
                
                html_content = self._extract_html_from_response(content)
                
                if "<!DOCTYPE html>" in html_content and "<html" in html_content:
                    logger.info(f"✅ HTML验证通过，使用LLM生成的模板")
                    return html_content, last_response, is_fallback  # is_fallback = False
                else:
                    if "<html" in html_content:
                        if "<!DOCTYPE html>" not in html_content:
                            html_content = "<!DOCTYPE html>\n" + html_content
                        logger.info(f"⚠️ HTML缺少DOCTYPE，已补充，使用LLM生成的模板")
                        return html_content, last_response, is_fallback  # is_fallback = False
                    else:
                        logger.warning(f"⚠️ 生成的HTML不完整，将在下一次尝试或回退到备用模板")
                        raise ValueError("生成的HTML不完整")
                    
            except Exception as e:
                logger.warning(f"⚠️ HTML生成失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))
                else:
                    logger.error("❌ HTML生成失败，使用备用模板")
                    is_fallback = True  # 标记使用备用模板
                    fallback_html = self._generate_fallback_html(stock_code, stock_name, simplified_data)
                    return fallback_html, last_response, is_fallback
        
        # 所有重试都失败
        logger.error("❌ 所有重试都失败，使用备用模板")
        is_fallback = True
        fallback_html = self._generate_fallback_html(stock_code, stock_name, simplified_data)
        return fallback_html, last_response, is_fallback
    
    async def _save_raw_response_to_file(self, stock_code: str, response_type: str, content: str) -> str:
        """保存原始LLM响应到文件（用于调试）"""
        try:
            project_root = Path(__file__).parent.parent.parent
            raw_dir = project_root / "data" / "raw_llm_responses"
            raw_dir.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{stock_code}_{response_type}_{timestamp}.txt"
            file_path = raw_dir / filename
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            logger.info(f"✅ 原始{response_type}响应已保存到: {file_path}")
            return str(file_path)
            
        except Exception as e:
            logger.error(f"❌ 保存原始响应失败: {e}")
            return ""
    
    def _extract_html_from_response(self, response: str) -> str:
        """从LLM响应中提取HTML内容"""
        import re
        
        logger.info(f"🔍 开始提取HTML，原始响应长度: {len(response)}")
        
        if response.strip().startswith('<!DOCTYPE html>') and '</html>' in response:
            logger.info(f"✅ 响应已经是完整HTML")
            return response.strip()
        
        html_match = re.search(r'```html\s*(.*?)\s*```', response, re.DOTALL)
        if html_match:
            extracted = html_match.group(1).strip()
            logger.info(f"✅ 从```html```代码块提取到HTML，长度: {len(extracted)}")
            return extracted
        
        html_start = response.find('<!DOCTYPE html>')
        if html_start == -1:
            html_start = response.find('<html')
        
        if html_start != -1:
            html_end = response.rfind('</html>')
            if html_end != -1:
                extracted = response[html_start:html_end + 7].strip()
                logger.info(f"✅ 提取到完整HTML，长度: {len(extracted)}")
                return extracted
            else:
                extracted = response[html_start:].strip()
                logger.info(f"⚠️ 提取到不完整HTML，长度: {len(extracted)}")
                return extracted
        
        logger.warning(f"⚠️ 无法提取HTML结构，返回原始响应")
        return response.strip()
    
    def _generate_fallback_html(self, stock_code: str, stock_name: str, simplified_data: Dict[str, Any]) -> str:
        """生成备用HTML"""
        mapped_data = self._map_to_legacy_format(simplified_data)
        
        core_review = mapped_data.get("core_review", {})
        risk_analysis = mapped_data.get("risk_analysis", {})
        short_term_outlook = mapped_data.get("short_term_outlook", {})
        
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes">
    <title>超级大脑 · {stock_code} {stock_name}</title>
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', sans-serif;
            background: #f8fafc;
            color: #1e293b;
            line-height: 1.8;
            font-size: 20px;
            padding: 0;
            margin: 0;
        }}
        .container {{
            max-width: 100%;
            padding: 0 20px;
            margin: 0 auto;
        }}
        .hero {{
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            color: white;
            padding: 40px 20px;
            border-radius: 0 0 30px 30px;
            margin-bottom: 30px;
        }}
        .hero h1 {{
            font-family: 'Poppins', sans-serif;
            font-size: 36px;
            margin-bottom: 20px;
            text-align: center;
        }}
        .hero .summary {{
            font-size: 22px;
            text-align: center;
            line-height: 1.6;
        }}
        .card {{
            background: #ffffff;
            border-radius: 20px;
            padding: 25px;
            margin-bottom: 25px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        }}
        .card h2 {{
            font-family: 'Poppins', sans-serif;
            font-size: 28px;
            color: #1e3c72;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid #e6f0ff;
        }}
        .review-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 15px;
        }}
        .review-item {{
            background: #f0f7ff;
            border-radius: 15px;
            padding: 20px;
        }}
        .review-item h4 {{
            font-size: 22px;
            color: #1e3c72;
            margin-bottom: 10px;
        }}
        .review-item p {{
            font-size: 20px;
        }}
        .risk-box, .strategy-box {{
            padding: 20px;
            border-radius: 15px;
            margin-bottom: 15px;
        }}
        .risk-box {{
            background: #fff8f0;
            border-left: 5px solid #f97316;
        }}
        .strategy-box {{
            background: #f0f7ff;
            border-left: 5px solid #2a5298;
        }}
        .highlight {{
            color: #1e3c72;
            font-weight: 700;
        }}
        .golden-quote {{
            font-family: 'Poppins', sans-serif;
            font-size: 24px;
            text-align: center;
            padding: 30px 20px;
            color: #1e3c72;
            font-weight: 600;
            margin-top: 20px;
            background: linear-gradient(135deg, #f8fafc, #ffffff);
            border-radius: 30px;
        }}
        @media (max-width: 480px) {{
            body {{ font-size: 19px; }}
            .hero h1 {{ font-size: 32px; }}
            .hero .summary {{ font-size: 21px; }}
            .card h2 {{ font-size: 26px; }}
        }}
    </style>
</head>
<body>
    <div class="hero">
        <div class="container">
            <h1>🧠 超级大脑 · {stock_code} {stock_name}</h1>
            <div class="summary">{mapped_data.get('executive_summary', '分析总结')}</div>
        </div>
    </div>

    <div class="container">
        <div class="card">
            <h2>🎯 超级大脑·决策结果</h2>
            <ul>
                {''.join([f'<li>{point}</li>' for point in mapped_data.get('decision_points', ['等待分析结果'])])}
            </ul>
        </div>

        <div class="card">
            <h2>📊 核心回顾</h2>
            <div class="review-grid">
                <div class="review-item">
                    <h4>📈 基本面</h4>
                    <p>{core_review.get('fundamentals', '无数据')}</p>
                </div>
                <div class="review-item">
                    <h4>📰 新闻面</h4>
                    <p>{core_review.get('news', '无数据')}</p>
                </div>
                <div class="review-item">
                    <h4>📉 技术面</h4>
                    <p>{core_review.get('technical', '无数据')}</p>
                </div>
                <div class="review-item">
                    <h4>🎭 情绪面</h4>
                    <p>{core_review.get('sentiment', '无数据')}</p>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>⚠️ 风险警示 & 应对策略</h2>
            <div class="risk-box">
                <strong class="highlight">🚨 潜在风险</strong>
                <p>{risk_analysis.get('risk', '暂无显著风险')}</p>
            </div>
            <div class="strategy-box">
                <strong class="highlight">🛡️ 应对策略</strong>
                <p>{risk_analysis.get('strategy', '保持关注')}</p>
            </div>
        </div>

        <div class="card">
            <h2>🔮 未来1-3天展望</h2>
            <p class="highlight">上涨可能性：{short_term_outlook.get('possibility', '中')}</p>
            <p>{short_term_outlook.get('reason', '市场情绪平稳')}</p>
        </div>

        <div class="card">
            <h2>✅ 接下来要做的事</h2>
            <ul>
                {''.join([f'<li>{item}</li>' for item in mapped_data.get('action_items', ['持续关注'])])}
            </ul>
        </div>

        <div class="golden-quote">
            {mapped_data.get('golden_quote', '谋定而后动，知止而有得')}
        </div>
    </div>
</body>
</html>"""
        return html
    
    def _get_default_simplified_data(self, original: Dict[str, Any]) -> Dict[str, Any]:
        """获取默认的简化数据结构"""
        symbol = original.get("symbol", "未知")
        recommendation = original.get("recommendation", "观望")
        confidence = original.get("confidence_score", 0)
        risk_level = original.get("risk_level", "中等")
        
        return {
            "title": f"{symbol} 投资分析报告",
            "executive_summary": f"分析了{symbol}，建议{recommendation}，置信度{confidence}%，风险等级{risk_level}。",
            "insight_and_decision": f"投资建议：{recommendation}（置信度{confidence}%）。风险等级：{risk_level}。建议密切关注市场动态。",
            "core_review": {
                "fundamentals": "基本面分析显示公司经营状况稳定，盈利能力良好。",
                "news": "近期无重大负面新闻，市场关注度一般。",
                "technical": "技术面呈现震荡整理态势，短期趋势不明朗。",
                "sentiment": "市场情绪中性偏谨慎，投资者观望情绪浓厚。"
            },
            "personal_view_and_risk": f"主要风险：{risk_level}风险水平，需关注市场波动和行业政策变化。",
            "炒作点分析": "当前无明显炒作热点，建议关注行业政策变化。",
            "short_term_outlook": {
                "possibility": "中",
                "reason": "短期市场可能维持震荡，缺乏明确上涨动力。如有利好刺激，可能小幅上涨。"
            },
            "core_disagreement": {
                "bullish_arguments": "公司基本面稳健，长期价值可期",
                "bearish_arguments": "短期市场情绪偏弱，技术面承压",
                "consensus": "需要更多利好刺激",
                "key_disagreement": "短期走势判断存在分歧"
            },
            "action_items": [
                "持续关注相关新闻和公告",
                "设置技术位止损点",
                "等待更好的入场时机"
            ],
            "golden_quote": "谋定而后动，知止而有得"
        }
    
    def _create_fallback_report(self, analysis_id: str, original_content: Dict[str, Any]) -> SimplifiedReport:
        """创建备用的简化报告"""
        default_data = self._get_default_simplified_data(original_content)
        mapped_data = self._map_to_legacy_format(default_data)
        
        stock_code = original_content.get("symbol", "未知")
        stock_name = original_content.get("stock_name", stock_code)
        
        html_content = self._generate_fallback_html(
            stock_code=stock_code,
            stock_name=stock_name,
            simplified_data=default_data
        )
        
        return SimplifiedReport(
            analysis_id=analysis_id,
            original_summary=original_content.get("summary", ""),
            executive_summary=mapped_data["executive_summary"],
            decision_points=mapped_data["decision_points"],
            core_review=mapped_data["core_review"],
            risk_analysis=mapped_data["risk_analysis"],
            short_term_outlook=mapped_data["short_term_outlook"],
            action_items=mapped_data["action_items"],
            golden_quote=mapped_data["golden_quote"],
            html_content=html_content,
            compression_ratio=0.5,
            stock_code=stock_code,
            stock_name=stock_name,
            llm_raw_response="备用模板生成（无LLM响应）"
        )
    
    async def _get_simplified_report_from_db(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        """从数据库获取简化报告"""
        try:
            db = get_mongo_db()
            report = await db.simplified_reports.find_one({"analysis_id": analysis_id})
            if report:
                report.pop("_id", None)
                return report
        except Exception as e:
            logger.error(f"❌ 从数据库获取简化报告失败: {e}")
        return None
    
    async def _save_simplified_report_to_db(self, report: SimplifiedReport) -> None:
        """保存简化报告到数据库"""
        try:
            db = get_mongo_db()
            report_dict = report.to_dict()
            
            await db.simplified_reports.update_one(
                {"analysis_id": report.analysis_id},
                {"$set": report_dict},
                upsert=True
            )
            
            logger.info(f"✅ 简化报告已保存到数据库: {report.analysis_id}")
            logger.info(f"  股票: {report.stock_code} {report.stock_name}")
            logger.info(f"  包含原始LLM响应: {'是' if report.llm_raw_response else '否'}")
            
        except Exception as e:
            logger.error(f"❌ 保存简化报告到数据库失败: {e}")
    
    async def _save_html_to_file(self, analysis_id: str, stock_code: str, html_content: str) -> str:
        """将HTML保存到文件"""
        try:
            project_root = Path(__file__).parent.parent.parent
            simplified_dir = project_root / "data" / "simplified_reports"
            simplified_dir.mkdir(parents=True, exist_ok=True)
            
            today = datetime.now().strftime("%Y%m%d")
            filename = f"{stock_code}_{analysis_id}_{today}.html"
            file_path = simplified_dir / filename
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            
            logger.info(f"✅ 简化报告HTML已保存到文件: {file_path}")
            return str(file_path)
            
        except Exception as e:
            logger.error(f"❌ 保存简化报告HTML到文件失败: {e}")
            return ""
    
    def _dict_to_report(self, data: Dict[str, Any]) -> SimplifiedReport:
        """将字典转换为SimplifiedReport对象"""
        return SimplifiedReport(
            analysis_id=data.get("analysis_id", ""),
            original_summary=data.get("original_summary", ""),
            executive_summary=data.get("executive_summary", ""),
            decision_points=data.get("decision_points", []),
            core_review=data.get("core_review", {}),
            risk_analysis=data.get("risk_analysis", {}),
            short_term_outlook=data.get("short_term_outlook", {}),
            action_items=data.get("action_items", []),
            golden_quote=data.get("golden_quote", ""),
            html_content=data.get("html_content", ""),
            compression_ratio=data.get("compression_ratio", 0.0),
            stock_code=data.get("stock_code", "未知"),
            stock_name=data.get("stock_name", "未知"),
            llm_raw_response=data.get("llm_raw_response", "")
        )
    
    async def get_simplified_report(self, analysis_id: str) -> Optional[SimplifiedReport]:
        """获取已生成的简化报告"""
        try:
            data = await self._get_simplified_report_from_db(analysis_id)
            if data:
                return self._dict_to_report(data)
        except Exception as e:
            logger.error(f"❌ 获取简化报告失败: {analysis_id} - {e}")
        return None


# 全局简化报告服务实例
_simplifier_instance = None


def get_report_simplifier() -> ReportSimplifier:
    """获取报告简化服务实例（单例）"""
    global _simplifier_instance
    if _simplifier_instance is None:
        _simplifier_instance = ReportSimplifier()
    return _simplifier_instance