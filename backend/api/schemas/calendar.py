"""Request/response schema for GET /calendar/weeks-in-month.

CLAUDE.md rule 7: the number of weeks in a month (W1-W5) must be derived
from the calendar, not hardcoded to always be 5. This endpoint is what lets
the frontend build the Assigned-Week dropdown's option list without
hardcoding — backend/weekly_planning/calendar_utils.py::weeks_in_month()
already computes this and is already relied on server-side
(backend/configuration/vendor_edits.py's assigned_week validation); this
just exposes the same function over HTTP.
"""
from pydantic import BaseModel


class WeeksInMonthResponse(BaseModel):
    month: str  # "YYYY-MM", echoed back from the request
    weeks: int  # 4 or 5 — how many W1-W5 tags are valid for this month
