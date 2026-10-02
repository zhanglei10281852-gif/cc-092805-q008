from __future__ import annotations


def make_right(client, plot: str = "OLD-A-001", holder: str = "李冬梅") -> dict:
    response = client.post(
        "/api/mortuary/burial-rights?actor=cemetery-clerk",
        json={"plot_code": plot, "holder_name": holder, "holder_identity": f"ID-{plot}", "starts_on": "2020-01-01", "expires_on": "2040-01-01", "case_id": None},
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_rule(client, code: str = "family-default", min_approvals: int = 1, max_objections: int = 0) -> dict:
    response = client.post(
        "/api/relocation/consent-rules",
        json={"code": code, "name": "亲属同意规则", "min_approvals": min_approvals, "max_objections": max_objections, "require_unanimous": False, "created_by": "park-manager"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_case(client, plot: str = "OLD-A-001", case_no: str = "RELO-001", rule: str = "family-default") -> dict:
    response = client.post(
        "/api/relocation/cases",
        json={"case_no": case_no, "source_plot_code": plot, "consent_rule_code": rule, "reason": "园区改造整体迁移", "created_by": "park-manager"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def add_opinion(client, case_id: int, kin_identity: str, stance: str = "approve", key: str | None = None, proof: str = "PROOF-001", verified: bool = True, name: str = "张明") -> dict:
    response = client.post(
        f"/api/relocation/cases/{case_id}/opinions",
        json={
            "kin_name": name,
            "kin_identity": kin_identity,
            "relationship": "子女",
            "proof_document_no": proof,
            "stance": stance,
            "note": "",
            "idempotency_key": key or f"op-{case_id}-{kin_identity}",
            "submitted_by": "case-officer",
        },
    )
    assert response.status_code == 201, response.text
    opinion = response.json()
    if verified:
        check = client.post(f"/api/relocation/opinions/{opinion['id']}/verify-proof", json={"verified_by": "notary-clerk"})
        assert check.status_code == 200, check.text
    return opinion


def approve_case(client, case_id: int) -> dict:
    response = client.post(f"/api/relocation/cases/{case_id}/evaluate-consent", json={"handled_by": "park-manager"})
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "consent_approved", response.text
    return response.json()


def confirm_plot(client, case_id: int, new_plot: str, key: str) -> dict:
    response = client.post(
        f"/api/relocation/cases/{case_id}/confirm-new-plot",
        json={"new_plot_code": new_plot, "reference": "SEL-1", "note": "", "idempotency_key": key, "recorded_by": "site-officer"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_case_freezes_right_version_and_requires_refreeze_after_change(client):
    right = make_right(client)
    make_rule(client)
    case = make_case(client)
    assert case["right_version_frozen"] == 1
    assert case["right_snapshot"]["holder_name"] == "李冬梅"
    assert case["right_snapshot"]["plot_code"] == "OLD-A-001"
    renewed = client.post(f"/api/mortuary/burial-rights/{right['id']}/renew", json={"years": 5, "handled_by": "cemetery-clerk", "payment_reference": "RIGHT-PAY-100"})
    assert renewed.status_code == 200
    add_opinion(client, case["id"], "KIN-001")
    blocked = client.post(f"/api/relocation/cases/{case['id']}/evaluate-consent", json={"handled_by": "park-manager"})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["context"]["current_version"] == 2
    refrozen = client.post(f"/api/relocation/cases/{case['id']}/refreeze-right", json={"reason": "续期完成后重新冻结", "handled_by": "park-manager"})
    assert refrozen.status_code == 200
    assert refrozen.json()["right_version_frozen"] == 2
    decision = approve_case(client, case["id"])
    assert decision["right_version"] == 2


def test_consent_evaluation_uses_verified_opinions_and_records_dependencies(client):
    make_right(client, "OLD-B-001")
    make_rule(client, "two-yes", min_approvals=2, max_objections=0)
    case = make_case(client, "OLD-B-001", "RELO-B-001", "two-yes")
    add_opinion(client, case["id"], "KIN-101", proof="PROOF-A")
    second = add_opinion(client, case["id"], "KIN-102", proof="PROOF-B", verified=False)
    add_opinion(client, case["id"], "KIN-103", stance="object", proof="PROOF-C")
    rejected = client.post(f"/api/relocation/cases/{case['id']}/evaluate-consent", json={"handled_by": "park-manager"})
    assert rejected.status_code == 200
    body = rejected.json()
    assert body["decision"] == "consent_rejected"
    assert body["reasons"]
    assert body["right_version"] == 1
    assert set(body["kin_identities"]) == {"KIN-101", "KIN-103"}
    assert set(body["proof_documents"]) == {"PROOF-A", "PROOF-C"}
    assert body["rule_snapshot"]["min_approvals"] == 2
    add_opinion(client, case["id"], "KIN-103", stance="approve", key="op-revise-103", proof="PROOF-C2")
    verified = client.post(f"/api/relocation/opinions/{second['id']}/verify-proof", json={"verified_by": "notary-clerk"})
    assert verified.status_code == 200
    approved = approve_case(client, case["id"])
    assert len(approved["opinion_ids"]) == 3
    detail = client.get(f"/api/relocation/cases/{case['id']}").json()
    assert detail["status"] == "approved"
    assert len(detail["opinions"]) == 4
    decisions = client.get(f"/api/relocation/cases/{case['id']}/decisions").json()
    assert [item["decision"] for item in decisions] == ["consent_rejected", "consent_approved"]


def test_judicial_hold_and_identity_dispute_block_progress(client):
    make_right(client, "OLD-C-001")
    make_rule(client)
    case = make_case(client, "OLD-C-001", "RELO-C-001")
    add_opinion(client, case["id"], "KIN-201")
    hold = client.post(f"/api/relocation/cases/{case['id']}/holds", json={"hold_type": "judicial", "reason": "法院裁定暂停迁移", "raised_by": "legal-affairs"})
    assert hold.status_code == 201
    blocked = client.post(f"/api/relocation/cases/{case['id']}/evaluate-consent", json={"handled_by": "park-manager"})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["context"]["holds"][0]["hold_type"] == "judicial"
    released = client.post(f"/api/relocation/holds/{hold.json()['id']}/release", json={"released_by": "legal-affairs", "note": "裁定解除"})
    assert released.status_code == 200
    approve_case(client, case["id"])
    confirm_plot(client, case["id"], "NEW-C-901", "plot-c-001")
    dispute = client.post(f"/api/relocation/cases/{case['id']}/holds", json={"hold_type": "identity_dispute", "reason": "亲属身份存疑", "raised_by": "legal-affairs"})
    assert dispute.status_code == 201
    blocked_step = client.post(
        f"/api/relocation/cases/{case['id']}/remains-handover",
        json={"reference": "HANDOVER-1", "note": "", "idempotency_key": "handover-c-001", "recorded_by": "site-officer"},
    )
    assert blocked_step.status_code == 409
    client.post(f"/api/relocation/holds/{dispute.json()['id']}/release", json={"released_by": "legal-affairs", "note": "身份核实完成"})
    handover = client.post(
        f"/api/relocation/cases/{case['id']}/remains-handover",
        json={"reference": "HANDOVER-1", "note": "", "idempotency_key": "handover-c-001", "recorded_by": "site-officer"},
    )
    assert handover.status_code == 201


def test_steps_are_ordered_idempotent_and_close_old_plot(client):
    right = make_right(client, "OLD-D-001")
    make_rule(client)
    case = make_case(client, "OLD-D-001", "RELO-D-001")
    add_opinion(client, case["id"], "KIN-301")
    approve_case(client, case["id"])
    early = client.post(
        f"/api/relocation/cases/{case['id']}/remains-handover",
        json={"reference": "HANDOVER-1", "note": "", "idempotency_key": "handover-d-001", "recorded_by": "site-officer"},
    )
    assert early.status_code == 409
    plot = confirm_plot(client, case["id"], "NEW-D-901", "plot-d-001")
    replay = client.post(
        f"/api/relocation/cases/{case['id']}/confirm-new-plot",
        json={"new_plot_code": "NEW-D-901", "reference": "SEL-1", "note": "", "idempotency_key": "plot-d-001", "recorded_by": "site-officer"},
    )
    assert replay.status_code == 201 and replay.json()["id"] == plot["id"]
    changed = client.post(
        f"/api/relocation/cases/{case['id']}/confirm-new-plot",
        json={"new_plot_code": "NEW-D-902", "reference": "SEL-2", "note": "", "idempotency_key": "plot-d-002", "recorded_by": "site-officer"},
    )
    assert changed.status_code == 409
    skipped = client.post(
        f"/api/relocation/cases/{case['id']}/construction-complete",
        json={"reference": "CONS-1", "note": "", "idempotency_key": "cons-d-001", "recorded_by": "site-officer"},
    )
    assert skipped.status_code == 409
    assert skipped.json()["error"]["context"]["missing_steps"] == ["remains_transferred"]
    handover = client.post(
        f"/api/relocation/cases/{case['id']}/remains-handover",
        json={"reference": "HANDOVER-1", "note": "", "idempotency_key": "handover-d-001", "recorded_by": "site-officer"},
    )
    assert handover.status_code == 201
    construction = client.post(
        f"/api/relocation/cases/{case['id']}/construction-complete",
        json={"reference": "CONS-1", "note": "", "idempotency_key": "cons-d-001", "recorded_by": "site-officer"},
    )
    assert construction.status_code == 201
    closed = client.post(
        f"/api/relocation/cases/{case['id']}/close-old-plot",
        json={"reference": "CLOSE-1", "note": "", "idempotency_key": "close-d-001", "recorded_by": "site-officer"},
    )
    assert closed.status_code == 201
    closed_replay = client.post(
        f"/api/relocation/cases/{case['id']}/close-old-plot",
        json={"reference": "CLOSE-1", "note": "", "idempotency_key": "close-d-001", "recorded_by": "site-officer"},
    )
    assert closed_replay.status_code == 201 and closed_replay.json()["id"] == closed.json()["id"]
    detail = client.get(f"/api/relocation/cases/{case['id']}").json()
    assert detail["status"] == "completed"
    assert [step["step"] for step in detail["steps"]] == ["new_plot_confirmed", "remains_transferred", "construction_completed", "old_plot_closed"]
    assert detail["source_right"]["status"] == "relocated"
    assert detail["source_right"]["id"] == right["id"]


def test_withdraw_preserves_collected_materials(client):
    make_right(client, "OLD-E-001")
    make_rule(client)
    case = make_case(client, "OLD-E-001", "RELO-E-001")
    add_opinion(client, case["id"], "KIN-401")
    approve_case(client, case["id"])
    confirm_plot(client, case["id"], "NEW-E-901", "plot-e-001")
    withdrawn = client.post(f"/api/relocation/cases/{case['id']}/withdraw", json={"reason": "家属协商后暂缓迁移", "withdrawn_by": "park-manager"})
    assert withdrawn.status_code == 200
    detail = client.get(f"/api/relocation/cases/{case['id']}").json()
    assert detail["status"] == "withdrawn"
    assert len(detail["opinions"]) == 1
    assert len(detail["decisions"]) == 1
    assert len(detail["steps"]) == 1
    blocked = client.post(
        f"/api/relocation/cases/{case['id']}/remains-handover",
        json={"reference": "HANDOVER-1", "note": "", "idempotency_key": "handover-e-001", "recorded_by": "site-officer"},
    )
    assert blocked.status_code == 409


def test_batch_screening_flags_duplicate_kin_and_plot_conflicts(client):
    make_right(client, "OLD-F-001")
    make_right(client, "OLD-F-002", holder="王琴")
    make_rule(client)
    first = make_case(client, "OLD-F-001", "RELO-F-001")
    second = make_case(client, "OLD-F-002", "RELO-F-002")
    add_opinion(client, first["id"], "KIN-501", proof="PROOF-F1")
    add_opinion(client, second["id"], "KIN-501", proof="PROOF-F2")
    add_opinion(client, second["id"], "KIN-502", proof="PROOF-F3")
    approve_case(client, first["id"])
    approve_case(client, second["id"])
    confirm_plot(client, first["id"], "NEW-F-901", "plot-f-001")
    confirm_plot(client, second["id"], "NEW-F-901", "plot-f-002")
    batch = client.post("/api/relocation/batches", json={"batch_no": "BATCH-2026-01", "name": "一期改造", "created_by": "park-manager"})
    assert batch.status_code == 201
    for case in (first, second):
        added = client.post(f"/api/relocation/batches/{batch.json()['id']}/items", json={"case_id": case["id"], "added_by": "park-manager"})
        assert added.status_code == 201
    report = client.get(f"/api/relocation/batches/{batch.json()['id']}/conflicts").json()
    assert report["duplicate_kin_authorizations"][0]["kin_identity"] == "KIN-501"
    assert set(report["duplicate_kin_authorizations"][0]["case_ids"]) == {first["id"], second["id"]}
    internal = [item for item in report["new_plot_conflicts"] if item["conflict_with"] == "batch"]
    assert internal and internal[0]["new_plot_code"] == "NEW-F-901"
    assert set(internal[0]["case_ids"]) == {first["id"], second["id"]}


def test_opinion_submission_is_idempotent(client):
    make_right(client, "OLD-G-001")
    make_rule(client)
    case = make_case(client, "OLD-G-001", "RELO-G-001")
    payload = {"kin_name": "张明", "kin_identity": "KIN-601", "relationship": "子女", "proof_document_no": "PROOF-G1", "stance": "approve", "note": "", "idempotency_key": "op-g-001", "submitted_by": "case-officer"}
    first = client.post(f"/api/relocation/cases/{case['id']}/opinions", json=payload)
    replay = client.post(f"/api/relocation/cases/{case['id']}/opinions", json=payload)
    assert first.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    conflict = client.post(f"/api/relocation/cases/{case['id']}/opinions", json={**payload, "stance": "object"})
    assert conflict.status_code == 409


def test_new_plot_with_active_right_is_rejected(client):
    make_right(client, "OLD-H-001")
    make_right(client, "NEW-H-901", holder="王琴")
    make_rule(client)
    case = make_case(client, "OLD-H-001", "RELO-H-001")
    add_opinion(client, case["id"], "KIN-701")
    approve_case(client, case["id"])
    response = client.post(
        f"/api/relocation/cases/{case['id']}/confirm-new-plot",
        json={"new_plot_code": "NEW-H-901", "reference": "SEL-1", "note": "", "idempotency_key": "plot-h-001", "recorded_by": "site-officer"},
    )
    assert response.status_code == 409
