from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

_ID_MAX_LEN = 30


class ReadinessCheckRequest(BaseModel):
    machine_id: str = Field(..., min_length=1, max_length=_ID_MAX_LEN)
    order_id: Optional[str] = Field(None, min_length=1, max_length=_ID_MAX_LEN)


class QualityEventPayload(BaseModel):
    machine_id: str = Field(..., min_length=1, max_length=_ID_MAX_LEN)
    order_id: str = Field(..., min_length=1, max_length=_ID_MAX_LEN)
    timestamp: str
    units_inspected: int = Field(..., ge=1, le=100_000)
    units_defective: int = Field(..., ge=0, le=100_000)
    defect_type: Optional[str] = Field(None, max_length=50)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v)
        except ValueError as exc:
            raise ValueError("timestamp must look like 'YYYY-MM-DD HH:MM:SS'") from exc
        return v

    @model_validator(mode="after")
    def validate_defect_count(self) -> "QualityEventPayload":
        if self.units_defective > self.units_inspected:
            raise ValueError("units_defective cannot exceed units_inspected")
        return self

    @property
    def defect_rate(self) -> float:
        return self.units_defective / self.units_inspected if self.units_inspected else 0.0


class ApprovalPayload(BaseModel):
    approved: bool
    notes: Optional[str] = Field(None, max_length=500)


class VerifyPayload(BaseModel):
    units_inspected: int = Field(..., ge=1, le=100_000)
    units_defective: int = Field(..., ge=0, le=100_000)

    @model_validator(mode="after")
    def validate_defect_count(self) -> "VerifyPayload":
        if self.units_defective > self.units_inspected:
            raise ValueError("units_defective cannot exceed units_inspected")
        return self
