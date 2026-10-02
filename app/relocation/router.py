from fastapi import APIRouter, Query

from app.relocation.schemas import (
    BatchCreate,
    BatchItemAdd,
    CaseWithdraw,
    ConsentEvaluate,
    ConsentRuleCreate,
    HoldCreate,
    HoldRelease,
    KinOpinionCreate,
    NewPlotConfirm,
    ProofVerify,
    RefreezeRight,
    RelocationCaseCreate,
    StepRecord,
)
from app.relocation.service import RelocationService

router = APIRouter(prefix="/api/relocation", tags=["relocation"])


@router.post("/consent-rules", status_code=201)
def create_rule(payload: ConsentRuleCreate) -> dict:
    return RelocationService().create_rule(payload.model_dump(mode="json"))


@router.get("/consent-rules")
def list_rules() -> list[dict]:
    return RelocationService().list_rules()


@router.post("/cases", status_code=201)
def create_case(payload: RelocationCaseCreate) -> dict:
    return RelocationService().create_case(payload.model_dump(mode="json"))


@router.get("/cases")
def list_cases(status: str | None = None, limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return RelocationService().list_cases(status, limit)


@router.get("/cases/{case_id}")
def get_case(case_id: int) -> dict:
    return RelocationService().get_case(case_id)


@router.post("/cases/{case_id}/opinions", status_code=201)
def submit_opinion(case_id: int, payload: KinOpinionCreate) -> dict:
    return RelocationService().submit_opinion(case_id, payload.model_dump(mode="json"))


@router.post("/opinions/{opinion_id}/verify-proof")
def verify_proof(opinion_id: int, payload: ProofVerify) -> dict:
    return RelocationService().verify_proof(opinion_id, payload.model_dump(mode="json"))


@router.post("/cases/{case_id}/evaluate-consent")
def evaluate_consent(case_id: int, payload: ConsentEvaluate) -> dict:
    return RelocationService().evaluate_consent(case_id, payload.model_dump(mode="json"))


@router.get("/cases/{case_id}/decisions")
def list_decisions(case_id: int) -> list[dict]:
    return RelocationService().list_decisions(case_id)


@router.post("/cases/{case_id}/refreeze-right")
def refreeze_right(case_id: int, payload: RefreezeRight) -> dict:
    return RelocationService().refreeze_right(case_id, payload.model_dump(mode="json"))


@router.post("/cases/{case_id}/holds", status_code=201)
def raise_hold(case_id: int, payload: HoldCreate) -> dict:
    return RelocationService().raise_hold(case_id, payload.model_dump(mode="json"))


@router.post("/holds/{hold_id}/release")
def release_hold(hold_id: int, payload: HoldRelease) -> dict:
    return RelocationService().release_hold(hold_id, payload.model_dump(mode="json"))


@router.post("/cases/{case_id}/confirm-new-plot", status_code=201)
def confirm_new_plot(case_id: int, payload: NewPlotConfirm) -> dict:
    return RelocationService().confirm_new_plot(case_id, payload.model_dump(mode="json"))


@router.post("/cases/{case_id}/remains-handover", status_code=201)
def record_remains_handover(case_id: int, payload: StepRecord) -> dict:
    return RelocationService().record_remains_handover(case_id, payload.model_dump(mode="json"))


@router.post("/cases/{case_id}/construction-complete", status_code=201)
def record_construction_complete(case_id: int, payload: StepRecord) -> dict:
    return RelocationService().record_construction_completed(case_id, payload.model_dump(mode="json"))


@router.post("/cases/{case_id}/close-old-plot", status_code=201)
def close_old_plot(case_id: int, payload: StepRecord) -> dict:
    return RelocationService().close_old_plot(case_id, payload.model_dump(mode="json"))


@router.post("/cases/{case_id}/withdraw")
def withdraw_case(case_id: int, payload: CaseWithdraw) -> dict:
    return RelocationService().withdraw(case_id, payload.model_dump(mode="json"))


@router.post("/batches", status_code=201)
def create_batch(payload: BatchCreate) -> dict:
    return RelocationService().create_batch(payload.model_dump(mode="json"))


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int) -> dict:
    return RelocationService().get_batch(batch_id)


@router.post("/batches/{batch_id}/items", status_code=201)
def add_batch_item(batch_id: int, payload: BatchItemAdd) -> dict:
    return RelocationService().add_batch_item(batch_id, payload.model_dump(mode="json"))


@router.get("/batches/{batch_id}/conflicts")
def batch_conflicts(batch_id: int) -> dict:
    return RelocationService().batch_conflicts(batch_id)
