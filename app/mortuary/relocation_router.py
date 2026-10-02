"""墓位迁移案件接口。"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.mortuary.relocation_schemas import (
    BatchCheckRequest,
    ConsentConfigUpsert,
    ConsentReview,
    HoldCreate,
    HoldLift,
    KinCreate,
    KinDispute,
    OpinionCreate,
    RelocationCaseCreate,
    RelocationFileCreate,
    SiteConfirm,
    StageComplete,
    WithdrawRequest,
)
from app.mortuary.relocation_service import RelocationService

router = APIRouter(prefix="/api/mortuary/relocations", tags=["mortuary-relocation"])


@router.put("/consent-config")
def upsert_consent_config(payload: ConsentConfigUpsert, actor: str = Query(min_length=2, max_length=80)) -> dict:
    return RelocationService().upsert_config(payload.model_dump(), actor)


@router.get("/consent-config")
def get_consent_config(scope: str = Query(default="global", pattern=r"^(global|project)$"),
                       scope_key: str = Query(default="", max_length=60)) -> dict:
    return RelocationService().get_config(scope, scope_key)


@router.post("/cases", status_code=201)
def create_relocation_case(payload: RelocationCaseCreate) -> dict:
    return RelocationService().create_case(payload.model_dump())


@router.get("/cases")
def list_relocation_cases(project_code: str | None = None, status: str | None = None,
                          limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return RelocationService().list_cases(project_code, status, limit)


@router.get("/cases/{case_id}")
def get_relocation_case(case_id: int) -> dict:
    return RelocationService().get_case(case_id)


@router.post("/cases/{case_id}/files", status_code=201)
def add_file(case_id: int, payload: RelocationFileCreate) -> dict:
    return RelocationService().add_file(case_id, payload.model_dump())


@router.post("/cases/{case_id}/kin", status_code=201)
def register_kin(case_id: int, payload: KinCreate) -> dict:
    return RelocationService().register_kin(case_id, payload.model_dump())


@router.post("/cases/{case_id}/kin/{kin_id}/dispute")
def dispute_kin(case_id: int, kin_id: int, payload: KinDispute) -> dict:
    return RelocationService().dispute_kin(case_id, kin_id, payload.model_dump())


@router.post("/cases/{case_id}/opinions", status_code=201)
def add_opinion(case_id: int, payload: OpinionCreate) -> dict:
    return RelocationService().add_opinion(case_id, payload.model_dump())


@router.post("/cases/{case_id}/holds", status_code=201)
def add_hold(case_id: int, payload: HoldCreate) -> dict:
    return RelocationService().add_hold(case_id, payload.model_dump())


@router.post("/cases/{case_id}/holds/{hold_id}/lift")
def lift_hold(case_id: int, hold_id: int, payload: HoldLift) -> dict:
    return RelocationService().lift_hold(case_id, hold_id, payload.model_dump())


@router.post("/cases/{case_id}/consent-review")
def review_consent(case_id: int, payload: ConsentReview) -> dict:
    return RelocationService().review_consent(case_id, payload.model_dump())


@router.post("/cases/{case_id}/site-confirm")
def confirm_site(case_id: int, payload: SiteConfirm) -> dict:
    return RelocationService().confirm_site(case_id, payload.model_dump())


@router.post("/cases/{case_id}/site-reset")
def reset_site(case_id: int, payload: WithdrawRequest) -> dict:
    return RelocationService().reset_site(case_id, payload.model_dump())


@router.post("/cases/{case_id}/remains-handover")
def remains_handover(case_id: int, payload: StageComplete) -> dict:
    return RelocationService().complete_stage(case_id, "remains_handover", payload.model_dump())


@router.post("/cases/{case_id}/construction-complete")
def construction_complete(case_id: int, payload: StageComplete) -> dict:
    return RelocationService().complete_stage(case_id, "construction_complete", payload.model_dump())


@router.post("/cases/{case_id}/old-plot-close")
def old_plot_close(case_id: int, payload: StageComplete) -> dict:
    return RelocationService().complete_stage(case_id, "old_plot_close", payload.model_dump())


@router.post("/cases/{case_id}/withdraw")
def withdraw(case_id: int, payload: WithdrawRequest) -> dict:
    return RelocationService().withdraw(case_id, payload.model_dump())


@router.get("/cases/{case_id}/decisions/{decision_id}")
def get_decision(case_id: int, decision_id: int) -> dict:
    return RelocationService().get_decision(case_id, decision_id)


@router.post("/batch-check")
def batch_check(payload: BatchCheckRequest) -> dict:
    return RelocationService().batch_check(payload.project_code)
