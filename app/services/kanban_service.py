import os
import time
import akshare as ak
import pandas as pd
import redis
import json
import logging
from datetime import timedelta
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# ===================== 配置项 =====================

CACHE_HOT = 300
CACHE_STATIC = 86400
REQ_DELAY = 0.4
# ===================================================

class KanbanService:
    def __init__(self):
        
                  # 从环境变量获取Redis配置
        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_port = int(os.getenv('REDIS_PORT', 6379))
        redis_password = os.getenv('REDIS_PASSWORD', None)
        redis_db = int(os.getenv('REDIS_DB', 0))
        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            db=redis_db,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5
        )

    def df_to_redis(self, df: pd.DataFrame, key: str, expire: int):
        try:
            json_data = df.to_json(orient="split", force_ascii=False)
            self.redis_client.setex(key, timedelta(seconds=expire), json_data)
        except Exception as e:
            logger.error(f"Redis 存储失败 {key}: {e}")

    def df_from_redis(self, key: str) -> pd.DataFrame:
        try:
            data = self.redis_client.get(key)
            if data:
                return pd.read_json(data, orient="split")
        except Exception as e:
            logger.error(f"Redis 读取失败 {key}: {e}")
        return pd.DataFrame()

    def safe_ak(self, func, key: str, static: bool = False) -> pd.DataFrame:
        expire = CACHE_STATIC if static else CACHE_HOT
        df = self.df_from_redis(key)
        if not df.empty:
            return df
        try:
            time.sleep(REQ_DELAY)
            df = func()
            self.df_to_redis(df, key, expire)
            return df
        except Exception as e:
            logger.error(f"AK调用失败 {key}: {e}")
            return self.df_from_redis(key)

    # ===================== 原始数据接口 =====================
    def get_a_stock_spot(self):
        return self.safe_ak(ak.stock_zh_a_spot_em, "ak:spot", static=False)

    def get_zt_pool(self):
        return self.safe_ak(ak.stock_zt_pool_em, "ak:zt", static=False)

    def get_north_flow(self):
        return self.safe_ak(ak.stock_hsgt_north_net_inflow, "ak:north:total", static=False)

    def get_sector_spot(self):
        return self.safe_ak(ak.stock_sector_spot_em, "ak:sector:spot", static=False)

    # ===================== 【核心】看板计算逻辑 =====================

    def get_market_sentiment(self) -> Dict:
        """大盘情绪分 0-100 + 涨跌家数 + 炸板率"""
        spot = self.get_a_stock_spot()
        zt = self.get_zt_pool()

        # 1. 涨跌家数
        up = len(spot[spot["涨跌幅"] > 0])
        down = len(spot[spot["涨跌幅"] < 0])
        zero = len(spot[spot["涨跌幅"] == 0])
        total = len(spot)

        # 2. 炸板率
        zt_num = len(zt[zt["涨停类型"] == "首板"])
        zt_continue = len(zt[zt["涨停类型"] == "连板"])
        zt_all = zt_num + zt_continue
        open_zt = len(zt[zt["炸板"] == "是"])
        zongban = zt_all + open_zt
        open_rate = round(open_zt / zongban * 100, 2) if zongban > 0 else 0

        # 3. 情绪分（0-100）
        up_ratio = up / total if total > 0 else 0
        sentiment = int(
            (up_ratio * 60) + ((100 - open_rate) * 0.4)
        )
        sentiment = max(0, min(100, sentiment))

        return {
            "sentiment_score": sentiment,
            "up": up,
            "down": down,
            "zero": zero,
            "total": total,
            "zt_count": zt_all,
            "open_zt": open_zt,
            "open_rate": open_rate
        }

    def get_zt_group(self) -> List[Dict]:
        """连板梯队分组（高度板 → 1板 → 2板...）"""
        zt = self.get_zt_pool()
        zt = zt.copy()
        zt["连板数"] = pd.to_numeric(zt["连板数"], errors="coerce").fillna(1).astype(int)
        groups = zt.groupby("连板数")

        result = []
        for level, g in sorted(groups.groups.items(), reverse=True):
            df = zt.iloc[g]
            items = df[["代码", "名称", "涨跌幅", "涨停类型"]].to_dict(orient="records")
            result.append({
                "level": int(level),
                "count": len(items),
                "stocks": items
            })
        return result

    def get_sector_rank(self) -> List[Dict]:
        """题材热度排行（按涨幅+上涨家数加权）"""
        sector = self.get_sector_spot()
        sector = sector.copy()
        sector["涨跌幅"] = pd.to_numeric(sector["涨跌幅"], errors="coerce")
        sector["上涨家数"] = pd.to_numeric(sector["上涨家数"], errors="coerce")
        sector["热度"] = sector["涨跌幅"] * 0.7 + (sector["上涨家数"] / 50) * 30
        sector = sector.sort_values("热度", ascending=False).head(20)

        return sector[["板块名称", "涨跌幅", "上涨家数", "热度"]].to_dict(orient="records")

    def get_risk_list(self) -> List[Dict]:
        """风险榜：跌幅榜 + 跌停股"""
        spot = self.get_a_stock_spot()
        down_stocks = spot.sort_values("涨跌幅", ascending=True).head(20)
        return down_stocks[["代码", "名称", "涨跌幅", "成交额"]].to_dict(orient="records")

    def get_fund_rank(self) -> List[Dict]:
        """资金排行：成交额最大的股票"""
        spot = self.get_a_stock_spot()
        spot["成交额"] = pd.to_numeric(spot["成交额"], errors="coerce")
        top = spot.sort_values("成交额", ascending=False).head(30)
        return top[["代码", "名称", "涨跌幅", "成交额"]].to_dict(orient="records")

    def get_kanban_all(self):
        """
        【看板总接口】
        一次性返回前端需要的所有数据
        """
        return {
            "market_sentiment": self.get_market_sentiment(),
            "zt_group": self.get_zt_group(),
            "sector_rank": self.get_sector_rank(),
            "risk_list": self.get_risk_list(),
            "fund_rank": self.get_fund_rank()
        }

_kanban_service: Optional[KanbanService] = None

async def get_kanban_service() -> KanbanService:
    global _kanban_service
    if _kanban_service is None:
        _kanban_service = KanbanService()
    return _kanban_service