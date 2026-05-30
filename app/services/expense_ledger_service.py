"""
支出账本服务层
提供收支记录的增删改查、月度扎帐等功能
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from bson import ObjectId

from app.models.expense_ledger import (
    ExpenseRecord,
    ExpenseRecordCreate,
    ExpenseRecordUpdate,
    ExpenseRecordResponse,
    MonthlySettlement,
    CategorySummary,
)

logger = logging.getLogger(__name__)

# MongoDB 集合名
COLLECTION = "expense_ledger"


class ExpenseLedgerService:
    """支出账本服务"""

    def __init__(self, db):
        self.db = db
        self.collection = db[COLLECTION]

    async def ensure_indexes(self):
        """确保数据库索引"""
        await self.collection.create_index("record_date")
        await self.collection.create_index("record_type")
        await self.collection.create_index("category")

    async def create_record(self, data: ExpenseRecordCreate) -> ExpenseRecordResponse:
        """创建一条收支记录"""
        now = datetime.now(timezone.utc)
        record = ExpenseRecord(
            record_type=data.record_type,
            category=data.category,
            amount=data.amount,
            description=data.description,
            record_date=data.record_date or now,
            created_at=now,
            updated_at=now,
        )
        result = await self.collection.insert_one(record.model_dump(exclude={"id"}))
        record.id = str(result.inserted_id)
        return self._to_response(record)

    async def get_record(
        self, record_id: str
    ) -> Optional[ExpenseRecordResponse]:
        """根据ID获取单条记录"""
        try:
            obj_id = ObjectId(record_id)
        except Exception:
            return None
        doc = await self.collection.find_one({"_id": obj_id})
        if doc is None:
            return None
        return self._doc_to_response(doc)

    async def update_record(
        self, record_id: str, data: ExpenseRecordUpdate
    ) -> Optional[ExpenseRecordResponse]:
        """更新收支记录"""
        try:
            obj_id = ObjectId(record_id)
        except Exception:
            return None

        update_data = data.model_dump(exclude_none=True)
        if not update_data:
            return await self.get_record(record_id)

        update_data["updated_at"] = datetime.now(timezone.utc)
        await self.collection.update_one(
            {"_id": obj_id}, {"$set": update_data}
        )
        return await self.get_record(record_id)

    async def delete_record(self, record_id: str) -> bool:
        """删除收支记录"""
        try:
            obj_id = ObjectId(record_id)
        except Exception:
            return False
        result = await self.collection.delete_one({"_id": obj_id})
        return result.deleted_count > 0

    async def list_records(
        self,
        record_type: Optional[str] = None,
        category: Optional[str] = None,
        year_month: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[ExpenseRecordResponse], int]:
        """
        查询收支记录列表
        year_month 格式：2026-05
        返回 (记录列表, 总数)
        """
        query = {}
        if record_type:
            query["record_type"] = record_type
        if category:
            query["category"] = category
        if year_month:
            try:
                year, month = year_month.split("-")
                from datetime import timezone as tz

                start = datetime(int(year), int(month), 1, tzinfo=tz.utc)
                if month == "12":
                    end_year = int(year) + 1
                    end_month = 1
                else:
                    end_year = int(year)
                    end_month = int(month) + 1
                end = datetime(end_year, end_month, 1, tzinfo=tz.utc)
                query["record_date"] = {"$gte": start, "$lt": end}
            except (ValueError, IndexError):
                pass

        total = await self.collection.count_documents(query)

        cursor = (
            self.collection.find(query)
            .sort("record_date", -1)
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        docs = await cursor.to_list(length=page_size)
        records = [self._doc_to_response(doc) for doc in docs]
        return records, total

    async def get_monthly_settlement(
        self, year_month: str
    ) -> Optional[MonthlySettlement]:
        """
        获取指定月份的扎帐结果
        year_month 格式：2026-05
        """
        try:
            year, month = year_month.split("-")
            year_i, month_i = int(year), int(month)
            from datetime import timezone as tz

            start = datetime(year_i, month_i, 1, tzinfo=tz.utc)
            if month_i == 12:
                end = datetime(year_i + 1, 1, 1, tzinfo=tz.utc)
            else:
                end = datetime(year_i, month_i + 1, 1, tzinfo=tz.utc)
        except (ValueError, IndexError):
            return None

        # 该月所有记录
        cursor = self.collection.find(
            {"record_date": {"$gte": start, "$lt": end}}
        ).sort("record_date", -1)
        docs = await cursor.to_list(length=None)

        if not docs:
            return MonthlySettlement(
                year_month=year_month,
                total_income=0,
                total_expense=0,
                balance=0,
                record_count=0,
                income_count=0,
                expense_count=0,
                details=[],
            )

        total_income = 0.0
        total_expense = 0.0
        income_count = 0
        expense_count = 0

        details = []
        for doc in docs:
            resp = self._doc_to_response(doc)
            details.append(resp)
            if doc["record_type"] == "income":
                total_income += doc["amount"]
                income_count += 1
            else:
                total_expense += doc["amount"]
                expense_count += 1

        balance = round(total_income - total_expense, 2)

        return MonthlySettlement(
            year_month=year_month,
            total_income=round(total_income, 2),
            total_expense=round(total_expense, 2),
            balance=balance,
            record_count=len(docs),
            income_count=income_count,
            expense_count=expense_count,
            details=details,
        )

    async def get_category_summary(
        self, year_month: str
    ) -> List[CategorySummary]:
        """
        获取指定月份的支出分类汇总
        """
        try:
            year, month = year_month.split("-")
            year_i, month_i = int(year), int(month)
            from datetime import timezone as tz

            start = datetime(year_i, month_i, 1, tzinfo=tz.utc)
            if month_i == 12:
                end = datetime(year_i + 1, 1, 1, tzinfo=tz.utc)
            else:
                end = datetime(year_i, month_i + 1, 1, tzinfo=tz.utc)
        except (ValueError, IndexError):
            return []

        pipeline = [
            {"$match": {"record_date": {"$gte": start, "$lt": end}}},
            {
                "$group": {
                    "_id": "$category",
                    "total_amount": {"$sum": "$amount"},
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"total_amount": -1}},
        ]

        cursor = self.collection.aggregate(pipeline)
        results = await cursor.to_list(length=None)

        if not results:
            return []

        total_all = sum(r["total_amount"] for r in results)

        summaries = []
        for r in results:
            pct = round(r["total_amount"] / total_all * 100, 2) if total_all > 0 else 0
            summaries.append(
                CategorySummary(
                    category=r["_id"],
                    total_amount=round(r["total_amount"], 2),
                    count=r["count"],
                    percentage=pct,
                )
            )
        return summaries

    def _to_response(self, record: ExpenseRecord) -> ExpenseRecordResponse:
        """将内部模型转为响应模型"""
        return ExpenseRecordResponse(
            _id=record.id or "",
            record_type=record.record_type,
            category=record.category,
            amount=record.amount,
            description=record.description,
            record_date=record.record_date,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _doc_to_response(self, doc: dict) -> ExpenseRecordResponse:
        """将MongoDB文档转为响应模型"""
        return ExpenseRecordResponse(
            _id=str(doc["_id"]),
            record_type=doc["record_type"],
            category=doc["category"],
            amount=doc["amount"],
            description=doc.get("description", ""),
            record_date=doc["record_date"],
            created_at=doc.get("created_at", doc["record_date"]),
            updated_at=doc.get("updated_at", doc["record_date"]),
        )
