"""Request/response schemas for the payments router."""
from pydantic import BaseModel


class LogPaymentRequest(BaseModel):
    """payment_date/week are no longer client-supplied — log_payment()
    determines both itself from the server's real current date."""

    vendor_id: int
    amount: float


class LogPaymentResponse(BaseModel):
    payment_status: str
