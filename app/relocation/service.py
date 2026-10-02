from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.core.clock import Clock, SystemClock, to_storage
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.database import get_connection, transaction
from app.mortuary.repository import MortuaryRepository
from app.relocation.repository import STEP_ORDER, TERMINAL_STATUSES, RelocationRepository


class RelocationService:
    def __init__(self, connection: sqlite3.Connection | None = None, clock: Clock | None = None) -> None:
        self.connection = connection or get_connection()
        self.clock = clock or SystemClock()
        MortuaryRepository(self.connection).ensure_schema()
        self.repository = RelocationRepository(self.connection)
        self.repository.ensure_schema()

    def now(self) -> str:
        return to_storage(self.clock.now())

    @staticmethod
    def _decorate_case(case: dict[str, Any]) -> dict[str, Any]:
        item = dict(case)
        item["right_snapshot"] = json.loads(item.pop("right_snapshot_json"))
        return item

    @staticmethod
    def _decorate_decision(decision: dict[str, Any]) -> dict[str, Any]:
        item = dict(decision)
        item["rule_snapshot"] = json.loads(item.pop("rule_snapshot_json"))
        item["opinion_ids"] = json.loads(item.pop("opinion_ids_json"))
        item["kin_identities"] = json.loads(item.pop("kin_identities_json"))
        item["proof_documents"] = json.loads(item.pop("proof_documents_json"))
        item["reasons"] = json.loads(item.pop("reasons_json"))
        return item

    @staticmethod
    def _decorate_step(step: dict[str, Any]) -> dict[str, Any]:
        item = dict(step)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    @staticmethod
    def _right_snapshot(right: dict[str, Any]) -> dict[str, Any]:
        return {
            "right_id": right["id"],
            "plot_code": right["plot_code"],
            "holder_name": right["holder_name"],
            "holder_identity": right["holder_identity"],
            "starts_on": right["starts_on"],
            "expires_on": right["expires_on"],
            "status": right["status"],
            "version": right["version"],
        }

    def _ensure_unblocked(self, repo: RelocationRepository, mortuary: MortuaryRepository, case: dict[str, Any]) -> None:
        holds = repo.active_holds(case["id"])
        if holds:
            raise ConflictError(
                "存在司法暂停或身份争议,后续步骤被阻断",
                context={"holds": [{"id": hold["id"], "hold_type": hold["hold_type"], "reason": hold["reason"]} for hold in holds]},
            )
        right = mortuary.right(case["right_id"])
        if right is None:
            raise ConflictError("冻结的权属记录不存在,无法继续办理")
        if int(right["version"]) != int(case["right_version_frozen"]):
            raise ConflictError(
                "权属在申请后已发生变更,需要重新冻结权属版本",
                context={"frozen_version": case["right_version_frozen"], "current_version": right["version"]},
            )

    def create_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            if repo.rule_code(payload["code"]):
                raise ConflictError("同意规则编码已存在")
            cursor = connection.execute(
                "INSERT INTO relocation_consent_rules(code,name,min_approvals,max_objections,require_unanimous,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (payload["code"], payload["name"], payload["min_approvals"], payload["max_objections"], 1 if payload["require_unanimous"] else 0, payload["created_by"], now, now),
            )
            rule = repo.rule(int(cursor.lastrowid)) or {}
            repo.event("consent_rule", rule["id"], "relocation.rule_created", payload["created_by"], {"code": rule["code"], "min_approvals": rule["min_approvals"], "max_objections": rule["max_objections"]}, now)
            return rule

    def list_rules(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM relocation_consent_rules ORDER BY code").fetchall()
        return [dict(row) for row in rows]

    def create_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            mortuary = MortuaryRepository(connection)
            if repo.case_no(payload["case_no"]):
                raise ConflictError("迁移案件编号已存在")
            right = mortuary.right_plot(payload["source_plot_code"])
            if right is None:
                raise NotFoundError("旧墓位权属不存在")
            if right["status"] not in {"active", "expired"}:
                raise ConflictError("旧墓位权属状态不允许发起迁移", context={"status": right["status"]})
            rule = repo.rule_code(payload["consent_rule_code"])
            if rule is None or not rule["active"]:
                raise NotFoundError("同意规则不存在或已停用")
            existing = repo.active_case_for_plot(payload["source_plot_code"])
            if existing:
                raise ConflictError("该墓位已存在进行中的迁移案件", context={"case_id": existing["id"], "case_no": existing["case_no"]})
            snapshot = self._right_snapshot(right)
            cursor = connection.execute(
                "INSERT INTO relocation_cases(case_no,source_plot_code,right_id,right_version_frozen,right_snapshot_json,consent_rule_code,reason,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (payload["case_no"], payload["source_plot_code"], right["id"], right["version"], json.dumps(snapshot, ensure_ascii=False, sort_keys=True), payload["consent_rule_code"], payload["reason"], payload["created_by"], now, now),
            )
            case = repo.case(int(cursor.lastrowid)) or {}
            repo.event("relocation_case", case["id"], "relocation.case_opened", payload["created_by"], {"case_no": case["case_no"], "source_plot_code": case["source_plot_code"], "right_version_frozen": right["version"]}, now)
            return self._decorate_case(case)

    def list_cases(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM relocation_cases"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [self._decorate_case(dict(row)) for row in self.connection.execute(sql, params).fetchall()]

    def get_case(self, case_id: int) -> dict[str, Any]:
        case = self.repository.case(case_id)
        if case is None:
            raise NotFoundError("迁移案件不存在")
        result = self._decorate_case(case)
        result["source_right"] = MortuaryRepository(self.connection).right(case["right_id"])
        result["opinions"] = self.repository.opinions(case_id)
        result["current_opinions"] = self.repository.latest_opinions(case_id)
        result["steps"] = [self._decorate_step(step) for step in self.repository.steps(case_id)]
        result["holds"] = self.repository.holds(case_id)
        result["decisions"] = [self._decorate_decision(decision) for decision in self.repository.decisions(case_id)]
        result["timeline"] = self.repository.timeline("relocation_case", case_id)
        return result

    def submit_opinion(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            existing = repo.opinion_key(case_id, payload["idempotency_key"])
            if existing:
                if existing["kin_identity"] != payload["kin_identity"] or existing["stance"] != payload["stance"] or existing["proof_document_no"] != payload["proof_document_no"]:
                    raise ConflictError("同一幂等键对应了不同意见内容")
                return existing
            if case["status"] != "collecting":
                raise ConflictError("当前状态不能提交亲属意见", context={"status": case["status"]})
            cursor = connection.execute(
                "INSERT INTO relocation_kin_opinions(case_id,kin_name,kin_identity,relationship,proof_document_no,stance,note,idempotency_key,submitted_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (case_id, payload["kin_name"], payload["kin_identity"], payload["relationship"], payload["proof_document_no"], payload["stance"], payload["note"], payload["idempotency_key"], payload["submitted_by"], now),
            )
            opinion = repo.opinion(int(cursor.lastrowid)) or {}
            repo.event("relocation_case", case_id, "relocation.opinion_submitted", payload["submitted_by"], {"opinion_id": opinion["id"], "kin_identity": payload["kin_identity"], "stance": payload["stance"], "proof_document_no": payload["proof_document_no"]}, now)
            return opinion

    def verify_proof(self, opinion_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            opinion = repo.opinion(opinion_id)
            if opinion is None:
                raise NotFoundError("亲属意见不存在")
            if opinion["proof_verified"]:
                return opinion
            case = repo.case(opinion["case_id"])
            if case and case["status"] in TERMINAL_STATUSES:
                raise ConflictError("案件已终结,不能核验关系证明", context={"status": case["status"]})
            connection.execute("UPDATE relocation_kin_opinions SET proof_verified=1,proof_verified_by=?,proof_verified_at=? WHERE id=?", (payload["verified_by"], now, opinion_id))
            repo.event("relocation_case", opinion["case_id"], "relocation.proof_verified", payload["verified_by"], {"opinion_id": opinion_id, "proof_document_no": opinion["proof_document_no"]}, now)
            return repo.opinion(opinion_id) or {}

    def evaluate_consent(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            mortuary = MortuaryRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            if case["status"] != "collecting":
                raise ConflictError("当前状态不能进行同意评估", context={"status": case["status"]})
            self._ensure_unblocked(repo, mortuary, case)
            rule = repo.rule_code(case["consent_rule_code"])
            if rule is None or not rule["active"]:
                raise ConflictError("案件配置的同意规则不可用", context={"rule_code": case["consent_rule_code"]})
            latest = repo.latest_opinions(case_id)
            valid = [opinion for opinion in latest if opinion["proof_verified"]]
            approvals = [opinion for opinion in valid if opinion["stance"] == "approve"]
            objections = [opinion for opinion in valid if opinion["stance"] == "object"]
            reasons: list[str] = []
            if len(approvals) < int(rule["min_approvals"]):
                reasons.append(f"有效同意人数不足:需要{rule['min_approvals']}人,当前{len(approvals)}人")
            if len(objections) > int(rule["max_objections"]):
                reasons.append(f"反对意见超过规则允许:{len(objections)}条,允许{rule['max_objections']}条")
            if rule["require_unanimous"] and objections:
                reasons.append("规则要求全体一致同意,仍存在反对意见")
            outcome = "consent_approved" if not reasons else "consent_rejected"
            rule_snapshot = {key: rule[key] for key in ("code", "name", "min_approvals", "max_objections", "require_unanimous")}
            cursor = connection.execute(
                "INSERT INTO relocation_decisions(case_id,decision,rule_code,rule_snapshot_json,right_version,opinion_ids_json,kin_identities_json,proof_documents_json,reasons_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    case_id,
                    outcome,
                    rule["code"],
                    json.dumps(rule_snapshot, ensure_ascii=False, sort_keys=True),
                    case["right_version_frozen"],
                    json.dumps([opinion["id"] for opinion in valid]),
                    json.dumps(sorted({opinion["kin_identity"] for opinion in valid}), ensure_ascii=False),
                    json.dumps(sorted({opinion["proof_document_no"] for opinion in valid}), ensure_ascii=False),
                    json.dumps(reasons, ensure_ascii=False),
                    payload["handled_by"],
                    now,
                ),
            )
            decision = repo.decision(int(cursor.lastrowid)) or {}
            if outcome == "consent_approved":
                connection.execute("UPDATE relocation_cases SET status='approved',version=version+1,updated_at=? WHERE id=?", (now, case_id))
            repo.event("relocation_case", case_id, f"relocation.{outcome}", payload["handled_by"], {"decision_id": decision["id"], "approvals": len(approvals), "objections": len(objections), "reasons": reasons}, now)
            return self._decorate_decision(decision)

    def list_decisions(self, case_id: int) -> list[dict[str, Any]]:
        if self.repository.case(case_id) is None:
            raise NotFoundError("迁移案件不存在")
        return [self._decorate_decision(decision) for decision in self.repository.decisions(case_id)]

    def refreeze_right(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            mortuary = MortuaryRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            if case["status"] in TERMINAL_STATUSES:
                raise ConflictError("案件已终结,不能重新冻结权属", context={"status": case["status"]})
            if repo.active_holds(case_id):
                raise ConflictError("存在司法暂停或身份争议,不能重新冻结权属")
            right = mortuary.right(case["right_id"])
            if right is None:
                raise NotFoundError("冻结的权属记录不存在")
            if right["status"] not in {"active", "expired"}:
                raise ConflictError("权属当前状态不允许重新冻结", context={"status": right["status"]})
            if int(right["version"]) == int(case["right_version_frozen"]):
                return self._decorate_case(case)
            snapshot = self._right_snapshot(right)
            connection.execute("UPDATE relocation_cases SET right_version_frozen=?,right_snapshot_json=?,version=version+1,updated_at=? WHERE id=?", (right["version"], json.dumps(snapshot, ensure_ascii=False, sort_keys=True), now, case_id))
            repo.event("relocation_case", case_id, "relocation.right_refrozen", payload["handled_by"], {"old_version": case["right_version_frozen"], "new_version": right["version"], "reason": payload["reason"]}, now)
            return self._decorate_case(repo.case(case_id) or {})

    def raise_hold(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            if case["status"] in TERMINAL_STATUSES:
                raise ConflictError("案件已终结,不能登记阻断", context={"status": case["status"]})
            if repo.active_hold_of_type(case_id, payload["hold_type"]):
                raise ConflictError("已存在同类型且未解除的阻断")
            cursor = connection.execute(
                "INSERT INTO relocation_holds(case_id,hold_type,reason,raised_by,raised_at) VALUES(?,?,?,?,?)",
                (case_id, payload["hold_type"], payload["reason"], payload["raised_by"], now),
            )
            hold = repo.hold(int(cursor.lastrowid)) or {}
            repo.event("relocation_case", case_id, "relocation.hold_raised", payload["raised_by"], {"hold_id": hold["id"], "hold_type": payload["hold_type"], "reason": payload["reason"]}, now)
            return hold

    def release_hold(self, hold_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            hold = repo.hold(hold_id)
            if hold is None:
                raise NotFoundError("阻断记录不存在")
            if hold["status"] == "released":
                return hold
            connection.execute("UPDATE relocation_holds SET status='released',released_by=?,released_at=?,release_note=? WHERE id=?", (payload["released_by"], now, payload["note"], hold_id))
            repo.event("relocation_case", hold["case_id"], "relocation.hold_released", payload["released_by"], {"hold_id": hold_id, "hold_type": hold["hold_type"], "note": payload["note"]}, now)
            return repo.hold(hold_id) or {}

    def confirm_new_plot(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            mortuary = MortuaryRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            existing = repo.step(case_id, "new_plot_confirmed")
            if existing:
                stored = json.loads(existing["payload_json"])
                if existing["idempotency_key"] == payload["idempotency_key"] and stored.get("new_plot_code") == payload["new_plot_code"]:
                    return self._decorate_step(existing)
                raise ConflictError("新墓位已确认,不能重复确认或变更", context={"step_id": existing["id"]})
            if case["status"] != "approved":
                raise ConflictError("案件未通过同意评估,不能确认新墓位", context={"status": case["status"]})
            self._ensure_unblocked(repo, mortuary, case)
            if payload["new_plot_code"] == case["source_plot_code"]:
                raise ValidationError("新墓位不能与原墓位相同")
            occupied = mortuary.right_plot(payload["new_plot_code"])
            if occupied and occupied["status"] == "active":
                raise ConflictError("新墓位已存在有效权属,不能作为迁入墓位", context={"right_id": occupied["id"]})
            step_payload = {"new_plot_code": payload["new_plot_code"]}
            connection.execute(
                "INSERT INTO relocation_steps(case_id,step,reference,note,payload_json,idempotency_key,recorded_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (case_id, "new_plot_confirmed", payload["reference"], payload["note"], json.dumps(step_payload, ensure_ascii=False, sort_keys=True), payload["idempotency_key"], payload["recorded_by"], now),
            )
            connection.execute("UPDATE relocation_cases SET status='implementing',new_plot_code=?,version=version+1,updated_at=? WHERE id=?", (payload["new_plot_code"], now, case_id))
            step = repo.step(case_id, "new_plot_confirmed") or {}
            repo.event("relocation_case", case_id, "relocation.new_plot_confirmed", payload["recorded_by"], {"step_id": step["id"], "new_plot_code": payload["new_plot_code"]}, now)
            return self._decorate_step(step)

    def _record_step(self, case_id: int, step: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            mortuary = MortuaryRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            existing = repo.step(case_id, step)
            if existing:
                if existing["idempotency_key"] == payload["idempotency_key"] and existing["reference"] == payload["reference"]:
                    return self._decorate_step(existing)
                raise ConflictError("该步骤已记录,不能重复登记或变更", context={"step_id": existing["id"]})
            if case["status"] != "implementing":
                raise ConflictError("案件不在实施阶段,不能登记该步骤", context={"status": case["status"]})
            self._ensure_unblocked(repo, mortuary, case)
            completed = {row["step"] for row in repo.steps(case_id)}
            missing = [name for name in STEP_ORDER[: STEP_ORDER.index(step)] if name not in completed]
            if missing:
                raise ConflictError("必须按顺序完成前序步骤", context={"missing_steps": missing})
            connection.execute(
                "INSERT INTO relocation_steps(case_id,step,reference,note,payload_json,idempotency_key,recorded_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (case_id, step, payload["reference"], payload["note"], "{}", payload["idempotency_key"], payload["recorded_by"], now),
            )
            step_row = repo.step(case_id, step) or {}
            if step == "old_plot_closed":
                connection.execute("UPDATE relocation_cases SET status='completed',version=version+1,updated_at=? WHERE id=?", (now, case_id))
                connection.execute("UPDATE burial_rights SET status='relocated',version=version+1,updated_at=? WHERE id=?", (now, case["right_id"]))
                repo.event("relocation_case", case_id, "relocation.case_completed", payload["recorded_by"], {"right_id": case["right_id"], "source_plot_code": case["source_plot_code"]}, now)
            repo.event("relocation_case", case_id, f"relocation.{step}", payload["recorded_by"], {"step_id": step_row["id"], "reference": payload["reference"]}, now)
            return self._decorate_step(step_row)

    def record_remains_handover(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._record_step(case_id, "remains_transferred", payload)

    def record_construction_completed(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._record_step(case_id, "construction_completed", payload)

    def close_old_plot(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._record_step(case_id, "old_plot_closed", payload)

    def withdraw(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            if case["status"] == "withdrawn":
                return self._decorate_case(case)
            if case["status"] == "completed":
                raise ConflictError("已完成的迁移案件不能撤回")
            connection.execute("UPDATE relocation_cases SET status='withdrawn',version=version+1,updated_at=? WHERE id=?", (now, case_id))
            repo.event("relocation_case", case_id, "relocation.withdrawn", payload["withdrawn_by"], {"reason": payload["reason"]}, now)
            return self._decorate_case(repo.case(case_id) or {})

    def create_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            if repo.batch_no(payload["batch_no"]):
                raise ConflictError("改造批次编号已存在")
            cursor = connection.execute(
                "INSERT INTO relocation_batches(batch_no,name,created_by,created_at) VALUES(?,?,?,?)",
                (payload["batch_no"], payload["name"], payload["created_by"], now),
            )
            batch = repo.batch(int(cursor.lastrowid)) or {}
            repo.event("relocation_batch", batch["id"], "relocation.batch_created", payload["created_by"], {"batch_no": batch["batch_no"]}, now)
            return batch

    def get_batch(self, batch_id: int) -> dict[str, Any]:
        batch = self.repository.batch(batch_id)
        if batch is None:
            raise NotFoundError("改造批次不存在")
        batch["cases"] = [self._decorate_case(case) for case in self.repository.batch_cases(batch_id)]
        return batch

    def add_batch_item(self, batch_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            if repo.batch(batch_id) is None:
                raise NotFoundError("改造批次不存在")
            if repo.case(payload["case_id"]) is None:
                raise NotFoundError("迁移案件不存在")
            existing = repo.batch_item(batch_id, payload["case_id"])
            if existing:
                return existing
            connection.execute("INSERT INTO relocation_batch_items(batch_id,case_id,added_by,added_at) VALUES(?,?,?,?)", (batch_id, payload["case_id"], payload["added_by"], now))
            repo.event("relocation_batch", batch_id, "relocation.batch_item_added", payload["added_by"], {"case_id": payload["case_id"]}, now)
            return repo.batch_item(batch_id, payload["case_id"]) or {}

    def batch_conflicts(self, batch_id: int) -> dict[str, Any]:
        repo = self.repository
        if repo.batch(batch_id) is None:
            raise NotFoundError("改造批次不存在")
        cases = repo.batch_cases(batch_id)
        case_ids = [case["id"] for case in cases]
        kin_map: dict[str, list[dict[str, Any]]] = {}
        for case in cases:
            for opinion in repo.latest_opinions(case["id"]):
                if opinion["proof_verified"] and opinion["stance"] == "approve":
                    kin_map.setdefault(opinion["kin_identity"], []).append({"case_id": case["id"], "opinion_id": opinion["id"], "kin_name": opinion["kin_name"]})
        duplicate_kin = [
            {"kin_identity": identity, "kin_name": entries[0]["kin_name"], "case_ids": sorted({entry["case_id"] for entry in entries}), "opinion_ids": [entry["opinion_id"] for entry in entries]}
            for identity, entries in sorted(kin_map.items())
            if len({entry["case_id"] for entry in entries}) > 1
        ]
        plot_conflicts: list[dict[str, Any]] = []
        plot_cases: dict[str, list[int]] = {}
        for case in cases:
            if case["new_plot_code"]:
                plot_cases.setdefault(case["new_plot_code"], []).append(case["id"])
        mortuary = MortuaryRepository(self.connection)
        for plot_code, ids in sorted(plot_cases.items()):
            if len(ids) > 1:
                plot_conflicts.append({"new_plot_code": plot_code, "conflict_with": "batch", "case_ids": sorted(ids), "detail": "批次内多个案件选择同一新墓位"})
            right = mortuary.right_plot(plot_code)
            if right and right["status"] == "active":
                plot_conflicts.append({"new_plot_code": plot_code, "conflict_with": "existing_right", "case_ids": sorted(ids), "right_id": right["id"], "detail": "新墓位已存在有效权属"})
        if plot_cases:
            placeholders = ",".join("?" for _ in case_ids) or "NULL"
            rows = self.connection.execute(
                f"SELECT id,case_no,new_plot_code FROM relocation_cases WHERE status IN ('approved','implementing') AND new_plot_code!='' AND id NOT IN ({placeholders})",
                case_ids,
            ).fetchall()
            for row in rows:
                if row["new_plot_code"] in plot_cases:
                    plot_conflicts.append({"new_plot_code": row["new_plot_code"], "conflict_with": "other_case", "case_ids": sorted(plot_cases[row["new_plot_code"]]), "other_case_id": row["id"], "other_case_no": row["case_no"], "detail": "批次外进行中案件已选择该新墓位"})
        return {"batch_id": batch_id, "case_ids": case_ids, "duplicate_kin_authorizations": duplicate_kin, "new_plot_conflicts": plot_conflicts}
