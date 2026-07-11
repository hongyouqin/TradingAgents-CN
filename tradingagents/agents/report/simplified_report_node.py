"""
简化报告节点 - 作为 LangGraph 的最后一个节点
接收所有分析报告和最终决策，调用 LLM 生成老板易懂的精简汇报（结构化 JSON）
"""

import json
import time
import re
import logging
from datetime import datetime
from typing import Dict, Any, Tuple

from tradingagents.utils.logging_init import get_logger

logger = get_logger("default")

# 提示词模板（与 app/services/report_simplifier.py 的 _load_prompt_template 保持一致）
SIMPLIFY_PROMPT_TEMPLATE = """你是一个专业的投资顾问"牛逼股票"，正在向一位炒股的散户汇报一份股票分析报告。

原始分析报告内容：
{original_content}

### "牛逼股票"角色设定：
- 你的名字叫"牛逼股票"。
- 语气像朋友聊天一样，轻松、诚恳、接地气。
- 你是深谙中国A股实盘技术形态的实战专家。
- 语言简洁、抓重点、有干货，能给出眼前一亮的观点。

### 技术分析铁律（必须严格遵守）：
1. 布林带收口后向上突破上轨 = 强势变盘信号，趋势延续，应持仓或加仓，不卖出。
2. 高位RSI超买 + 布林收口向上 = 强势多头钝化，极易继续上涨，不盲目看空。
3. 严禁单纯因为RSI超买就建议卖出，必须结合布林带形态。
4. 布林收口=变盘窗口，向上突破=多头启动，不是见顶。
5. 技术面必须包含：量价关系 + Trend-Emotion-Timing 四大量化指标。

### 汇报要求：
1. 标题直观，不出现"报告"二字。
2. 100字以内简洁总结。
3. 深度洞察+决策：300字以内，给出战略判断。
4. 【新增】公司系统性介绍：简洁介绍公司主营业务、行业地位、核心竞争力
5. 【新增】股票题材关键词：列出3-8个最核心的题材/概念标签
6. 核心回顾（基本面、新闻面、技术面、情绪面）：
  技术面必须通俗解释：
  - Trend-Score 趋势得分
  - Emotion-Index 情绪指数
  - Anchored Trend-Score 锚定趋势得分
  - Timing-Indicator 时机指标
7. 个人看法与风险警示。
8. 炒作点分析：是否符合A股"故事驱动估值"。
9. 综合投资建议：不简单说买入/卖出，结合趋势、情绪、时机、布林形态。
10. 多空双方核心分歧与底层逻辑。
11. 待办事项 3 条。
12. 结尾金句。

请返回纯净JSON，包含以下字段：
- title: 标题
- executive_summary: 一句话总结
- company_intro: 公司系统性介绍
- theme_keywords: 股票题材关键词，数组格式
- insight_and_decision: 深度洞察和决策
- core_review:
    - fundamentals: 基本面
    - news: 新闻面
    - technical: 技术面（必须含布林带+TET）
    - sentiment: 情绪面
- personal_view_and_risk: 看法与风险
- 炒作点分析: 炒作点分析
- short_term_outlook:
    - possibility: 高/中/低
    - reason: 原因
- core_disagreement:
    - bullish_arguments: 看涨
    - bearish_arguments: 看跌
    - consensus: 共识
    - key_disagreement: 核心分歧
- action_items: 3条待办
- golden_quote: 金句
- valuation_metrics: （估值指标，从基本面报表数据计算，若数据不足则填null）
    - ps_ratio: 市销率 (PS, TTM)
    - pe_ratio: 市盈率 (PE, TTM)
    - pb_ratio: 市净率 (PB)
    - net_margin: 净利率 (%)
    - revenue_growth: 营收增长率 (%)

确保返回有效JSON，无任何多余文字。"""

