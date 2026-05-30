"""
支出账本数据模型
用于记录充值分配、token消耗、服务购买等收支明细
"""

from datetime import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from bson import ObjectId

class ExpenseRecord(BaseModel):
    """收支记录模型"""
    # ✅ 必须保留 id，否则 record.id = 会报错
    id: Optional[str] = None

    record_type: Literal["income", "expense"] = Field(
        ..., description="类型：income=收入, expense=支出"
    )
    category: str = Field(
        ..., description="分类，如：充值、token消耗、服务购买、其他"
    )
    amount: float = Field(..., gt=0, description="金额（元），正数")
    description: str = Field(default="", description="备注说明")
    record_date: datetime = Field(
        default_factory=datetime.utcnow, description="发生日期"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_encoders = {ObjectId: str}
        populate_by_name = True


class ExpenseRecordCreate(BaseModel):
    """创建收支记录请求"""
    record_type: Literal["income", "expense"] = Field(
        ..., description="类型：income=收入, expense=支出"
    )
    category: str = Field(
        ..., description="分类，如：充值、token消耗、服务购买、其他"
    )
    amount: float = Field(..., gt=0, description="金额（元）")
    description: str = Field(default="", description="备注说明")
    record_date: Optional[datetime] = Field(
        None, description="发生日期，不填则默认当前时间"
    )


class ExpenseRecordUpdate(BaseModel):
    """更新收支记录请求"""
    record_type: Optional[Literal["income", "expense"]] = None
    category: Optional[str] = None
    amount: Optional[float] = Field(None, gt=0)
    description: Optional[str] = None
    record_date: Optional[datetime] = None


class ExpenseRecordResponse(BaseModel):
    """收支记录响应"""
    id: str = Field(..., alias="_id")
    record_type: str
    category: str
    amount: float
    description: str
    record_date: datetime
    created_at: datetime
    updated_at: datetime

    class Config:
        json_encoders = {ObjectId: str}
        populate_by_name = True


class MonthlySettlement(BaseModel):
    """月度扎帐结果"""
    year_month: str = Field(..., description="月份，格式：2026-05")
    total_income: float = Field(default=0, description="总收入")
    total_expense: float = Field(default=0, description="总支出")
    balance: float = Field(default=0, description="结余（收入-支出）")
    record_count: int = Field(default=0, description="记录数")
    income_count: int = Field(default=0, description="收入笔数")
    expense_count: int = Field(default=0, description="支出笔数")
    details: List[ExpenseRecordResponse] = Field(
        default_factory=list, description="该月明细"
    )


class CategorySummary(BaseModel):
    """分类汇总"""
    category: str
    total_amount: float
    count: int
    percentage: float
