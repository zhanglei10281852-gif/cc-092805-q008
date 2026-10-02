from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class KinStance(str, Enum):
    approve = "approve"
    object = "object"


class HoldType(str, Enum):
    judicial = "judicial"
    identity_dispute = "identity_dispute"
    payment_dispute = "payment_dispute"


class ConsentRuleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=120)
    min_approvals: int = Field(default=1, ge=1, le=100)
    max_objections: int = Field(default=0, ge=0, le=100)
    require_unanimous: bool = False
    created_by: str = Field(min_length=2, max_length=80)


class RelocationCaseCreate(BaseModel):
    case_no: str = Field(min_length=3, max_length=80)
    source_plot_code: str = Field(min_length=2, max_length=80)
    consent_rule_code: str = Field(min_length=2, max_length=40)
    reason: str = Field(min_length=2, max_length=500)
    created_by: str = Field(min_length=2, max_length=80)


class KinOpinionCreate(BaseModel):
    kin_name: str = Field(min_length=2, max_length=120)
    kin_identity: str = Field(min_length=4, max_length=80)
    relationship: str = Field(min_length=2, max_length=60)
    proof_document_no: str = Field(min_length=4, max_length=120)
    stance: KinStance
    note: str = Field(default="", max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=120)
    submitted_by: str = Field(min_length=2, max_length=80)


class ProofVerify(BaseModel):
    verified_by: str = Field(min_length=2, max_length=80)


class ConsentEvaluate(BaseModel):
    handled_by: str = Field(min_length=2, max_length=80)


class RefreezeRight(BaseModel):
    reason: str = Field(min_length=2, max_length=500)
    handled_by: str = Field(min_length=2, max_length=80)


class HoldCreate(BaseModel):
    hold_type: HoldType
    reason: str = Field(min_length=2, max_length=500)
    raised_by: str = Field(min_length=2, max_length=80)


class HoldRelease(BaseModel):
    released_by: str = Field(min_length=2, max_length=80)
    note: str = Field(default="", max_length=500)


class NewPlotConfirm(BaseModel):
    new_plot_code: str = Field(min_length=2, max_length=80)
    reference: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=120)
    recorded_by: str = Field(min_length=2, max_length=80)


class StepRecord(BaseModel):
    reference: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=120)
    recorded_by: str = Field(min_length=2, max_length=80)


class CaseWithdraw(BaseModel):
    reason: str = Field(min_length=2, max_length=500)
    withdrawn_by: str = Field(min_length=2, max_length=80)


class BatchCreate(BaseModel):
    batch_no: str = Field(min_length=3, max_length=80)
    name: str = Field(min_length=2, max_length=120)
    created_by: str = Field(min_length=2, max_length=80)


class BatchItemAdd(BaseModel):
    case_id: int = Field(gt=0)
    added_by: str = Field(min_length=2, max_length=80)