# HTML 生成提示词模板（与 app/services/report_simplifier.py 的 _load_html_generation_prompt 保持一致）
# 使用 .replace() 而非 .format() 以避免 CSS 花括号冲突
_HTML_GENERATION_PROMPT = """根据以下股票分析数据，生成一个适合老板查看的HTML报告页面。

股票代码：{stock_code}
股票名称：{stock_name}
分析数据：
{simplified_data}

要求：
1. 页面标题使用 "牛逼股票 · {stock_name}({stock_code})"
2. 英雄区标题必须显示：牛逼股票 · {stock_name} {stock_code}
3. 英雄区：深蓝色渐变背景，文字白色，包含标题和 executive_summary
4. 所有内容用卡片式布局，白色背景，圆角
5. 卡片文字：深灰色 #1e293b
6. 标题文字统一使用：深蓝色 #1e40af 或 橙色 #f59e0b，加粗，禁止纯黑色标题
7. 核心词汇：橙色 #f59e0b 或 深蓝色 #1e40af 加粗

8. 模块顺序（必须按此顺序展示）：
    - 【新增】公司介绍 + 题材关键词（放在最顶部，英雄区之后第一个模块）
    - 深度洞察和决策结果
    - 核心回顾（基本面、新闻面、技术面、情绪面）
    - Trend-Emotion-Timing 四指标展示区
    - 个人看法与风险警示
    - 炒作点分析
    - 短期展望
    - 多空分歧
    - 待办事项
    - 金句

✅ 趋势-情绪-时机 指标布局规则：
    - 电脑端：4个并排一行
    - 手机端：2×2 两行两列
    - 每个指标卡片：浅蓝背景 #eff6ff，深蓝色文字 #1e40af，文字居中

9. 适配手机端，使用viewport，禁止缩放滚动
10. 只返回完整HTML代码，不要任何解释
11. 确保包含 <!DOCTYPE html>

颜色规范：
- 英雄区背景：linear-gradient(135deg, #1e3c72 0%, #2a5298 100%)
- 卡片：#ffffff
- 标题：#1e40af
- 风险：#ef4444
- TET卡片：#eff6ff
- 题材标签：浅橙色背景 #fff7ed，橙色文字 #f97316，圆角小标签

直接输出纯净HTML代码即可："""


def create_simplified_report_node(llm):
    """创建简化报告节点

    Args:
        llm: LLM 实例（通常使用 quick_thinking_llm）

    Returns:
        节点函数，接收 state 并返回包含 simplified_report 的 dict
    """

    def simplified_report_node(state) -> dict:
        company_name = state.get("company_of_interest", "未知")
        logger.info(f"📋 [Simplified Report] 开始为 {company_name} 生成简化报告")

        # 1. 收集所有分析报告、决策、TET 指标 和 数据质量信息
        original_content, tet_indicators, data_quality = _collect_reports(state)

        # 2. 构建 prompt
        prompt = SIMPLIFY_PROMPT_TEMPLATE.format(
            original_content=json.dumps(original_content, ensure_ascii=False, indent=2)
        )

        # 3. 调用 LLM（带重试）
        simplified_data = _call_llm_with_retry(llm, prompt)

        # 4. 计算压缩比例
        original_str = json.dumps(original_content, ensure_ascii=False)
        simplified_str = json.dumps(simplified_data, ensure_ascii=False)
        compression_ratio = 1 - (len(simplified_str) / len(original_str)) if original_str else 0

        # 5. 提取股票信息
        stock_code = original_content.get("symbol", "未知")
        stock_name = (
            original_content.get("stock_name")
            or original_content.get("name")
            or stock_code
        )

        # 6. 组装简化报告（不含 html_content）
        simplified_report = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "compression_ratio": round(compression_ratio, 4),
            "executive_summary": simplified_data.get("executive_summary", ""),
            "title": simplified_data.get("title", ""),
            "company_intro": simplified_data.get("company_intro", ""),
            "theme_keywords": simplified_data.get("theme_keywords", []),
            "insight_and_decision": simplified_data.get("insight_and_decision", ""),
            "core_review": simplified_data.get("core_review", {}),
            "personal_view_and_risk": simplified_data.get("personal_view_and_risk", ""),
            "炒作点分析": simplified_data.get("炒作点分析", ""),
            "short_term_outlook": simplified_data.get("short_term_outlook", {}),
            "core_disagreement": simplified_data.get("core_disagreement", {}),
            "action_items": simplified_data.get("action_items", []),
            "golden_quote": simplified_data.get("golden_quote", "谋定而后动，知止而有得"),
            # TET 原始数值（从 market_report 解析，非 LLM 重新解释）
            "tet_indicators": tet_indicators if tet_indicators else simplified_data.get("tet_indicators", {}),
            # 数据质量信息（从原始工具输出提取，非 LLM 生成）
            "data_quality": data_quality if data_quality else {},
            # 估值指标（LLM 从基本面数据提取）
            "valuation_metrics": simplified_data.get("valuation_metrics", {}) if isinstance(simplified_data.get("valuation_metrics"), dict) else {},
        }

        # 7. 使用 _html_generation_prompt 通过 LLM 生成 HTML（保持与线上一致的风格）
        logger.info(f"🎨 [Simplified Report] 开始为 {company_name} 生成 HTML 页面")
        html_content = _call_llm_for_html_with_retry(
            llm, stock_code, stock_name, simplified_report
        )
        simplified_report["html_content"] = html_content
        logger.info(f"✅ [Simplified Report] HTML 页面生成完成: {len(html_content)} 字符")

        logger.info(f"✅ [Simplified Report] 生成完成: {company_name}, 压缩比例: {compression_ratio:.2%}")

        return {"simplified_report": simplified_report}

    return simplified_report_node


