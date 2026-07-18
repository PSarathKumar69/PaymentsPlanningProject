"""Request/response schemas for the model1/model2/model3 routers."""
from typing import Any

from pydantic import BaseModel


class GeneratePlanRequest(BaseModel):
    available_funds: float


class GeneratePlanResponse(BaseModel):
    available_funds: float
    guaranteed_total: float
    escalation: bool
    escalation_shortfall: float
    bucket_need: dict[str, float] | None = None  # model 3 only
    allocations: list[dict[str, Any]]


class MinimumFundsRequiredResponse(BaseModel):
    total: float
    breakdown: list[dict[str, Any]]
    must_pay_with_outstanding_balance: list[str]
