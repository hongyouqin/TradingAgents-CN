from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from bson import ObjectId


class RechargePackage(BaseModel):
    """充值套餐模型"""
    id: Optional[str] = Field(None, alias="_id")
    package_id: str  # 套餐唯一标识，如 PACK_001
    name: str  # 套餐名称
    price: float  # 支付金额（元）
    power: int  # 基础算力
    bonus: int = 0  # 赠送算力
    popular: bool = False  # 是否热门推荐
    description: str  # 描述
    sort_order: int = 0  # 排序顺序
    is_active: bool = True  # 是否上架
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_encoders = {ObjectId: str}
        populate_by_name = True

    @property
    def total_power(self) -> int:
        """总获得算力"""
        return self.power + self.bonus

    @property
    def unit_price(self) -> float:
        """单价（元/算力）"""
        if self.total_power == 0:
            return 0
        return round(self.price / self.total_power, 2)


class RechargePackageCreate(BaseModel): 
    """创建套餐请求"""
    package_id: str
    name: str
    price: float
    power: int
    bonus: int = 0
    popular: bool = False
    description: str
    sort_order: int = 0
    is_active: bool = True


class RechargePackageUpdate(BaseModel):
    """更新套餐请求"""
    name: Optional[str] = None
    price: Optional[float] = None
    power: Optional[int] = None
    bonus: Optional[int] = None
    popular: Optional[bool] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None