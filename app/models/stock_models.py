"""
股票数据模型 - 基于现有集合扩展
采用方案B: 在现有集合基础上扩展字段，保持向后兼容
"""
from datetime import datetime, date
from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field
from bson import ObjectId


def to_str_id(v: Any) -> str:
    """ObjectId转字符串工具函数"""
    try:
        if isinstance(v, ObjectId):
            return str(v)
        return str(v)
    except Exception:
        return ""


# 枚举类型定义
MarketType = Literal["CN", "HK", "US"]  # 市场类型
ExchangeType = Literal["SZSE", "SSE", "SEHK", "NYSE", "NASDAQ"]  # 交易所
StockStatus = Literal["L", "D", "P"]  # 上市状态: L-上市 D-退市 P-暂停
CurrencyType = Literal["CNY", "HKD", "USD"]  # 货币类型


class MarketInfo(BaseModel):
    """市场信息结构 - 新增字段"""
    market: MarketType = Field(..., description="市场标识")
    exchange: ExchangeType = Field(..., description="交易所代码")
    exchange_name: str = Field(..., description="交易所名称")
    currency: CurrencyType = Field(..., description="交易货币")
    timezone: str = Field(..., description="时区")
    trading_hours: Optional[Dict[str, Any]] = Field(None, description="交易时间")


class TechnicalIndicators(BaseModel):
    """技术指标结构 - 分类扩展设计"""
    # 趋势指标
    trend: Optional[Dict[str, float]] = Field(None, description="趋势指标")
    # 震荡指标  
    oscillator: Optional[Dict[str, float]] = Field(None, description="震荡指标")
    # 通道指标
    channel: Optional[Dict[str, float]] = Field(None, description="通道指标")
    # 成交量指标
    volume: Optional[Dict[str, float]] = Field(None, description="成交量指标")
    # 波动率指标
    volatility: Optional[Dict[str, float]] = Field(None, description="波动率指标")
    # 自定义指标
    custom: Optional[Dict[str, Any]] = Field(None, description="自定义指标")


class StockBasicInfoExtended(BaseModel):
    """
    股票基础信息扩展模型 - 基于现有 stock_basic_info 集合
    统一使用 symbol 作为主要股票代码字段
    """
    # === 标准化字段 (主要字段) ===
    symbol: str = Field(..., description="6位股票代码", pattern=r"^\d{6}$")
    full_symbol: str = Field(..., description="完整标准化代码(如 000001.SZ)")
    name: str = Field(..., description="股票名称")

    # === 兼容字段 (保持向后兼容) ===
    code: Optional[str] = Field(None, description="6位股票代码(已废弃,使用symbol)")

    # === 基础信息字段 ===
    area: Optional[str] = Field(None, description="所在地区")
    industry: Optional[str] = Field(None, description="行业")
    market: Optional[str] = Field(None, description="交易所名称")
    list_date: Optional[str] = Field(None, description="上市日期")
    sse: Optional[str] = Field(None, description="板块")
    sec: Optional[str] = Field(None, description="所属板块")
    source: Optional[str] = Field(None, description="数据来源")
    updated_at: Optional[datetime] = Field(None, description="更新时间")

    # 市值字段
    total_mv: Optional[float] = Field(None, description="总市值(亿元)")
    circ_mv: Optional[float] = Field(None, description="流通市值(亿元)")

    # 财务指标
    pe: Optional[float] = Field(None, description="市盈率")
    pb: Optional[float] = Field(None, description="市净率")
    pe_ttm: Optional[float] = Field(None, description="滚动市盈率")
    pb_mrq: Optional[float] = Field(None, description="最新市净率")
    roe: Optional[float] = Field(None, description="净资产收益率")

    # 交易指标
    turnover_rate: Optional[float] = Field(None, description="换手率%")
    volume_ratio: Optional[float] = Field(None, description="量比")

    # === 扩展字段 ===
    name_en: Optional[str] = Field(None, description="英文名称")
    
    # 新增市场信息
    market_info: Optional[MarketInfo] = Field(None, description="市场信息")
    
    # 新增标准化字段
    board: Optional[str] = Field(None, description="板块标准化")
    industry_code: Optional[str] = Field(None, description="行业代码")
    sector: Optional[str] = Field(None, description="所属板块标准化（GICS行业）")
    delist_date: Optional[str] = Field(None, description="退市日期")
    status: Optional[StockStatus] = Field(None, description="上市状态")
    is_hs: Optional[bool] = Field(None, description="是否沪深港通标的")

    # 新增股本信息
    total_shares: Optional[float] = Field(None, description="总股本")
    float_shares: Optional[float] = Field(None, description="流通股本")

    # 港股特有字段
    lot_size: Optional[int] = Field(None, description="每手股数（港股特有）")

    # 货币字段
    currency: Optional[CurrencyType] = Field(None, description="交易货币")
    
    # 版本控制
    data_version: Optional[int] = Field(None, description="数据版本")
    
    class Config:
        # 允许额外字段，保持向后兼容
        extra = "allow"
        # 示例数据
        json_schema_extra = {
            "example": {
                # 标准化字段
                "symbol": "000001",
                "full_symbol": "000001.SZ",
                "name": "平安银行",

                # 基础信息
                "area": "深圳",
                "industry": "银行",
                "market": "深圳证券交易所",
                "sse": "主板",
                "total_mv": 2500.0,
                "pe": 5.2,
                "pb": 0.8,

                # 扩展字段
                "market_info": {
                    "market": "CN",
                    "exchange": "SZSE",
                    "exchange_name": "深圳证券交易所",
                    "currency": "CNY",
                    "timezone": "Asia/Shanghai"
                },
                "status": "L",
                "data_version": 1
            }
        }


