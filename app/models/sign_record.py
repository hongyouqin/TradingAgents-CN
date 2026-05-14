from pydantic import BaseModel
from datetime import datetime, date

class SignRecord(BaseModel):
    user_id: str
    sign_date: date
    sign_time: datetime
