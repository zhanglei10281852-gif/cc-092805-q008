"""墓位迁移案件接口模型。"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ConsentRuleType(str, Enum):
    unanimous = "unanimous"
    majority = "majority"
    threshold = "threshold"
    holder_only = "holder_only"


class ConsentConfigUpsert(BaseModel):
    scope: str = Field(default="global", pattern=r"^(global|project)$")
    scope_key: str = Field(default="", max_length=60)
    rule_type: ConsentRuleType
    threshold: float | None = Field(default=None, ge=0, le=1)
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_rule(self):
        if self.rule_type is ConsentRuleType.threshold and self.threshold is None:
            raise ValueError("threshold 规则必须提供同意比例")
        if self.scope == "project" and not self.scope_key:
            raise ValueError("项目级配置必须提供 scope_key（项目编号）")
        return self


class RelocationCaseCreate(BaseModel):
    project_code: str = Field(min_length=2, max_length=60)
    plot_code: str = Field(min_length=2, max_length=80)
    created_by: str = Field(min_length=2, max_length=80)


class RelocationFileCreate(BaseModel):
    kind: str = Field(pattern=r"^(relationship_proof|consent_letter|judicial_notice|site_record|handover_receipt|completion_report|closure_record|other)$")
    file_code: str = Field(min_length=2, max_length=120)
    title: str = Field(min_length=2, max_length=200)
    sha256: str = Field(min_length=12, max_length=128)
    size_bytes: int = Field(default=0, ge=0, le=10_000_000_000)
    content_type: str = Field(default="application/octet-stream", max_length=120)
    uploaded_by: str = Field(min_length=2, max_length=80)


class KinCreate(BaseModel):
    relative_name: str = Field(min_length=2, max_length=120)
    relative_identity: str = Field(min_length=4, max_length=80)
    relationship: str = Field(min_length=2, max_length=40)
    contact: str = Field(default="", max_length=80)
    proof_file_code: str | None = Field(default=None, max_length=120)
    registered_by: str = Field(default="case-staff", min_length=2, max_length=80)


class HoldCreate(BaseModel):
    hold_type: str = Field(pattern=r"^(judicial_suspension|identity_dispute)$")
    kin_id: int | None = Field(default=None, gt=0)
    reference: str = Field(min_length=2, max_length=120)
    file_code: str | None = Field(default=None, max_length=120)
    reason: str = Field(default="", max_length=1000)
    raised_by: str = Field(min_length=2, max_length=80)


class HoldLift(BaseModel):
    lifted_by: str = Field(min_length=2, max_length=80)
    note: str = Field(default="", max_length=1000)


class KinDispute(BaseModel):
    raised_by: str = Field(min_length=2, max_length=80)
    reason: str = Field(min_length=2, max_length=1000)


class OpinionCreate(BaseModel):
    kin_id: int = Field(gt=0)
    decision: str = Field(pattern=r"^(consent|object|abstain)$")
    file_code: str | None = Field(default=None, max_length=120)
    collected_by: str = Field(min_length=2, max_length=80)
    allow_duplicate: bool = False


class ConsentReview(BaseModel):
    actor: str = Field(min_length=2, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=120)


class SiteConfirm(BaseModel):
    new_plot_code: str = Field(min_length=2, max_length=80)
    file_code: str | None = Field(default=None, max_length=120)
    actor: str = Field(min_length=2, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=120)


class StageComplete(BaseModel):
    file_code: str | None = Field(default=None, max_length=120)
    actor: str = Field(min_length=2, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=120)


class WithdrawRequest(BaseModel):
    actor: str = Field(min_length=2, max_length=80)
    reason: str = Field(min_length=2, max_length=500)


class BatchCheckRequest(BaseModel):
    project_code: str = Field(min_length=2, max_length=60)