def _collect_reports(state: dict):
    """从 state 中收集所有分析报告和 TET 指标数值

    Returns:
        (content_dict, tet_indicators_dict, data_quality_dict)
    """
    stock_code = state.get("company_of_interest", "未知")
    # 尝试获取股票名称（company_of_interest 实际是股票代码，不是公司名）
    stock_name = _resolve_stock_name(stock_code)
    content = {
        "symbol": stock_code,
        "stock_name": stock_name,
        "trade_date": state.get("trade_date", ""),
    }

    # 收集各分析师的报告
    report_fields = [
        ("market_report", "市场分析报告"),
        ("sentiment_report", "情绪分析报告"),
        ("news_report", "新闻分析报告"),
        ("fundamentals_report", "基本面分析报告"),
    ]
    for field, label in report_fields:
        value = state.get(field, "")
        if isinstance(value, str) and len(value.strip()) > 10:
            content[label] = value.strip()

    # 收集投资计划
    investment_plan = state.get("investment_plan", "")
    if isinstance(investment_plan, str) and len(investment_plan.strip()) > 10:
        content["投资计划"] = investment_plan.strip()

    # 收集交易员计划
    trader_plan = state.get("trader_investment_plan", "")
    if isinstance(trader_plan, str) and len(trader_plan.strip()) > 10:
        content["交易员计划"] = trader_plan.strip()

    # 收集最终决策
    final_decision = state.get("final_trade_decision", "")
    if isinstance(final_decision, str) and len(final_decision.strip()) > 10:
        content["最终交易决策"] = final_decision.strip()

    # 收集研究团队辩论状态
    debate_state = state.get("investment_debate_state", {})
    if isinstance(debate_state, dict):
        judge_decision = debate_state.get("judge_decision", "")
        if isinstance(judge_decision, str) and len(judge_decision.strip()) > 10:
            content["研究团队决策"] = judge_decision.strip()

    # 收集风险管理团队状态
    risk_state = state.get("risk_debate_state", {})
    if isinstance(risk_state, dict):
        judge_decision = risk_state.get("judge_decision", "")
        if isinstance(judge_decision, str) and len(judge_decision.strip()) > 10:
            content["风险管理决策"] = judge_decision.strip()

    # --- 从 market_report 中提取 TET 原始数值 ---
    tet_indicators = {}
    market_report = state.get("market_report", "")
    if isinstance(market_report, str) and market_report:
        # 支持多种 LLM 输出格式：
        #   "趋势得分（Trend-Score）: 0.27"   ← 工具原始输出
        #   "趋势得分0.27"                    ← LLM 紧凑格式
        #   "趋势得分为0.27"                  ← LLM 中文格式
        #   "趋势得分 0.27（弱）"              ← LLM 带注释格式
        tet_patterns = [
            ("trend_score", r"趋势得分[^0-9+-]*?([-+]?\d+\.?\d*)"),
            ("emotion_index", r"情绪指数[^0-9+-]*?([-+]?\d+\.?\d*)"),
            ("anchored_trend_score", r"锚定趋势[^0-9+-]*?([-+]?\d+\.?\d*)"),
            ("timing_indicator", r"时机指标[^0-9+-]*?([-+]?\d+\.?\d*)"),
        ]
        for key, pattern in tet_patterns:
            match = re.search(pattern, market_report)
            if match:
                try:
                    tet_indicators[key] = float(match.group(1))
                except ValueError:
                    pass
        if tet_indicators:
            logger.info(f"📊 [TET] 从 market_report 提取到: {tet_indicators}")
        else:
            logger.warning(f"⚠️ [TET] 未从 market_report 中提取到 TET 数值")
            logger.debug(f"  market_report 前500字符: {market_report[:500]}")

    # 如果 market_report 没提取到，尝试从 messages 中的工具输出提取
    if not tet_indicators:
        tet_indicators = _extract_tet_from_messages(state)
        if tet_indicators:
            logger.info(f"📊 [TET] 从 messages 工具输出提取到: {tet_indicators}")

    # --- 从 messages 中的工具输出提取数据质量信息（数据条数、数据期间）---
    data_quality = _extract_data_quality_from_messages(state)
    if data_quality:
        logger.info(f"📊 [数据质量] 从 messages 工具输出提取到: {data_quality}")

    return content, tet_indicators, data_quality


