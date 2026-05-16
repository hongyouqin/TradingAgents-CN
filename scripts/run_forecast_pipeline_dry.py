import asyncio
import json
from datetime import datetime

import sys
sys.path.insert(0, r"E:\workspace\TradingAgents-CN")

from app.services.forecast_data_pipeline import get_forecast_pipeline

# Minimal async mock collection and db to simulate required Mongo operations
class MockCursor:
    def __init__(self, docs):
        self._docs = docs
        self._sort = None
        self._limit = None

    def sort(self, *args):
        # support sort("field", -1) or sort([(field, -1)])
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            self._sort = args[0]
        elif len(args) == 2:
            self._sort = [(args[0], args[1])]
        else:
            # fallback
            self._sort = args
        return self

    def limit(self, n):
        self._limit = n
        return self

    async def to_list(self, length=None):
        docs = list(self._docs)
        if self._sort:
            for key, order in reversed(self._sort):
                docs.sort(key=lambda d: d.get(key) or 0, reverse=(order < 0))
        if self._limit is not None:
            docs = docs[: self._limit]
        if length is not None:
            docs = docs[:length]
        return docs

class MockAggregateCursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, length=None):
        if length is None:
            return self.docs
        return self.docs[:length]

class MockCollection:
    def __init__(self, docs):
        self.docs = docs

    async def count_documents(self, match):
        return len(self._find_docs(match))

    def find(self, match, projection=None):
        docs = self._find_docs(match)
        return MockCursor(docs)

    async def find_one(self, query):
        docs = self._find_docs(query)
        return docs[0] if docs else None

    def aggregate(self, pipeline):
        # emulate aggregate returning a cursor with to_list
        match = {}
        group = None
        for stage in pipeline:
            if "$match" in stage:
                match = stage["$match"]
            if "$group" in stage:
                group = stage["$group"]
        docs = self._find_docs(match)
        if not group:
            return MockAggregateCursor([])
        gid = group.get("_id")
        def extract_field(expr):
            # expr may be a string like "$field", or a dict like {"$ifNull": ["$field", 0]},
            # or {"$toDouble": "$field"}, or a constant number.
            if isinstance(expr, str) and expr.startswith("$"):
                return expr[1:]
            if isinstance(expr, dict):
                # take first value and try to extract
                v = next(iter(expr.values()), None)
                if isinstance(v, str) and v.startswith("$"):
                    return v[1:]
                if isinstance(v, list) and v:
                    first = v[0]
                    if isinstance(first, str) and first.startswith("$"):
                        return first[1:]
                # could be nested dict, try deeper
                if isinstance(v, dict):
                    return extract_field(v)
            return None

        if isinstance(gid, str) and gid.startswith("$"):
            field = gid[1:]
            groups = {}
            for d in docs:
                val = d
                for part in field.split('.'):
                    if isinstance(val, dict):
                        val = val.get(part)
                    else:
                        val = None
                        break
                key = val
                groups.setdefault(key, []).append(d)
            out = []
            for k, arr in groups.items():
                doc = {"_id": k}
                for fname, expr in group.items():
                    if fname == "_id":
                        continue
                    if isinstance(expr, dict) and "$avg" in expr:
                        path = extract_field(expr["$avg"]) if expr.get("$avg") is not None else None
                        vals = [float(x.get(path) or 0) for x in arr] if path else [float(expr["$avg"] or 0) for _ in arr]
                        doc[fname] = sum(vals) / len(vals) if vals else 0
                    if isinstance(expr, dict) and "$sum" in expr:
                        arg = expr["$sum"]
                        # handle different arg types
                        if isinstance(arg, dict) and "$ifNull" in arg:
                            path = extract_field(arg["$ifNull"]) or (arg["$ifNull"][0] if arg["$ifNull"] else None)
                            vals = [float(x.get(path) or 0) for x in arr] if isinstance(path, str) else [0 for _ in arr]
                            doc[fname] = sum(vals)
                        elif isinstance(arg, str) and arg.startswith("$"):
                            path = arg[1:]
                            doc[fname] = sum([float(x.get(path) or 0) for x in arr])
                        elif isinstance(arg, (int, float)):
                            doc[fname] = arg * len(arr)
                        else:
                            # fallback try extracting
                            path = extract_field(arg)
                            if path:
                                doc[fname] = sum([float(x.get(path) or 0) for x in arr])
                            else:
                                doc[fname] = 0
                out.append(doc)
            return MockAggregateCursor(out)
        else:
            doc = {"_id": None}
            for fname, expr in group.items():
                if fname == "_id":
                    continue
                if isinstance(expr, dict) and "$avg" in expr:
                    path = extract_field(expr["$avg"]) if expr.get("$avg") is not None else None
                    vals = [float(x.get(path) or 0) for x in docs] if path else [float(expr["$avg"] or 0) for _ in docs]
                    doc[fname] = sum(vals) / len(vals) if vals else 0
                if isinstance(expr, dict) and "$sum" in expr:
                    arg = expr["$sum"]
                    if isinstance(arg, dict) and "$ifNull" in arg:
                        path = extract_field(arg["$ifNull"]) or (arg["$ifNull"][0] if arg["$ifNull"] else None)
                        vals = [float(x.get(path) or 0) for x in docs] if isinstance(path, str) else [0 for _ in docs]
                        doc[fname] = sum(vals)
                    elif isinstance(arg, str) and arg.startswith("$"):
                        path = arg[1:]
                        doc[fname] = sum([float(x.get(path) or 0) for x in docs])
                    elif isinstance(arg, (int, float)):
                        doc[fname] = arg * len(docs)
                    else:
                        path = extract_field(arg)
                        if path:
                            doc[fname] = sum([float(x.get(path) or 0) for x in docs])
                        else:
                            doc[fname] = 0
            return MockAggregateCursor([doc])

    def _find_docs(self, match):
        def matches(d, q):
            if not q:
                return True
            for k, v in q.items():
                if k == "$or":
                    ok = False
                    for sub in v:
                        if matches(d, sub):
                            ok = True
                            break
                    if not ok:
                        return False
                    continue
                if isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        return False
                elif isinstance(v, dict):
                    val = d.get(k)
                    for op, opv in v.items():
                        if op == "$gte" and not (val >= opv):
                            return False
                        if op == "$lte" and not (val <= opv):
                            return False
                        if op == "$gt" and not (val > opv):
                            return False
                        if op == "$lt" and not (val < opv):
                            return False
                else:
                    if d.get(k) != v:
                        return False
            return True
        return [d for d in self.docs if matches(d, match)]

