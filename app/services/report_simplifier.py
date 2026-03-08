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
        compression_ratio: float = 0.0
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
        return """你是一个专业的投资顾问“超级大脑”，正在向老板汇报一份股票分析报告。

文件里面是一份股票的分析报告。我现在需要帮我读懂这份报告。

现在我要求你扮演我的私人投资顾问。你的名字叫“超级大脑”，我是公司的老板，你是我手下的员工，你参与了这份股票分析报告的撰写，你现在要向老板汇报这份报告，用简单的言语让老板读懂这份报告。

原始分析报告内容：
{original_content}

给老板汇报你的总结、你的看法、你的分析、你的建议等等。如果股票分析报告中，有一些陷阱、埋的坑、隐患，你要识别出来这些不好的意图，并帮助老板避坑。

### “超级大脑”说话的语气、行为、语言风格要求：
- 这里你的语气像是一个军师给主帅出谋划策一样，也像是朋友间聊天一样，谈笑风生。
- 你是一位深谙中国人情世故哲学的“董秘”。你的任务不是生硬的会议记录，而是为老板提供一份清晰、有趣、简洁的“汇报”。

### 汇报要求：
1. 首先用一段简单的文字总结这份报告（尽量用2-3句话概括完成，要求言简意赅）。
2. 超级大脑的决策结果（一个点一个点的总结）。总结要精炼，不要长篇大论。如果谈话内容中，存在一些陷阱、埋的坑、隐患，你要识别出来这些不好的意图，并在你的决策结果里面指出。
3. 报告的核心回顾（解读基本面、新闻面、技术面、市场情绪面）。
4. 你的看法与风险警示。如果有必要请说出相应的应对策略。
5. 未来1-3天，如果股票出现上涨，请分析这方面的可能性和原因（是否存在短期炒作、庄家控盘、散户情绪、板块爆炒、相关股票题材关联、重大新闻披露、等等原因？如果有，请说明）。
6. 列出3条左右接下来要做的事（接下来的行动是什么）（待办事项）。
7. 最后以一句对老板说的“金句”来作为结尾。

请将以上内容组织成JSON格式返回，包含以下字段：
- executive_summary: 一句话总结（字符串）
- decision_points: 决策要点列表（每个要点是一个字符串，最多5个）
- core_review: 核心回顾对象，包含以下字段：
    - fundamentals: 基本面解读（字符串）
    - news: 新闻面解读（字符串）
    - technical: 技术面解读（字符串）
    - sentiment: 情绪面解读（字符串）
- risk_analysis: 风险分析对象，包含以下字段：
    - risk: 潜在风险（字符串）
    - strategy: 应对策略（字符串）
- short_term_outlook: 短期展望对象，包含以下字段：
    - possibility: 上涨可能性（如"高"、"中"、"低"）
    - reason: 原因分析（字符串）
- action_items: 待办事项列表（每个事项是一个字符串，最多3条）
- golden_quote: 金句（字符串）

确保返回的是有效的JSON格式，不要包含任何其他文字说明。"""
    
    def _load_html_generation_prompt(self) -> str:
        """加载HTML生成提示词模板"""
        return """根据以下股票分析汇报数据，创建一个专业的H5网页：

    汇报数据：
    {simplified_data}

    网页设计要求：
    1. 采用现代科技感设计风格，使用Poppins/Inter字体组合
    2. 使用以下核心配色方案：
    - 主色调：#1e40af（深蓝色）
    - 强调色：#3b82f6（亮蓝色）
    - 警示色：#f59e0b（橙色）
    - 危险色：#ef4444（红色）
    - 背景色：#f8fafc（浅灰蓝）
    - 卡片背景：#ffffff（纯白）
    - 文字颜色：#1e293b（深灰）

    3. 字体大小规范：
    - 正文：至少20px（手机上）
    - 标题h1：2.5rem左右
    - 标题h2：2.0rem左右
    - 标题h3：1.6rem左右
    - 列表项：1.3rem左右

    4. 布局要求：
    - 英雄区（header）：深蓝色渐变背景(135deg, #1e40af, #3b82f6)，圆角20px，内边距25-30px，包含汇报标题和总结
    - 所有内容区块使用卡片式布局（圆角16px，内边距30px，阴影0 4px 12px rgba(0,0,0,0.08)）
    - 卡片悬停时有轻微上浮效果
    - 关键数据用迷你卡片网格展示（3列，可换行）
    - 核心建议用特殊卡片样式区分（不同左边框颜色）
    - 行动步骤用带数字标识的列表展示（圆形数字图标）
    - 尾部金句区：渐变背景，带大号引导符号

    5. 强调标记：
    - 核心词汇用highlight类（橙色背景加粗）
    - 危险提示用danger类（红色背景或红色文字加粗）
    - 警告用warning类（橙色文字加粗）
    - 技巧提示用tip类（绿色背景）

    6. 可视化要求：
    - 研究数据用迷你卡片展示，每个卡片包含：标题、大号数值、趋势说明
    - 趋势向下用红色(#ef4444)，中性用橙色(#f59e0b)
    - 卡片背景用浅蓝色渐变

    7. 响应式要求：
    - 最大宽度800px，居中显示
    - 移动端适配：字体适当缩小，卡片内边距减少
    - 使用viewport适配所有手机屏幕

    8. 内容结构要求：
    - 必须包含汇报的所有内容
    - 核心结论速览（左侧边蓝色边框卡片）
    - 决策建议（左侧边橙色边框卡片）
    - 四维深度分析（基本面、新闻面、技术面、情绪面）
    - 风险警示（左侧边红色边框卡片）
    - 上涨可能性分析
    - 下一步行动计划（带数字标识）
    - 尾部金句

    9. 代码规范：
    - 使用CSS变量定义所有颜色
    - 添加适当的CSS注释
    - 确保HTML结构语义化
    - 引入Google Fonts字体

    要求：
    - 只返回完整的HTML代码，不要包含任何解释性文字
    - 确保HTML代码可以直接在浏览器中运行
    - 代码要整洁、专业，注释清晰
    - 根据汇报数据的实际情况，灵活调整具体内容，但保持整体结构和样式规范"""
    
    
    def _load_html_generation_prompt2(self) -> str:
        """加载HTML生成提示词模板"""
        return """根据以下股票分析汇报数据，创建一个专业的H5网页：

汇报数据：
{simplified_data}

网页设计要求：
1. 采用现代科技感设计风格（Poppins/Inter字体组合）
2. 研究数据可以通过可视化图表和可视化卡片展示，让老板可以更直观地探索数据，更好地理解内容。如果觉得没有必要强行展示图表，可以不用显示图表。
3. 老板年龄大了，眼神不太好，网页里面的字尽量做大一些（建议正文至少18px，标题更大）。
4. 网页的背景颜色用白色或者浅色。
5. 文字里面的核心重点词汇、句子用css标签加粗或者加颜色显示（建议使用深蓝色#1e3c72作为强调色）。
6. 重点关注页面在手机上浏览的体验，不考虑电脑端适配。
7. 需要包含汇报的所有内容，结构清晰。
8. 页面顶部英雄区呈现汇报的“标题”和“总结”，英雄区部分选一个颜色使用深色到浅色的渐变（建议使用蓝色系#1e3c72到#2a5298），营造有趣的氛围，英雄区与后面模块内容之间要有一定的留白区域。
9. 页面尾部只显示金句作为结尾。
10. 使用响应式设计，确保在各种手机屏幕上都能良好显示。
11. 增加适当的间距和留白，提高可读性。
12. 使用卡片式布局，每个模块独立成卡片，增强视觉层次感。

要求：
- 只返回完整的HTML代码，不要包含任何解释性文字
- 确保HTML代码可以直接在浏览器中运行
- 引入必要的字体和样式
- 代码要整洁，有适当的注释
- 颜色搭配要专业、舒适，适合长时间阅读"""
    
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
            simplified_data = await self._call_llm_for_simplification(
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
            
            # 7. 调用LLM生成HTML页面
            html_content = await self._generate_html_by_llm(
                stock_code=original_content.get("symbol", "未知"),
                stock_name=original_content.get("stock_name", original_content.get("symbol", "未知")),
                simplified_data=simplified_data
            )
            
            # 8. 创建简化报告对象
            simplified_report = SimplifiedReport(
                analysis_id=request.analysis_id,
                original_summary=original_summary,
                executive_summary=simplified_data.get("executive_summary", ""),
                decision_points=simplified_data.get("decision_points", []),
                core_review=simplified_data.get("core_review", {}),
                risk_analysis=simplified_data.get("risk_analysis", {}),
                short_term_outlook=simplified_data.get("short_term_outlook", {}),
                action_items=simplified_data.get("action_items", []),
                golden_quote=simplified_data.get("golden_quote", "谋定而后动，知止而有得"),
                html_content=html_content,
                compression_ratio=compression_ratio
            )
            
            # 9. 保存到数据库和缓存
            await self._save_simplified_report_to_db(simplified_report)
            self._cache[cache_key] = simplified_report
            
            # 10. 可选：保存HTML到文件
            await self._save_html_to_file(request.analysis_id, original_content.get("symbol", "未知"), html_content)
            
            logger.info(f"✅ 简化报告生成完成: {request.analysis_id}, 压缩比例: {compression_ratio:.2%}")
            return simplified_report
            
        except Exception as e:
            logger.error(f"❌ 生成简化报告失败: {e}")
            # 返回一个基本的简化报告，避免前端报错
            return self._create_fallback_report(request.analysis_id, request.original_content)
    
    async def _call_llm_for_simplification(
        self, 
        original_content: Dict[str, Any],
        max_length: int,
        language: str
    ) -> Dict[str, Any]:
        """调用LLM生成简化内容（带重试机制）"""
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                # 1. 获取模型配置
                llm_config = self._get_llm_config()
                
                logger.info(f"🔧 [简化报告] 尝试 {attempt + 1}/{max_retries}")
                logger.info(f"  模型: {llm_config['model_name']}")
                logger.info(f"  供应商: {llm_config['provider']}")
                logger.info(f"  API地址: {llm_config['backend_url']}")
                logger.info(f"  API Key: {'已配置' if llm_config.get('api_key') else '未配置（将使用环境变量）'}")
                
                # 2. 准备提示词
                prompt = self._build_optimized_prompt(original_content, max_length, language)
                
                # 3. 使用TradingAgents的create_llm_by_provider创建LLM实例
                llm = create_llm_by_provider(
                    provider=llm_config["provider"],
                    model=llm_config["model_name"],
                    backend_url=llm_config["backend_url"],
                    temperature=0.4,  # 略高一点的温度，让设计更有创意
                    max_tokens=4000,  # 增加token限制以容纳完整的HTML
                    timeout=80,
                    api_key=llm_config["api_key"]
                )
                
                # 4. 调用LLM（同步调用，因为create_llm_by_provider返回的是同步LLM）
                # 使用线程池执行，避免阻塞事件循环
                loop = asyncio.get_event_loop()
                
                # 根据不同LLM类型选择合适的调用方法
                if hasattr(llm, 'ainvoke'):
                    # 如果有异步方法，直接使用
                    response = await llm.ainvoke(prompt)
                else:
                    # 否则在线程池中执行同步调用
                    response = await loop.run_in_executor(
                        None, 
                        llm.invoke, 
                        prompt
                    )
                
                # 5. 处理响应
                if hasattr(response, 'content'):
                    # LangChain消息对象
                    content = response.content
                elif isinstance(response, str):
                    content = response
                else:
                    content = str(response)
                
                logger.info(f"✅ LLM响应长度: {len(content)} 字符")
                
                # 6. 解析JSON响应
                return self._parse_llm_response(content)
                
            except Exception as e:
                logger.warning(f"⚠️ LLM调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))  # 指数退避
                else:
                    # 最后一次失败，返回默认结构
                    logger.error(f"❌ 所有重试都失败，返回默认结构")
                    return self._get_default_simplified_data(original_content)
        
        return self._get_default_simplified_data(original_content)
    
    def _build_optimized_prompt(self, content: Dict[str, Any], max_length: int, language: str) -> str:
        """构建优化后的提示词（直接传递原始报告内容）"""
        # 直接使用完整的原始报告内容
        return self._prompt_template.format(
            original_content=json.dumps(content, ensure_ascii=False, indent=2)
        )
    
    def _get_llm_config(self) -> Dict[str, Any]:
        """获取LLM配置
        优先从环境变量获取，如果没有则从统一配置中获取
        """
        
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
            
            # 修正：使用单引号
            logger.info(f"  模型: {model_name}")
            logger.info(f"  提供商: {provider_info['provider']}")
            logger.info(f"  后端URL: {provider_info['backend_url']}")
            
            return {
                "model_name": model_name,
                "provider": provider_info["provider"],
                "backend_url": provider_info["backend_url"],
                "api_key": provider_info.get("api_key")
            }
        
        
    async def _generate_html_by_llm(self, stock_code: str, stock_name: str, simplified_data: Dict[str, Any]) -> str:
        """调用LLM生成HTML页面"""
        max_retries = 2
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                # 1. 获取模型配置
                llm_config = self._get_llm_config()
                
                # 2. 准备HTML生成提示词
                # 补充股票信息到简化数据中
                enhanced_data = {
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "date": datetime.now().strftime("%Y年%m月%d日"),
                    **simplified_data
                }
                
                prompt = self._html_generation_prompt.format(
                    simplified_data=json.dumps(enhanced_data, ensure_ascii=False, indent=2)
                )
                
                # 3. 创建LLM实例
                llm = create_llm_by_provider(
                    provider=llm_config["provider"],
                    model=llm_config["model_name"],
                    backend_url=llm_config["backend_url"],
                    temperature=0.4,  # 略高一点的温度，让设计更有创意
                    max_tokens=4000,  # 增加token限制以容纳完整的HTML
                    timeout=80,
                    api_key=llm_config["api_key"]
                )
                
                # 4. 调用LLM
                loop = asyncio.get_event_loop()
                
                if hasattr(llm, 'ainvoke'):
                    response = await llm.ainvoke(prompt)
                else:
                    response = await loop.run_in_executor(None, llm.invoke, prompt)
                
                # 5. 处理响应
                if hasattr(response, 'content'):
                    content = response.content
                elif isinstance(response, str):
                    content = response
                else:
                    content = str(response)
                
                logger.info(f"✅ HTML生成响应长度: {len(content)} 字符")
                
                # 6. 提取HTML内容（处理可能的代码块包裹）
                html_content = self._extract_html_from_response(content)
                
                # 验证HTML基本结构
                if "<!DOCTYPE html>" in html_content and "<html" in html_content and "</html>" in html_content:
                    return html_content
                else:
                    raise ValueError("生成的HTML不完整")
                
            except Exception as e:
                logger.warning(f"⚠️ HTML生成失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))
                else:
                    # 使用备用HTML模板
                    logger.error("❌ HTML生成失败，使用备用模板")
                    return self._generate_fallback_html(stock_code, stock_name, simplified_data)
        
        # 返回备用HTML
        return self._generate_fallback_html(stock_code, stock_name, simplified_data)
    
    def _extract_html_from_response(self, response: str) -> str:
        """从LLM响应中提取HTML内容"""
        import re
        
        # 尝试提取```html```包裹的内容
        html_match = re.search(r'```html\s*(.*?)\s*```', response, re.DOTALL)
        if html_match:
            return html_match.group(1).strip()
        
        # 尝试提取任何HTML内容
        html_start = response.find('<!DOCTYPE html>')
        if html_start == -1:
            html_start = response.find('<html')
        
        if html_start != -1:
            html_end = response.rfind('</html>')
            if html_end != -1:
                return response[html_start:html_end + 7].strip()
        
        # 如果都提取不到，返回原始响应
        return response.strip()
    
    def _generate_fallback_html(self, stock_code: str, stock_name: str, simplified_data: Dict[str, Any]) -> str:
        """生成备用HTML（当LLM生成失败时）"""
        core_review = simplified_data.get("core_review", {})
        risk_analysis = simplified_data.get("risk_analysis", {})
        short_term_outlook = simplified_data.get("short_term_outlook", {})
        
        # 构建基础的移动端友好HTML
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>超级大脑 · {stock_code} 分析汇报</title>
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', sans-serif;
            background: #ffffff;
            color: #1e293b;
            line-height: 1.8;
            font-size: 20px; /* 大号字体 */
            padding: 0;
            margin: 0;
        }}
        .container {{
            max-width: 100%;
            padding: 0 20px;
            margin: 0 auto;
        }}
        /* 英雄区 */
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
        /* 卡片样式 */
        .card {{
            background: #f8f9fa;
            border-radius: 20px;
            padding: 25px;
            margin-bottom: 25px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        }}
        .card h2 {{
            font-family: 'Poppins', sans-serif;
            font-size: 28px;
            color: #1e3c72;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid #e6f0ff;
        }}
        /* 列表样式 */
        ul {{
            list-style: none;
            padding-left: 10px;
        }}
        li {{
            margin-bottom: 15px;
            padding-left: 10px;
            position: relative;
            font-size: 20px;
        }}
        li:before {{
            content: "•";
            color: #2a5298;
            font-weight: bold;
            position: absolute;
            left: -15px;
            font-size: 24px;
        }}
        /* 核心回顾网格 */
        .review-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 15px;
        }}
        .review-item {{
            background: #e6f0ff;
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
        /* 风险和策略 */
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
        /* 重点强调 */
        .highlight {{
            color: #1e3c72;
            font-weight: 700;
        }}
        /* 金句 */
        .golden-quote {{
            font-family: 'Poppins', sans-serif;
            font-size: 24px;
            text-align: center;
            padding: 30px 20px;
            color: #1e3c72;
            font-weight: 600;
            margin-top: 20px;
        }}
        /* 响应式调整 */
        @media (max-width: 480px) {{
            body {{ font-size: 19px; }}
            .hero h1 {{ font-size: 32px; }}
            .hero .summary {{ font-size: 21px; }}
            .card h2 {{ font-size: 26px; }}
            li {{ font-size: 19px; }}
        }}
    </style>
</head>
<body>
    <!-- 英雄区 -->
    <div class="hero">
        <div class="container">
            <h1>🧠 超级大脑 · {stock_code} {stock_name}</h1>
            <div class="summary">{simplified_data.get('executive_summary', '分析总结')}</div>
        </div>
    </div>

    <div class="container">
        <!-- 决策结果 -->
        <div class="card">
            <h2>🎯 超级大脑·决策结果</h2>
            <ul>
                {''.join([f'<li>{point}</li>' for point in simplified_data.get('decision_points', [])])}
            </ul>
        </div>

        <!-- 核心回顾 -->
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

        <!-- 风险与策略 -->
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

        <!-- 短期展望 -->
        <div class="card">
            <h2>🔮 未来1-3天展望</h2>
            <p class="highlight">上涨可能性：{short_term_outlook.get('possibility', '中')}</p>
            <p>{short_term_outlook.get('reason', '市场情绪平稳')}</p>
        </div>

        <!-- 待办事项 -->
        <div class="card">
            <h2>✅ 接下来要做的事</h2>
            <ul>
                {''.join([f'<li>{item}</li>' for item in simplified_data.get('action_items', [])])}
            </ul>
        </div>

        <!-- 金句结尾 -->
        <div class="golden-quote">
            {simplified_data.get('golden_quote', '谋定而后动，知止而有得')}
        </div>
    </div>
</body>
</html>"""
        return html
    
    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """解析LLM响应，处理各种格式"""
        try:
            # 直接解析JSON
            return json.loads(response)
        except:
            try:
                # 尝试提取JSON
                import re
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group())
            except:
                pass
            
            try:
                # 尝试提取被```json```包裹的JSON
                json_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
                if json_block:
                    return json.loads(json_block.group(1))
            except:
                pass
        
        # 解析失败，记录错误并返回默认结构
        logger.error(f"❌ 解析LLM响应失败，响应内容: {response[:200]}...")
        return self._get_default_simplified_data({})
    
    def _get_default_simplified_data(self, original: Dict[str, Any]) -> Dict[str, Any]:
        """获取默认的简化数据结构（当LLM失败时）"""
        symbol = original.get("symbol", "未知")
        recommendation = original.get("recommendation", "观望")
        confidence = original.get("confidence_score", 0)
        risk_level = original.get("risk_level", "中等")
        
        return {
            "executive_summary": f"分析了{symbol}，建议{recommendation}，置信度{confidence}%，风险等级{risk_level}。",
            "decision_points": [
                f"投资建议：{recommendation}（置信度{confidence}%）",
                f"风险等级：{risk_level}",
                "建议密切关注市场动态"
            ],
            "core_review": {
                "fundamentals": "基本面分析显示公司经营状况稳定，盈利能力良好。",
                "news": "近期无重大负面新闻，市场关注度一般。",
                "technical": "技术面呈现震荡整理态势，短期趋势不明朗。",
                "sentiment": "市场情绪中性偏谨慎，投资者观望情绪浓厚。"
            },
            "risk_analysis": {
                "risk": f"主要风险：{risk_level}风险水平，需关注市场波动和行业政策变化。",
                "strategy": "建议分批建仓，设置止损位，控制仓位风险。"
            },
            "short_term_outlook": {
                "possibility": "中",
                "reason": "短期市场可能维持震荡，缺乏明确上涨动力。如有利好刺激，可能小幅上涨。"
            },
            "action_items": [
                "持续关注相关新闻和公告",
                "设置技术位止损点",
                "等待更好的入场时机"
            ],
            "golden_quote": "谋定而后动，知止而有得"
        }
    
    def _create_fallback_report(self, analysis_id: str, original_content: Dict[str, Any]) -> SimplifiedReport:
        """创建备用的简化报告（当完全失败时）"""
        default_data = self._get_default_simplified_data(original_content)
        
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
            executive_summary=default_data["executive_summary"],
            decision_points=default_data["decision_points"],
            core_review=default_data["core_review"],
            risk_analysis=default_data["risk_analysis"],
            short_term_outlook=default_data["short_term_outlook"],
            action_items=default_data["action_items"],
            golden_quote=default_data["golden_quote"],
            html_content=html_content,
            compression_ratio=0.5
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
            
            # 转换为字典
            report_dict = report.to_dict()
            
            # 保存到 simplified_reports 集合
            await db.simplified_reports.update_one(
                {"analysis_id": report.analysis_id},
                {"$set": report_dict},
                upsert=True
            )
            
            logger.info(f"✅ 简化报告已保存到数据库: {report.analysis_id}")
            
        except Exception as e:
            logger.error(f"❌ 保存简化报告到数据库失败: {e}")
    
    async def _save_html_to_file(self, analysis_id: str, stock_code: str, html_content: str) -> str:
        """将HTML保存到文件"""
        try:
            import os
            from pathlib import Path
            
            # 获取项目根目录
            project_root = Path(__file__).parent.parent.parent
            
            # 创建简化报告目录
            simplified_dir = project_root / "data" / "simplified_reports"
            simplified_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成文件名：股票代码_分析ID_日期.html
            from datetime import datetime
            today = datetime.now().strftime("%Y%m%d")
            filename = f"{stock_code}_{analysis_id}_{today}.html"
            file_path = simplified_dir / filename
            
            # 保存HTML文件
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
            compression_ratio=data.get("compression_ratio", 0.0)
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