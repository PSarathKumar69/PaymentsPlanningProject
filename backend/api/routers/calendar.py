"""Calendar router — exposes weeks_in_month() over HTTP (see
backend/api/schemas/calendar.py's docstring for why this exists)."""
from fastapi import APIRouter, HTTPException

from backend.weekly_planning.calendar_utils import weeks_in_month

from ..schemas.calendar import WeeksInMonthResponse

router = APIRouter(tags=["calendar"])


@router.get("/calendar/weeks-in-month", response_model=WeeksInMonthResponse)
def get_weeks_in_month(month: str):
    """`month` is "YYYY-MM" (docs/14's own convention, no day)."""
    try:
        year_str, month_str = month.split("-")
        year, month_num = int(year_str), int(month_str)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"month must be 'YYYY-MM', got {month!r}") from None
    return {"month": month, "weeks": weeks_in_month(year, month_num)}