class MarketQuotesExtended(BaseModel):
    """
    实时行情扩展模型 - 基于现有 market_quotes 集合
    统一使用 symbol 作为主要股票代码字段
    """
    # === 标准化字段 (主要字段) ===
    symbol: str = Field(..., description="6位股票代码", pattern=r"^\d{6}$")
    full_symbol: Optional[str] = Field(None, description="完整标准化代码")
    market: Optional[MarketType] = Field(None, description="市场标识")

    # === 兼容字段 (保持向后兼容) ===
    code: Optional[str] = Field(None, description="6位股票代码(已废弃,使用symbol)")

    # === 行情字段 ===
    close: Optional[float] = Field(None, description="收盘价")
    pct_chg: Optional[float] = Field(None, description="涨跌幅%")
    amount: Optional[float] = Field(None, description="成交额")
    open: Optional[float] = Field(None, description="开盘价")
    high: Optional[float] = Field(None, description="最高价")
    low: Optional[float] = Field(None, description="最低价")
    pre_close: Optional[float] = Field(None, description="前收盘价")
    trade_date: Optional[str] = Field(None, description="交易日期")
    updated_at: Optional[datetime] = Field(None, description="更新时间")
    
    # 新增行情字段
    current_price: Optional[float] = Field(None, description="当前价格(与close相同)")
    change: Optional[float] = Field(None, description="涨跌额")
    volume: Optional[float] = Field(None, description="成交量")
    turnover_rate: Optional[float] = Field(None, description="换手率")
    volume_ratio: Optional[float] = Field(None, description="量比")
    
    # 五档行情
    bid_prices: Optional[List[float]] = Field(None, description="买1-5价")
    bid_volumes: Optional[List[float]] = Field(None, description="买1-5量")
    ask_prices: Optional[List[float]] = Field(None, description="卖1-5价")
    ask_volumes: Optional[List[float]] = Field(None, description="卖1-5量")
    
    # 时间戳
    timestamp: Optional[datetime] = Field(None, description="行情时间戳")
    
    # 数据源和版本
    data_source: Optional[str] = Field(None, description="数据来源")
    data_version: Optional[int] = Field(None, description="数据版本")
    
    class Config:
        extra = "allow"
        json_schema_extra = {
            "example": {
                # 标准化字段
                "symbol": "000001",
                "full_symbol": "000001.SZ",
                "market": "CN",

                # 行情字段
                "close": 12.65,
                "pct_chg": 1.61,
                "amount": 1580000000,
                "open": 12.50,
                "high": 12.80,
                "low": 12.30,
                "trade_date": "2024-01-15",

                # 扩展字段
                "current_price": 12.65,
                "change": 0.20,
                "volume": 125000000
            }
        }


# 数据库操作相关的响应模型
class StockBasicInfoResponse(BaseModel):
    """股票基础信息API响应模型"""
    success: bool = True
    data: Optional[StockBasicInfoExtended] = None
    message: str = ""


class MarketQuotesResponse(BaseModel):
    """实时行情API响应模型"""
    success: bool = True
    data: Optional[MarketQuotesExtended] = None
    message: str = ""


class StockListResponse(BaseModel):
    """股票列表API响应模型"""
    success: bool = True
    data: Optional[List[StockBasicInfoExtended]] = None
    total: int = 0
    page: int = 1
    page_size: int = 20
    message: str = ""


# ===================== 预约披露日模块 =====================


