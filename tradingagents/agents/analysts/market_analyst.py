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
        # 工具调用计数器 + 死循环熔断
        tool_call_count = state.get("market_tool_call_count", 0)
        max_tool_calls = 3
        logger.info(f"🔧 市场分析师节点开始 [死循环修复] 当前工具调用次数: {tool_call_count}/{max_tool_calls}")

        # 达到最大次数直接停止
        if tool_call_count >= max_tool_calls:
            logger.warning(f"⚠️ 已达到最大工具调用次数，停止调用")
            return {
                "messages": state["messages"],
                "market_report": "已达到最大数据查询次数，基于现有信息完成市场技术分析",
                "market_tool_call_count": tool_call_count
            }

        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        # 5 年前日期
        current_date_dt = datetime.strptime(current_date, "%Y-%m-%d")
        start_date_5y = (current_date_dt - relativedelta(years=5)).strftime("%Y-%m-%d")
        logger.info(f"📅 [5年数据] 开始日期: {start_date_5y} | 结束日期: {current_date}")

        from tradingagents.utils.stock_utils import StockUtils
        market_info = StockUtils.get_market_info(ticker)
        company_name = _get_company_name(ticker, market_info)

        # ===== TET 指标已集成到 get_stock_market_data_unified 返回数据中，无需单独计算 =====
        tools = [toolkit.get_stock_market_data_unified]

        # ====================== 【关键修改】加入布林带实盘铁律 ======================
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是专业A股实盘技术分析师，严格遵守：量价为王 → 布林形态 → TET 择时。\n"
                    "\n"
                    "📊 【技术分析铁律——必须严格遵守】\n"
                    "1. 布林带收口后向上突破上轨 = 强势变盘向上，属于趋势延续信号 → 持仓/加仓，不卖出\n"
                    "2. 高位 RSI 超买 + 布林收口向上 = 强势多头钝化，极易继续上涨 → 不盲目看空\n"
                    "3. 严禁单纯因 RSI 超买就建议卖出\n"
                    "4. 上涨趋势中，缩量突破不一定是假突破，收口突破本就属于缩量蓄势\n"
                    "5. 所有判断必须结合：量价 + 布林带 + TET 指标\n"
                    "\n"
                    "📋 分析标的：\n"
                    "- 公司：{company_name}\n"
                    "- 代码：{ticker}\n"
                    "- 市场：{market_name}\n"
                    "- 货币：{currency_symbol}\n"
                    "- 日期：{current_date}\n"
                    "\n"
                    "🔧 工具规则：\n"
                    "1. 无数据 → 调用 get_stock_market_data_unified 获取 5 年日K\n"
                    "2. 有数据 → 禁止重复调用\n"
                    "\n"
                    "📝 输出格式（严格顺序）：\n"
                    "## 📊 股票基本信息\n"
                    "## 📦 量价关系分析\n"
                    "## 📈 传统技术指标（均线、MACD、RSI、布林带）\n"
                    "## 📉 短中期趋势\n"
                    "## 💡 投资建议（结合布林形态 + TET）\n"
                    "\n"
                    "⚠️ 约束：\n"
                    "- 不推翻 TET 方向\n"
                    "- 优先看布林带形态判断趋势真假\n"
                    "- 简洁、专业、实战化\n"
                    "\n"
                    "使用中文输出。",
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

        prompt = prompt.partial(tool_names=", ".join(tool_names))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(start_date_5y=start_date_5y)
        prompt = prompt.partial(ticker=ticker)
        prompt = prompt.partial(company_name=company_name)
        prompt = prompt.partial(market_name=market_info['market_name'])
        prompt = prompt.partial(currency_name=market_info['currency_name'])
        prompt = prompt.partial(currency_symbol=market_info['currency_symbol'])

        logger.info(f"📊 [市场分析师] LLM类型: {llm.__class__.__name__}")
        logger.info(f"📊 [市场分析师] LLM模型: {getattr(llm, 'model_name', 'unknown')}")
        logger.info(f"📊 [市场分析师] 公司名称: {company_name}")
        logger.info(f"📊 [市场分析师] 股票代码: {ticker}")
        logger.info(f"📊 [市场分析师] 5年起始日期自动设置: {start_date_5y}")

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": state["messages"]})

        # Google 模型
        if GoogleToolCallHandler.is_google_model(llm):
            analysis_prompt_template = GoogleToolCallHandler.create_analysis_prompt(
                ticker=ticker,
                company_name=company_name,
                analyst_type="市场技术面",
                specific_requirements="优先量价、布林带形态，TET 择时，布林收口突破=强势向上，不盲目看空"
            )
            report, messages = GoogleToolCallHandler.handle_google_tool_calls(
                result=result, llm=llm, tools=tools, state=state,
                analysis_prompt_template=analysis_prompt_template, analyst_name="市场分析师"
            )
            return {
                "messages": state["messages"] + [result],
                "market_report": report,
                "market_tool_call_count": tool_call_count + 1
            }

        # 标准模型
        else:
            if len(result.tool_calls) == 0:
                report = result.content
            else:
                try:
                    from langchain_core.messages import ToolMessage, HumanMessage
                    tool_messages = []
                    for tool_call in result.tool_calls:
                        tool_name = tool_call.get('name')
                        tool_args = tool_call.get('args', {})
                        tool_id = tool_call.get('id')
                        tool_result = None
                        for tool in tools:
                            tname = tool.name if hasattr(tool, 'name') else tool.__name__
                            if tname == tool_name:
                                try:
                                    tool_result = tool.invoke(tool_args)
                                except Exception as e:
                                    tool_result = f"工具失败：{e}"
                        if not tool_result:
                            tool_result = f"未找到工具 {tool_name}"
                        tm = ToolMessage(content=str(tool_result), tool_call_id=tool_id)
                        tool_messages.append(tm)

                    analysis_prompt = f"""基于5年量价数据生成实战技术分析报告。
强制规则：
1. 布林收口向上突破 = 强势延续 → 持仓/加仓
2. 高位 RSI 超买 + 布林向上 = 强势钝化 → 不看空卖出
3. 以 TET 为择时核心
4. 优先量价验证

结构：
# {company_name}({ticker}) 技术分析报告
## 一、基本信息
## 二、量价分析
## 三、传统指标（布林带重点）
## 四、趋势判断
## 五、操作建议（实战）
"""
                    messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
                    final_result = llm.invoke(messages)
                    report = final_result.content

                except Exception as e:
                    logger.error(f"❌ 工具执行异常: {e}")
                    traceback.print_exc()
                    report = f"工具执行异常：{e}"

            return {
                "messages": state["messages"] + [result],
                "market_report": report,
                "market_tool_call_count": tool_call_count + 1
            }

    return market_analyst_node