def _extract_tet_from_messages(state: dict) -> dict:
    """从 state 的 messages 中查找工具输出，提取 TET 数值

    LangGraph 的工具调用结果会以 ToolMessage 存储在 messages 中，
    其中包含 _compute_tet_section 生成的原始格式数据。
    """
    tet_indicators = {}
    messages = state.get("messages", [])
    if not messages:
        return tet_indicators

    # 查找所有 ToolMessage 或带 tool 标记的消息
    for msg in messages:
        content = ""
        if hasattr(msg, "content"):
            content = msg.content or ""
        elif isinstance(msg, dict):
            content = msg.get("content", "") or ""

        if not isinstance(content, str) or len(content) < 50:
            continue

        # 检查是否包含 TET 关键词
        if "趋势得分" not in content and "情绪指数" not in content:
            continue

        tet_patterns = [
            ("trend_score", r"趋势得分[^0-9+-]*?([-+]?\d+\.?\d*)"),
            ("emotion_index", r"情绪指数[^0-9+-]*?([-+]?\d+\.?\d*)"),
            ("anchored_trend_score", r"锚定趋势[^0-9+-]*?([-+]?\d+\.?\d*)"),
            ("timing_indicator", r"时机指标[^0-9+-]*?([-+]?\d+\.?\d*)"),
        ]
        for key, pattern in tet_patterns:
            if key in tet_indicators:
                continue  # 已提取到
            match = re.search(pattern, content)
            if match:
                try:
                    tet_indicators[key] = float(match.group(1))
                except ValueError:
                    pass

        if len(tet_indicators) >= 4:
            break  # 全部提取到就提前退出

    return tet_indicators


def _extract_data_quality_from_messages(state: dict) -> dict:
    """从 state 的 messages 中查找工具输出，提取数据质量信息（数据期间、数据条数）

    _format_stock_data_response 输出的原始数据包含：
      数据期间: START_DATE 至 END_DATE
      数据条数: XXX条

    但市场分析师 LLM 在生成 market_report 时可能丢弃这些信息，
    因此直接从原始 ToolMessage 中提取最可靠。
    """
    data_quality = {}
    messages = state.get("messages", [])
    if not messages:
        return data_quality

    for msg in messages:
        content = ""
        if hasattr(msg, "content"):
            content = msg.content or ""
        elif isinstance(msg, dict):
            content = msg.get("content", "") or ""

        if not isinstance(content, str) or len(content) < 50:
            continue

        # 检查是否包含数据期间信息
        if "数据期间" not in content and "数据区间" not in content and "数据条数" not in content:
            continue

        # 提取数据期间/区间: START 至 END（兼容全角/半角冒号）
        period_match = re.search(
            r"数据(?:期间|区间)\s*[:\uff1a]\s*(\d{4}-\d{2}-\d{2})\s*至\s*(\d{4}-\d{2}-\d{2})",
            content,
        )
        if period_match:
            data_quality["data_start"] = period_match.group(1)
            data_quality["data_end"] = period_match.group(2)

        # 提取数据条数: XXX条
        row_match = re.search(r"数据条数:\s*(\d+)\s*条", content)
        if row_match:
            data_quality["rows"] = int(row_match.group(1))

        if len(data_quality) >= 3:
            break  # 三个字段都提取到就退出

    return data_quality


