from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
import traceback
from datetime import datetime
from dateutil.relativedelta import relativedelta

# 导入分析模块日志装饰器
from tradingagents.utils.tool_logging import log_analyst_module

# 导入统一日志系统
from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")

# 导入Google工具调用处理器
from tradingagents.agents.utils.google_tool_handler import GoogleToolCallHandler


def _get_company_name(ticker: str, market_info: dict) -> str:
    """
    根据股票代码获取公司名称

    Args:
        ticker: 股票代码
        market_info: 市场信息字典

    Returns:
        str: 公司名称
    """
    try:
        if market_info['is_china']:
            # 中国A股：使用统一接口获取股票信息
            from tradingagents.dataflows.interface import get_china_stock_info_unified
            stock_info = get_china_stock_info_unified(ticker)

            logger.debug(f"📊 [市场分析师] 获取股票信息返回: {stock_info[:200] if stock_info else 'None'}...")

            # 解析股票名称
            if stock_info and "股票名称:" in stock_info:
                company_name = stock_info.split("股票名称:")[1].split("\n")[0].strip()
                logger.info(f"✅ [市场分析师] 成功获取中国股票名称: {ticker} -> {company_name}")
                return company_name
            else:
                # 降级方案：尝试直接从数据源管理器获取
                logger.warning(f"⚠️ [市场分析师] 无法从统一接口解析股票名称: {ticker}，尝试降级方案")
                try:
                    from tradingagents.dataflows.data_source_manager import get_china_stock_info_unified as get_info_dict
                    info_dict = get_info_dict(ticker)
                    if info_dict and info_dict.get('name'):
                        company_name = info_dict['name']
                        logger.info(f"✅ [市场分析师] 降级方案成功获取股票名称: {ticker} -> {company_name}")
                        return company_name
                except Exception as e:
                    logger.error(f"❌ [市场分析师] 降级方案也失败: {e}")

                logger.error(f"❌ [市场分析师] 所有方案都无法获取股票名称: {ticker}")
                return f"股票代码{ticker}"

        elif market_info['is_hk']:
            # 港股：使用改进的港股工具
            try:
                from tradingagents.dataflows.providers.hk.improved_hk import get_hk_company_name_improved
                company_name = get_hk_company_name_improved(ticker)
                logger.debug(f"📊 [DEBUG] 使用改进港股工具获取名称: {ticker} -> {company_name}")
                return company_name
            except Exception as e:
                logger.debug(f"📊 [DEBUG] 改进港股工具获取名称失败: {e}")
                # 降级方案：生成友好的默认名称
                clean_ticker = ticker.replace('.HK', '').replace('.hk', '')
                return f"港股{clean_ticker}"

        elif market_info['is_us']:
            # 美股：使用简单映射或返回代码
            us_stock_names = {
                'AAPL': '苹果公司',
                'TSLA': '特斯拉',
                'NVDA': '英伟达',
                'MSFT': '微软',
                'GOOGL': '谷歌',
                'AMZN': '亚马逊',
                'META': 'Meta',
                'NFLX': '奈飞'
            }

            company_name = us_stock_names.get(ticker.upper(), f"美股{ticker}")
            logger.debug(f"📊 [DEBUG] 美股名称映射: {ticker} -> {company_name}")
            return company_name

        else:
            return f"股票{ticker}"

    except Exception as e:
        logger.error(f"❌ [DEBUG] 获取公司名称失败: {e}")
        return f"股票{ticker}"


