"""
行情数据字段规范化工具
统一 market_quotes 集合的写入字段，去除数据源特有字段

设计目标：
- 不管数据源是 tushare/akshare/baostock，写入 market_quotes 的字段集保持一致
- 多余的数据源特有字段在写入前被映射或丢弃
- 向后兼容：保留现有的 code/symbol 双重索引

标准字段集 (CORE_FIELDS)：
  code, symbol, close, pct_chg, amount, volume, open, high, low, pre_close,
  trade_date, updated_at

扩展字段 (EXTENDED_FIELDS)：
  data_source, full_symbol, change, turnover_rate, volume_ratio,
  name, market_info, last_sync, sync_status
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# ========== 定义标准字段集 ==========

CORE_FIELDS = {
    "code",         # 6位股票代码
    "symbol",       # 同 code
    "close",        # 收盘价/最新价
    "pct_chg",      # 涨跌幅(%)
    "amount",       # 成交额(元)
    "volume",       # 成交量(股)
    "open",         # 开盘价
    "high",         # 最高价
    "low",          # 最低价
    "pre_close",    # 昨收价
    "trade_date",   # 交易日期(YYYYMMDD)
    "updated_at",   # 更新时间
}

EXTENDED_FIELDS = {
    "data_source",     # 数据来源
    "full_symbol",     # 完整代码(如 600160.SS)
    "change",          # 涨跌额
    "turnover_rate",   # 换手率
    "volume_ratio",    # 量比
    "name",            # 股票名称
    "market_info",     # 市场信息JSON
    "last_sync",       # 最后同步时间
    "sync_status",     # 同步状态
}

ALL_STANDARD_FIELDS = CORE_FIELDS | EXTENDED_FIELDS

# ========== 字段映射表 ==========
# 数据源特定字段 -> 标准字段
# 格式: {源字段: 目标字段}
# 如果目标字段为空字符串，表示直接丢弃该字段
FIELD_MAPPING: Dict[str, str] = {
    # AKShare 特有字段
    "price": "close",              # akshare 的 price -> close
    "change_percent": "pct_chg",   # akshare 的 change_percent -> pct_chg
    "high_price": "high",          # akshare 的 high_price -> high
    "low_price": "low",            # akshare 的 low_price -> low
    "open_price": "open",          # akshare 的 open_price -> open
    "current_price": "close",      # akshare 的 current_price -> close
    "ts_code": "full_symbol",      # akshare 的 ts_code -> full_symbol

    # 待丢弃的冗余字段（映射到空字符串表示丢弃）
    "num": "",                     # AKShare 内部序号，无业务含义
    "circ_mv": "",                 # 流通市值(属于 stock_basic_info)
    "total_mv": "",                # 总市值(属于 stock_basic_info)
    "pb": "",                      # 市净率(属于 stock_basic_info)
    "pe": "",                      # 市盈率(属于 stock_basic_info)
    "pe_ttm": "",                  # 滚动市盈率(属于 stock_basic_info)
}

# 需要从数据库中清理的冗余字段（清洗脚本使用）
REDUNDANT_FIELDS_TO_CLEAN = [
    "price", "change_percent", "high_price", "low_price", "open_price",
    "current_price", "ts_code", "num", "circ_mv", "total_mv",
    "pb", "pe", "pe_ttm",
]


def normalize_quotes_data(
    raw_data: Dict[str, Any],
    data_source: Optional[str] = None,
) -> Dict[str, Any]:
    """
    规范化行情数据：将原始数据按标准字段映射，丢弃多余字段

    Args:
        raw_data: 原始数据字典（来自数据源 provider）
        data_source: 数据源标识（如 'tushare', 'akshare'），None 时不覆盖

    Returns:
        规范化后的数据字典（只包含标准字段）
    """
    normalized: Dict[str, Any] = {}

    # 1. 先复制所有标准字段中已有的值
    for field in ALL_STANDARD_FIELDS:
        if field in raw_data and raw_data[field] is not None:
            normalized[field] = raw_data[field]

    # 2. 执行字段映射（将非标准字段映射到标准字段名）
    for src_field, dst_field in FIELD_MAPPING.items():
        if src_field in raw_data and raw_data[src_field] is not None:
            if dst_field:  # 有目标字段，执行映射
                # 只在目标字段尚未设置时才映射
                if dst_field not in normalized:
                    normalized[dst_field] = raw_data[src_field]
            # dst_field == "" 表示丢弃，什么都不做

    # 3. 确保必填字段都存在（code 和 symbol）
    code = raw_data.get("code") or raw_data.get("symbol") or ""
    if code and len(str(code).strip()) > 0:
        code6 = str(code).strip()
        # 提取数字部分
        import re
        digits = re.sub(r"\D", "", code6)
        if digits:
            code6 = digits.zfill(6)
        normalized.setdefault("code", code6)
        normalized.setdefault("symbol", code6)
    elif "code" in normalized or "symbol" in normalized:
        c = normalized.get("code") or normalized.get("symbol", "")
        normalized.setdefault("code", c)
        normalized.setdefault("symbol", c)

    # 4. 设置更新时间
    if "updated_at" not in normalized:
        normalized["updated_at"] = datetime.utcnow()

    # 5. 设置数据源
    if data_source and "data_source" not in raw_data:
        normalized["data_source"] = data_source

    return normalized


def get_allowed_fields_set() -> set:
    """
    获取允许写入 market_quotes 的所有字段

    Returns:
        允许的字段集合
    """
    return ALL_STANDARD_FIELDS