def _resolve_stock_name(stock_code: str) -> str:
    """解析股票代码对应的公司名称"""
    try:
        from pymongo import MongoClient
        from app.core.config import settings
        client = MongoClient(settings.MONGO_URI)
        db = client[settings.MONGO_DB]
        # 优先从 stock_basic_info 查询
        info = db.stock_basic_info.find_one({"symbol": stock_code}, {"name": 1})
        if info and info.get("name"):
            client.close()
            return str(info["name"])
        # 其次从 stock_daily_quotes 取一条记录的 name 或 code 字段
        doc = db.stock_daily_quotes.find_one({"symbol": stock_code}, {"name": 1, "code": 1})
        if doc and doc.get("name"):
            client.close()
            return str(doc["name"])
        client.close()
    except Exception:
        pass
    return stock_code


def _call_llm_with_retry(llm, prompt: str, max_retries: int = 3) -> dict:
    """调用 LLM 生成简化内容（带重试）"""
    last_exception = None

    for attempt in range(max_retries):
        try:
            logger.info(f"🔄 [Simplified Report] LLM调用 尝试 {attempt + 1}/{max_retries}")

            start_time = time.time()
            response = llm.invoke(prompt)
            elapsed = time.time() - start_time

            if hasattr(response, 'content'):
                content = response.content
            elif isinstance(response, str):
                content = response
            else:
                content = str(response)

            logger.info(f"✅ [Simplified Report] LLM响应长度: {len(content)} 字符, 耗时: {elapsed:.2f}秒")

            # 解析 JSON
            parsed = _parse_json_response(content)
            if parsed:
                return parsed

            logger.warning(f"⚠️ [Simplified Report] JSON解析失败，尝试 {attempt + 1}/{max_retries}")

        except Exception as e:
            last_exception = e
            logger.warning(f"⚠️ [Simplified Report] LLM调用失败 (尝试 {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))

    logger.error(f"❌ [Simplified Report] 所有重试都失败: {last_exception}")
    return _get_default_simplified_data()


def _parse_json_response(response: str) -> dict:
    """解析 LLM 的 JSON 响应"""
    try:
        cleaned = response.strip()
        # 移除 markdown 代码块标记
        if cleaned.startswith('```'):
            import re
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', cleaned, re.DOTALL | re.IGNORECASE)
            if json_match:
                cleaned = json_match.group(1)

        data = json.loads(cleaned)
        logger.info(f"✅ [Simplified Report] JSON解析成功")
        return data

    except json.JSONDecodeError:
        # 尝试从文本中提取 JSON
        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                logger.info(f"✅ [Simplified Report] 从文本中提取JSON成功")
                return data
            except json.JSONDecodeError:
                pass
        return {}


def _get_default_simplified_data() -> dict:
    """获取默认的简化数据结构"""
    return {
        "title": "股票分析简报",
        "executive_summary": "分析已完成，建议结合详细报告做出决策。",
        "company_intro": "",
        "theme_keywords": [],
        "insight_and_decision": "综合各项指标，建议保持关注。",
        "core_review": {
            "fundamentals": "基本面分析显示公司经营状况稳定。",
            "news": "近期无重大负面新闻。",
            "technical": "技术面呈现震荡整理态势。",
            "sentiment": "市场情绪中性偏谨慎。"
        },
        "personal_view_and_risk": "当前无明显显著风险。",
        "炒作点分析": "当前无明显炒作热点。",
        "short_term_outlook": {
            "possibility": "中",
            "reason": "短期市场可能维持震荡。"
        },
        "core_disagreement": {
            "bullish_arguments": "公司基本面稳健",
            "bearish_arguments": "短期市场情绪偏弱",
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


def _call_llm_for_html_with_retry(llm, stock_code: str, stock_name: str, simplified_data: dict, max_retries: int = 3) -> str:
    """调用 LLM 使用 _html_generation_prompt 生成 HTML 页面（带重试）

    与 app/services/report_simplifier.py 的 _generate_html_by_llm 逻辑保持一致。
    使用 .replace() 而非 .format() 以避免 CSS 花括号冲突。

    Returns:
        HTML 字符串（如果全部失败则返回空字符串）
    """
    # 构建增强数据（包含股票信息和日期）
    enhanced_data = {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "date": datetime.now().strftime("%Y年%m月%d日"),
        **simplified_data
    }

    # 使用 .replace() 填充占位符，避免 CSS 花括号与 .format() 冲突
    prompt = _HTML_GENERATION_PROMPT.replace("{stock_code}", stock_code)
    prompt = prompt.replace("{stock_name}", stock_name)
    prompt = prompt.replace("{simplified_data}", json.dumps(enhanced_data, ensure_ascii=False, indent=2))

    last_response = ""

    for attempt in range(max_retries):
        try:
            logger.info(f"🎨 [HTML生成] LLM调用 尝试 {attempt + 1}/{max_retries}")
            start_time = time.time()

            response = llm.invoke(prompt)
            elapsed = time.time() - start_time

            if hasattr(response, 'content'):
                content = response.content
            elif isinstance(response, str):
                content = response
            else:
                content = str(response)

            last_response = content
            logger.info(f"🎨 [HTML生成] LLM响应长度: {len(content)} 字符, 耗时: {elapsed:.2f}秒")

            # 提取 HTML
            html_content = _extract_html_from_response(content)

            # 验证 HTML 完整性
            if "<!DOCTYPE html>" in html_content and "<html" in html_content and "</html>" in html_content:
                logger.info(f"✅ [HTML生成] HTML 验证通过")
                return html_content
            elif "<html" in html_content:
                if "<!DOCTYPE html>" not in html_content:
                    html_content = "<!DOCTYPE html>\n" + html_content
                if "</html>" not in html_content:
                    html_content += "\n</html>"
                logger.warning(f"⚠️ [HTML生成] HTML 不完整，已修复")
                return html_content
            else:
                logger.warning(f"⚠️ [HTML生成] 未提取到有效 HTML，重试 {attempt + 1}/{max_retries}")

        except Exception as e:
            logger.warning(f"⚠️ [HTML生成] LLM调用失败 (尝试 {attempt + 1}): {e}")

        if attempt < max_retries - 1:
            time.sleep(2 * (attempt + 1))

    logger.error(f"❌ [HTML生成] 所有重试都失败，返回空字符串")
    return ""


def _extract_html_from_response(response: str) -> str:
    """从 LLM 响应中提取 HTML 内容（与 report_simplifier.py 的 _extract_html_from_response 一致）"""
    if not response:
        return ""

    stripped = response.strip()

    if stripped.startswith('<!DOCTYPE html>') and '</html>' in stripped:
        logger.info(f"✅ [HTML提取] 响应已是完整 HTML")
        return stripped

    # 尝试提取 ```html ... ``` 代码块
    html_match = re.search(r'```html\s*(.*?)\s*```', stripped, re.DOTALL)
    if html_match:
        extracted = html_match.group(1).strip()
        logger.info(f"✅ [HTML提取] 从 ```html``` 代码块提取成功，长度: {len(extracted)}")
        return extracted

    # 尝试查找 <!DOCTYPE html> 或 <html 标签
    html_start = stripped.find('<!DOCTYPE html>')
    if html_start == -1:
        html_start = stripped.find('<html')
    if html_start != -1:
        html_end = stripped.rfind('</html>')
        if html_end != -1:
            extracted = stripped[html_start:html_end + 7]
            logger.info(f"✅ [HTML提取] 提取到 HTML，长度: {len(extracted)}")
            return extracted
        else:
            extracted = stripped[html_start:]
            logger.warning(f"⚠️ [HTML提取] HTML 不完整（缺少 </html>），长度: {len(extracted)}")
            return extracted

    logger.warning(f"⚠️ [HTML提取] 无法提取 HTML，返回原始响应")
    return stripped
