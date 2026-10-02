"""墓位迁移案件领域测试。"""
from __future__ import annotations


def make_right(client, plot_code: str = "OLD-001", holder: str = "ID-H-001") -> dict:
    response = client.post("/api/mortuary/burial-rights?actor=cemetery-clerk", json={
        "plot_code": plot_code, "holder_name": "权属人", "holder_identity": holder,
        "starts_on": "2020-01-01", "expires_on": "2040-01-01", "case_id": None,
    })
    assert response.status_code == 201, response.text
    return response.json()


def set_rule(client, rule_type: str = "unanimous", threshold: float | None = None, project: str | None = None) -> None:
    body: dict = {"rule_type": rule_type}
    if threshold is not None:
        body["threshold"] = threshold
    if project is not None:
        body["scope"] = "project"
        body["scope_key"] = project
    response = client.put("/api/mortuary/relocations/consent-config?actor=admin", json=body)
    assert response.status_code == 200, response.text


def open_case(client, plot_code: str = "OLD-001", project: str = "PARK-2026", holder: str = "ID-H-001") -> dict:
    make_right(client, plot_code, holder)
    response = client.post("/api/mortuary/relocations/cases", json={
        "project_code": project, "plot_code": plot_code, "created_by": "relocation-clerk",
    })
    assert response.status_code == 201, response.text
    return response.json()


def add_file(client, case_id: int, code: str, kind: str) -> dict:
    response = client.post(f"/api/mortuary/relocations/cases/{case_id}/files", json={
        "kind": kind, "file_code": code, "title": code, "sha256": "a" * 64,
        "size_bytes": 10, "uploaded_by": "case-staff",
    })
    assert response.status_code == 201, response.text
    return response.json()


def add_kin(client, case_id: int, identity: str, proof_code: str, name: str = "亲属") -> dict:
    response = client.post(f"/api/mortuary/relocations/cases/{case_id}/kin", json={
        "relative_name": name, "relative_identity": identity, "relationship": "子女",
        "contact": "13800000000", "proof_file_code": proof_code, "registered_by": "case-staff",
    })
    assert response.status_code == 201, response.text
    return response.json()


