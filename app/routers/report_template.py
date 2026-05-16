from pathlib import Path
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, status, Query
from app.routers.auth_db import get_current_user
from app.core.response import ok

router = APIRouter(prefix="/report-template", tags=["report-template"])


async def get_admin_user(user: dict = Depends(get_current_user)):
    if not user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="权限不足")
    return user


# directory to store uploaded templates (web/data/report_templates)
REPORT_DIR = Path(__file__).resolve().parents[2] / "web" / "data" / "report_templates"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
MAX_SLOTS = 3
ALLOWED_CONTENT_PREFIX = ("image/",)
MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5MB per image


def _slot_filename(slot: int, ext: str) -> str:
    return f"template{slot}{ext}"


def _find_slot_file(slot: int) -> Optional[Path]:
    for p in REPORT_DIR.glob(f"template{slot}.*"):
        return p
    return None


@router.get("/", include_in_schema=True)
async def get_report_templates():
    """Public endpoint (no auth) returning up to 3 image URLs for the report template."""
    images = []
    for slot in range(1, MAX_SLOTS + 1):
        p = _find_slot_file(slot)
        if p and p.exists():
            images.append({"slot": slot, "url": f"/report-templates/{p.name}"})
        else:
            images.append({"slot": slot, "url": None})
    return ok(data={"images": images})


@router.post("/upload", include_in_schema=True)
async def upload_report_template(
    file: UploadFile = File(...),
    slot: Optional[int] = Query(None, ge=1, le=MAX_SLOTS),
    admin=Depends(get_admin_user)
):
    """Admin-only: upload or replace a report template image. If slot omitted, place into first empty slot.

    Returns the slot and public url.
    """
    # validate content type
    content_type = file.content_type or ""
    if not any(content_type.startswith(pref) for pref in ALLOWED_CONTENT_PREFIX):
        raise HTTPException(status_code=400, detail="Unsupported file type")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File too large")

    # determine extension
    orig = Path(file.filename)
    ext = orig.suffix.lower() if orig.suffix else ''
    if not ext:
        # fallback from content type
        if content_type == 'image/png':
            ext = '.png'
        elif content_type in ('image/jpeg','image/jpg'):
            ext = '.jpg'
        else:
            ext = '.bin'

    target_slot = slot
    if target_slot is None:
        # find first empty slot
        target_slot = None
        for s in range(1, MAX_SLOTS + 1):
            if not _find_slot_file(s):
                target_slot = s
                break
        if target_slot is None:
            raise HTTPException(status_code=400, detail="All slots are occupied; provide slot to replace")

    # remove existing file for that slot
    existing = _find_slot_file(target_slot)
    if existing and existing.exists():
        try:
            existing.unlink()
        except Exception:
            pass

    filename = _slot_filename(target_slot, ext)
    outp = REPORT_DIR / filename
    try:
        with open(outp, 'wb') as f:
            f.write(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    return ok(data={"slot": target_slot, "url": f"/report-templates/{filename}"})


@router.delete("/{slot}")
async def delete_report_template(slot: int, admin=Depends(get_admin_user)):
    if slot < 1 or slot > MAX_SLOTS:
        raise HTTPException(status_code=400, detail="invalid slot")
    p = _find_slot_file(slot)
    if not p or not p.exists():
        raise HTTPException(status_code=404, detail="slot not set")
    try:
        p.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to delete: {e}")
    return ok(message=f"slot {slot} cleared")
