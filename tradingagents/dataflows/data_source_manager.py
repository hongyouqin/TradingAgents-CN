#!/usr/bin/env python3
"""
数据源管理器
统一管理中国股票数据源的选择和切换，支持Tushare、AKShare、BaoStock等
"""

import asyncio
import nest_asyncio
import os
import time
from typing import Dict, List, Optional, Any
from enum import Enum
import warnings
import pandas as pd
import numpy as np

# 导入日志模块
from tradingagents.utils.logging_manager import get_logger
logger = get_logger('agents')
warnings.filterwarnings('ignore')

# 导入统一日志系统
from tradingagents.utils.logging_init import setup_dataflow_logging
logger = setup_dataflow_logging()

# 导入统一数据源编码
from tradingagents.constants import DataSourceCode
from .interface import get_china_stock_info_tushare

nest_asyncio.apply()

def run_async_safe(coroutine):
    """
    安全运行异步协程的辅助函数。
    如果已有事件循环，则在当前循环中安排任务；
    如果没有，则创建新循环运行。
    """
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(coroutine)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coroutine)
        finally:
            loop.close()


class ChinaDataSource(Enum):
    """
    中国股票数据源枚举

    注意：这个枚举与 tradingagents.constants.DataSourceCode 保持同步
    值使用统一的数据源编码
    """
    MONGODB = DataSourceCode.MONGODB  # MongoDB数据库缓存（最高优先级）
    TUSHARE = DataSourceCode.TUSHARE
    AKSHARE = DataSourceCode.AKSHARE
    BAOSTOCK = DataSourceCode.BAOSTOCK


class USDataSource(Enum):
    """
    美股数据源枚举

    注意：这个枚举与 tradingagents.constants.DataSourceCode 保持同步
    值使用统一的数据源编码
    """
    MONGODB = DataSourceCode.MONGODB  # MongoDB数据库缓存（最高优先级）
    YFINANCE = DataSourceCode.YFINANCE  # Yahoo Finance（免费，股票价格和技术指标）
    ALPHA_VANTAGE = DataSourceCode.ALPHA_VANTAGE  # Alpha Vantage（基本面和新闻）
    FINNHUB = DataSourceCode.FINNHUB  # Finnhub（备用数据源）


