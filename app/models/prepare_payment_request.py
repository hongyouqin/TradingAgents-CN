
from typing import Optional
from pydantic import BaseModel, Field


class PreparePaymentRequest(BaseModel):
    redirect_url: Optional[str] = Field(
        None, 
        description="H5支付回跳地址，如果不传则使用系统默认地址"
    )
    
    class Config:
        schema_extra = {
            "example": {
                "redirect_url": "https://yourdomain.com/payment/callback"
            }
        }