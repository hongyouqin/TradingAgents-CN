
from typing import Optional
from pydantic import BaseModel, Field

class PreparePaymentRequest(BaseModel):
    # 创建订单必须传的字段
    package_id: str = Field(..., description="充值套餐ID，例如 PACK_001")
    payment_scene: str = Field(..., description="支付场景：JSAPI / NATIVE / H5")
    
    # 可选字段
    redirect_url: Optional[str] = Field(
        None, 
        description="H5支付回跳地址"
    )

    class Config:
        schema_extra = {
            "example": {
                "package_id": "PACK_001",
                "payment_scene": "JSAPI",
                "redirect_url": "https://yourdomain.com/payment/result"
            }
        }