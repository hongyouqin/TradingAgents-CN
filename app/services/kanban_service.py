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
REQ_DELAY = 1.0  # 服务器用 1 秒，绝对不被封
# ===================================================

class KanbanService:
    def __init__(self):
        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_port = int(os.getenv('REDIS_PORT', 6379))
        redis_password = os.getenv('REDIS_PASSWORD', None)
        redis_db = int(os.getenv('REDIS_DB', 0))
        
        try:
            self.redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                db=redis_db,
                decode_responses=False,
                socket_timeout=3,
                socket_connect_timeout=3
            )
        except Exception as e:
            logger.error(f"Redis 连接失败: {e}")
            self.redis_client = None

    def df_to_redis(self, df: pd.DataFrame, key: str, expire: int):
        if not self.redis_client or df.empty:
            return
        try:
            json_data = df.to_json(orient="split", force_ascii=False)
            self.redis_client.setex(key, timedelta(seconds=expire), json_data)
        except Exception as e:
            logger.warning(f"Redis 存储失败 {key}: {e}")

    def df_from_redis(self, key: str) -> pd.DataFrame:
        if not self.redis_client:
            return pd.DataFrame()
        try:
            data = self.redis_client.get(key)
            if data:
                return pd.read_json(data, orient="split")
        except Exception as e:
            logger.warning(f"Redis 读取失败 {key}: {e}")
        return pd.DataFrame()

    def safe_ak(self, func, key: str, static: bool = False) -> pd.DataFrame:
        expire = CACHE_STATIC if static else CACHE_HOT
        df = self.df_from_redis(key)
        if not df.empty:
            return df

        try:
            time.sleep(REQ_DELAY)
            df = func()
            logger.info(f"AK接口调用成功 {key}, 获取到 {len(df)} 条数据")
            logger.info("df内容={df}")
            self.df_to_redis(df, key, expire)
            return df
        except Exception as e:
            logger.error(f"AK调用失败 {key}: {e}")
            return pd.DataFrame()  # 绝对兜底

    # ===================== 原始数据 =====================
    def get_a_stock_spot(self):
        return self.safe_ak(ak.stock_zh_a_spot_em, "ak:spot", static=False)

    def get_zt_pool(self):
        return self.safe_ak(ak.stock_zt_pool_em, "ak:zt", static=False)

    def get_sector_spot(self):
        return self.safe_ak(ak.stock_sector_spot_em, "ak:sector:spot", static=False)

    # ===================== 核心计算（100% 不报错） =====================
    def get_market_sentiment(self) -> Dict:
        try:
            spot = self.get_a_stock_spot()
            zt = self.get_zt_pool()
            if spot.empty:
                return {"sentiment_score":0,"up":0,"down":0,"zero":0,"total":0,"zt_count":0,"open_zt":0,"open_rate":0}
            
            up = len(spot[spot["涨跌幅"]>0]) if "涨跌幅" in spot else 0
            down = len(spot[spot["涨跌幅"]<0]) if "涨跌幅" in spot else 0
            zero = len(spot[spot["涨跌幅"]==0]) if "涨跌幅" in spot else 0
            total = len(spot)
            zt_all = len(zt)
            open_zt = len(zt[zt["炸板"]=="是"]) if "炸板" in zt else 0
            zongban = zt_all + open_zt
            open_rate = round(open_zt/zongban*100,2) if zongban>0 else 0
            up_ratio = up/total if total>0 else 0
            sentiment = int(up_ratio*60 + (100-open_rate)*0.4)
            sentiment = max(0, min(100, sentiment))
            
            return {
                "sentiment_score": sentiment, "up":up,"down":down,"zero":zero,"total":total,
                "zt_count":zt_all,"open_zt":open_zt,"open_rate":open_rate
            }
        except:
            return {"sentiment_score":0,"up":0,"down":0,"zero":0,"total":0,"zt_count":0,"open_zt":0,"open_rate":0}

    def get_zt_group(self) -> List[Dict]:
        try:
            zt = self.get_zt_pool()
            if zt.empty or "连板数" not in zt: return []
            zt["连板数"] = pd.to_numeric(zt["连板数"], errors="coerce").fillna(1).astype(int)
            groups = zt.groupby("连板数")
            res = []
            for lv, g in sorted(groups.groups.items(), reverse=True):
                df = zt.iloc[g]
                items = df[["代码","名称","涨跌幅","涨停类型"]].to_dict(orient="records")
                res.append({"level":int(lv),"count":len(items),"stocks":items})
            return res
        except:
            return []

    def get_sector_rank(self) -> List[Dict]:
        try:
            sector = self.get_sector_spot()
            if sector.empty: return []
            sector["涨跌幅"] = pd.to_numeric(sector["涨跌幅"], errors="coerce")
            sector["上涨家数"] = pd.to_numeric(sector["上涨家数"], errors="coerce")
            sector["热度"] = sector["涨跌幅"]*0.7 + (sector["上涨家数"]/50)*30
            sector = sector.sort_values("热度", ascending=False).head(20)
            return sector[["板块名称","涨跌幅","上涨家数","热度"]].to_dict(orient="records")
        except:
            return []

    def get_risk_list(self) -> List[Dict]:
        try:
            spot = self.get_a_stock_spot()
            if spot.empty or "涨跌幅" not in spot: return []
            down = spot.sort_values("涨跌幅", ascending=True).head(20)
            return down[["代码","名称","涨跌幅","成交额"]].to_dict(orient="records")
        except:
            return []

    def get_fund_rank(self) -> List[Dict]:
        try:
            spot = self.get_a_stock_spot()
            if spot.empty or "成交额" not in spot: return []
            spot["成交额"] = pd.to_numeric(spot["成交额"], errors="coerce")
            top = spot.sort_values("成交额", ascending=False).head(30)
            return top[["代码","名称","涨跌幅","成交额"]].to_dict(orient="records")
        except:
            return []

    def get_kanban_all(self):
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