def consent(client, case_id: int, kin_id: int, decision: str = "consent", key_file: str | None = None, allow_duplicate: bool = False) -> int:
    body: dict = {"kin_id": kin_id, "decision": decision, "collected_by": "case-staff", "allow_duplicate": allow_duplicate}
    if key_file:
        body["file_code"] = key_file
    response = client.post(f"/api/mortuary/relocations/cases/{case_id}/opinions", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def register_consenting_kin(client, case_id: int, identity: str, proof: str, letter: str | None = None, **kwargs) -> dict:
    add_file(client, case_id, proof, "relationship_proof")
    kin = add_kin(client, case_id, identity, proof)
    if letter:
        add_file(client, case_id, letter, "consent_letter")
    consent(client, case_id, kin["id"], "consent", letter, **kwargs)
    return kin


def review(client, case_id: int, key: str = "review-key-0001"):
    return client.post(f"/api/mortuary/relocations/cases/{case_id}/consent-review",
                       json={"actor": "reviewer", "idempotency_key": key})


def test_full_flow_freezes_right_version_and_follows_stage_order(client):
    case = open_case(client)
    assert case["right_version"] == 1
    assert case["right_snapshot"]["plot_code"] == "OLD-001"
    snapshot_hash = case["snapshot_hash"]

    # 权属随后续期，迁移案件仍冻结在申请时版本。
    renewed = client.post(f"/api/mortuary/burial-rights/{case['right_id']}/renew",
                          json={"years": 5, "handled_by": "cemetery-clerk", "payment_reference": "PAY-RELOC-001"})
    assert renewed.status_code == 200

    register_consenting_kin(client, case["id"], "ID-K-1", "PROOF-1", "LETTER-1")
    approved = review(client, case["id"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "consent_approved"

    # 未通过同意审定前不能选址（这里已通过）；阶段必须按顺序，先跳过交接直接施工应失败。
    add_file(client, case["id"], "SITE-1", "site_record")
    site = client.post(f"/api/mortuary/relocations/cases/{case['id']}/site-confirm", json={
        "new_plot_code": "NEW-101", "file_code": "SITE-1", "actor": "planner", "idempotency_key": "site-key-0001"})
    assert site.status_code == 200 and site.json()["status"] == "site_selected"

    out_of_order = client.post(f"/api/mortuary/relocations/cases/{case['id']}/construction-complete", json={
        "file_code": None, "actor": "worker", "idempotency_key": "construction-skip"})
    assert out_of_order.status_code == 409

    add_file(client, case["id"], "HANDOVER-1", "handover_receipt")
    handover = client.post(f"/api/mortuary/relocations/cases/{case['id']}/remains-handover", json={
        "file_code": "HANDOVER-1", "actor": "worker", "idempotency_key": "handover-key-0001"})
    assert handover.status_code == 200 and handover.json()["status"] == "remains_handed_over"

    # 交接幂等重放。
    replay = client.post(f"/api/mortuary/relocations/cases/{case['id']}/remains-handover", json={
        "file_code": "HANDOVER-1", "actor": "worker", "idempotency_key": "handover-key-0001"})
    assert replay.status_code == 200 and replay.json()["status"] == "remains_handed_over"

    add_file(client, case["id"], "DONE-1", "completion_report")
    construction = client.post(f"/api/mortuary/relocations/cases/{case['id']}/construction-complete", json={
        "file_code": "DONE-1", "actor": "worker", "idempotency_key": "construction-key-0001"})
    assert construction.status_code == 200 and construction.json()["status"] == "construction_completed"

    add_file(client, case["id"], "CLOSE-1", "closure_record")
    closed = client.post(f"/api/mortuary/relocations/cases/{case['id']}/old-plot-close", json={
        "file_code": "CLOSE-1", "actor": "manager", "idempotency_key": "close-key-0001"})
    assert closed.status_code == 200 and closed.json()["status"] == "old_plot_closed"

    detail = client.get(f"/api/mortuary/relocations/cases/{case['id']}").json()
    assert detail["snapshot_hash"] == snapshot_hash
    assert [d["point"] for d in detail["decisions"]] == [
        "consent_review", "site_confirm", "remains_handover", "construction_complete", "old_plot_close"]
    types = [event["event_type"] for event in detail["timeline"]]
    assert types == ["relocation.applied", "kin.registered", "kin.opinion_collected", "consent.approved",
                     "site.confirmed", "remains_handover.completed", "construction_complete.completed",
                     "old_plot_close.completed"]


def test_kin_without_relationship_proof_rejected(client):
    case = open_case(client, "OLD-P", "PROJ-P")
    response = client.post(f"/api/mortuary/relocations/cases/{case['id']}/kin", json={
        "relative_name": "无证明亲属", "relative_identity": "ID-X", "relationship": "子女",
        "contact": "", "proof_file_code": None})
    assert response.status_code == 422


def test_unanimous_rule_object_blocks_and_decision_is_persisted(client):
    case = open_case(client, "OLD-U", "PROJ-U")
    register_consenting_kin(client, case["id"], "ID-U-1", "PROOF-U1")
    add_file(client, case["id"], "PROOF-U2", "relationship_proof")
    kin2 = add_kin(client, case["id"], "ID-U-2", "PROOF-U2")
    consent(client, case["id"], kin2["id"], "object")
    rejected = review(client, case["id"], "review-unanimous")
    assert rejected.status_code == 409
    detail = client.get(f"/api/mortuary/relocations/cases/{case['id']}").json()
    assert detail["status"] == "applied"
    decision = detail["decisions"][-1]
    assert decision["result"] == "rejected"
    assert decision["basis"]["objects"][0]["relative_identity"] == "ID-U-2"


def test_majority_and_threshold_rules(client):
    set_rule(client, "majority")
    case = open_case(client, "OLD-M", "PROJ-M")
    register_consenting_kin(client, case["id"], "ID-M-1", "PROOF-M1")
    add_file(client, case["id"], "PROOF-M2", "relationship_proof")
    kin2 = add_kin(client, case["id"], "ID-M-2", "PROOF-M2")
    consent(client, case["id"], kin2["id"], "abstain")
    # 两人中仅一人同意，多数规则不通过。
    assert review(client, case["id"], "review-majority-1").status_code == 409

    # 同意规则在申请时冻结：比例规则对预先配置好的新项目案件生效。
    set_rule(client, "threshold", threshold=0.5, project="PROJ-T")
    case_t = open_case(client, "OLD-T", "PROJ-T")
    register_consenting_kin(client, case_t["id"], "ID-T-1", "PROOF-T1")
    add_file(client, case_t["id"], "PROOF-T2", "relationship_proof")
    kin_t2 = add_kin(client, case_t["id"], "ID-T-2", "PROOF-T2")
    consent(client, case_t["id"], kin_t2["id"], "abstain")
    approved = review(client, case_t["id"], "review-threshold-1")
    assert approved.status_code == 200
    assert approved.json()["consent_rule"]["rule_type"] == "threshold"


def test_holder_only_rule(client):
    set_rule(client, "holder_only")
    case = open_case(client, "OLD-H", "PROJ-H", holder="ID-HOLDER")
    register_consenting_kin(client, case["id"], "ID-HOLDER", "PROOF-H1")
    assert review(client, case["id"], "review-holder-1").status_code == 200


def test_judicial_suspension_blocks_consent_and_stages_then_resumes(client):
    case = open_case(client, "OLD-J", "PROJ-J")
    register_consenting_kin(client, case["id"], "ID-J-1", "PROOF-J1")

    add_file(client, case["id"], "COURT-1", "judicial_notice")
    hold = client.post(f"/api/mortuary/relocations/cases/{case['id']}/holds", json={
        "hold_type": "judicial_suspension", "kin_id": None, "reference": "COURT-2026-001",
        "file_code": "COURT-1", "reason": "法院查封", "raised_by": "legal-staff"})
    assert hold.status_code == 201
    hold_id = hold.json()["id"]

    blocked = review(client, case["id"], "review-blocked")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["context"]["holds"][0]["hold_type"] == "judicial_suspension"

    # 阻断决定已留痕；同一键不能重放为通过。
    replay = review(client, case["id"], "review-blocked")
    assert replay.status_code == 409 and "曾被阻断" in replay.json()["error"]["message"]

    lift = client.post(f"/api/mortuary/relocations/cases/{case['id']}/holds/{hold_id}/lift",
                       json={"lifted_by": "judge-liaison", "note": "暂停解除"})
    assert lift.status_code == 200 and lift.json()["status"] == "lifted"

    approved = review(client, case["id"], "review-after-lift")
    assert approved.status_code == 200

    # 阶段进行中再次暂停会阻断施工，阻断记录持久化后解除，用新键可继续。
    add_file(client, case["id"], "SITE-J", "site_record")
    site = client.post(f"/api/mortuary/relocations/cases/{case['id']}/site-confirm", json={
        "new_plot_code": "NEW-J1", "file_code": "SITE-J", "actor": "planner", "idempotency_key": "site-judicial-1"})
    assert site.status_code == 200
    add_file(client, case["id"], "HAND-J", "handover_receipt")
    client.post(f"/api/mortuary/relocations/cases/{case['id']}/holds", json={
        "hold_type": "judicial_suspension", "reference": "COURT-2026-002", "file_code": None,
        "reason": "二次暂停", "raised_by": "legal-staff"})
    blocked_handover = client.post(f"/api/mortuary/relocations/cases/{case['id']}/remains-handover", json={
        "file_code": "HAND-J", "actor": "worker", "idempotency_key": "handover-blocked"})
    assert blocked_handover.status_code == 409
    hold2 = client.get(f"/api/mortuary/relocations/cases/{case['id']}").json()["holds"][-1]["id"]
    client.post(f"/api/mortuary/relocations/cases/{case['id']}/holds/{hold2}/lift",
                json={"lifted_by": "judge-liaison", "note": "解除"})
    resumed = client.post(f"/api/mortuary/relocations/cases/{case['id']}/remains-handover", json={
        "file_code": "HAND-J", "actor": "worker", "idempotency_key": "handover-blocked"})
    # 被阻断占用的键不能复用，换新键成功。
    assert resumed.status_code == 409 and "曾被阻断" in resumed.json()["error"]["message"]
    resumed_new = client.post(f"/api/mortuary/relocations/cases/{case['id']}/remains-handover", json={
        "file_code": "HAND-J", "actor": "worker", "idempotency_key": "handover-resume"})
    assert resumed_new.status_code == 200


def test_identity_dispute_excludes_kin_and_blocks(client):
    case = open_case(client, "OLD-D", "PROJ-D")
    register_consenting_kin(client, case["id"], "ID-D-1", "PROOF-D1")
    add_file(client, case["id"], "PROOF-D2", "relationship_proof")
    kin2 = add_kin(client, case["id"], "ID-D-2", "PROOF-D2")
    consent(client, case["id"], kin2["id"], "consent")
    dispute = client.post(f"/api/mortuary/relocations/cases/{case['id']}/kin/{kin2['id']}/dispute",
                          json={"raised_by": "manager", "reason": "身份证件疑似伪造"})
    assert dispute.status_code == 200 and dispute.json()["hold_type"] == "identity_dispute"
    detail = client.get(f"/api/mortuary/relocations/cases/{case['id']}").json()
    assert next(kin for kin in detail["kin"] if kin["id"] == kin2["id"])["status"] == "disputed"

    # 身份争议以生效暂停形式阻断审定。
    blocked = review(client, case["id"], "review-dispute")
    assert blocked.status_code == 409

    hold_id = client.get(f"/api/mortuary/relocations/cases/{case['id']}").json()["holds"][-1]["id"]
    client.post(f"/api/mortuary/relocations/cases/{case['id']}/holds/{hold_id}/lift",
                json={"lifted_by": "manager", "note": "公安核实无误"})
    approved = review(client, case["id"], "review-dispute-ok")
    assert approved.status_code == 200
    detail = client.get(f"/api/mortuary/relocations/cases/{case['id']}").json()
    assert all(kin["status"] == "registered" for kin in detail["kin"])


def test_withdraw_keeps_all_materials(client):
    case = open_case(client, "OLD-W", "PROJ-W")
    register_consenting_kin(client, case["id"], "ID-W-1", "PROOF-W1", "LETTER-W1")
    assert review(client, case["id"], "review-w").status_code == 200
    withdrawn = client.post(f"/api/mortuary/relocations/cases/{case['id']}/withdraw",
                            json={"actor": "manager", "reason": "家属协商暂缓"})
    assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "withdrawn"

    detail = client.get(f"/api/mortuary/relocations/cases/{case['id']}").json()
    assert len(detail["kin"]) == 1
    assert len(detail["opinions"]) == 1
    assert len(detail["files"]) == 2
    assert {d["point"] for d in detail["decisions"]} >= {"consent_review", "withdraw"}
    # 撤回后不能再收材料或意见。
    assert client.post(f"/api/mortuary/relocations/cases/{case['id']}/files", json={
        "kind": "other", "file_code": "AFTER", "title": "撤回后", "sha256": "b" * 64,
        "uploaded_by": "case-staff"}).status_code == 409


def test_decision_dependencies_are_queryable(client):
    case = open_case(client, "OLD-Q", "PROJ-Q")
    kin = register_consenting_kin(client, case["id"], "ID-Q-1", "PROOF-Q1", "LETTER-Q1")
    review_response = review(client, case["id"], "review-q")
    assert review_response.status_code == 200
    decision_id = client.get(f"/api/mortuary/relocations/cases/{case['id']}").json()["decisions"][0]["id"]
    response = client.get(f"/api/mortuary/relocations/cases/{case['id']}/decisions/{decision_id}")
    assert response.status_code == 200
    deps = response.json()["dependencies"]
    assert deps["right_version"] == 1
    assert deps["right_snapshot"]["holder_identity"]
    assert deps["kin"][0]["kin_id"] == kin["id"]
    assert deps["kin"][0]["proof_file_id"]
    assert {f["kind"] for f in deps["files"]} == {"relationship_proof"}
    assert deps["opinions"][0]["decision"] == "consent"


def test_site_reset_allows_reselect_and_releases_plot(client):
    case_a = open_case(client, "OLD-R1", "PROJ-R")
    register_consenting_kin(client, case_a["id"], "ID-R-1", "PROOF-R1")
    assert review(client, case_a["id"], "review-r1").status_code == 200
    add_file(client, case_a["id"], "SITE-R1", "site_record")
    assert client.post(f"/api/mortuary/relocations/cases/{case_a['id']}/site-confirm", json={
        "new_plot_code": "NEW-R", "file_code": "SITE-R1", "actor": "planner", "idempotency_key": "site-reset-r1"}).status_code == 200
    reset = client.post(f"/api/mortuary/relocations/cases/{case_a['id']}/site-reset",
                        json={"actor": "planner", "reason": "地质问题重新选址"})
    assert reset.status_code == 200 and reset.json()["status"] == "consent_approved"
    assert reset.json()["new_plot_code"] is None

    # 释放出的新墓位可被另一案件占用。
    case_b = open_case(client, "OLD-R2", "PROJ-R", holder="ID-H-R2")
    register_consenting_kin(client, case_b["id"], "ID-R-2", "PROOF-R2")
    assert review(client, case_b["id"], "review-r2").status_code == 200
    add_file(client, case_b["id"], "SITE-R2", "site_record")
    taken = client.post(f"/api/mortuary/relocations/cases/{case_b['id']}/site-confirm", json={
        "new_plot_code": "NEW-R", "file_code": "SITE-R2", "actor": "planner", "idempotency_key": "site-reset-r2"})
    assert taken.status_code == 200


def test_batch_check_flags_duplicate_authorization_and_plot_conflict(client):
    set_rule(client, "majority")
    case_a = open_case(client, "OLD-B1", "BATCH-1")
    case_b = open_case(client, "OLD-B2", "BATCH-1", holder="ID-H-B2")
    add_file(client, case_a["id"], "PROOF-B", "relationship_proof")
    kin_a = add_kin(client, case_a["id"], "ID-SAME", "PROOF-B", name="张重")
    # 第二案件复用同一亲属身份，需先各自有关系证明。
    add_file(client, case_b["id"], "PROOF-B2", "relationship_proof")
    kin_b = add_kin(client, case_b["id"], "ID-SAME", "PROOF-B2", name="张重")

    consent(client, case_a["id"], kin_a["id"])
    # 未确认重复时拒绝。
    response = client.post(f"/api/mortuary/relocations/cases/{case_b['id']}/opinions",
                           json={"kin_id": kin_b["id"], "decision": "consent", "collected_by": "case-staff"})
    assert response.status_code == 409
    assert response.json()["error"]["context"]["other_cases"][0]["plot_code"] == "OLD-B1"
    consent(client, case_b["id"], kin_b["id"], allow_duplicate=True)

    # 两个案件各自凑够多数并选址到同一新墓位。
    for case, suffix, key in ((case_a, "A", "b1"), (case_b, "B", "b2")):
        add_file(client, case["id"], f"PROOF-OTHER-{suffix}", "relationship_proof")
        other = add_kin(client, case["id"], f"ID-OTHER-{suffix}", f"PROOF-OTHER-{suffix}")
        consent(client, case["id"], other["id"], "consent")
        assert review(client, case["id"], f"review-{key}").status_code == 200
        add_file(client, case["id"], f"SITE-{suffix}", "site_record")

    assert client.post(f"/api/mortuary/relocations/cases/{case_a['id']}/site-confirm", json={
        "new_plot_code": "NEW-DUP", "file_code": "SITE-A", "actor": "planner", "idempotency_key": "site-batch-b1"}).status_code == 200
    conflict = client.post(f"/api/mortuary/relocations/cases/{case_b['id']}/site-confirm", json={
        "new_plot_code": "NEW-DUP", "file_code": "SITE-B", "actor": "planner", "idempotency_key": "site-batch-b2"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["context"]["case_id"] == case_a["id"]

    report = client.post("/api/mortuary/relocations/batch-check", json={"project_code": "BATCH-1"})
    assert report.status_code == 200
    data = report.json()
    dup = data["duplicate_authorizations"][0]
    assert dup["relative_identity"] == "ID-SAME"
    assert dup["needs_confirmation"] is True
    assert data["new_plot_conflicts"] == []  # 唯一约束在写入时即阻止冲突落库


def test_batch_check_reports_blocked_cases(client):
    case = open_case(client, "OLD-Z", "BATCH-Z")
    register_consenting_kin(client, case["id"], "ID-Z-1", "PROOF-Z1")
    client.post(f"/api/mortuary/relocations/cases/{case['id']}/holds", json={
        "hold_type": "judicial_suspension", "reference": "COURT-Z", "file_code": None,
        "reason": "暂停", "raised_by": "legal"})
    report = client.post("/api/mortuary/relocations/batch-check", json={"project_code": "BATCH-Z"}).json()
    assert report["blocked_cases"] == [case["id"]]
