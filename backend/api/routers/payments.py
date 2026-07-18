"""Payments router — daily manual payment entry."""
from fastapi import APIRouter

from backend.shared.payment_logging import log_payment

from ..schemas.payments import LogPaymentRequest, LogPaymentResponse

router = APIRouter(tags=["payments"])


@router.post("/payments", response_model=LogPaymentResponse)
def post_payment(body: LogPaymentRequest):
    status = log_payment(body.vendor_id, body.amount, session=None)
    return {"payment_status": status.value}