class DisclosureCalendarItem(BaseModel):
    """预约披露日数据模型 - 对应 MongoDB disclosure_calendar 集合"""
    stock_code: str = Field(..., description="6位股票代码")
    stock_name: str = Field(..., description="股票简称")
    first_schedule: Optional[datetime] = Field(None, description="首次预约时间")
    first_change: Optional[datetime] = Field(None, description="一次变更日期")
    second_change: Optional[datetime] = Field(None, description="二次变更日期")
    third_change: Optional[datetime] = Field(None, description="三次变更日期")
    actual_disclosure: Optional[datetime] = Field(None, description="实际披露时间")
    latest_date: datetime = Field(..., description="最新预约披露日（取优先顺序：三次变更 > 二次变更 > 一次变更 > 首次预约）")
    data_date: str = Field(..., description="数据对应的财报截止日期（如 20260630）")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="数据更新时间")

    class Config:
        extra = "allow"
        json_schema_extra = {
            "example": {
                "stock_code": "002107",
                "stock_name": "沃华医药",
                "first_schedule": "2026-07-16T00:00:00",
                "latest_date": "2026-07-16T00:00:00",
                "data_date": "20260630",
            }
        }


class DisclosureCalendarResponse(BaseModel):
    """单条披露日API响应"""
    success: bool = True
    data: Optional[DisclosureCalendarItem] = None
    message: str = ""


class DisclosureCalendarListResponse(BaseModel):
    """披露日列表API响应"""
    success: bool = True
    data: Optional[List[DisclosureCalendarItem]] = None
    total: int = 0
    page: int = 1
    page_size: int = 20
    message: str = ""


# ===================== 股票热度（雪球）模块 =====================


class StockHotXueqiuItem(BaseModel):
    """雪球股票热度数据模型 - 对应 MongoDB stock_hot_xq 集合"""
    stock_code: str = Field(..., description="股票代码（如 SH600519）")
    stock_name: str = Field(..., description="股票名称")
    followers: int = Field(0, description="关注人数")
    current_price: Optional[float] = Field(None, description="现价")
    category: str = Field(..., description="分类: 最热门 / 本周新增")
    rank: int = Field(0, description="排名")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="更新时间")

    class Config:
        extra = "allow"
        json_schema_extra = {
            "example": {
                "stock_code": "SH600519",
                "stock_name": "贵州茅台",
                "followers": 3671061,
                "current_price": 1194.45,
                "category": "最热门",
                "rank": 1,
            }
        }


class StockHotXueqiuResponse(BaseModel):
    """雪球热度列表API响应"""
    success: bool = True
    data: Optional[List[StockHotXueqiuItem]] = None
    total: int = 0
    message: str = ""


class StockHotXueqiuByCategoryResponse(BaseModel):
    """雪球热度按分类查询API响应"""
    success: bool = True
    data: Optional[Dict[str, List[StockHotXueqiuItem]]] = None
    message: str = ""


# ===================== 交易排行榜（雪球）模块 =====================


class StockHotDealXueqiuItem(BaseModel):
    """雪球交易排行数据模型 - 对应 MongoDB stock_hot_deal_xq 集合"""
    stock_code: str = Field(..., description="股票代码（如 SH600519）")
    stock_name: str = Field(..., description="股票名称")
    deal_attention: int = Field(0, description="交易关注度")
    current_price: Optional[float] = Field(None, description="最新价")
    rank: int = Field(0, description="排名")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="更新时间")

    class Config:
        extra = "allow"
        json_schema_extra = {
            "example": {
                "stock_code": "SH600519",
                "stock_name": "贵州茅台",
                "deal_attention": 473,
                "current_price": 1194.45,
                "rank": 1,
            }
        }


class StockHotDealXueqiuResponse(BaseModel):
    """雪球交易排行API响应"""
    success: bool = True
    data: Optional[List[StockHotDealXueqiuItem]] = None
    total: int = 0
    message: str = ""


# ===================== 人气榜（东方财富）模块 =====================


class StockHotRankEMItem(BaseModel):
    """东方财富人气榜数据模型 - 对应 MongoDB stock_hot_rank_em 集合"""
    stock_code: str = Field(..., description="股票代码（如 SZ301308）")
    stock_name: str = Field(..., description="股票名称")
    current_price: Optional[float] = Field(None, description="最新价")
    change_amount: Optional[float] = Field(None, description="涨跌额")
    change_percent: Optional[float] = Field(None, description="涨跌幅")
    rank: int = Field(0, description="当前排名")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="更新时间")

    class Config:
        extra = "allow"
        json_schema_extra = {
            "example": {
                "stock_code": "SZ301308",
                "stock_name": "江波龙",
                "current_price": 618.02,
                "change_amount": 19.41,
                "change_percent": 3.14,
                "rank": 1,
            }
        }


class StockHotRankEMResponse(BaseModel):
    """东方财富人气榜API响应"""
    success: bool = True
    data: Optional[List[StockHotRankEMItem]] = None
    total: int = 0
    message: str = ""