class DataSourceManager:
    """数据源管理器"""

    def __init__(self):
        """初始化数据源管理器"""
        self.use_mongodb_cache = self._check_mongodb_enabled()
        self.default_source = self._get_default_source()
        self.available_sources = self._check_available_sources()
        self.current_source = self.default_source

        self.cache_manager = None
        self.cache_enabled = False
        try:
            from .cache import get_cache
            self.cache_manager = get_cache()
            self.cache_enabled = True
            logger.info(f"✅ 统一缓存管理器已启用")
        except Exception as e:
            logger.warning(f"⚠️ 统一缓存管理器初始化失败: {e}")

        logger.info(f"📊 数据源管理器初始化完成")
        logger.info(f"   MongoDB缓存: {'✅ 已启用' if self.use_mongodb_cache else '❌ 未启用'}")
        logger.info(f"   统一缓存: {'✅ 已启用' if self.cache_enabled else '❌ 未启用'}")
        logger.info(f"   默认数据源: {self.default_source.value}")
        logger.info(f"   可用数据源: {[s.value for s in self.available_sources]}")

    def _check_mongodb_enabled(self) -> bool:
        """检查是否启用MongoDB缓存"""
        from tradingagents.config.runtime_settings import use_app_cache_enabled
        return use_app_cache_enabled()

    def _get_data_source_priority_order(self, symbol: Optional[str] = None) -> List[ChinaDataSource]:
        market_category = self._identify_market_category(symbol)
        try:
            from app.core.database import get_mongo_db_sync
            db = get_mongo_db_sync()
            config_collection = db.system_configs
            config_data = config_collection.find_one({"is_active": True}, sort=[("version", -1)])

            if config_data and config_data.get('data_source_configs'):
                data_source_configs = config_data.get('data_source_configs', [])
                enabled_sources = []
                for ds in data_source_configs:
                    if not ds.get('enabled', True):
                        continue
                    market_categories = ds.get('market_categories', [])
                    if market_categories and market_category and market_category not in market_categories:
                        continue
                    enabled_sources.append(ds)

                enabled_sources.sort(key=lambda x: x.get('priority', 0), reverse=True)
                source_mapping = {
                    DataSourceCode.TUSHARE: ChinaDataSource.TUSHARE,
                    DataSourceCode.AKSHARE: ChinaDataSource.AKSHARE,
                    DataSourceCode.BAOSTOCK: ChinaDataSource.BAOSTOCK,
                }
                result = []
                for ds in enabled_sources:
                    ds_type = ds.get('type', '').lower()
                    if ds_type in source_mapping:
                        source = source_mapping[ds_type]
                        if source != ChinaDataSource.MONGODB and source in self.available_sources:
                            result.append(source)
                if result:
                    logger.info(f"✅ [数据源优先级] 市场={market_category or '全部'}, 从数据库读取: {[s.value for s in result]}")
                    return result
        except Exception as e:
            logger.warning(f"⚠️ [数据源优先级] 从数据库读取失败: {e}，使用默认顺序")

        default_order = [ChinaDataSource.AKSHARE, ChinaDataSource.TUSHARE, ChinaDataSource.BAOSTOCK]
        return [s for s in default_order if s in self.available_sources]

    def _identify_market_category(self, symbol: Optional[str]) -> Optional[str]:
        if not symbol:
            return None
        try:
            from tradingagents.utils.stock_utils import StockUtils, StockMarket
            market = StockUtils.identify_stock_market(symbol)
            market_mapping = {
                StockMarket.CHINA_A: 'a_shares',
                StockMarket.US: 'us_stocks',
                StockMarket.HONG_KONG: 'hk_stocks',
            }
            return market_mapping.get(market)
        except Exception as e:
            logger.warning(f"⚠️ [市场识别] 识别失败: {e}")
            return None

    def _get_default_source(self) -> ChinaDataSource:
        env_source = os.getenv('DEFAULT_CHINA_DATA_SOURCE', DataSourceCode.AKSHARE).lower()
        source_mapping = {
            DataSourceCode.TUSHARE: ChinaDataSource.TUSHARE,
            DataSourceCode.AKSHARE: ChinaDataSource.AKSHARE,
            DataSourceCode.BAOSTOCK: ChinaDataSource.BAOSTOCK,
        }
        return source_mapping.get(env_source, ChinaDataSource.AKSHARE)

    # ==================== Tushare数据接口 ====================
    def get_china_stock_data_tushare(self, symbol: str, start_date: str, end_date: str) -> str:
        original_source = self.current_source
        self.current_source = ChinaDataSource.TUSHARE
        try:
            return self._get_tushare_data(symbol, start_date, end_date)
        finally:
            self.current_source = original_source

    def get_fundamentals_data(self, symbol: str) -> str:
        logger.info(f"📊 [数据来源: {self.current_source.value}] 开始获取基本面数据: {symbol}",
                   extra={'symbol': symbol, 'data_source': self.current_source.value, 'event_type': 'fundamentals_fetch_start'})
        start_time = time.time()
        try:
            if self.current_source == ChinaDataSource.MONGODB:
                result = self._get_mongodb_fundamentals(symbol)
            elif self.current_source == ChinaDataSource.TUSHARE:
                result = self._get_tushare_fundamentals(symbol)
            elif self.current_source == ChinaDataSource.AKSHARE:
                result = self._get_akshare_fundamentals(symbol)
            else:
                result = self._generate_fundamentals_analysis(symbol)

            duration = time.time() - start_time
            result_length = len(result) if result else 0
            if result and "❌" not in result:
                logger.info(f"✅ [数据来源: {self.current_source.value}] 成功获取基本面数据: {symbol} ({result_length}字符, 耗时{duration:.2f}秒)",
                           extra={'symbol': symbol, 'data_source': self.current_source.value, 'duration': duration, 'result_length': result_length, 'event_type': 'fundamentals_fetch_success'})
                return result
            else:
                logger.warning(f"⚠️ [数据来源: {self.current_source.value}失败] 基本面数据质量异常，尝试降级: {symbol}",
                              extra={'symbol': symbol, 'data_source': self.current_source.value, 'event_type': 'fundamentals_fetch_fallback'})
                return self._try_fallback_fundamentals(symbol)
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"❌ [数据来源: {self.current_source.value}异常] 获取基本面数据失败: {symbol} - {e}",
                        extra={'symbol': symbol, 'data_source': self.current_source.value, 'duration': duration, 'error': str(e), 'event_type': 'fundamentals_fetch_exception'}, exc_info=True)
            return self._try_fallback_fundamentals(symbol)

    def get_china_stock_fundamentals_tushare(self, symbol: str) -> str:
        return self._get_tushare_fundamentals(symbol)

    def get_news_data(self, symbol: str = None, hours_back: int = 24, limit: int = 20) -> List[Dict[str, Any]]:
        logger.info(f"📰 [数据来源: {self.current_source.value}] 开始获取新闻数据: {symbol or '市场新闻'}, 回溯{hours_back}小时",
                   extra={'symbol': symbol, 'hours_back': hours_back, 'limit': limit, 'data_source': self.current_source.value, 'event_type': 'news_fetch_start'})
        start_time = time.time()
        try:
            if self.current_source == ChinaDataSource.MONGODB:
                result = self._get_mongodb_news(symbol, hours_back, limit)
            elif self.current_source == ChinaDataSource.TUSHARE:
                result = self._get_tushare_news(symbol, hours_back, limit)
            elif self.current_source == ChinaDataSource.AKSHARE:
                result = self._get_akshare_news(symbol, hours_back, limit)
            else:
                logger.warning(f"⚠️ 数据源 {self.current_source.value} 不支持新闻数据")
                result = []

            duration = time.time() - start_time
            result_count = len(result) if result else 0
            if result and result_count > 0:
                logger.info(f"✅ [数据来源: {self.current_source.value}] 成功获取新闻数据: {symbol or '市场新闻'} ({result_count}条, 耗时{duration:.2f}秒)",
                           extra={'symbol': symbol, 'data_source': self.current_source.value, 'news_count': result_count, 'duration': duration, 'event_type': 'news_fetch_success'})
                return result
            else:
                logger.warning(f"⚠️ [数据来源: {self.current_source.value}] 未获取到新闻数据: {symbol or '市场新闻'}，尝试降级",
                              extra={'symbol': symbol, 'data_source': self.current_source.value, 'duration': duration, 'event_type': 'news_fetch_fallback'})
                return self._try_fallback_news(symbol, hours_back, limit)
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"❌ [数据来源: {self.current_source.value}异常] 获取新闻数据失败: {symbol or '市场新闻'} - {e}",
                        extra={'symbol': symbol, 'data_source': self.current_source.value, 'duration': duration, 'error': str(e), 'event_type': 'news_fetch_exception'}, exc_info=True)
            return self._try_fallback_news(symbol, hours_back, limit)

    def _check_available_sources(self) -> List[ChinaDataSource]:
        available = []
        enabled_sources_in_db = set()
        try:
            from app.core.database import get_mongo_db_sync
            db = get_mongo_db_sync()
            config_collection = db.system_configs
            config_data = config_collection.find_one({"is_active": True}, sort=[("version", -1)])
            if config_data and config_data.get('data_source_configs'):
                data_source_configs = config_data.get('data_source_configs', [])
                for ds in data_source_configs:
                    if ds.get('enabled', True):
                        ds_type = ds.get('type', '').lower()
                        enabled_sources_in_db.add(ds_type)
                logger.info(f"✅ [数据源配置] 从数据库读取到已启用的数据源: {enabled_sources_in_db}")
            else:
                enabled_sources_in_db = {'mongodb', 'tushare', 'akshare', 'baostock'}
        except Exception as e:
            logger.warning(f"⚠️ [数据源配置] 从数据库读取失败: {e}")
            enabled_sources_in_db = {'mongodb', 'tushare', 'akshare', 'baostock'}

        if self.use_mongodb_cache and 'mongodb' in enabled_sources_in_db:
            try:
                from tradingagents.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
                adapter = get_mongodb_cache_adapter()
                if adapter.use_app_cache and adapter.db is not None:
                    available.append(ChinaDataSource.MONGODB)
                    logger.info("✅ MongoDB数据源可用且已启用（最高优先级）")
            except Exception as e:
                logger.warning(f"⚠️ MongoDB数据源不可用: {e}")

        datasource_configs = self._get_datasource_configs_from_db()
        if 'tushare' in enabled_sources_in_db:
            try:
                import tushare as ts
                token = datasource_configs.get('tushare', {}).get('api_key') or os.getenv('TUSHARE_TOKEN')
                if token:
                    available.append(ChinaDataSource.TUSHARE)
                    source = "数据库配置" if datasource_configs.get('tushare', {}).get('api_key') else "环境变量"
                    logger.info(f"✅ Tushare数据源可用且已启用 (API Key来源: {source})")
            except ImportError:
                logger.warning("⚠️ Tushare数据源不可用: 库未安装")

        if 'akshare' in enabled_sources_in_db:
            try:
                import akshare as ak
                available.append(ChinaDataSource.AKSHARE)
                logger.info("✅ AKShare数据源可用且已启用")
            except ImportError:
                logger.warning("⚠️ AKShare数据源不可用: 库未安装")

        if 'baostock' in enabled_sources_in_db:
            try:
                import baostock as bs
                available.append(ChinaDataSource.BAOSTOCK)
                logger.info(f"✅ BaoStock数据源可用且已启用")
            except ImportError:
                logger.warning(f"⚠️ BaoStock数据源不可用: 库未安装")

        return available

    def _get_datasource_configs_from_db(self) -> dict:
        try:
            from app.core.database import get_mongo_db_sync
            db = get_mongo_db_sync()
            config = db.system_configs.find_one({"is_active": True})
            if not config:
                return {}
            datasource_configs = config.get('data_source_configs', [])
            result = {}
            for ds_config in datasource_configs:
                name = ds_config.get('name', '').lower()
                result[name] = {
                    'api_key': ds_config.get('api_key', ''),
                    'api_secret': ds_config.get('api_secret', ''),
                    'config_params': ds_config.get('config_params', {})
                }
            return result
        except Exception as e:
            logger.warning(f"⚠️ 从数据库读取数据源配置失败: {e}")
            return {}

    def get_current_source(self) -> ChinaDataSource:
        return self.current_source

    def set_current_source(self, source: ChinaDataSource) -> bool:
        if source in self.available_sources:
            self.current_source = source
            logger.info(f"✅ 数据源已切换到: {source.value}")
            return True
        else:
            logger.error(f"❌ 数据源不可用: {source.value}")
            return False

    def get_data_adapter(self):
        if self.current_source == ChinaDataSource.MONGODB:
            return self._get_mongodb_adapter()
        elif self.current_source == ChinaDataSource.TUSHARE:
            return self._get_tushare_adapter()
        elif self.current_source == ChinaDataSource.AKSHARE:
            return self._get_akshare_adapter()
        elif self.current_source == ChinaDataSource.BAOSTOCK:
            return self._get_baostock_adapter()
        else:
            raise ValueError(f"不支持的数据源: {self.current_source}")

    def _get_mongodb_adapter(self):
        try:
            from tradingagents.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
            return get_mongodb_cache_adapter()
        except ImportError as e:
            logger.error(f"❌ MongoDB适配器导入失败: {e}")
            return None

    def _get_tushare_adapter(self):
        try:
            from .providers.china.tushare import get_tushare_provider
            return get_tushare_provider()
        except ImportError as e:
            logger.error(f"❌ Tushare提供器导入失败: {e}")
            return None

    def _get_akshare_adapter(self):
        try:
            from .providers.china.akshare import get_akshare_provider
            return get_akshare_provider()
        except ImportError as e:
            logger.error(f"❌ AKShare适配器导入失败: {e}")
            return None

    def _get_baostock_adapter(self):
        try:
            from .providers.china.baostock import get_baostock_provider
            return get_baostock_provider()
        except ImportError as e:
            logger.error(f"❌ BaoStock适配器导入失败: {e}")
            return None

    def _get_cached_data(self, symbol: str, start_date: str = None, end_date: str = None, max_age_hours: int = 24) -> Optional[pd.DataFrame]:
        if not self.cache_enabled or not self.cache_manager:
            return None
        try:
            cache_key = self.cache_manager.find_cached_stock_data(symbol=symbol, start_date=start_date, end_date=end_date, max_age_hours=max_age_hours)
            if cache_key:
                cached_data = self.cache_manager.load_stock_data(cache_key)
                if cached_data is not None and not cached_data.empty:
                    logger.debug(f"📦 从缓存获取{symbol}数据: {len(cached_data)}条")
                    return cached_data
        except Exception as e:
            logger.warning(f"⚠️ 从缓存读取数据失败: {e}")
        return None

    def _save_to_cache(self, symbol: str, data: pd.DataFrame, start_date: str = None, end_date: str = None):
        if not self.cache_enabled or not self.cache_manager:
            return
        try:
            if data is not None and not data.empty:
                self.cache_manager.save_stock_data(symbol, data, start_date, end_date)
                logger.debug(f"💾 保存{symbol}数据到缓存: {len(data)}条")
        except Exception as e:
            logger.warning(f"⚠️ 保存数据到缓存失败: {e}")

    def _get_volume_safely(self, data: pd.DataFrame) -> float:
        try:
            if 'volume' in data.columns:
                return data['volume'].iloc[-1]
            elif 'vol' in data.columns:
                return data['vol'].iloc[-1]
            else:
                return 0
        except Exception:
            return 0

    def _format_stock_data_response(self, data: pd.DataFrame, symbol: str, stock_name: str, start_date: str, end_date: str) -> str:
        try:
            original_data_count = len(data)
            logger.info(f"📊 [技术指标] 开始计算技术指标，原始数据: {original_data_count}条")
            if 'date' in data.columns:
                data = data.sort_values('date')

            data['ma5'] = data['close'].rolling(window=5, min_periods=1).mean()
            data['ma10'] = data['close'].rolling(window=10, min_periods=1).mean()
            data['ma20'] = data['close'].rolling(window=20, min_periods=1).mean()
            data['ma60'] = data['close'].rolling(window=60, min_periods=1).mean()

            delta = data['close'].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)

            avg_gain6 = gain.ewm(com=5, adjust=True).mean()
            avg_loss6 = loss.ewm(com=5, adjust=True).mean()
            rs6 = avg_gain6 / avg_loss6.replace(0, np.nan)
            data['rsi6'] = 100 - (100 / (1 + rs6))

            avg_gain12 = gain.ewm(com=11, adjust=True).mean()
            avg_loss12 = loss.ewm(com=11, adjust=True).mean()
            rs12 = avg_gain12 / avg_loss12.replace(0, np.nan)
            data['rsi12'] = 100 - (100 / (1 + rs12))

            avg_gain24 = gain.ewm(com=23, adjust=True).mean()
            avg_loss24 = loss.ewm(com=23, adjust=True).mean()
            rs24 = avg_gain24 / avg_loss24.replace(0, np.nan)
            data['rsi24'] = 100 - (100 / (1 + rs24))

            gain14 = gain.rolling(window=14, min_periods=1).mean()
            loss14 = loss.rolling(window=14, min_periods=1).mean()
            rs14 = gain14 / loss14.replace(0, np.nan)
            data['rsi14'] = 100 - (100 / (1 + rs14))

            ema12 = data['close'].ewm(span=12, adjust=False).mean()
            ema26 = data['close'].ewm(span=26, adjust=False).mean()
            data['macd_dif'] = ema12 - ema26
            data['macd_dea'] = data['macd_dif'].ewm(span=9, adjust=False).mean()
            data['macd'] = (data['macd_dif'] - data['macd_dea']) * 2

            data['boll_mid'] = data['close'].rolling(window=20, min_periods=1).mean()
            std = data['close'].rolling(window=20, min_periods=1).std()
            data['boll_upper'] = data['boll_mid'] + 2 * std
            data['boll_lower'] = data['boll_mid'] - 2 * std

            logger.info(f"✅ [技术指标] 技术指标计算完成")
            display_rows = min(5, len(data))
            display_data = data.tail(display_rows)
            latest_data = data.iloc[-1]

            latest_price = latest_data.get('close', 0)
            prev_close = data.iloc[-2].get('close', latest_price) if len(data) > 1 else latest_price
            change = latest_price - prev_close
            change_pct = (change / prev_close * 100) if prev_close != 0 else 0

            from tradingagents.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
            adapter = get_mongodb_cache_adapter()
            mq = adapter.get_market_quotes(symbol)

            if mq is not None and hasattr(mq, 'close'):
                display_price = mq.close
                logger.info(f'✅ 使用实时行情价格: {display_price}; 日线收盘价: {latest_price}')
            else:
                display_price = latest_price
                logger.warning(f'⚠️ 未获取到实时行情，使用日线收盘价代替: {display_price} 股票代码: {symbol}')

            result = f"📊 {stock_name}({symbol}) - 技术分析数据\n"
            result += f"数据期间: {start_date} 至 {end_date}\n"
            result += f"数据条数: {original_data_count}条 (展示最近{display_rows}个交易日)\n\n"
            result += f"💰 最新价格: ¥{display_price:.2f}\n"
            result += f"📈 涨跌额: {change:+.2f} ({change_pct:+.2f}%)\n\n"

            result += f"📊 移动平均线 (MA):\n"
            result += f"   MA5:  ¥{latest_data['ma5']:.2f} {'(价格在MA5上方 ↑)' if latest_price > latest_data['ma5'] else '(价格在MA5下方 ↓)'}\n"
            result += f"   MA10: ¥{latest_data['ma10']:.2f} {'(价格在MA10上方 ↑)' if latest_price > latest_data['ma10'] else '(价格在MA10下方 ↓)'}\n"
            result += f"   MA20: ¥{latest_data['ma20']:.2f} {'(价格在MA20上方 ↑)' if latest_price > latest_data['ma20'] else '(价格在MA20下方 ↓)'}\n"
            result += f"   MA60: ¥{latest_data['ma60']:.2f} {'(价格在MA60上方 ↑)' if latest_price > latest_data['ma60'] else '(价格在MA60下方 ↓)'}\n\n"

            result += f"📈 MACD指标:\n"
            result += f"   DIF:  {latest_data['macd_dif']:.3f}\n"
            result += f"   DEA:  {latest_data['macd_dea']:.3f}\n"
            result += f"   MACD: {latest_data['macd']:.3f} {'(多头 ↑)' if latest_data['macd'] > 0 else '(空头 ↓)'}\n"

            if len(data) > 1:
                prev_dif = data.iloc[-2]['macd_dif']
                prev_dea = data.iloc[-2]['macd_dea']
                curr_dif = latest_data['macd_dif']
                curr_dea = latest_data['macd_dea']
                if prev_dif <= prev_dea and curr_dif > curr_dea:
                    result += "   ⚠️ MACD金叉信号（DIF上穿DEA）\n\n"
                elif prev_dif >= prev_dea and curr_dif < curr_dea:
                    result += "   ⚠️ MACD死叉信号（DIF下穿DEA）\n\n"
                else:
                    result += "\n"
            else:
                result += "\n"

            rsi6 = latest_data['rsi6']
            rsi12 = latest_data['rsi12']
            rsi24 = latest_data['rsi24']
            result += f"📉 RSI指标 (同花顺风格):\n"
            result += f"   RSI6:  {rsi6:.2f} {'(超买 ⚠️)' if rsi6 >= 80 else '(超卖 ⚠️)' if rsi6 <= 20 else ''}\n"
            result += f"   RSI12: {rsi12:.2f} {'(超买 ⚠️)' if rsi12 >= 80 else '(超卖 ⚠️)' if rsi12 <= 20 else ''}\n"
            result += f"   RSI24: {rsi24:.2f} {'(超买 ⚠️)' if rsi24 >= 80 else '(超卖 ⚠️)' if rsi24 <= 20 else ''}\n"

            if rsi6 > rsi12 > rsi24:
                result += "   趋势: 多头排列 ↑\n\n"
            elif rsi6 < rsi12 < rsi24:
                result += "   趋势: 空头排列 ↓\n\n"
            else:
                result += "   趋势: 震荡整理 ↔\n\n"

            result += f"📊 布林带 (BOLL):\n"
            result += f"   上轨: ¥{latest_data['boll_upper']:.2f}\n"
            result += f"   中轨: ¥{latest_data['boll_mid']:.2f}\n"
            result += f"   下轨: ¥{latest_data['boll_lower']:.2f}\n"

            boll_position = (latest_price - latest_data['boll_lower']) / (latest_data['boll_upper'] - latest_data['boll_lower']) * 100
            result += f"   价格位置: {boll_position:.1f}%"
            if boll_position >= 80:
                result += " (接近上轨，可能超买 ⚠️)\n\n"
            elif boll_position <= 20:
                result += " (接近下轨，可能超卖 ⚠️)\n\n"
            else:
                result += " (中性区域)\n\n"

            result += f"📊 价格统计 (最近{display_rows}个交易日):\n"
            result += f"   最高价: ¥{display_data['high'].max():.2f}\n"
            result += f"   最低价: ¥{display_data['low'].min():.2f}\n"
            result += f"   平均价: ¥{display_data['close'].mean():.2f}\n"
            volume_value = self._get_volume_safely(display_data)
            result += f"   平均成交量: {volume_value:,.0f}股\n"
            return result
        except Exception as e:
            logger.error(f"❌ 格式化数据响应失败: {e}", exc_info=True)
            return f"❌ 格式化{symbol}数据失败: {e}"

    def get_stock_dataframe(self, symbol: str, start_date: str = None, end_date: str = None, period: str = "daily") -> pd.DataFrame:
        logger.info(f"📊 [DataFrame接口] 获取股票数据: {symbol} ({start_date} 到 {end_date})")
        try:
            df = None
            if self.current_source == ChinaDataSource.MONGODB:
                from tradingagents.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
                adapter = get_mongodb_cache_adapter()
                df = adapter.get_historical_data(symbol, start_date, end_date, period=period)
            elif self.current_source == ChinaDataSource.TUSHARE:
                from .providers.china.tushare import get_tushare_provider
                provider = get_tushare_provider()
                df = provider.get_daily_data(symbol, start_date, end_date)
            elif self.current_source == ChinaDataSource.AKSHARE:
                from .providers.china.akshare import get_akshare_provider
                provider = get_akshare_provider()
                df = provider.get_stock_data(symbol, start_date, end_date)
            elif self.current_source == ChinaDataSource.BAOSTOCK:
                from .providers.china.baostock import get_baostock_provider
                provider = get_baostock_provider()
                df = provider.get_stock_data(symbol, start_date, end_date)

            if df is not None and not df.empty:
                logger.info(f"✅ [DataFrame接口] 从 {self.current_source.value} 获取成功: {len(df)}条")
                return self._standardize_dataframe(df)

            logger.warning(f"⚠️ [DataFrame接口] {self.current_source.value} 失败，尝试降级")
            for source in self.available_sources:
                if source == self.current_source:
                    continue
                try:
                    if source == ChinaDataSource.MONGODB:
                        from tradingagents.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
                        adapter = get_mongodb_cache_adapter()
                        df = adapter.get_historical_data(symbol, start_date, end_date, period=period)
                    elif source == ChinaDataSource.TUSHARE:
                        from .providers.china.tushare import get_tushare_provider
                        provider = get_tushare_provider()
                        df = provider.get_daily_data(symbol, start_date, end_date)
                    elif source == ChinaDataSource.AKSHARE:
                        from .providers.china.akshare import get_akshare_provider
                        provider = get_akshare_provider()
                        df = provider.get_stock_data(symbol, start_date, end_date)
                    elif source == ChinaDataSource.BAOSTOCK:
                        from .providers.china.baostock import get_baostock_provider
                        provider = get_baostock_provider()
                        df = provider.get_stock_data(symbol, start_date, end_date)

                    if df is not None and not df.empty:
                        logger.info(f"✅ [DataFrame接口] 降级到 {source.value} 成功: {len(df)}条")
                        return self._standardize_dataframe(df)
                except Exception as e:
                    logger.warning(f"⚠️ [DataFrame接口] {source.value} 失败: {e}")
                    continue

            logger.error(f"❌ [DataFrame接口] 所有数据源都失败: {symbol}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"❌ [DataFrame接口] 获取失败: {e}", exc_info=True)
            return pd.DataFrame()

    def _standardize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        out = df.copy()
        colmap = {
            'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close',
            'Volume': 'vol', 'Amount': 'amount', 'symbol': 'code', 'Symbol': 'code',
            'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close',
            'vol': 'vol', 'volume': 'vol', 'amount': 'amount', 'code': 'code',
            'date': 'date', 'trade_date': 'date',
            '日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close',
            '成交量': 'vol', '成交额': 'amount', '涨跌幅': 'pct_change', '涨跌额': 'change',
        }
        out = out.rename(columns={c: colmap.get(c, c) for c in out.columns})
        if 'date' in out.columns:
            try:
                out['date'] = pd.to_datetime(out['date'])
                out = out.sort_values('date')
            except Exception:
                pass
        if 'pct_change' not in out.columns and 'close' in out.columns:
            out['pct_change'] = out['close'].pct_change() * 100.0
        return out

    def get_stock_data(self, symbol: str, start_date: str = None, end_date: str = None, period: str = "daily") -> str:
        logger.info(f"📊 [数据来源: {self.current_source.value}] 开始获取{period}数据: {symbol}",
                   extra={'symbol': symbol, 'start_date': start_date, 'end_date': end_date, 'period': period, 'data_source': self.current_source.value, 'event_type': 'data_fetch_start'})
        logger.info(f"🔍 [股票代码追踪] DataSourceManager.get_stock_data 接收到的股票代码: '{symbol}' (类型: {type(symbol)})")
        start_time = time.time()
        try:
            actual_source = None
            if self.current_source == ChinaDataSource.MONGODB:
                result, actual_source = self._get_mongodb_data(symbol, start_date, end_date, period)
            elif self.current_source == ChinaDataSource.TUSHARE:
                logger.info(f"🔍 [股票代码追踪] 调用 Tushare 数据源，传入参数: symbol='{symbol}', period='{period}'")
                result = self._get_tushare_data(symbol, start_date, end_date, period)
                actual_source = "tushare"
            elif self.current_source == ChinaDataSource.AKSHARE:
                result = self._get_akshare_data(symbol, start_date, end_date, period)
                actual_source = "akshare"
            elif self.current_source == ChinaDataSource.BAOSTOCK:
                result = self._get_baostock_data(symbol, start_date, end_date, period)
                actual_source = "baostock"
            else:
                result = f"❌ 不支持的数据源: {self.current_source.value}"
                actual_source = None

            duration = time.time() - start_time
            result_length = len(result) if result else 0
            is_success = result and "❌" not in result and "错误" not in result
            display_source = actual_source or self.current_source.value

            if is_success:
                logger.info(f"✅ [数据来源: {display_source}] 成功获取股票数据: {symbol} ({result_length}字符, 耗时{duration:.2f}秒)",
                           extra={'symbol': symbol, 'start_date': start_date, 'end_date': end_date, 'data_source': display_source, 'actual_source': actual_source, 'requested_source': self.current_source.value, 'duration': duration, 'result_length': result_length, 'event_type': 'data_fetch_success'})
                return result
            else:
                logger.warning(f"⚠️ [数据来源: {self.current_source.value}失败] 数据质量异常，尝试降级到其他数据源: {symbol}",
                              extra={'symbol': symbol, 'start_date': start_date, 'end_date': end_date, 'data_source': self.current_source.value, 'duration': duration, 'result_length': result_length, 'event_type': 'data_fetch_warning'})
                fallback_result_tuple = self._try_fallback_sources(symbol, start_date, end_date)
                fallback_result = self._extract_result_from_tuple(fallback_result_tuple)
                if fallback_result and "❌" not in fallback_result and "错误" not in fallback_result:
                    logger.info(f"✅ [数据来源: 备用数据源] 降级成功获取数据: {symbol}")
                    return fallback_result
                else:
                    logger.error(f"❌ [数据来源: 所有数据源失败] 所有数据源都无法获取有效数据: {symbol}")
                    return result
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"❌ [数据获取] 异常失败: {e}",
                        extra={'symbol': symbol, 'start_date': start_date, 'end_date': end_date, 'data_source': self.current_source.value, 'duration': duration, 'error': str(e), 'event_type': 'data_fetch_exception'}, exc_info=True)
            return self._extract_result_from_tuple(self._try_fallback_sources(symbol, start_date, end_date))

    def _get_mongodb_data(self, symbol: str, start_date: str, end_date: str, period: str = "daily") -> tuple[str, str | None]:
        logger.debug(f"📊 [MongoDB] 调用参数: symbol={symbol}, start_date={start_date}, end_date={end_date}, period={period}")
        try:
            from tradingagents.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
            adapter = get_mongodb_cache_adapter()
            df = adapter.get_historical_data(symbol, start_date, end_date, period=period)
            if df is not None and not df.empty:
                logger.info(f"✅ [数据来源: MongoDB缓存] 成功获取{period}数据: {symbol} ({len(df)}条记录)")
                stock_name = f'股票{symbol}'
                if 'name' in df.columns and not df['name'].empty:
                    stock_name = df['name'].iloc[0]
                result = self._format_stock_data_response(df, symbol, stock_name, start_date, end_date)
                logger.info(f"✅ [MongoDB] 已计算技术指标: MA5/10/20/60, MACD, RSI, BOLL")
                return result, "mongodb"
            else:
                logger.info(f"🔄 [MongoDB] 未找到{period}数据: {symbol}，开始尝试备用数据源")
                return self._try_fallback_sources(symbol, start_date, end_date, period)
        except Exception as e:
            logger.error(f"❌ [数据来源: MongoDB异常] 获取{period}数据失败: {symbol}, 错误: {e}")
            return self._try_fallback_sources(symbol, start_date, end_date, period)

    def _get_tushare_data(self, symbol: str, start_date: str, end_date: str, period: str = "daily") -> str:
        logger.debug(f"📊 [Tushare] 调用参数: symbol={symbol}, start_date={start_date}, end_date={end_date}, period={period}")
        logger.info(f"🔍 [股票代码追踪] _get_tushare_data 接收到的股票代码: '{symbol}'")
        start_time = time.time()
        try:
            cached_data = self._get_cached_data(symbol, start_date, end_date, max_age_hours=24)
            if cached_data is not None and not cached_data.empty:
                logger.info(f"✅ [缓存命中] 从缓存获取{symbol}数据")
                provider = self._get_tushare_adapter()
                stock_name = f'股票{symbol}'
                if provider:
                    stock_info = run_async_safe(provider.get_stock_basic_info(symbol))
                    stock_name = stock_info.get('name', f'股票{symbol}') if stock_info else f'股票{symbol}'
                return self._format_stock_data_response(cached_data, symbol, stock_name, start_date, end_date)

            provider = self._get_tushare_adapter()
            if not provider:
                return f"❌ Tushare提供器不可用"
            data = run_async_safe(provider.get_historical_data(symbol, start_date, end_date))
            if data is not None and not data.empty:
                self._save_to_cache(symbol, data, start_date, end_date)
                stock_info = run_async_safe(provider.get_stock_basic_info(symbol))
                stock_name = stock_info.get('name', f'股票{symbol}') if stock_info else f'股票{symbol}'
                result = self._format_stock_data_response(data, symbol, stock_name, start_date, end_date)
                duration = time.time() - start_time
                logger.info(f"🔍 [DataSourceManager详细日志] 调用完成，耗时: {duration:.3f}秒")
                return result
            else:
                return f"❌ 未获取到{symbol}的有效数据"
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"❌ [Tushare] 调用失败: {e}, 耗时={duration:.2f}s", exc_info=True)
            raise

    def _get_akshare_data(self, symbol: str, start_date: str, end_date: str, period: str = "daily") -> str:
        logger.debug(f"📊 [AKShare] 调用参数: symbol={symbol}, start_date={start_date}, end_date={end_date}, period={period}")
        start_time = time.time()
        try:
            from .providers.china.akshare import get_akshare_provider
            provider = get_akshare_provider()
            data = run_async_safe(provider.get_historical_data(symbol, start_date, end_date, period))
            duration = time.time() - start_time
            if data is not None and not data.empty:
                stock_info = run_async_safe(provider.get_stock_basic_info(symbol))
                stock_name = stock_info.get('name', f'股票{symbol}') if stock_info else f'股票{symbol}'
                result = self._format_stock_data_response(data, symbol, stock_name, start_date, end_date)
                logger.info(f"✅ [AKShare] 已计算技术指标: MA5/10/20/60, MACD, RSI, BOLL")
                return result
            else:
                return f"❌ 未能获取{symbol}的股票数据"
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"❌ [AKShare] 调用失败: {e}, 耗时={duration:.2f}s", exc_info=True)
            return f"❌ AKShare获取{symbol}数据失败: {e}"

    def _get_baostock_data(self, symbol: str, start_date: str, end_date: str, period: str = "daily") -> str:
        from .providers.china.baostock import get_baostock_provider
        provider = get_baostock_provider()
        data = run_async_safe(provider.get_historical_data(symbol, start_date, end_date, period))
        if data is not None and not data.empty:
            stock_info = run_async_safe(provider.get_stock_basic_info(symbol))
            stock_name = stock_info.get('name', f'股票{symbol}') if stock_info else f'股票{symbol}'
            result = self._format_stock_data_response(data, symbol, stock_name, start_date, end_date)
            logger.info(f"✅ [BaoStock] 已计算技术指标: MA5/10/20/60, MACD, RSI, BOLL")
            return result
        else:
            return f"❌ 未能获取{symbol}的股票数据"

    def _extract_result_from_tuple(self, result_tuple: tuple) -> str:
        if result_tuple is None:
            return ""
        if isinstance(result_tuple, tuple) and len(result_tuple) > 0:
            result = result_tuple[0]
            if isinstance(result, str):
                logger.info(f"🔍 [提取] 原始字符串长度: {len(result)}")
                return result
            else:
                return str(result)
        return str(result_tuple) if result_tuple else ""

    def _try_fallback_sources(self, symbol: str, start_date: str, end_date: str, period: str = "daily") -> tuple[str, str | None]:
        logger.info(f"🔄 [{self.current_source.value}] 失败，尝试备用数据源获取{period}数据: {symbol}")
        fallback_order = self._get_data_source_priority_order(symbol)
        for source in fallback_order:
            if source != self.current_source and source in self.available_sources:
                try:
                    logger.info(f"🔄 [备用数据源] 尝试 {source.value} 获取{period}数据: {symbol}")
                    if source == ChinaDataSource.TUSHARE:
                        result = self._get_tushare_data(symbol, start_date, end_date, period)
                    elif source == ChinaDataSource.AKSHARE:
                        result = self._get_akshare_data(symbol, start_date, end_date, period)
                    elif source == ChinaDataSource.BAOSTOCK:
                        result = self._get_baostock_data(symbol, start_date, end_date, period)
                    else:
                        continue
                    if "❌" not in result:
                        logger.info(f"✅ [备用数据源-{source.value}] 成功获取{period}数据: {symbol}")
                        return result, source.value
                except Exception as e:
                    logger.error(f"❌ [备用数据源-{source.value}] 获取失败: {symbol}, 错误: {e}")
                    continue
        logger.error(f"❌ [所有数据源失败] 无法获取{period}数据: {symbol}")
        return f"❌ 所有数据源都无法获取{symbol}的{period}数据", None

    def get_stock_info(self, symbol: str) -> Dict:
        logger.info(f"📊 [数据来源: {self.current_source.value}] 开始获取股票信息: {symbol}")
        try:
            from tradingagents.config.runtime_settings import use_app_cache_enabled
            use_cache = use_app_cache_enabled(False)
        except Exception as e:
            use_cache = False

        if use_cache:
            try:
                from .cache.app_adapter import get_basics_from_cache, get_market_quote_dataframe
                doc = get_basics_from_cache(symbol)
                if doc:
                    name = doc.get('name') or doc.get('stock_name') or ''
                    board_labels = {'主板', '中小板', '创业板', '科创板'}
                    raw_industry = (doc.get('industry') or doc.get('industry_name') or '').strip()
                    sec_or_cat = (doc.get('sec') or doc.get('category') or '').strip()
                    market_val = (doc.get('market') or '').strip()
                    industry_val = raw_industry or sec_or_cat or '未知'
                    if raw_industry in board_labels:
                        if not market_val:
                            market_val = raw_industry
                        if sec_or_cat:
                            industry_val = sec_or_cat
                    result = {
                        'symbol': symbol,
                        'name': name or f'股票{symbol}',
                        'area': doc.get('area', '未知'),
                        'industry': industry_val or '未知',
                        'market': market_val or doc.get('market', '未知'),
                        'list_date': doc.get('list_date', '未知'),
                        'source': 'app_cache'
                    }
                    try:
                        df = get_market_quote_dataframe(symbol)
                        if df is not None and not df.empty:
                            row = df.iloc[-1]
                            result['current_price'] = row.get('close')
                            result['change_pct'] = row.get('pct_chg')
                            result['volume'] = row.get('volume')
                            result['quote_date'] = row.get('date')
                            result['quote_source'] = 'market_quotes'
                    except Exception:
                        pass
                    if name:
                        logger.info(f"✅ [数据来源: MongoDB-stock_basic_info] 成功获取: {symbol}")
                        return result
            except Exception as e:
                logger.error(f"❌ [数据来源: MongoDB异常] 获取股票信息失败: {e}", exc_info=True)

        try:
            if self.current_source == ChinaDataSource.TUSHARE:
                info_str = get_china_stock_info_tushare(symbol)
                result = self._parse_stock_info_string(info_str, symbol)
                if result.get('name') and result['name'] != f'股票{symbol}':
                    logger.info(f"✅ [数据来源: Tushare-股票信息] 成功获取: {symbol}")
                    return result
                else:
                    return self._try_fallback_stock_info(symbol)
            else:
                adapter = self.get_data_adapter()
                if adapter and hasattr(adapter, 'get_stock_info'):
                    result = adapter.get_stock_info(symbol)
                    if result.get('name') and result['name'] != f'股票{symbol}':
                        logger.info(f"✅ [数据来源: {self.current_source.value}-股票信息] 成功获取: {symbol}")
                        return result
                    else:
                        return self._try_fallback_stock_info(symbol)
                else:
                    return self._try_fallback_stock_info(symbol)
        except Exception as e:
            logger.error(f"❌ [数据来源: {self.current_source.value}异常] 获取股票信息失败: {e}", exc_info=True)
            return self._try_fallback_stock_info(symbol)

    def get_stock_basic_info(self, stock_code: str = None) -> Optional[Dict[str, Any]]:
        if stock_code is None:
            logger.info("📊 获取所有股票列表")
            try:
                from tradingagents.config.database_manager import get_database_manager
                db_manager = get_database_manager()
                if db_manager and db_manager.is_mongodb_available():
                    collection = db_manager.mongodb_db['stock_basic_info']
                    stocks = list(collection.find({}, {'_id': 0}))
                    if stocks:
                        logger.info(f"✅ 从MongoDB获取所有股票: {len(stocks)}条")
                        return stocks
            except Exception as e:
                logger.warning(f"⚠️ 从MongoDB获取所有股票失败: {e}")
            return []

        try:
            result = self.get_stock_info(stock_code)
            if result and result.get('name'):
                return result
            else:
                return {'error': f'未找到股票 {stock_code} 的信息'}
        except Exception as e:
            logger.error(f"❌ 获取股票信息失败: {e}")
            return {'error': str(e)}

    def get_stock_data_with_fallback(self, stock_code: str, start_date: str, end_date: str) -> str:
        logger.info(f"📊 获取股票数据: {stock_code} ({start_date} 到 {end_date})")
        try:
            return self.get_stock_data(stock_code, start_date, end_date)
        except Exception as e:
            logger.error(f"❌ 获取股票数据失败: {e}")
            return f"❌ 获取股票数据失败: {str(e)}\n\n💡 建议：\n1. 检查网络连接\n2. 确认股票代码格式正确\n3. 检查数据源配置"

    def _try_fallback_stock_info(self, symbol: str) -> Dict:
        logger.error(f"🔄 {self.current_source.value}失败，尝试备用数据源获取股票信息...")
        available_sources = self.available_sources.copy()
        if self.current_source in available_sources:
            available_sources.remove(self.current_source)
        for source in available_sources:
            try:
                logger.info(f"🔄 尝试备用数据源获取股票信息: {source.value}")
                if source == ChinaDataSource.TUSHARE:
                    result = self._get_tushare_stock_info(symbol=symbol)
                elif source == ChinaDataSource.AKSHARE:
                    result = self._get_akshare_stock_info(symbol)
                elif source == ChinaDataSource.BAOSTOCK:
                    result = self._get_baostock_stock_info(symbol)
                else:
                    original_source = self.current_source
                    self.current_source = source
                    adapter = self.get_data_adapter()
                    self.current_source = original_source
                    if adapter and hasattr(adapter, 'get_stock_info'):
                        result = adapter.get_stock_info(symbol)
                    else:
                        continue
                if result.get('name') and result['name'] != f'股票{symbol}':
                    logger.info(f"✅ [数据来源: 备用数据源] 降级成功获取股票信息: {source.value}")
                    return result
            except Exception as e:
                logger.error(f"❌ 备用数据源{source.value}失败: {e}")
                continue
        logger.error(f"❌ 所有数据源都无法获取{symbol}的股票信息")
        return {'symbol': symbol, 'name': f'股票{symbol}', 'source': 'unknown'}

    def _get_tushare_stock_info(self, symbol: str) -> Dict:
        info_str = get_china_stock_info_tushare(symbol)
        return self._parse_stock_info_string(info_str, symbol)

    def _get_akshare_stock_info(self, symbol: str) -> Dict:
        try:
            import akshare as ak
            if symbol.startswith('6'):
                akshare_symbol = f"sh{symbol}"
            elif symbol.startswith(('0', '3', '2')):
                akshare_symbol = f"sz{symbol}"
            elif symbol.startswith(('8', '4')):
                akshare_symbol = f"bj{symbol}"
            else:
                akshare_symbol = symbol
            stock_info = ak.stock_individual_info_em(symbol=akshare_symbol)
            if not stock_info.empty:
                info = {'symbol': symbol, 'source': 'akshare'}
                name_row = stock_info[stock_info['item'] == '股票简称']
                info['name'] = name_row['value'].iloc[0] if not name_row.empty else f'股票{symbol}'
                info['area'] = '未知'
                info['industry'] = '未知'
                info['market'] = '未知'
                info['list_date'] = '未知'
                return info
            else:
                return {'symbol': symbol, 'name': f'股票{symbol}', 'source': 'akshare'}
        except Exception as e:
            logger.error(f"❌ [股票信息] AKShare获取失败: {symbol}, 错误: {e}")
            return {'symbol': symbol, 'name': f'股票{symbol}', 'source': 'akshare', 'error': str(e)}

    def _get_baostock_stock_info(self, symbol: str) -> Dict:
        try:
            import baostock as bs
            bs_code = f"sh.{symbol}" if symbol.startswith('6') else f"sz.{symbol}"
            lg = bs.login()
            if lg.error_code != '0':
                return {'symbol': symbol, 'name': f'股票{symbol}', 'source': 'baostock'}
            rs = bs.query_stock_basic(code=bs_code)
            data_list = []
            while rs.next():
                data_list.append(rs.get_row_data())
            bs.logout()
            if data_list:
                info = {'symbol': symbol, 'source': 'baostock'}
                info['name'] = data_list[0][1]
                info['area'] = '未知'
                info['industry'] = '未知'
                info['market'] = '未知'
                info['list_date'] = data_list[0][2]
                return info
            else:
                return {'symbol': symbol, 'name': f'股票{symbol}', 'source': 'baostock'}
        except Exception as e:
            logger.error(f"❌ [股票信息] BaoStock获取失败: {e}")
            return {'symbol': symbol, 'name': f'股票{symbol}', 'source': 'baostock', 'error': str(e)}

    def _parse_stock_info_string(self, info_str: str, symbol: str) -> Dict:
        try:
            info = {'symbol': symbol, 'source': self.current_source.value}
            for line in info_str.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    key, value = key.strip(), value.strip()
                    if '股票名称' in key:
                        info['name'] = value
                    elif '所属行业' in key:
                        info['industry'] = value
                    elif '所属地区' in key:
                        info['area'] = value
                    elif '上市市场' in key:
                        info['market'] = value
                    elif '上市日期' in key:
                        info['list_date'] = value
            return info
        except Exception as e:
            logger.error(f"⚠️ 解析股票信息失败: {e}")
            return {'symbol': symbol, 'name': f'股票{symbol}', 'source': self.current_source.value}

    # ==================== 基本面数据获取方法 ====================
    def _get_mongodb_fundamentals(self, symbol: str) -> str:
        logger.debug(f"📊 [MongoDB] 调用参数: symbol={symbol}")
        try:
            from tradingagents.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
            adapter = get_mongodb_cache_adapter()
            financial_data = adapter.get_financial_data(symbol)
            if financial_data is not None:
                if isinstance(financial_data, pd.DataFrame) and not financial_data.empty:
                    logger.info(f"✅ [数据来源: MongoDB-财务数据] 成功获取: {symbol} ({len(financial_data)}条记录)")
                    return self._format_financial_data(symbol, financial_data.to_dict('records'))
                elif isinstance(financial_data, list) and len(financial_data) > 0:
                    logger.info(f"✅ [数据来源: MongoDB-财务数据] 成功获取: {symbol} ({len(financial_data)}条记录)")
                    return self._format_financial_data(symbol, financial_data)
                elif isinstance(financial_data, dict):
                    logger.info(f"✅ [数据来源: MongoDB-财务数据] 成功获取: {symbol} (单条记录)")
                    return self._format_financial_data(symbol, [financial_data])
            logger.warning(f"⚠️ [数据来源: MongoDB] 未找到财务数据: {symbol}，降级到其他数据源")
            return self._try_fallback_fundamentals(symbol)
        except Exception as e:
            logger.error(f"❌ [数据来源: MongoDB异常] 获取财务数据失败: {e}", exc_info=True)
            return self._try_fallback_fundamentals(symbol)

    def _get_tushare_fundamentals(self, symbol: str) -> str:
        logger.warning(f"⚠️ Tushare基本面数据功能暂时不可用")
        return f"⚠️ Tushare基本面数据功能暂时不可用，请使用其他数据源"

    def _get_akshare_fundamentals(self, symbol: str) -> str:
        logger.debug(f"📊 [AKShare] 调用参数: symbol={symbol}")
        try:
            logger.info(f"📊 [数据来源: AKShare-生成分析] 生成基本面分析: {symbol}")
            return self._generate_fundamentals_analysis(symbol)
        except Exception as e:
            logger.error(f"❌ [数据来源: AKShare异常] 生成基本面分析失败: {e}")
            return f"❌ 生成{symbol}基本面分析失败: {e}"

    def _get_valuation_indicators(self, symbol: str) -> Dict:
        try:
            from tradingagents.config.database_manager import get_database_manager
            db_manager = get_database_manager()
            if not db_manager.is_mongodb_available():
                return {}
            collection = db_manager.mongodb_db['stock_basic_info']
            result = collection.find_one({'ts_code': symbol})
            if result:
                return {
                    'pe': result.get('pe'),
                    'pb': result.get('pb'),
                    'pe_ttm': result.get('pe_ttm'),
                    'total_mv': result.get('total_mv'),
                    'circ_mv': result.get('circ_mv')
                }
            return {}
        except Exception as e:
            logger.error(f"获取{symbol}估值指标失败: {e}")
            return {}

    def _format_financial_data(self, symbol: str, financial_data: List[Dict]) -> str:
        try:
            if not financial_data:
                return f"❌ 未找到{symbol}的财务数据"
            latest = financial_data[0]
            report = f"📊 {symbol} 基本面数据（来自MongoDB）\n\n"
            report += f"📅 报告期: {latest.get('report_period', latest.get('end_date', '未知'))}\n"
            report += f"📈 数据来源: MongoDB财务数据库\n\n"

            report += "💰 财务指标:\n"
            revenue = latest.get('revenue') or latest.get('total_revenue')
            if revenue is not None:
                report += f"   营业总收入: {revenue:,.2f}\n"
            net_profit = latest.get('net_profit') or latest.get('net_income')
            if net_profit is not None:
                report += f"   净利润: {net_profit:,.2f}\n"
            total_assets = latest.get('total_assets')
            if total_assets is not None:
                report += f"   总资产: {total_assets:,.2f}\n"
            total_liab = latest.get('total_liab')
            if total_liab is not None:
                report += f"   总负债: {total_liab:,.2f}\n"
            total_equity = latest.get('total_equity')
            if total_equity is not None:
                report += f"   股东权益: {total_equity:,.2f}\n"

            report += "\n📊 估值指标:\n"
            valuation_data = self._get_valuation_indicators(symbol)
            if valuation_data:
                pe = valuation_data.get('pe')
                if pe is not None:
                    report += f"   市盈率(PE): {pe:.2f}\n"
                pb = valuation_data.get('pb')
                if pb is not None:
                    report += f"   市净率(PB): {pb:.2f}\n"
                pe_ttm = valuation_data.get('pe_ttm')
                if pe_ttm is not None:
                    report += f"   市盈率TTM(PE_TTM): {pe_ttm:.2f}\n"
                total_mv = valuation_data.get('total_mv')
                if total_mv is not None:
                    report += f"   总市值: {total_mv:.2f}亿元\n"
                circ_mv = valuation_data.get('circ_mv')
                if circ_mv is not None:
                    report += f"   流通市值: {circ_mv:.2f}亿元\n"
            else:
                pe = latest.get('pe')
                if pe is not None:
                    report += f"   市盈率(PE): {pe:.2f}\n"
                pb = latest.get('pb')
                if pb is not None:
                    report += f"   市净率(PB): {pb:.2f}\n"

            report += "\n💹 盈利能力:\n"
            roe = latest.get('roe')
            if roe is not None:
                report += f"   净资产收益率(ROE): {roe:.2f}%\n"
            roa = latest.get('roa')
            if roa is not None:
                report += f"   总资产收益率(ROA): {roa:.2f}%\n"
            gross_margin = latest.get('gross_margin')
            if gross_margin is not None:
                report += f"   毛利率: {gross_margin:.2f}%\n"
            netprofit_margin = latest.get('netprofit_margin') or latest.get('net_margin')
            if netprofit_margin is not None:
                report += f"   净利率: {netprofit_margin:.2f}%\n"

            n_cashflow_act = latest.get('n_cashflow_act')
            if n_cashflow_act is not None:
                report += "\n💰 现金流:\n"
                report += f"   经营活动现金流: {n_cashflow_act:,.2f}\n"
                n_cashflow_inv_act = latest.get('n_cashflow_inv_act')
                if n_cashflow_inv_act is not None:
                    report += f"   投资活动现金流: {n_cashflow_inv_act:,.2f}\n"
                c_cash_equ_end_period = latest.get('c_cash_equ_end_period')
                if c_cash_equ_end_period is not None:
                    report += f"   期末现金及等价物: {c_cash_equ_end_period:,.2f}\n"

            report += f"\n📝 共有 {len(financial_data)} 期财务数据\n"
            return report
        except Exception as e:
            logger.error(f"❌ 格式化财务数据失败: {e}")
            return f"❌ 格式化{symbol}财务数据失败: {e}"

    def _generate_fundamentals_analysis(self, symbol: str) -> str:
        try:
            stock_info = self.get_stock_info(symbol)
            report = f"📊 {symbol} 基本面分析（生成）\n\n"
            report += f"📈 股票名称: {stock_info.get('name', '未知')}\n"
            report += f"🏢 所属行业: {stock_info.get('industry', '未知')}\n"
            report += f"📍 所属地区: {stock_info.get('area', '未知')}\n"
            report += f"📅 上市日期: {stock_info.get('list_date', '未知')}\n\n"
            report += "⚠️ 注意: 详细财务数据需要从数据源获取\n"
            report += "💡 建议: 启用MongoDB缓存以获取完整的财务数据\n"
            return report
        except Exception as e:
            logger.error(f"❌ 生成基本面分析失败: {e}")
            return f"❌ 生成{symbol}基本面分析失败: {e}"

    def _try_fallback_fundamentals(self, symbol: str) -> str:
        logger.error(f"🔄 {self.current_source.value}失败，尝试备用数据源获取基本面...")
        fallback_order = self._get_data_source_priority_order(symbol)
        for source in fallback_order:
            if source != self.current_source and source in self.available_sources:
                try:
                    logger.info(f"🔄 尝试备用数据源获取基本面: {source.value}")
                    if source == ChinaDataSource.TUSHARE:
                        result = self._get_tushare_fundamentals(symbol)
                    elif source == ChinaDataSource.AKSHARE:
                        result = self._get_akshare_fundamentals(symbol)
                    else:
                        continue
                    if result and "❌" not in result:
                        logger.info(f"✅ [数据来源: 备用数据源] 降级成功获取基本面: {source.value}")
                        return result
                except Exception as e:
                    logger.error(f"❌ 备用数据源{source.value}异常: {e}")
                    continue
        logger.warning(f"⚠️ [数据来源: 生成分析] 所有数据源失败，生成基本分析: {symbol}")
        return self._generate_fundamentals_analysis(symbol)

    def _get_mongodb_news(self, symbol: str, hours_back: int, limit: int) -> List[Dict[str, Any]]:
        try:
            from tradingagents.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
            adapter = get_mongodb_cache_adapter()
            news_data = adapter.get_news_data(symbol, hours_back=hours_back, limit=limit)
            if news_data and len(news_data) > 0:
                logger.info(f"✅ [数据来源: MongoDB-新闻] 成功获取: {symbol or '市场新闻'} ({len(news_data)}条)")
                return news_data
            else:
                logger.warning(f"⚠️ [数据来源: MongoDB] 未找到新闻: {symbol or '市场新闻'}，降级到其他数据源")
                return self._try_fallback_news(symbol, hours_back, limit)
        except Exception as e:
            logger.error(f"❌ [数据来源: MongoDB] 获取新闻失败: {e}")
            return self._try_fallback_news(symbol, hours_back, limit)

    def _get_tushare_news(self, symbol: str, hours_back: int, limit: int) -> List[Dict[str, Any]]:
        logger.warning(f"⚠️ [数据来源: Tushare] Tushare新闻功能暂时不可用")
        return []

    def _get_akshare_news(self, symbol: str, hours_back: int, limit: int) -> List[Dict[str, Any]]:
        logger.warning(f"⚠️ [数据来源: AKShare] AKShare新闻功能暂时不可用")
        return []

    def _try_fallback_news(self, symbol: str, hours_back: int, limit: int) -> List[Dict[str, Any]]:
        logger.error(f"🔄 {self.current_source.value}失败，尝试备用数据源获取新闻...")
        fallback_order = self._get_data_source_priority_order(symbol)
        for source in fallback_order:
            if source != self.current_source and source in self.available_sources:
                try:
                    logger.info(f"🔄 尝试备用数据源获取新闻: {source.value}")
                    if source == ChinaDataSource.TUSHARE:
                        result = self._get_tushare_news(symbol, hours_back, limit)
                    elif source == ChinaDataSource.AKSHARE:
                        result = self._get_akshare_news(symbol, hours_back, limit)
                    else:
                        continue
                    if result and len(result) > 0:
                        logger.info(f"✅ [数据来源: 备用数据源] 降级成功获取新闻: {source.value}")
                        return result
                except Exception as e:
                    logger.error(f"❌ 备用数据源{source.value}异常: {e}")
                    continue
        logger.warning(f"⚠️ [数据来源: 所有数据源失败] 无法获取新闻: {symbol or '市场新闻'}")
        return []


