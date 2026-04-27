from datetime import datetime, timedelta
from pymongo import MongoClient
from app.core.config import settings

# 日志
try:
    from tradingagents.utils.logging_manager import get_logger
    logger = get_logger('user_stat_service')
except ImportError:
    import logging
    logger = logging.getLogger('user_stat_service')


# ==============================================
# 📊 用户统计服务
# ==============================================
class UserStatService:
    def __init__(self):
        self.client = MongoClient(settings.MONGO_URI)
        self.db = self.client[settings.MONGO_DB]

        # 业务表
        self.users = self.db["users"]                  # 用户表
        self.orders = self.db["recharge_orders"]       # 充值订单
        self.power_records = self.db["power_transactions"]  # 算力消费（报告生成）
        self.stats = self.db["user_stats"]             # 统计表（每天一条）

    def close(self):
        if hasattr(self, "client"):
            self.client.close()

    def __del__(self):
        self.close()

    # --------------------------------------------------------------------------
    # 1. 用户基础统计
    # --------------------------------------------------------------------------
    def get_total_users(self) -> int:
        """总用户数"""
        return self.users.count_documents({})

    def get_daily_register(self, date=None) -> int:
        """单日注册数"""
        if not date:
            date = datetime.utcnow()
        s = datetime(date.year, date.month, date.day, 0, 0, 0)
        e = s + timedelta(days=1)
        return self.users.count_documents({"created_at": {"$gte": s, "$lt": e}})

    def get_dau(self, date=None) -> int:
        """日活 DAU"""
        if not date:
            date = datetime.utcnow()
        s = datetime(date.year, date.month, date.day, 0, 0, 0)
        e = s + timedelta(days=1)
        return self.users.count_documents({"last_login": {"$gte": s, "$lt": e}})

    def get_mau(self, date=None) -> int:
        """月活 MAU"""
        if not date:
            date = datetime.utcnow()
        s = datetime(date.year, date.month, 1, 0, 0, 0)
        if date.month == 12:
            e = datetime(date.year + 1, 1, 1)
        else:
            e = datetime(date.year, date.month + 1, 1)
        return self.users.count_documents({"last_login": {"$gte": s, "$lt": e}})

    def get_7days_inactive(self) -> int:
        """7天未登录用户"""
        t = datetime.utcnow() - timedelta(days=7)
        return self.users.count_documents({
            "$or": [
                {"last_login": {"$lt": t}},
                {"last_login": None, "created_at": {"$lt": t}}
            ]
        })

    # --------------------------------------------------------------------------
    # 2. 充值统计（来自你的 order_service）
    # --------------------------------------------------------------------------
    def get_daily_recharge(self, date=None) -> float:
        """今日充值总额（已支付）"""
        if not date:
            date = datetime.utcnow()
        s = datetime(date.year, date.month, date.day, 0, 0, 0)
        e = s + timedelta(days=1)

        pipeline = [
            {"$match": {
                "status": "PAID",
                "paid_at": {"$gte": s, "$lt": e}
            }},
            {"$group": {"_id": None, "total": {"$sum": "$price"}}}
        ]
        res = list(self.orders.aggregate(pipeline))
        return round(res[0]["total"], 2) if res else 0.0

    def get_monthly_recharge(self, date=None) -> float:
        """本月充值总额"""
        if not date:
            date = datetime.utcnow()
        s = datetime(date.year, date.month, 1, 0, 0, 0)
        e = datetime(date.year + 1, 1, 1) if date.month == 12 else \
            datetime(date.year, date.month + 1, 1)

        pipeline = [
            {"$match": {
                "status": "PAID",
                "paid_at": {"$gte": s, "$lt": e}
            }},
            {"$group": {"_id": None, "total": {"$sum": "$price"}}}
        ]
        res = list(self.orders.aggregate(pipeline))
        return round(res[0]["total"], 2) if res else 0.0

    # --------------------------------------------------------------------------
    # 3. 报告/分析生成次数（来自你的 power_transactions）
    # 完全贴合：submit_single_analysis 接口的算力消费
    # --------------------------------------------------------------------------
    def get_daily_report_count(self, date=None) -> int:
        """今日生成报告次数 = 今日算力消费次数"""
        if not date:
            date = datetime.utcnow()
        s = datetime(date.year, date.month, date.day, 0, 0, 0)
        e = s + timedelta(days=1)

        return self.power_records.count_documents({
            "transaction_type": "CONSUME",
            "status": "CONFIRMED",
            "created_at": {"$gte": s, "$lt": e}
        })

    def get_monthly_report_count(self, date=None) -> int:
        """本月报告生成次数"""
        if not date:
            date = datetime.utcnow()
        s = datetime(date.year, date.month, 1, 0, 0, 0)
        e = datetime(date.year + 1, 1, 1) if date.month == 12 else \
            datetime(date.year, date.month + 1, 1)

        return self.power_records.count_documents({
            "transaction_type": "CONSUME",
            "status": "CONFIRMED",
            "created_at": {"$gte": s, "$lt": e}
        })

    # --------------------------------------------------------------------------
    # 4. 生成并保存每日统计
    # --------------------------------------------------------------------------
    def generate_daily_stats(self) -> dict:
        now = datetime.utcnow()
        date_str = now.strftime("%Y-%m-%d")

        data = {
            "date": date_str,
            "generated_at": now,

            # 用户
            "total_users": self.get_total_users(),
            "daily_register": self.get_daily_register(),
            "dau": self.get_dau(),
            "mau": self.get_mau(),
            "7d_inactive": self.get_7days_inactive(),

            # 充值
            "daily_recharge": self.get_daily_recharge(),
            "monthly_recharge": self.get_monthly_recharge(),

            # 报告/分析次数
            "daily_reports": self.get_daily_report_count(),
            "monthly_reports": self.get_monthly_report_count()
        }

        # 每天一条，覆盖更新
        self.stats.update_one({"date": date_str}, {"$set": data}, upsert=True)
        logger.info(f"✅ 每日统计已保存：{date_str}")
        return data

    # --------------------------------------------------------------------------
    # 5. 获取今日/历史统计
    # --------------------------------------------------------------------------
    def get_today(self):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        return self.stats.find_one({"date": today}, {"_id": 0})

    def get_history(self, days=30):
        return list(self.stats.find({}, {"_id": 0}).sort("date", -1).limit(days))


# 全局实例
user_stat_service = UserStatService()