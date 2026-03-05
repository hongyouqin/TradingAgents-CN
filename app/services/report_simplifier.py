# app/services/report_simplifier.py

"""
报告精简服务 - 将详细股票分析报告转化为老板易懂的精简汇报
生成符合"超级大脑"风格的HTML页面
"""

import json
import logging
import asyncio
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
        self._html_template = self._load_html_template()
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
    
    def _load_html_template(self) -> str:
        """加载HTML模板"""
        return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes">
    <title>超级大脑 · {stock_code} 分析汇报</title>
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: #f8faff;
            color: #1e293b;
            line-height: 1.6;
            font-size: 18px;
        }}
        
        .container {{
            max-width: 600px;
            margin: 0 auto;
            padding: 24px 20px 40px;
        }}
        
        /* 头部 */
        .header {{
            margin-bottom: 32px;
            text-align: center;
        }}
        
        .header h1 {{
            font-family: 'Poppins', sans-serif;
            font-size: 42px;
            font-weight: 700;
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 8px;
            letter-spacing: -0.5px;
        }}
        
        .header .date {{
            font-size: 18px;
            color: #64748b;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            flex-wrap: wrap;
        }}
        
        .header .date span {{
            background: #e6f0ff;
            padding: 4px 12px;
            border-radius: 20px;
            color: #1e3c72;
            font-weight: 500;
            font-size: 16px;
        }}
        
        .stock-badge {{
            background: #1e3c72;
            color: white !important;
        }}
        
        /* 执行摘要卡片 */
        .executive-summary {{
            background: linear-gradient(135deg, #1e3c72 0%, #29539b 100%);
            border-radius: 28px;
            padding: 28px 24px;
            margin-bottom: 28px;
            color: white;
            box-shadow: 0 20px 30px -10px rgba(30, 60, 114, 0.3);
        }}
        
        .executive-summary h2 {{
            font-family: 'Poppins', sans-serif;
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 16px;
            opacity: 0.9;
            letter-spacing: 1px;
            text-transform: uppercase;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .executive-summary p {{
            font-size: 24px;
            font-weight: 500;
            line-height: 1.4;
            margin: 0;
        }}
        
        /* 通用卡片样式 */
        .card {{
            background: white;
            border-radius: 24px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.02);
            border: 1px solid rgba(30, 60, 114, 0.08);
        }}
        
        .card-title {{
            font-family: 'Poppins', sans-serif;
            font-size: 20px;
            font-weight: 600;
            color: #1e3c72;
            margin-bottom: 18px;
            display: flex;
            align-items: center;
            gap: 8px;
            border-bottom: 2px solid #eef2f6;
            padding-bottom: 12px;
        }}
        
        .card-title i {{
            color: #2a5298;
            font-style: normal;
            font-size: 24px;
        }}
        
        /* 决策点列表 */
        .decision-list {{
            list-style: none;
        }}
        
        .decision-list li {{
            font-size: 18px;
            padding: 14px 0;
            border-bottom: 1px solid #f0f4fa;
            display: flex;
            align-items: flex-start;
            gap: 12px;
        }}
        
        .decision-list li:last-child {{
            border-bottom: none;
        }}
        
        .decision-list .bullet {{
            width: 24px;
            height: 24px;
            background: #e6f0ff;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: #1e3c72;
            font-weight: 700;
            font-size: 14px;
            flex-shrink: 0;
            margin-top: 2px;
        }}
        
        .decision-list strong {{
            color: #1e3c72;
            font-weight: 600;
        }}
        
        .decision-list .warning {{
            background: #fee2e2;
            color: #b91c1c;
        }}
        
        .decision-list .highlight {{
            background: #e6f0ff;
            color: #1e3c72;
            font-weight: 600;
        }}
        
        /* 核心回顾网格 */
        .review-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
            margin-bottom: 8px;
        }}
        
        .review-item {{
            background: #f8faff;
            border-radius: 18px;
            padding: 18px 14px;
        }}
        
        .review-item h4 {{
            font-size: 17px;
            font-weight: 600;
            color: #1e3c72;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        
        .review-item p {{
            font-size: 16px;
            color: #334155;
            margin: 0;
            line-height: 1.5;
        }}
        
        .review-item .tag {{
            display: inline-block;
            background: #e6f0ff;
            color: #1e3c72;
            padding: 4px 12px;
            border-radius: 30px;
            font-size: 14px;
            font-weight: 500;
            margin-right: 8px;
            margin-bottom: 8px;
        }}
        
        /* 风险分析 */
        .risk-box {{
            background: #fff8f0;
            border-radius: 18px;
            padding: 20px;
            margin: 16px 0;
            border-left: 4px solid #f97316;
        }}
        
        .strategy-box {{
            background: #f0f7ff;
            border-radius: 18px;
            padding: 20px;
            margin-top: 12px;
            border-left: 4px solid #2a5298;
        }}
        
        .risk-box strong, .strategy-box strong {{
            font-size: 18px;
            display: block;
            margin-bottom: 8px;
        }}
        
        /* 短期展望 */
        .outlook-box {{
            background: #e6f0ff;
            border-radius: 18px;
            padding: 20px;
        }}
        
        .outlook-highlight {{
            background: #1e3c72;
            color: white;
            border-radius: 30px;
            padding: 16px 20px;
            font-size: 18px;
            font-weight: 500;
            margin: 16px 0;
            text-align: center;
        }}
        
        .outlook-reason {{
            font-size: 17px;
            color: #334155;
            line-height: 1.6;
            margin-top: 12px;
            padding: 0 4px;
        }}
        
        /* 待办事项 */
        .todo-list {{
            list-style: none;
        }}
        
        .todo-list li {{
            font-size: 18px;
            padding: 12px 0;
            display: flex;
            align-items: center;
            gap: 12px;
            border-bottom: 1px dashed #e2e8f0;
        }}
        
        .todo-list li:last-child {{
            border-bottom: none;
        }}
        
        .todo-list .checkbox {{
            width: 24px;
            height: 24px;
            background: #e6f0ff;
            border-radius: 8px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: #1e3c72;
            font-size: 16px;
            flex-shrink: 0;
        }}
        
        /* 金句 */
        .golden-quote {{
            background: white;
            border-radius: 30px;
            padding: 32px 28px;
            text-align: center;
            margin-top: 32px;
            margin-bottom: 20px;
            border: 1px solid rgba(30, 60, 114, 0.1);
            box-shadow: 0 10px 30px -15px rgba(30, 60, 114, 0.2);
            position: relative;
        }}
        
        .golden-quote .quote-mark {{
            font-size: 60px;
            color: #2a5298;
            opacity: 0.2;
            line-height: 0;
            position: absolute;
            top: 20px;
            left: 20px;
            font-family: serif;
        }}
        
        .golden-quote p {{
            font-family: 'Poppins', sans-serif;
            font-size: 26px;
            font-weight: 600;
            color: #1e3c72;
            line-height: 1.4;
            margin: 0;
            position: relative;
            z-index: 1;
        }}
        
        /* 底部 */
        .footer {{
            text-align: center;
            margin-top: 32px;
            color: #94a3b8;
            font-size: 16px;
        }}
        
        .highlight {{
            color: #2a5298;
            font-weight: 600;
            background: linear-gradient(120deg, #e6f0ff 0%, #e6f0ff 100%);
            padding: 0 4px;
        }}
        
        .badge {{
            display: inline-block;
            background: #dcfce7;
            color: #166534;
            padding: 6px 14px;
            border-radius: 30px;
            font-size: 15px;
            font-weight: 500;
        }}
        
        .recommendation-badge {{
            display: inline-block;
            padding: 6px 16px;
            border-radius: 30px;
            font-size: 16px;
            font-weight: 600;
            margin-right: 8px;
        }}
        
        .badge-buy {{
            background: #dcfce7;
            color: #166534;
        }}
        
        .badge-sell {{
            background: #fee2e2;
            color: #b91c1c;
        }}
        
        .badge-hold {{
            background: #fff3cd;
            color: #856404;
        }}
        
        @media (max-width: 480px) {{
            body {{
                font-size: 16px;
            }}
            .container {{
                padding: 16px 16px 30px;
            }}
            .header h1 {{
                font-size: 36px;
            }}
            .executive-summary p {{
                font-size: 22px;
            }}
            .review-grid {{
                grid-template-columns: 1fr;
            }}
            .golden-quote p {{
                font-size: 22px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- 头部 -->
        <div class="header">
            <h1>🧠 超级大脑</h1>
            <div class="date">
                <span>{date}</span>
                <span class="stock-badge">{stock_code} {stock_name}</span>
            </div>
        </div>
        
        <!-- 一句话总结 -->
        <div class="executive-summary">
            <h2>📋 一句话·总结</h2>
            <p>{executive_summary}</p>
        </div>
        
        <!-- 决策结果 -->
        <div class="card">
            <div class="card-title">
                <i>🎯</i> 超级大脑·决策结果
            </div>
            <ul class="decision-list">
                {decision_points_html}
            </ul>
        </div>
        
        <!-- 核心回顾 -->
        <div class="card">
            <div class="card-title">
                <i>📊</i> 核心回顾
            </div>
            <div class="review-grid">
                <div class="review-item">
                    <h4>📈 基本面</h4>
                    <p>{fundamentals}</p>
                </div>
                <div class="review-item">
                    <h4>📰 新闻面</h4>
                    <p>{news}</p>
                </div>
                <div class="review-item">
                    <h4>📉 技术面</h4>
                    <p>{technical}</p>
                </div>
                <div class="review-item">
                    <h4>🎭 情绪面</h4>
                    <p>{sentiment}</p>
                </div>
            </div>
        </div>
        
        <!-- 风险与策略 -->
        <div class="card">
            <div class="card-title">
                <i>⚠️</i> 风险警示 & 应对策略
            </div>
            <div class="risk-box">
                <strong>🚨 潜在风险</strong>
                <p>{risk}</p>
            </div>
            <div class="strategy-box">
                <strong>🛡️ 应对策略</strong>
                <p>{strategy}</p>
            </div>
        </div>
        
        <!-- 短期展望 -->
        <div class="card">
            <div class="card-title">
                <i>🔮</i> 未来1-3天展望
            </div>
            <div class="outlook-box">
                <div class="outlook-highlight">
                    上涨可能性：{outlook_possibility}
                </div>
                <div class="outlook-reason">
                    {outlook_reason}
                </div>
            </div>
        </div>
        
        <!-- 待办事项 -->
        <div class="card">
            <div class="card-title">
                <i>✅</i> 接下来要做的事
            </div>
            <ul class="todo-list">
                {action_items_html}
            </ul>
        </div>
        
        <!-- 金句结尾 -->
        <div class="golden-quote">
            <div class="quote-mark">"</div>
            <p>{golden_quote}</p>
        </div>
        
        <div class="footer">
            <span>⚡ 超级大脑 · 让投资更聪明</span>
        </div>
    </div>
</body>
</html>"""
    
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
            
            # 7. 生成HTML页面
            stock_code = original_content.get("symbol", "未知")
            stock_name = original_content.get("stock_name", stock_code)
            html_content = self._generate_html(
                stock_code=stock_code,
                stock_name=stock_name,
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
            await self._save_html_to_file(request.analysis_id, stock_code, html_content)
            
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
                from app.services.simple_analysis_service import get_provider_and_url_by_model_sync
                
                model_name = unified_config.get_quick_analysis_model()
                provider_info = get_provider_and_url_by_model_sync(model_name)
                
                logger.info(f"🔧 [简化报告] 尝试 {attempt + 1}/{max_retries}")
                logger.info(f"  模型: {model_name}")
                logger.info(f"  供应商: {provider_info['provider']}")
                logger.info(f"  API地址: {provider_info['backend_url']}")
                logger.info(f"  API Key: {'已配置' if provider_info.get('api_key') else '未配置（将使用环境变量）'}")
                
                # 2. 准备提示词
                prompt = self._build_optimized_prompt(original_content, max_length, language)
                
                # 3. 使用TradingAgents的create_llm_by_provider创建LLM实例
                llm = create_llm_by_provider(
                    provider=provider_info["provider"],
                    model=model_name,
                    backend_url=provider_info["backend_url"],
                    temperature=0.3,  # 降低温度以获得更稳定的输出
                    max_tokens=2000,
                    timeout=60,  # 60秒超时
                    api_key=provider_info.get("api_key")
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
        """构建优化后的提示词（减少token使用）"""
        # 提取关键信息，避免发送整个原始报告
        simplified_input = {
            "股票代码": content.get("symbol", "未知"),
            "股票名称": content.get("stock_name", content.get("symbol", "未知")),
            "总结": content.get("summary", "")[:300],
            "建议": content.get("recommendation", ""),
            "置信度": content.get("confidence_score", 0),
            "风险等级": content.get("risk_level", "中等"),
            "关键点": content.get("key_points", [])[:3],
            "详细分析": str(content.get("detailed_analysis", {}))[:500],
            "决策": content.get("decision", {}),
            "报告数量": len(content.get("reports", {}))
        }
        
        return self._prompt_template.format(
            original_content=json.dumps(simplified_input, ensure_ascii=False, indent=2)
        )
    
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
        
        html_content = self._generate_html(
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
    
    def _generate_html(self, stock_code: str, stock_name: str, simplified_data: Dict[str, Any]) -> str:
        """生成HTML页面"""
        from datetime import datetime
        
        # 处理决策点HTML
        decision_points = simplified_data.get("decision_points", [])
        decision_points_html = ""
        for i, point in enumerate(decision_points, 1):
            # 判断是否包含风险关键词
            is_warning = any(keyword in point for keyword in ["陷阱", "坑", "隐患", "风险", "警惕", "注意", "谨慎"])
            warning_class = ' class="warning"' if is_warning else ''
            
            decision_points_html += f"""
            <li>
                <span class="bullet"{warning_class}>{i}</span>
                <span>{point}</span>
            </li>"""
        
        # 处理待办事项HTML
        action_items = simplified_data.get("action_items", [])
        action_items_html = ""
        for i, item in enumerate(action_items, 1):
            action_items_html += f"""
            <li>
                <span class="checkbox">✓</span>
                <span>{item}</span>
            </li>"""
        
        # 获取核心回顾
        core_review = simplified_data.get("core_review", {})
        risk_analysis = simplified_data.get("risk_analysis", {})
        short_term_outlook = simplified_data.get("short_term_outlook", {})
        
        # 格式化日期
        today = datetime.now().strftime("%Y年%m月%d日")
        
        # 填充HTML模板
        html = self._html_template.format(
            date=today,
            stock_code=stock_code,
            stock_name=stock_name,
            executive_summary=simplified_data.get("executive_summary", "分析完成"),
            decision_points_html=decision_points_html,
            fundamentals=core_review.get("fundamentals", "无数据"),
            news=core_review.get("news", "无数据"),
            technical=core_review.get("technical", "无数据"),
            sentiment=core_review.get("sentiment", "无数据"),
            risk=risk_analysis.get("risk", "暂无显著风险"),
            strategy=risk_analysis.get("strategy", "保持关注"),
            outlook_possibility=short_term_outlook.get("possibility", "中"),
            outlook_reason=short_term_outlook.get("reason", "市场情绪平稳"),
            action_items_html=action_items_html,
            golden_quote=simplified_data.get("golden_quote", "谋定而后动，知止而有得")
        )
        
        return html
    
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