# 全局数据源管理器实例
_data_source_manager = None

def get_data_source_manager() -> DataSourceManager:
    """获取全局数据源管理器实例"""
    global _data_source_manager
    if _data_source_manager is None:
        _data_source_manager = DataSourceManager()
    return _data_source_manager


def get_china_stock_data_unified(symbol: str, start_date: str, end_date: str) -> str:
    """
    统一的中国股票数据获取接口
    自动使用配置的数据源，支持备用数据源
    """
    logger.info(f"🔍 [股票代码追踪] data_source_manager.get_china_stock_data_unified 接收到股票代码: {symbol}")
    manager = get_data_source_manager()
    return manager.get_stock_data(symbol, start_date, end_date)


def get_china_stock_info_unified(symbol: str) -> Dict:
    """统一的中国股票信息获取接口"""
    manager = get_data_source_manager()
    return manager.get_stock_info(symbol)


# ==================== 兼容性接口 ====================
def get_stock_data_service() -> DataSourceManager:
    return get_data_source_manager()


# ==================== 美股数据源管理器 ====================
class USDataSourceManager:
    def __init__(self):
        self.use_mongodb_cache = self._check_mongodb_enabled()
        self.available_sources = self._check_available_sources()
        self.default_source = self._get_default_source()
        self.current_source = self.default_source
        logger.info(f"📊 美股数据源管理器初始化完成")
        logger.info(f"   MongoDB缓存: {'✅ 已启用' if self.use_mongodb_cache else '❌ 未启用'}")
        logger.info(f"   默认数据源: {self.default_source.value}")
        logger.info(f"   可用数据源: {[s.value for s in self.available_sources]}")

    def _check_mongodb_enabled(self) -> bool:
        from tradingagents.config.runtime_settings import use_app_cache_enabled
        return use_app_cache_enabled()

    def _get_data_source_priority_order(self, symbol: Optional[str] = None) -> List[USDataSource]:
        try:
            from app.core.database import get_mongo_db_sync
            db = get_mongo_db_sync()
            groupings = list(db.datasource_groupings.find({"market_category_id": "us_stocks", "enabled": True}).sort("priority", -1))
            if groupings:
                source_mapping = {
                    'yfinance': USDataSource.YFINANCE,
                    'yahoo_finance': USDataSource.YFINANCE,
                    'alpha_vantage': USDataSource.ALPHA_VANTAGE,
                    'finnhub': USDataSource.FINNHUB,
                }
                result = []
                for g in groupings:
                    ds_name = g.get('data_source_name', '').lower()
                    if ds_name in source_mapping:
                        source = source_mapping[ds_name]
                        if source != USDataSource.MONGODB and source in self.available_sources:
                            result.append(source)
                if result:
                    logger.info(f"✅ [美股数据源优先级] 从数据库读取: {[s.value for s in result]}")
                    return result
        except Exception as e:
            logger.warning(f"⚠️ [美股数据源优先级] 从数据库读取失败: {e}")
        default_order = [USDataSource.YFINANCE, USDataSource.ALPHA_VANTAGE, USDataSource.FINNHUB]
        return [s for s in default_order if s in self.available_sources]

    def _get_default_source(self) -> USDataSource:
        if self.use_mongodb_cache:
            return USDataSource.MONGODB
        env_source = os.getenv('DEFAULT_US_DATA_SOURCE', DataSourceCode.YFINANCE).lower()
        source_mapping = {
            DataSourceCode.YFINANCE: USDataSource.YFINANCE,
            DataSourceCode.ALPHA_VANTAGE: USDataSource.ALPHA_VANTAGE,
            DataSourceCode.FINNHUB: USDataSource.FINNHUB,
        }
        return source_mapping.get(env_source, USDataSource.YFINANCE)

    def _check_available_sources(self) -> List[USDataSource]:
        available = []
        if self.use_mongodb_cache:
            available.append(USDataSource.MONGODB)
            logger.info("✅ MongoDB缓存数据源可用")
        enabled_sources_in_db = self._get_enabled_sources_from_db()
        datasource_configs = self._get_datasource_configs_from_db()

        if 'yfinance' in enabled_sources_in_db:
            try:
                import yfinance
                available.append(USDataSource.YFINANCE)
                logger.info("✅ yfinance数据源可用且已启用")
            except ImportError:
                logger.warning("⚠️ yfinance数据源不可用: 未安装 yfinance 库")

        if 'alpha_vantage' in enabled_sources_in_db:
            api_key = datasource_configs.get('alpha_vantage', {}).get('api_key') or os.getenv("ALPHA_VANTAGE_API_KEY")
            if api_key:
                available.append(USDataSource.ALPHA_VANTAGE)
                logger.info(f"✅ Alpha Vantage数据源可用且已启用")

        if 'finnhub' in enabled_sources_in_db:
            api_key = datasource_configs.get('finnhub', {}).get('api_key') or os.getenv("FINNHUB_API_KEY")
            if api_key:
                available.append(USDataSource.FINNHUB)
                logger.info(f"✅ Finnhub数据源可用且已启用")
        return available

    def _get_enabled_sources_from_db(self) -> List[str]:
        try:
            from app.core.database import get_mongo_db_sync
            db = get_mongo_db_sync()
            groupings = list(db.datasource_groupings.find({"market_category_id": "us_stocks", "enabled": True}))
            name_mapping = {'alpha vantage': 'alpha_vantage', 'yahoo finance': 'yfinance', 'finnhub': 'finnhub'}
            return [name_mapping.get(g.get('data_source_name', '').lower(), g.get('data_source_name', '').lower()) for g in groupings]
        except Exception as e:
            logger.warning(f"⚠️ 从数据库读取启用的数据源失败: {e}")
            return ['yfinance', 'alpha_vantage', 'finnhub']

    def _get_datasource_configs_from_db(self) -> dict:
        try:
            from app.core.database import get_mongo_db_sync
            db = get_mongo_db_sync()
            config = db.system_configs.find_one({"is_active": True})
            if not config:
                return {}
            datasource_configs = config.get('data_source_configs', [])
            result = {}
            for ds_config in datasource_configs:
                name = ds_config.get('name', '').lower()
                result[name] = {'api_key': ds_config.get('api_key', ''), 'api_secret': ds_config.get('api_secret', '')}
            return result
        except Exception as e:
            logger.warning(f"⚠️ 从数据库读取数据源配置失败: {e}")
            return {}

    def get_current_source(self) -> USDataSource:
        return self.current_source

    def set_current_source(self, source: USDataSource) -> bool:
        if source in self.available_sources:
            self.current_source = source
            logger.info(f"✅ 美股数据源已切换到: {source.value}")
            return True
        else:
            logger.error(f"❌ 美股数据源不可用: {source.value}")
            return False


_us_data_source_manager = None

def get_us_data_source_manager() -> USDataSourceManager:
    global _us_data_source_manager
    if _us_data_source_manager is None:
        _us_data_source_manager = USDataSourceManager()
    return _us_data_source_manager