class MockDB:
    def __init__(self, data):
        self._data = data
    def __getitem__(self, name):
        return MockCollection(self._data.get(name, []))
    async def list_collection_names(self):
        return list(self._data.keys())

# create small synthetic dataset for 2026-05-15
SAMPLE_DATE = "2026-05-15"
stock_daily_quotes = [
    {"trade_date": SAMPLE_DATE, "pct_chg": 2.5, "amount": 1000000, "turnover_rate": 1.2, "code": "000001.SZ", "industry": "金融"},
    {"trade_date": SAMPLE_DATE, "pct_chg": 9.0, "amount": 500000, "turnover_rate": 3.5, "code": "000002.SZ", "industry": "科技"},
    {"trade_date": SAMPLE_DATE, "pct_chg": -6.0, "amount": 300000, "turnover_rate": 2.0, "code": "000003.SZ", "industry": "能源"},
    {"trade_date": SAMPLE_DATE.replace('-', ''), "pct_chg": 1.0, "amount": 200000, "turnover_rate": 0.5, "code": "000004.SZ", "industry": "消费"},
]
news = [
    {"publish_date": "2026-05-15T18:00:00", "title": "政策利好 A 股", "source": "来源A", "url": "http://news/a", "summary": "重要政策发布"},
    {"publish_date": "2026-05-15T20:00:00", "title": "外盘收盘回暖", "source": "来源B", "url": "http://news/b", "summary": "美股上涨"},
]
# Prepare mock data dict
mock_data = {
    "stock_daily_quotes": stock_daily_quotes,
    "news": news,
    # fund flow collections absent to test fallback
}

async def main():
    db = MockDB(mock_data)
    pipeline = get_forecast_pipeline()
    date_key = SAMPLE_DATE
    print(f"Dry-run pipeline for {date_key}")

    payload = {"date": date_key}
    payload["market_basic"] = await pipeline.fetch_market_basic(db, date_key)
    payload["emotion_data"] = await pipeline.fetch_emotion_data(db, date_key)
    payload["sector_data"] = await pipeline.fetch_sector_data(db, date_key)
    payload["fund_flow"] = await pipeline.fetch_fund_flow(db, date_key)
    payload["macro_news"] = await pipeline.fetch_macro_news(db, date_key)
    payload["tech_index"] = await pipeline.fetch_tech_index(db, date_key)
    payload["stock_popular"] = await pipeline.fetch_stock_popular(db, date_key)
    payload["history_benchmark"] = await pipeline.fetch_history_benchmark(db, date_key)

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

if __name__ == '__main__':
    asyncio.run(main())