def create_market_analyst(llm, toolkit):

    def market_analyst_node(state):
        logger.debug(f"📈 [DEBUG] ===== 市场分析师节点开始 =====")

        # 工具调用计数器
        tool_call_count = state.get("market_tool_call_count", 0)
        max_tool_calls = 3
        logger.info(f"🔧 [死循环修复] 当前工具调用次数: {tool_call_count}/{max_tool_calls}")

        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        # ====================== 自动计算 3 年前日期（Trend-Emotion-Timing 必须）======================
        current_date_dt = datetime.strptime(current_date, "%Y-%m-%d")
        start_date_3y = (current_date_dt - relativedelta(years=3)).strftime("%Y-%m-%d")
        logger.info(f"📅 [3年数据] 开始日期: {start_date_3y} | 结束日期: {current_date}")

        # 根据股票代码格式选择数据源
        from tradingagents.utils.stock_utils import StockUtils
        market_info = StockUtils.get_market_info(ticker)
        company_name = _get_company_name(ticker, market_info)

        # 统一工具
        logger.info(f"📊 [市场分析师] 使用统一市场数据工具，自动识别股票类型")
        tools = [toolkit.get_stock_market_data_unified]

        tool_names_debug = []
        for tool in tools:
            if hasattr(tool, 'name'):
                tool_names_debug.append(tool.name)
            elif hasattr(tool, '__name__'):
                tool_names_debug.append(tool.__name__)
            else:
                tool_names_debug.append(str(tool))
        logger.info(f"📊 [市场分析师] 绑定的工具: {tool_names_debug}")

        # ====================== 提示词：自动拉取3年数据 + Trend-Emotion-Timing ======================
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是一位专业的股票技术分析师，与其他分析师协作。\n"
                    "\n"
                    "📋 **分析对象：**\n"
                    "- 公司名称：{company_name}\n"
                    "- 股票代码：{ticker}\n"
                    "- 所属市场：{market_name}\n"
                    "- 计价货币：{currency_name}（{currency_symbol}）\n"
                    "- 分析日期：{current_date}\n"
                    "\n"
                    "🔧 **工具使用：**\n"
                    "你可以使用以下工具：{tool_names}\n"
                    "⚠️ 重要工作流程：\n"
                    "1. 如果消息历史中没有工具结果，立即调用 get_stock_market_data_unified 工具\n"
                    "   - ticker: {ticker}\n"
                    "   - start_date: {start_date_3y}\n"
                    "   - end_date: {current_date}\n"
                    "   注意：必须获取 3 年全部日频K线数据，用于趋势-情绪-时机分析\n"
                    "2. 如果消息历史中已经有工具结果（ToolMessage），立即基于工具数据生成最终分析报告\n"
                    "3. 不要重复调用工具！一次工具调用就足够了！\n"
                    "4. 接收到工具数据后，必须立即生成完整的技术分析报告，不要再调用任何工具\n"
                    "\n"
                    "📝 **输出格式要求（必须严格遵守）：**\n"
                    "\n"
                    "## 📊 股票基本信息\n"
                    "- 公司名称：{company_name}\n"
                    "- 股票代码：{ticker}\n"
                    "- 所属市场：{market_name}\n"
                    "\n"
                    "## 📈 传统技术指标分析\n"
                    "[分析移动平均线、MACD、RSI、布林带等]\n"
                    "\n"
                    "## 🧭 趋势-情绪-时机量化分析（Trend-Emotion-Timing）\n"
                    "### 1. 趋势得分（Trend-Score）\n"
                    "[基于多周期价格动量、均线、交叉信号，给出-1到1之间的趋势得分，判断趋势强弱]\n"
                    "\n"
                    "### 2. 情绪指数（Emotion-Index）\n"
                    "[基于震荡指标与超买超卖，给出-1到1之间的情绪指数，判断市场情绪状态]\n"
                    "\n"
                    "### 3. 锚定趋势得分（Anchored Trend-Score）\n"
                    "[剔除短期情绪干扰后的真实中期趋势]\n"
                    "\n"
                    "### 4. 时机指标（Timing-Indicator）\n"
                    "[公式：锚定趋势得分 − 情绪指数，用于判断最佳买卖时机]\n"
                    "- 时机指标 > 1.0：优质买入时机\n"
                    "- 时机指标 < -1.0：优质卖出时机\n"
                    "- 介于-1.0 ~ 1.0：观望\n"
                    "\n"
                    "## 📉 价格趋势分析\n"
                    "[结合趋势-情绪-时机框架，分析短期、中期趋势]\n"
                    "\n"
                    "## 💡 投资建议（基于时机指标）\n"
                    "[明确给出：买入 / 持有 / 卖出，并说明依据：趋势、情绪、时机指标判断]\n"
                    "\n"
                    "⚠️ **重要提醒：**\n"
                    "- 必须使用上述格式输出，不要自创标题格式\n"
                    "- 所有价格数据使用{currency_name}（{currency_symbol}）表示\n"
                    "- 确保在分析中正确使用公司名称\"{company_name}\"和股票代码\"{ticker}\"\n"
                    "- 不要在标题中使用\"技术分析报告\"等自创标题\n"
                    "- 如果你有明确的技术面投资建议（买入/持有/卖出），请在投资建议部分明确标注\n"
                    "- 不要使用'最终交易建议'前缀，因为最终决策需要综合所有分析师的意见\n"
                    "\n"
                    "请使用中文，基于真实数据进行分析。",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        tool_names = []
        for tool in tools:
            if hasattr(tool, 'name'):
                tool_names.append(tool.name)
            elif hasattr(tool, '__name__'):
                tool_names.append(tool.__name__)
            else:
                tool_names.append(str(tool))

        prompt = prompt.partial(tool_names=", ".join(tool_names))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(start_date_3y=start_date_3y)
        prompt = prompt.partial(ticker=ticker)
        prompt = prompt.partial(company_name=company_name)
        prompt = prompt.partial(market_name=market_info['market_name'])
        prompt = prompt.partial(currency_name=market_info['currency_name'])
        prompt = prompt.partial(currency_symbol=market_info['currency_symbol'])

        logger.info(f"📊 [市场分析师] LLM类型: {llm.__class__.__name__}")
        logger.info(f"📊 [市场分析师] LLM模型: {getattr(llm, 'model_name', 'unknown')}")
        logger.info(f"📊 [市场分析师] 公司名称: {company_name}")
        logger.info(f"📊 [市场分析师] 股票代码: {ticker}")
        logger.info(f"📊 [市场分析师] 3年起始日期自动设置: {start_date_3y}")

        chain = prompt | llm.bind_tools(tools)

        logger.info(f"📊 [市场分析师] 开始调用LLM...")
        result = chain.invoke({"messages": state["messages"]})
        logger.info(f"📊 [市场分析师] LLM调用完成")

        # 使用统一的Google工具调用处理器
        if GoogleToolCallHandler.is_google_model(llm):
            logger.info(f"📊 [市场分析师] 检测到Google模型，使用统一工具调用处理器")

            analysis_prompt_template = GoogleToolCallHandler.create_analysis_prompt(
                ticker=ticker,
                company_name=company_name,
                analyst_type="市场分析",
                specific_requirements="必须使用3年日频K线数据，重点做趋势-情绪-时机（Trend-Emotion-Timing）分析。"
            )

            report, messages = GoogleToolCallHandler.handle_google_tool_calls(
                result=result,
                llm=llm,
                tools=tools,
                state=state,
                analysis_prompt_template=analysis_prompt_template,
                analyst_name="市场分析师"
            )

            return {
                "messages": [result],
                "market_report": report,
                "market_tool_call_count": tool_call_count + 1
            }
        else:
            logger.info(f"📊 [市场分析师] 非Google模型，使用标准处理逻辑")

            if len(result.tool_calls) == 0:
                report = result.content
                logger.info(f"📊 [市场分析师] ✅ 直接回复（无工具调用），长度: {len(report)}")
            else:
                logger.info(f"📊 [市场分析师] 🔧 检测到工具调用")

                try:
                    from langchain_core.messages import ToolMessage, HumanMessage
                    tool_messages = []

                    for tool_call in result.tool_calls:
                        tool_name = tool_call.get('name')
                        tool_args = tool_call.get('args', {})
                        tool_id = tool_call.get('id')
                        logger.debug(f"📊 [DEBUG] 执行工具: {tool_name}, 参数: {tool_args}")

                        tool_result = None
                        for tool in tools:
                            current_tool_name = None
                            if hasattr(tool, 'name'):
                                current_tool_name = tool.name
                            elif hasattr(tool, '__name__'):
                                current_tool_name = tool.__name__

                            if current_tool_name == tool_name:
                                try:
                                    tool_result = tool.invoke(tool_args)
                                    logger.debug(f"📊 [DEBUG] 工具执行成功，结果长度: {len(str(tool_result))}")
                                    break
                                except Exception as tool_error:
                                    logger.error(f"❌ [DEBUG] 工具执行失败: {tool_error}")
                                    tool_result = f"工具执行失败: {str(tool_error)}"

                        if tool_result is None:
                            tool_result = f"未找到工具: {tool_name}"

                        tool_message = ToolMessage(
                            content=str(tool_result),
                            tool_call_id=tool_id
                        )
                        tool_messages.append(tool_message)

                    analysis_prompt = f"""现在请基于上述工具获取的 3 年日频K线数据，生成详细的技术分析报告。

**分析对象：**
- 公司名称：{company_name}
- 股票代码：{ticker}
- 所属市场：{market_info['market_name']}
- 计价货币：{market_info['currency_name']}（{market_info['currency_symbol']}）

**输出格式要求（必须严格遵守）：**

# **{company_name}（{ticker}）技术分析报告**
**分析日期：{current_date}**

---

## 一、股票基本信息
- **公司名称**：{company_name}
- **股票代码**：{ticker}
- **所属市场**：{market_info['market_name']}
- **当前价格**：[从工具数据中获取] {market_info['currency_symbol']}
- **涨跌幅**：[从工具数据中获取]
- **成交量**：[从工具数据中获取]

---

## 二、传统技术指标分析
### 1. 移动平均线（MA）分析
### 2. MACD指标分析
### 3. RSI相对强弱指标
### 4. 布林带（BOLL）分析

---

## 三、趋势-情绪-时机量化分析（Trend-Emotion-Timing）
### 1. 趋势得分（Trend-Score）
基于多周期动量、均线、趋势信号，给出-1~1之间的趋势强度。

### 2. 情绪指数（Emotion-Index）
基于超买超卖、震荡指标，给出-1~1之间的情绪状态。

### 3. 锚定趋势得分（Anchored Trend-Score）
剔除短期情绪干扰后的中期真实趋势。

### 4. 时机指标（Timing-Indicator）
计算公式：锚定趋势得分 − 情绪指数
- > 1.0 = 优质买入
- < -1.0 = 优质卖出
- 中间 = 观望

---

## 四、价格趋势分析
### 1. 短期趋势（5-10个交易日）
### 2. 中期趋势（20-60个交易日）
### 3. 成交量分析

---

## 五、投资建议（基于时机指标）
### 1. 综合评估
### 2. 操作建议（买入/持有/卖出）
### 3. 关键价格区间（支撑位、压力位）

---

**重要要求：**
- 严格按格式输出
- 数据真实、数值明确
- 必须包含趋势、情绪、时机三部分分析
- 使用中文，专业、简洁、可直接用于投资决策
"""

                    messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
                    final_result = llm.invoke(messages)
                    report = final_result.content

                    logger.info(f"📊 [市场分析师] 生成完整分析报告，长度: {len(report)}")

                    return {
                        "messages": [result] + tool_messages + [final_result],
                        "market_report": report,
                        "market_tool_call_count": tool_call_count + 1
                    }

                except Exception as e:
                    logger.error(f"❌ [市场分析师] 工具执行或分析生成失败: {e}")
                    traceback.print_exc()
                    report = f"市场分析师调用了工具但分析生成失败: {[call.get('name', 'unknown') for call in result.tool_calls]}"

                    return {
                        "messages": [result],
                        "market_report": report,
                        "market_tool_call_count": tool_call_count + 1
                    }

            return {
                "messages": [result],
                "market_report": report,
                "market_tool_call_count": tool_call_count + 1
            }

    return market_analyst_node