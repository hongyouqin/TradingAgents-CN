"""
支出账本 API 路由
提供收支记录的增删改查、月度扎帐接口
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database import get_database
from app.core.response import ok, fail
from app.models.expense_ledger import (
    ExpenseRecordCreate,
    ExpenseRecordUpdate,
)
from app.services.expense_ledger_service import ExpenseLedgerService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/expense", tags=["支出账本"])


def get_service(db=Depends(get_database)) -> ExpenseLedgerService:
    """获取支出账本服务实例"""
    return ExpenseLedgerService(db)


@router.post("/records", summary="创建收支记录")
async def create_record(
    data: ExpenseRecordCreate,
    service: ExpenseLedgerService = Depends(get_service),
):
    """
        创建一条收入或支出记录
        record_type: income/expense
        category: "分类，如：充值、token消耗、服务购买、其他"
    """
    try:
        # 确保索引
        await service.ensure_indexes()
        record = await service.create_record(data)
        return ok(data=record.model_dump(by_alias=True), message="记录创建成功")
    except Exception as e:
        logger.error(f"创建收支记录失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建失败: {str(e)}")


@router.get("/records", summary="查询收支记录列表")
async def list_records(
    record_type: Optional[str] = Query(None, description="筛选类型：income/expense"),
    category: Optional[str] = Query(None, description="筛选分类"),
    year_month: Optional[str] = Query(None, description="筛选月份，格式：2026-05"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    service: ExpenseLedgerService = Depends(get_service),
):
    """查询收支记录，支持按类型、分类、月份筛选"""
    try:
        records, total = await service.list_records(
            record_type=record_type,
            category=category,
            year_month=year_month,
            page=page,
            page_size=page_size,
        )
        return ok(
            data={
                "items": [r.model_dump(by_alias=True) for r in records],
                "total": total,
                "page": page,
                "page_size": page_size,
            },
            message="查询成功",
        )
    except Exception as e:
        logger.error(f"查询收支记录失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.get("/records/{record_id}", summary="获取单条收支记录")
async def get_record(
    record_id: str,
    service: ExpenseLedgerService = Depends(get_service),
):
    """根据ID获取单条收支记录"""
    try:
        record = await service.get_record(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        return ok(data=record.model_dump(by_alias=True), message="查询成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取收支记录失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.put("/records/{record_id}", summary="更新收支记录")
async def update_record(
    record_id: str,
    data: ExpenseRecordUpdate,
    service: ExpenseLedgerService = Depends(get_service),
):
    """更新收支记录"""
    try:
        record = await service.update_record(record_id, data)
        if record is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        return ok(data=record.model_dump(by_alias=True), message="更新成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新收支记录失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")


@router.delete("/records/{record_id}", summary="删除收支记录")
async def delete_record(
    record_id: str,
    service: ExpenseLedgerService = Depends(get_service),
):
    """删除收支记录"""
    try:
        success = await service.delete_record(record_id)
        if not success:
            raise HTTPException(status_code=404, detail="记录不存在")
        return ok(message="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除收支记录失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@router.get("/monthly-settlement", summary="月度扎帐")
async def monthly_settlement(
    year_month: str = Query(..., description="月份，格式：2026-05"),
    service: ExpenseLedgerService = Depends(get_service),
):
    """获取指定月份的扎帐结果，包含总收入、总支出、结余及明细"""
    try:
        settlement = await service.get_monthly_settlement(year_month)
        if settlement is None:
            return fail(message="月份格式错误，正确格式：2026-05")
        return ok(
            data=settlement.model_dump(by_alias=True),
            message="扎帐查询成功",
        )
    except Exception as e:
        logger.error(f"月度扎帐查询失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"扎帐查询失败: {str(e)}")


@router.get("/category-summary", summary="支出分类汇总")
async def category_summary(
    year_month: str = Query(..., description="月份，格式：2026-05"),
    service: ExpenseLedgerService = Depends(get_service),
):
    """获取指定月份的支出按分类汇总情况"""
    try:
        summaries = await service.get_category_summary(year_month)
        return ok(
            data=[s.model_dump() for s in summaries],
            message="分类汇总查询成功",
        )
    except Exception as e:
        logger.error(f"分类汇总查询失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分类汇总查询失败: {str(e)}")
