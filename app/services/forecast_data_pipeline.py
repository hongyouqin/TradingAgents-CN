import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

class ForecastDataPipeline:
    """Orchestrates daily data pull and cleaning for tomorrow forecast.

    Implementation is intentionally lightweight: fetch_* methods are best-effort
    and may call existing AKShare/Tushare services when available. Results are
    saved via ForecastService (app.services.forecast_service).
    """

    def __init__(self):
        pass

    def _trade_date_query(self, date_key: str) -> dict:
        """Support both YYYY-MM-DD and YYYYMMDD trade_date formats."""
        try:
            alt = date_key.replace("-", "") if date_key else date_key
        except Exception:
            alt = date_key
        return {"trade_date": {"$in": [date_key, alt]}}

    async def run_daily_pipeline(self, db, target_date: Optional[str] = None) -> dict:
        """Run full daily pipeline and persist cleaned data.

        Args:
            db: AsyncIOMotorDatabase
            target_date: ISO date string (YYYY-MM-DD). Defaults to UTC today.
        Returns:
            The aggregated cleaned payload saved to DB.
        """
        if target_date is None:
            target_date = datetime.utcnow().date().isoformat()

        from app.services.forecast_service import get_forecast_service
        svc = get_forecast_service(db)
        await svc.ensure_indexes()

        payload = {
            "date": target_date,
            "market_basic": {},
            "emotion_data": {},
            "sector_data": {},
            "fund_flow": {},
            "macro_news": {},
            "tech_index": {},
            "stock_popular": {},
            "history_benchmark": {},
        }

        # Each fetch is best-effort; failure of a sub-step won't abort the whole pipeline
        try:
            payload["market_basic"] = await self.fetch_market_basic(db, target_date)
        except Exception as e:
            logger.warning(f"fetch_market_basic failed: {e}")

        try:
            payload["emotion_data"] = await self.fetch_emotion_data(db, target_date)
        except Exception as e:
            logger.warning(f"fetch_emotion_data failed: {e}")

        try:
            payload["sector_data"] = await self.fetch_sector_data(db, target_date)
        except Exception as e:
            logger.warning(f"fetch_sector_data failed: {e}")

        try:
            payload["fund_flow"] = await self.fetch_fund_flow(db, target_date)
        except Exception as e:
            logger.warning(f"fetch_fund_flow failed: {e}")

        try:
            payload["macro_news"] = await self.fetch_macro_news(db, target_date)
        except Exception as e:
            logger.warning(f"fetch_macro_news failed: {e}")

        try:
            payload["tech_index"] = await self.fetch_tech_index(db, target_date)
        except Exception as e:
            logger.warning(f"fetch_tech_index failed: {e}")

        try:
            payload["stock_popular"] = await self.fetch_stock_popular(db, target_date)
        except Exception as e:
            logger.warning(f"fetch_stock_popular failed: {e}")

        try:
            payload["history_benchmark"] = await self.fetch_history_benchmark(db, target_date)
        except Exception as e:
            logger.warning(f"fetch_history_benchmark failed: {e}")

        # Save cleaned payload
        try:
            await svc.save_daily_market_data(target_date, payload)
        except Exception as e:
            logger.error(f"Failed to save daily market data for {target_date}: {e}")
            raise

        logger.info(f"Daily pipeline completed for {target_date}")
        return payload

    # --- Fetch helpers (stubs; integrate data-source services as needed) ---
    async def fetch_market_basic(self, db, date_key: str) -> dict:
        """Fetch basic market summary (index K-lines, MA, breadth).

        Strategy:
        - Try to reuse tushare/akshare worker summary APIs if available.
        - Fallback: aggregate from stock_daily_quotes for the given trade_date.
        """
        # Prefer existing worker services if available
        try:
            from app.worker.tushare_sync_service import get_tushare_sync_service
            service = await get_tushare_sync_service()
            if hasattr(service, "get_market_summary"):
                return await service.get_market_summary(date_key)
        except Exception:
            pass

        try:
            from app.worker.akshare_sync_service import get_akshare_sync_service
            service = await get_akshare_sync_service()
            if hasattr(service, "get_market_summary"):
                return await service.get_market_summary(date_key)
        except Exception:
            pass

        # Fallback aggregation from stock_daily_quotes
        try:
            coll = db["stock_daily_quotes"]
            match = self._trade_date_query(date_key)

            total_count = await coll.count_documents(match)
            if total_count == 0:
                return {"note": "no_daily_quotes_for_date", "date": date_key, "total_count": 0}

            # breadth counts
            count_up = await coll.count_documents({"trade_date": date_key, "pct_chg": {"$gt": 0}})
            count_down = await coll.count_documents({"trade_date": date_key, "pct_chg": {"$lt": 0}})
            limit_up = await coll.count_documents({"trade_date": date_key, "pct_chg": {"$gte": 9.0}})
            limit_down = await coll.count_documents({"trade_date": date_key, "pct_chg": {"$lte": -9.0}})

            # aggregate average pct_chg and total amount
            pipeline = [
                {"$match": match},
                {"$group": {"_id": None, "avg_pct": {"$avg": "$pct_chg"}, "sum_amount": {"$sum": {"$ifNull": ["$amount", 0]}}}}
            ]
            agg = await coll.aggregate(pipeline).to_list(length=1)
            avg_pct = agg[0]["avg_pct"] if agg else 0
            sum_amount = agg[0]["sum_amount"] if agg else 0

            # top gainers / losers
            top_gainers = await coll.find(match, {"_id": 0, "code": 1, "pct_chg": 1, "amount": 1}).sort("pct_chg", -1).limit(10).to_list(length=10)
            top_losers = await coll.find(match, {"_id": 0, "code": 1, "pct_chg": 1, "amount": 1}).sort("pct_chg", 1).limit(10).to_list(length=10)

            summary = {
                "date": date_key,
                "total_count": int(total_count),
                "count_up": int(count_up),
                "count_down": int(count_down),
                "limit_up": int(limit_up),
                "limit_down": int(limit_down),
                "avg_pct": float(avg_pct) if avg_pct is not None else 0.0,
                "sum_amount": float(sum_amount) if sum_amount is not None else 0.0,
                "top_gainers": top_gainers,
                "top_losers": top_losers,
                "note": "aggregated_from_stock_daily_quotes"
            }

            return summary
        except Exception as e:
            logger.exception(f"Fallback market_basic aggregation failed: {e}")
            return {"note": "error", "error": str(e)}

    async def fetch_emotion_data(self, db, date_key: str) -> dict:
        """Fetch emotion/breadth metrics: limit-up/down counts, strong movers, breadth ratios.

        Improved behavior:
        - Support trade_date formats via _trade_date_query
        - Compute ratios and provide more robust top movers sorting
        - Return consistent keys for downstream agents
        """
        try:
            coll = db["stock_daily_quotes"]
            match = self._trade_date_query(date_key)

            total = await coll.count_documents(match)
            if total == 0:
                return {"date": date_key, "note": "no_data", "total": 0}

            # counts
            limit_up = await coll.count_documents({**match, **{"pct_chg": {"$gte": 9.0}}})
            limit_down = await coll.count_documents({**match, **{"pct_chg": {"$lte": -9.0}}})
            strong_up = await coll.count_documents({**match, **{"pct_chg": {"$gte": 5.0}}})
            strong_down = await coll.count_documents({**match, **{"pct_chg": {"$lte": -5.0}}})
            up_count = await coll.count_documents({**match, **{"pct_chg": {"$gt": 0}}})
            down_count = await coll.count_documents({**match, **{"pct_chg": {"$lt": 0}}})

            # aggregate stats
            pipeline = [
                {"$match": match},
                {"$group": {"_id": None, "avg_pct": {"$avg": "$pct_chg"}, "avg_turnover": {"$avg": {"$ifNull": ["$turnover_rate", 0]}}, "sum_amount": {"$sum": {"$ifNull": ["$amount", 0]}}}}
            ]
            agg = await coll.aggregate(pipeline).to_list(length=1)
            avg_pct = agg[0].get("avg_pct", 0.0) if agg else 0.0
            avg_turnover = agg[0].get("avg_turnover", 0.0) if agg else 0.0
            sum_amount = agg[0].get("sum_amount", 0.0) if agg else 0.0

            # top movers: sort by pct_chg desc (gainers) and by absolute move for overall movers
            top_gainers = await coll.find(match, {"_id": 0, "code": 1, "pct_chg": 1, "amount": 1}).sort([("pct_chg", -1), ("amount", -1)]).limit(10).to_list(length=10)
            top_losers = await coll.find(match, {"_id": 0, "code": 1, "pct_chg": 1, "amount": 1}).sort([("pct_chg", 1), ("amount", -1)]).limit(10).to_list(length=10)

            # overall top movers by absolute pct_chg
            top_movers = await coll.find(match, {"_id": 0, "code": 1, "pct_chg": 1, "amount": 1}).sort([("pct_chg", -1), ("amount", -1)]).limit(20).to_list(length=20)

            return {
                "date": date_key,
                "total": int(total),
                "up_count": int(up_count),
                "down_count": int(down_count),
                "limit_up": int(limit_up),
                "limit_down": int(limit_down),
                "strong_up": int(strong_up),
                "strong_down": int(strong_down),
                "avg_pct": float(avg_pct),
                "avg_turnover": float(avg_turnover),
                "sum_amount": float(sum_amount),
                "top_gainers": top_gainers,
                "top_losers": top_losers,
                "top_movers": top_movers,
                "note": "aggregated_from_stock_daily_quotes"
            }
        except Exception as e:
            logger.exception(f"fetch_emotion_data failed: {e}")
            return {"note": "error", "error": str(e)}

    async def fetch_sector_data(self, db, date_key: str) -> dict:
        """Aggregate performance by industry/sector using stock_basic_info join.

        Fallback strategy:
        1) Try $lookup with stock_basic_info on "code" field (primary).
        2) If no results or join yields only null industries, try joining on "symbol" field.
        3) If still empty, aggregate top gainers/losers and flag as "no_industry_field" 
           so downstream agents can derive sector insights from individual stocks.
        """
        try:
            coll = db["stock_daily_quotes"]
            match = self._trade_date_query(date_key)

            # Quick check: does stock_basic_info exist?
            names = await db.list_collection_names()
            has_basic_info = "stock_basic_info" in names

            sectors = []
            note = "aggregated_by_industry"

            if has_basic_info:
                # Attempt primary lookup on "code"
                pipeline = [
                    {"$match": match},
                    {"$lookup": {"from": "stock_basic_info", "localField": "code", "foreignField": "code", "as": "basic"}},
                    {"$unwind": {"path": "$basic", "preserveNullAndEmptyArrays": True}},
                    {"$group": {"_id": "$basic.industry", "avg_pct": {"$avg": "$pct_chg"}, "sum_amount": {"$sum": {"$ifNull": ["$amount", 0]}}, "count": {"$sum": 1}}},
                    {"$sort": {"sum_amount": -1}},
                    {"$limit": 30}
                ]
                res = await coll.aggregate(pipeline).to_list(length=30)
                for r in res:
                    industry = r.get("_id")
                    if industry:  # only include non-null industries
                        sectors.append({
                            "industry": str(industry),
                            "avg_pct": float(r.get("avg_pct") or 0),
                            "sum_amount": float(r.get("sum_amount") or 0),
                            "count": int(r.get("count") or 0)
                        })

                # Fallback: if primary lookup returned few/no sectors, try "symbol" field
                if len(sectors) < 3:
                    pipeline2 = [
                        {"$match": match},
                        {"$lookup": {"from": "stock_basic_info", "localField": "symbol", "foreignField": "code", "as": "basic"}},
                        {"$unwind": {"path": "$basic", "preserveNullAndEmptyArrays": True}},
                        {"$group": {"_id": "$basic.industry", "avg_pct": {"$avg": "$pct_chg"}, "sum_amount": {"$sum": {"$ifNull": ["$amount", 0]}}, "count": {"$sum": 1}}},
                        {"$sort": {"sum_amount": -1}},
                        {"$limit": 30}
                    ]
                    res2 = await coll.aggregate(pipeline2).to_list(length=30)
                    seen = {s["industry"] for s in sectors}
                    for r in res2:
                        industry = r.get("_id")
                        if industry and str(industry) not in seen:
                            sectors.append({
                                "industry": str(industry),
                                "avg_pct": float(r.get("avg_pct") or 0),
                                "sum_amount": float(r.get("sum_amount") or 0),
                                "count": int(r.get("count") or 0)
                            })

                # Re-sort by sum_amount desc
                sectors.sort(key=lambda x: x["sum_amount"], reverse=True)
                sectors = sectors[:30]

                if not sectors:
                    note = "no_industry_in_stock_basic_info"
            else:
                note = "stock_basic_info_collection_missing"

            # Final fallback: if no sectors, provide top individual stocks as context
            if not sectors:
                note = note + "|fallback_top_stocks_provided"
                top_stocks = await coll.find(
                    match,
                    {"_id": 0, "code": 1, "name": 1, "pct_chg": 1, "amount": 1}
                ).sort([("pct_chg", -1), ("amount", -1)]).limit(20).to_list(length=20)

                # Also get top losers for balance
                top_losers = await coll.find(
                    match,
                    {"_id": 0, "code": 1, "name": 1, "pct_chg": 1, "amount": 1}
                ).sort([("pct_chg", 1), ("amount", -1)]).limit(10).to_list(length=10)

                return {
                    "date": date_key,
                    "top_sectors": [],
                    "top_stocks_fallback": {
                        "gainers": [{"code": s.get("code"), "name": s.get("name"), "pct_chg": s.get("pct_chg")} for s in top_stocks],
                        "losers": [{"code": s.get("code"), "name": s.get("name"), "pct_chg": s.get("pct_chg")} for s in top_losers]
                    },
                    "note": note
                }

            return {"date": date_key, "top_sectors": sectors, "note": note}
        except Exception as e:
            logger.exception(f"fetch_sector_data failed: {e}")
            return {"note": "error", "error": str(e)}

    async def fetch_fund_flow(self, db, date_key: str) -> dict:
        """Attempt to read common fund flow collections (northbound, money_flow).

        Improvements:
        - Accept common date keys (date/trade_date)
        - If no direct collection, approximate net inflow by aggregating amounts
        """
        try:
            candidates = ["north_money", "northbound", "money_flow", "capital_flow", "fund_flow"]
            names = await db.list_collection_names()
            for name in candidates:
                if name in names:
                    coll = db[name]
                    # accept multiple date field names
                    doc = await coll.find_one({"$or": [{"date": date_key}, {"trade_date": date_key}, {"date": date_key.replace('-', '')}, {"trade_date": date_key.replace('-', '')}]})
                    if doc:
                        return {"date": date_key, "source": name, "data": doc}

            # try market_capital_flow with flexible keys
            if "market_capital_flow" in names:
                coll = db["market_capital_flow"]
                doc = await coll.find_one({"$or": [{"date": date_key}, {"trade_date": date_key}, {"date": date_key.replace('-', '')}]})
                if doc:
                    return {"date": date_key, "source": "market_capital_flow", "data": doc}

            # Fallback: approximate net inflow by aggregating traded amounts grouped by sign
            if "stock_daily_quotes" in names:
                sq = db["stock_daily_quotes"]
                match = self._trade_date_query(date_key)
                pipeline = [
                    {"$match": match},
                    {"$group": {"_id": {"sign": {"$cond": [{"$gt": ["$pct_chg", 0]}, "inflow", "outflow"]}}, "sum_amount": {"$sum": {"$ifNull": ["$amount", 0]}}, "count": {"$sum": 1}}}
                ]
                agg = await sq.aggregate(pipeline).to_list(length=10)
                inflow = 0.0
                outflow = 0.0
                for a in agg:
                    if a.get("_id") and a["_id"].get("sign") == "inflow":
                        inflow = a.get("sum_amount", 0)
                    else:
                        outflow = a.get("sum_amount", 0)
                approx = {"approx_inflow_amount": float(inflow), "approx_outflow_amount": float(outflow), "net": float(inflow - outflow)}
                return {"date": date_key, "source": "approx_from_stock_daily_quotes", "data": approx}

            return {"date": date_key, "note": "no_fund_flow_collections_found"}
        except Exception as e:
            logger.exception(f"fetch_fund_flow failed: {e}")
            return {"note": "error", "error": str(e)}

    async def fetch_macro_news(self, db, date_key: str) -> dict:
        """Collect macro/news items from common news collections for traceability.

        Improvements:
        - Support multiple date field names and YYYYMMDD format
        - Support publish time windows (match beginning of ISO date)
        - Deduplicate by URL or title, return concise summary field
        - Limit and sort by publish time if available
        """
        try:
            candidates = ["macro_news", "stock_news", "news", "news_data"]
            found = []
            names = await db.list_collection_names()
            seen_urls = set()
            seen_titles = set()

            # helper to build queries that match various date fields or date prefix
            date_alt = date_key.replace("-", "") if date_key else date_key
            date_prefix = date_key if len(date_key) == 10 else date_key[:10]
            query_variants = [{"publish_date": date_key}, {"trade_date": date_key}, {"date": date_key}, {"publish_date": date_alt}, {"trade_date": date_alt}, {"date": date_alt}, {"publish_date": {"$regex": f"^{date_prefix}"}}]

            for name in candidates:
                if name in names:
                    coll = db[name]
                    cursor = coll.find({"$or": query_variants}, {"_id": 0, "title": 1, "source": 1, "url": 1, "summary": 1, "content": 1, "publish_date": 1}).sort([("publish_date", -1)])
                    docs = await cursor.limit(200).to_list(length=200)
                    for d in docs:
                        url = d.get("url")
                        title = (d.get("title") or "").strip()
                        if url and url in seen_urls:
                            continue
                        # dedupe by normalized title if no url
                        tnorm = title.lower()
                        if (not url) and tnorm in seen_titles:
                            continue
                        if url:
                            seen_urls.add(url)
                        if title:
                            seen_titles.add(tnorm)

                        summary = d.get("summary") or (d.get("content") or "")[:240]
                        item = {
                            "title": title,
                            "source": d.get("source") or name,
                            "url": url,
                            "summary": summary,
                            "publish_date": d.get("publish_date")
                        }
                        found.append(item)

            # final dedupe already applied; limit results
            if not found:
                return {"date": date_key, "items": [], "note": "no_news_found"}

            # keep top 100
            return {"date": date_key, "items": found[:100]}
        except Exception as e:
            logger.exception(f"fetch_macro_news failed: {e}")
            return {"note": "error", "error": str(e)}

    async def fetch_tech_index(self, db, date_key: str) -> dict:
        """Compute lightweight technical summaries (market breadth, median metrics)."""
        try:
            coll = db["stock_daily_quotes"]
            match = self._trade_date_query(date_key)
            total = await coll.count_documents(match)
            if total == 0:
                return {"date": date_key, "note": "no_data"}

            # counts
            up_count = await coll.count_documents({**match, **{"pct_chg": {"$gt": 0}}})
            overbought = await coll.count_documents({**match, **{"pct_chg": {"$gt": 2.0}}})
            oversold = await coll.count_documents({**match, **{"pct_chg": {"$lt": -2.0}}})

            # median-like avg
            pipeline = [{"$match": match}, {"$group": {"_id": None, "median_pct": {"$avg": "$pct_chg"}, "median_turnover": {"$avg": {"$ifNull": ["$turnover_rate", 0]}}}}]
            agg = await coll.aggregate(pipeline).to_list(length=1)
            median_pct = agg[0].get("median_pct", 0) if agg else 0
            median_turnover = agg[0].get("median_turnover", 0) if agg else 0

            return {
                "date": date_key,
                "total": int(total),
                "up_ratio": float(up_count)/total if total else 0.0,
                "overbought_ratio": float(overbought)/total if total else 0.0,
                "oversold_ratio": float(oversold)/total if total else 0.0,
                "median_pct": float(median_pct),
                "median_turnover": float(median_turnover)
            }
        except Exception as e:
            logger.exception(f"fetch_tech_index failed: {e}")
            return {"note": "error", "error": str(e)}

    async def fetch_stock_popular(self, db, date_key: str) -> dict:
        """Return top traded stocks by amount as proxy for popularity."""
        try:
            coll = db["stock_daily_quotes"]
            match = self._trade_date_query(date_key)
            top = await coll.find(match, {"_id": 0, "code": 1, "amount": 1, "turnover_rate": 1}).sort("amount", -1).limit(50).to_list(length=50)
            return {"date": date_key, "top_by_amount": top}
        except Exception as e:
            logger.exception(f"fetch_stock_popular failed: {e}")
            return {"note": "error", "error": str(e)}

    async def fetch_history_benchmark(self, db, date_key: str) -> dict:
        """Find historical dates with similar market avg pct and compute next-day avg distribution.

        Notes:
        - Handle trade_date formats (YYYY-MM-DD and YYYYMMDD)
        - Use rolling window of recent dates
        """
        try:
            coll = db["stock_daily_quotes"]
            # compute per-date avg_pct for last 400 days
            pipeline = [
                {"$match": {}},
                {"$group": {"_id": "$trade_date", "avg_pct": {"$avg": "$pct_chg"}}},
                {"$sort": {"_id": 1}}
            ]
            per_date = await coll.aggregate(pipeline).to_list(length=400)
            if not per_date:
                return {"note": "no_history"}

            date_avgs = [(r["_id"], float(r["avg_pct"])) for r in per_date]

            # helper to compare date forms
            alt_date = date_key.replace("-", "")
            today_avg = None
            for d, a in date_avgs:
                if d == date_key or d == alt_date:
                    today_avg = a
                    break

            if today_avg is None:
                today_pipeline = [{"$match": self._trade_date_query(date_key)}, {"$group": {"_id": None, "avg_pct": {"$avg": "$pct_chg"}}}]
                t = await coll.aggregate(today_pipeline).to_list(length=1)
                today_avg = float(t[0].get("avg_pct", 0)) if t else 0.0

            # find similar dates within +/-0.2
            similar = []
            for i, (d, a) in enumerate(date_avgs):
                if abs(a - today_avg) <= 0.2 and (d != date_key and d != alt_date):
                    if i + 1 < len(date_avgs):
                        next_avg = date_avgs[i + 1][1]
                        similar.append({"date": d, "avg_pct": a, "next_avg_pct": next_avg})

            if not similar:
                return {"date": date_key, "today_avg": today_avg, "matches": 0}

            next_avgs = [s["next_avg_pct"] for s in similar]
            mean_next = sum(next_avgs) / len(next_avgs)
            return {"date": date_key, "today_avg": today_avg, "matches": len(similar), "mean_next_avg_pct": float(mean_next), "samples": similar[:20]}
        except Exception as e:
            logger.exception(f"fetch_history_benchmark failed: {e}")
            return {"note": "error", "error": str(e)}


# Simple factory
_pipeline_instance: Optional[ForecastDataPipeline] = None

def get_forecast_pipeline():
    global _pipeline_instance
    if _pipeline_instance is None:
        _pipeline_instance = ForecastDataPipeline()
    return _pipeline_instance
