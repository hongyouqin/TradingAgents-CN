from fastapi import APIRouter, Depends
from fastapi import HTTPException

from app.core.database import get_database
from app.routers.auth_db import get_current_user
from app.services.power_account_service import power_account_service
from app.services.sign_service import SignService

router = APIRouter(prefix="/sign", tags=["签到"])

@router.post("/submit")
async def submit_sign(db = Depends(get_database), current_user=Depends(get_current_user)):
    user_id = getattr(current_user, 'id', None) or getattr(current_user, 'user_id', None) or str(current_user)
    svc = SignService(db, power_account_service)
    await svc.init()
    try:
        result = await svc.submit_sign(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"success": True, "data": result, "message": ""}

@router.get("/status")
async def status(db = Depends(get_database), current_user=Depends(get_current_user)):
    user_id = getattr(current_user, 'id', None) or getattr(current_user, 'user_id', None) or str(current_user)
    svc = SignService(db, power_account_service)
    await svc.init()
    has_signed = await svc.has_signed_today(user_id)
    current_power = None
    if hasattr(power_account_service, 'get_balance'):
        current_power = await power_account_service.get_balance(user_id)
    return {"success": True, "data": {"has_signed": has_signed, "can_sign_today": not has_signed, "current_power": current_power}, "message": ""}
