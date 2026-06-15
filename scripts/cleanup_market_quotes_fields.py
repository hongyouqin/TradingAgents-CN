"""
清理 market_quotes 集合中的冗余字段脚本

执行的操作：
1. 移除冗余字段（如 price, change_percent, high_price, low_price 等 AKShare 特有字段）
2. 确保每条记录都有统一的核心字段集
3. 打印清理统计信息

使用方式：
    python scripts/cleanup_market_quotes_fields.py
"""

from pymongo import MongoClient, UpdateOne
from datetime import datetime
import sys
import os

# 添加项目根路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 从环境变量或直接配置获取连接
MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb://admin:tradingagents123@47.101.161.124:27017/tradingagents?authSource=admin"
)
MONGO_DB = os.getenv("MONGO_DB", "tradingagents")

# 需要清理的冗余字段（这些字段在规范化后不再需要）
REDUNDANT_FIELDS = [
    # AKShare 特有冗余字段（有标准字段替代）
    "price",           # → close
    "change_percent",  # → pct_chg
    "high_price",      # → high
    "low_price",       # → low
    "open_price",      # → open
    "current_price",   # → close
    "ts_code",         # → full_symbol
    "num",             # 无业务含义

    # 属于 stock_basic_info 的字段（不应存在于 market_quotes）
    "circ_mv",
    "total_mv",
    "pb",
    "pe",
    "pe_ttm",
]


def cleanup():
    """执行清理"""
    print(f" 连接 MongoDB: {MONGO_URI}")
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB]
    coll = db["market_quotes"]

    total = coll.estimated_document_count()
    print(f" market_quotes 总记录数: {total}")

    stats = {}

    # 1. 统计每个冗余字段的分布
    print("\n 扫描冗余字段分布...")
    for field in REDUNDANT_FIELDS:
        count = coll.count_documents({field: {"$exists": True}})
        if count > 0:
            stats[field] = count
            print(f"    {field}: {count} 条记录 ({count/total*100:.1f}%)")

    if not stats:
        print("   没有发现冗余字段，无需清理")
        client.close()
        return

    # 2. 执行清理：$unset 所有冗余字段
    print(f"\n 正在清理 {len(stats)} 个冗余字段...")

    # 构建 $unset 命令
    unset_spec = {field: "" for field in stats.keys()}

    # 使用 update_many 一次性清理
    result = coll.update_many(
        {},  # 匹配所有文档
        {"$unset": unset_spec}
    )

    print(f"\n 清理完成:")
    print(f"   - 匹配文档数: {result.matched_count}")
    print(f"   - 修改文档数: {result.modified_count}")
    print(f"   - 已移除字段: {', '.join(stats.keys())}")

    # 3. 验证清理结果
    print("\n 验证清理结果...")
    remaining = 0
    for field in stats.keys():
        count = coll.count_documents({field: {"$exists": True}})
        if count > 0:
            print(f"    {field}: 仍有 {count} 条记录未清理")
            remaining += count

    if remaining == 0:
        print("   所有冗余字段已成功清除")
    else:
        print(f"    仍有 {remaining} 条记录残留")

    # 4. 打印最终字段分布（抽样一条记录）
    sample = coll.find_one({}, {"_id": 0})
    if sample:
        print(f"\n 清理后示例记录字段 ({len(sample)} 个):")
        for k, v in sorted(sample.items()):
            print(f"  {k}: {type(v).__name__} = {str(v)[:60]}")

    client.close()
    print("\n 清理脚本执行完毕")


if __name__ == "__main__":
    cleanup()
