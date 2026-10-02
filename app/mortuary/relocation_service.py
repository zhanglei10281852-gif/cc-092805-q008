"""墓位迁移案件服务。

阶段顺序固定为：申请（冻结权属版本）→ 同意审定 → 新墓位确认 → 遗骨交接 →
施工完成 → 旧墓位关闭。任一阶段存在生效中的司法暂停或身份争议都会被阻断。
所有亲属、意见、文件、暂停与决定记录只追加不删除，撤回审批后材料仍然可查。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from app.core.clock import Clock, SystemClock, to_storage
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.database import get_connection, transaction
from app.mortuary.relocation_repository import RelocationRepository
from app.mortuary.repository import MortuaryRepository


DEFAULT_RULE = {"rule_type": "unanimous", "threshold": None, "params": {}}

# 每个阶段动作要求案件所处的状态，以及成功后进入的状态。
STAGE_FLOW: dict[str, tuple[str, str]] = {
    "site_confirm": ("consent_approved", "site_selected"),
    "remains_handover": ("site_selected", "remains_handed_over"),
    "construction_complete": ("remains_handed_over", "construction_completed"),
    "old_plot_close": ("construction_completed", "old_plot_closed"),
}
STAGE_LABELS = {
    "consent_review": "亲属同意审定",
    "site_confirm": "新墓位确认",
    "remains_handover": "遗骨交接",
    "construction_complete": "施工完成",
    "old_plot_close": "旧墓位关闭",
}
WITHDRAWABLE = {"applied", "consent_approved", "site_selected"}
FILE_KIND_FOR_POINT = {
    "site_confirm": "site_record",
    "remains_handover": "handover_receipt",
    "construction_complete": "completion_report",
    "old_plot_close": "closure_record",
}


class RelocationService:
    def __init__(self, connection: sqlite3.Connection | None = None, clock: Clock | None = None) -> None:
        self.connection = connection or get_connection()
        self.clock = clock or SystemClock()
        self.repository = RelocationRepository(self.connection)
        self.mortuary = MortuaryRepository(self.connection)
        self.repository.ensure_schema()

    def now(self) -> str:
        return to_storage(self.clock.now())

    # ---------- 配置 ----------

    def get_config(self, scope: str = "global", scope_key: str = "") -> dict[str, Any]:
        config = self.repository.config(scope, scope_key)
        if config is None:
            return {"scope": scope, "scope_key": scope_key, **DEFAULT_RULE, "configured": False}
        return {
            "scope": scope,
            "scope_key": scope_key,
            "rule_type": config["rule_type"],
            "threshold": json.loads(config["params_json"]).get("threshold"),
            "params": json.loads(config["params_json"]),
            "updated_by": config["updated_by"],
            "updated_at": config["updated_at"],
            "configured": True,
        }

    def upsert_config(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        scope = payload.get("scope", "global")
        scope_key = payload.get("scope_key", "") if scope == "project" else ""
        rule_type = payload["rule_type"]
        params = dict(payload.get("params") or {})
        if rule_type == "threshold":
            threshold = payload.get("threshold")
            if threshold is None:
                raise ValidationError("threshold 规则必须提供同意比例")
            params["threshold"] = threshold
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            repo.upsert_config(scope, scope_key, rule_type, params, actor, now)
        return self.get_config(scope, scope_key)

    def _resolve_rule(self, connection: sqlite3.Connection, project_code: str) -> dict[str, Any]:
        repo = RelocationRepository(connection)
        project = repo.config("project", project_code)
        config = project or repo.config("global", "")
        if config is None:
            return dict(DEFAULT_RULE)
        return {"rule_type": config["rule_type"], "threshold": json.loads(config["params_json"]).get("threshold"),
                "params": json.loads(config["params_json"])}

    # ---------- 案件 ----------

    def create_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            rights = MortuaryRepository(connection)
            if repo.case_plot(payload["project_code"], payload["plot_code"]):
                raise ConflictError("该墓位在本改造项目中已建立迁移案件")
            right = rights.right_plot(payload["plot_code"])
            if right is None:
                raise NotFoundError("墓位权属不存在，无法建立迁移案件")
            rule = self._resolve_rule(connection, payload["project_code"])
            snapshot = {key: right[key] for key in sorted(right.keys())}
            snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            snapshot_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
            case_id = repo.insert_case({
                "project_code": payload["project_code"],
                "plot_code": payload["plot_code"],
                "right_id": right["id"],
                "right_version": right["version"],
                "right_snapshot_json": snapshot_json,
                "snapshot_hash": snapshot_hash,
                "consent_rule_json": json.dumps(rule, ensure_ascii=False, sort_keys=True),
                "created_by": payload["created_by"],
                "now": now,
            })
            rights.event("relocation_case", case_id, "relocation.applied", payload["created_by"], {
                "project_code": payload["project_code"], "plot_code": payload["plot_code"],
                "right_id": right["id"], "right_version": right["version"], "snapshot_hash": snapshot_hash,
                "consent_rule": rule,
            }, now)
            return self.get_case(case_id, repo)

    def list_cases(self, project_code: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_cases(project_code, status, limit)

    def get_case(self, case_id: int, repo: RelocationRepository | None = None) -> dict[str, Any]:
        repo = repo or self.repository
        case = repo.case(case_id)
        if case is None:
            raise NotFoundError("迁移案件不存在")
        case["right_snapshot"] = json.loads(case.pop("right_snapshot_json"))
        case["consent_rule"] = json.loads(case.pop("consent_rule_json"))
        case["kin"] = repo.list_kin(case_id)
        case["files"] = repo.list_files(case_id)
        case["opinions"] = repo.list_opinions(case_id)
        case["holds"] = repo.list_holds(case_id)
        case["active_holds"] = [hold["id"] for hold in repo.active_holds(case_id)]
        case["decisions"] = repo.list_decisions(case_id)
        case["timeline"] = self._timeline(repo, case_id)
        return case

    @staticmethod
    def _timeline(repo: RelocationRepository, case_id: int) -> list[dict[str, Any]]:
        # 复用殡葬领域事件表作为迁移案件时间线。
        rows = repo.connection.execute(
            "SELECT * FROM mortuary_events WHERE aggregate_type='relocation_case' AND aggregate_id=? ORDER BY id",
            (str(case_id),),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    # ---------- 文件与亲属 ----------

    def add_file(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            if case["status"] == "withdrawn":
                raise ConflictError("案件已撤回，不能再补充文件")
            if repo.file_code(case_id, payload["file_code"]):
                raise ConflictError("案件内文件编码已存在")
            file_id = repo.insert_file(case_id, {**payload, "now": now})
            return repo.file(file_id) or {}

    def _require_file(self, repo: RelocationRepository, case_id: int, file_code: str | None, kind: str | None = None) -> int | None:
        if file_code is None:
            return None
        file = repo.file_code(case_id, file_code)
        if file is None:
            raise NotFoundError(f"文件 {file_code} 不存在")
        if file["status"] != "available":
            raise ConflictError(f"文件 {file_code} 已作废，不能作为依据")
        if kind and file["kind"] != kind:
            raise ValidationError(f"文件类型应为 {kind}，实际为 {file['kind']}")
        return file["id"]

    def register_kin(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            if case["status"] == "withdrawn":
                raise ConflictError("案件已撤回，不能再登记亲属")
            proof_file_id = self._require_file(repo, case_id, payload.get("proof_file_code"), "relationship_proof")
            if proof_file_id is None:
                raise ValidationError("登记亲属必须提供关系证明文件（relationship_proof）")
            existing = connection.execute(
                "SELECT 1 FROM relocation_kin WHERE case_id=? AND relative_identity=?",
                (case_id, payload["relative_identity"]),
            ).fetchone()
            if existing:
                raise ConflictError("该亲属已在案件中登记")
            kin_id = repo.insert_kin(case_id, payload, proof_file_id, now)
            MortuaryRepository(connection).event("relocation_case", case_id, "kin.registered", payload["registered_by"], {
                "kin_id": kin_id, "relative_identity": payload["relative_identity"],
                "relationship": payload["relationship"], "proof_file_id": proof_file_id,
            }, now)
            return repo.kin(kin_id) or {}

    def add_opinion(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            if case["status"] == "withdrawn":
                raise ConflictError("案件已撤回，不能再收集意见")
            kin = repo.kin(payload["kin_id"])
            if kin is None or kin["case_id"] != case_id:
                raise NotFoundError("亲属不存在或不属于该案件")
            if not kin["proof_file_id"]:
                raise ConflictError("亲属缺少关系证明，意见不能计入")
            file_id = self._require_file(repo, case_id, payload.get("file_code"), "consent_letter")
            already = connection.execute(
                "SELECT 1 FROM relocation_opinions WHERE case_id=? AND kin_id=?",
                (case_id, kin["id"]),
            ).fetchone()
            if already:
                raise ConflictError("该亲属意见已收集，不能重复登记")
            duplicate_cases: list[dict[str, Any]] = []
            if payload["decision"] == "consent":
                rows = connection.execute(
                    "SELECT o.id opinion_id,o.duplicate_acknowledged,c.id case_id,c.plot_code FROM relocation_opinions o "
                    "JOIN relocation_kin k ON k.id=o.kin_id JOIN relocation_cases c ON c.id=o.case_id "
                    "WHERE c.project_code=? AND k.relative_identity=? AND o.decision='consent' AND c.status<>'withdrawn' AND c.id<>?",
                    (case["project_code"], kin["relative_identity"], case_id),
                ).fetchall()
                duplicate_cases = [dict(row) for row in rows]
            if duplicate_cases and not payload.get("allow_duplicate"):
                unacknowledged = [row for row in duplicate_cases if not row["duplicate_acknowledged"]]
                if unacknowledged:
                    raise ConflictError("同一亲属已在本项目其他案件中授权同意，须确认重复授权", context={
                        "relative_identity": kin["relative_identity"],
                        "other_cases": [{"case_id": row["case_id"], "plot_code": row["plot_code"], "opinion_id": row["opinion_id"]} for row in unacknowledged],
                    })
            opinion_id = repo.insert_opinion(case_id, {**payload, "file_id": file_id}, now)
            MortuaryRepository(connection).event("relocation_case", case_id, "kin.opinion_collected", payload["collected_by"], {
                "opinion_id": opinion_id, "kin_id": kin["id"], "decision": payload["decision"],
                "duplicate_acknowledged": bool(duplicate_cases), "file_id": file_id,
            }, now)
            return repo.one(connection.execute("SELECT * FROM relocation_opinions WHERE id=?", (opinion_id,)).fetchone()) or {}

    # ---------- 暂停与争议 ----------

    def add_hold(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            if case["status"] == "withdrawn":
                raise ConflictError("案件已撤回")
            file_id = self._require_file(repo, case_id, payload.get("file_code"), "judicial_notice") if payload["hold_type"] == "judicial_suspension" else self._require_file(repo, case_id, payload.get("file_code"))
            kin_id = payload.get("kin_id")
            if kin_id is not None:
                kin = repo.kin(kin_id)
                if kin is None or kin["case_id"] != case_id:
                    raise NotFoundError("亲属不存在或不属于该案件")
            active = [hold for hold in repo.active_holds(case_id)
                      if hold["hold_type"] == payload["hold_type"] and hold["reference"] == payload["reference"]]
            if active:
                return repo.hold(active[0]["id"]) or {}
            hold_id = repo.insert_hold(case_id, {**payload, "file_id": file_id}, now)
            if payload["hold_type"] == "identity_dispute" and kin_id is not None:
                repo.set_kin_status(kin_id, "disputed")
            MortuaryRepository(connection).event("relocation_case", case_id, "hold.raised", payload["raised_by"], {
                "hold_id": hold_id, "hold_type": payload["hold_type"], "reference": payload["reference"],
                "kin_id": kin_id, "file_id": file_id,
            }, now)
            return repo.hold(hold_id) or {}

    def lift_hold(self, case_id: int, hold_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            hold = repo.hold(hold_id)
            if hold is None or hold["case_id"] != case_id:
                raise NotFoundError("暂停记录不存在或不属于该案件")
            if hold["status"] == "lifted":
                return hold
            repo.lift_hold(hold_id, payload["lifted_by"], payload.get("note", ""), now)
            if hold["hold_type"] == "identity_dispute" and hold["kin_id"] is not None:
                still_disputed = connection.execute(
                    "SELECT 1 FROM relocation_holds WHERE kin_id=? AND status='active' AND id<>?",
                    (hold["kin_id"], hold_id),
                ).fetchone()
                if not still_disputed:
                    repo.set_kin_status(hold["kin_id"], "registered")
            MortuaryRepository(connection).event("relocation_case", case_id, "hold.lifted", payload["lifted_by"], {
                "hold_id": hold_id, "hold_type": hold["hold_type"], "note": payload.get("note", ""),
            }, now)
            return repo.hold(hold_id) or {}

    def dispute_kin(self, case_id: int, kin_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """登记亲属身份争议，争议期间该亲属不计入同意，且阻断后续步骤。"""
        return self.add_hold(case_id, {
            "hold_type": "identity_dispute",
            "kin_id": kin_id,
            "reference": f"identity-dispute-kin-{kin_id}",
            "file_code": None,
            "reason": payload["reason"],
            "raised_by": payload["raised_by"],
        })

    # ---------- 同意审定 ----------

    def _evaluate_consent(self, connection: sqlite3.Connection, case: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        repo = RelocationRepository(connection)
        kin_rows = [kin for kin in repo.list_kin(case["id"]) if kin["status"] == "registered"]
        opinions = repo.list_opinions(case["id"])
        opinion_by_kin = {op["kin_id"]: op for op in opinions}
        consents = [kin for kin in kin_rows if opinion_by_kin.get(kin["id"], {}).get("decision") == "consent"]
        objects = [kin for kin in kin_rows if opinion_by_kin.get(kin["id"], {}).get("decision") == "object"]
        total = len(kin_rows)
        rule = json.loads(case["consent_rule_json"]) if isinstance(case["consent_rule_json"], str) else case["consent_rule"]
        rule_type = rule["rule_type"]
        if rule_type == "unanimous":
            approved = total > 0 and len(consents) == total
        elif rule_type == "majority":
            approved = total > 0 and len(consents) * 2 > total
        elif rule_type == "threshold":
            threshold = float(rule.get("threshold", 0))
            approved = total > 0 and (len(consents) / total) >= threshold
        elif rule_type == "holder_only":
            holder_identity = json.loads(case["right_snapshot_json"])["holder_identity"] if isinstance(case["right_snapshot_json"], str) else case["right_snapshot"]["holder_identity"]
            holder_consents = [kin for kin in consents if kin["relative_identity"] == holder_identity]
            approved = bool(holder_consents) and not objects
        else:  # pragma: no cover - 配置写入时已约束
            approved = False
        basis = {
            "rule": rule,
            "right_version": case["right_version"],
            "snapshot_hash": case["snapshot_hash"],
            "total_registered_kin": total,
            "consents": [{"kin_id": kin["id"], "relative_identity": kin["relative_identity"],
                          "opinion_id": opinion_by_kin[kin["id"]]["id"],
                          "proof_file_id": kin["proof_file_id"]} for kin in consents],
            "objects": [{"kin_id": kin["id"], "relative_identity": kin["relative_identity"],
                         "opinion_id": opinion_by_kin[kin["id"]]["id"]} for kin in objects],
            "no_response": [{"kin_id": kin["id"], "relative_identity": kin["relative_identity"]}
                            for kin in kin_rows if kin["id"] not in opinion_by_kin],
        }
        return approved, basis

    def review_consent(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        failure: ConflictError | None = None
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            replay = repo.decision_idempotency(case_id, payload["idempotency_key"])
            if replay:
                if replay["result"] == "approved":
                    return self.get_case(case_id, repo)
                raise ConflictError("该审定键曾被阻断或未获通过，不能重放为通过，请使用新的申请键", context={"decision_id": replay["id"], "result": replay["result"]})
            if case["status"] != "applied":
                raise ConflictError("只有申请状态的案件可以审定亲属同意", context={"status": case["status"]})
            holds = repo.active_holds(case_id)
            if holds:
                basis = {"holds": [{"hold_id": hold["id"], "hold_type": hold["hold_type"], "reference": hold["reference"]} for hold in holds]}
                repo.insert_decision(case_id, "consent_review", "blocked", {}, basis, payload["actor"], payload["idempotency_key"], now)
                MortuaryRepository(connection).event("relocation_case", case_id, "consent.blocked", payload["actor"], basis, now)
                failure = ConflictError("存在司法暂停或身份争议，同意审定被阻断", context=basis)
            else:
                approved, basis = self._evaluate_consent(connection, case)
                result = "approved" if approved else "rejected"
                repo.insert_decision(case_id, "consent_review", result, basis["rule"], basis, payload["actor"], payload["idempotency_key"], now)
                MortuaryRepository(connection).event("relocation_case", case_id,
                                                     "consent.approved" if approved else "consent.rejected",
                                                     payload["actor"], basis, now)
                if approved:
                    repo.update_status(case_id, "consent_approved", now)
                    result_case = self.get_case(case_id, repo)
                else:
                    failure = ConflictError("亲属意见未满足配置的同意规则", context={"consents": basis["consents"], "objects": basis["objects"], "no_response": basis["no_response"]})
        # 离开 with 后事务已提交：驳回/阻断的决定同样留痕。
        if failure is not None:
            raise failure
        return result_case

    # ---------- 顺序阶段 ----------

    def _guard_stage(self, repo: RelocationRepository, case_id: int, point: str, idempotency_key: str) -> tuple[dict[str, Any], bool, list[dict[str, Any]]]:
        """返回（案件, 是否幂等重放, 生效中的暂停）。状态与存在性错误直接抛出。"""
        case = repo.case(case_id)
        if case is None:
            raise NotFoundError("迁移案件不存在")
        replay = repo.decision_idempotency(case_id, idempotency_key)
        if replay:
            if replay["result"] == "approved":
                return case, True, []
            raise ConflictError("该幂等键对应的申请曾被阻断，暂停解除后请使用新的申请键重试",
                                context={"decision_id": replay["id"], "result": replay["result"]})
        required_status, _ = STAGE_FLOW[point]
        if case["status"] != required_status:
            raise ConflictError(f"阶段顺序错误：{STAGE_LABELS[point]}要求案件处于{required_status}", context={"status": case["status"]})
        return case, False, repo.active_holds(case_id)

    def confirm_site(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        failure: ConflictError | None = None
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case, replayed, holds = self._guard_stage(repo, case_id, "site_confirm", payload["idempotency_key"])
            if replayed:
                return self.get_case(case_id, repo)
            if holds:
                basis = {"holds": [{"hold_id": hold["id"], "hold_type": hold["hold_type"], "reference": hold["reference"]} for hold in holds]}
                repo.insert_decision(case_id, "site_confirm", "blocked", {}, basis, payload["actor"], payload["idempotency_key"], now)
                MortuaryRepository(connection).event("relocation_case", case_id, "site.blocked", payload["actor"], basis, now)
                failure = ConflictError("存在司法暂停或身份争议，新墓位确认被阻断", context=basis)
            else:
                new_plot_code = payload["new_plot_code"]
                if new_plot_code == case["plot_code"]:
                    raise ValidationError("新墓位不能与旧墓位相同")
                file_id = self._require_file(repo, case_id, payload.get("file_code"), "site_record")
                taken = repo.plot_taken(new_plot_code, case_id)
                if taken:
                    raise ConflictError("新墓位已被其他迁移案件占用", context={"case_id": taken["id"], "plot_code": taken["plot_code"]})
                repo.update_status(case_id, "site_selected", now, new_plot_code=new_plot_code)
                basis = {"new_plot_code": new_plot_code, "file_id": file_id, "right_version": case["right_version"], "snapshot_hash": case["snapshot_hash"]}
                repo.insert_decision(case_id, "site_confirm", "approved", {}, basis, payload["actor"], payload["idempotency_key"], now)
                MortuaryRepository(connection).event("relocation_case", case_id, "site.confirmed", payload["actor"], basis, now)
                result_case = self.get_case(case_id, repo)
        if failure is not None:
            raise failure
        return result_case

    def complete_stage(self, case_id: int, point: str, payload: dict[str, Any]) -> dict[str, Any]:
        expected_kind = FILE_KIND_FOR_POINT[point]
        now = self.now()
        failure: ConflictError | None = None
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case, replayed, holds = self._guard_stage(repo, case_id, point, payload["idempotency_key"])
            if replayed:
                return self.get_case(case_id, repo)
            if holds:
                basis = {"holds": [{"hold_id": hold["id"], "hold_type": hold["hold_type"], "reference": hold["reference"]} for hold in holds]}
                repo.insert_decision(case_id, point, "blocked", {}, basis, payload["actor"], payload["idempotency_key"], now)
                MortuaryRepository(connection).event("relocation_case", case_id, f"{point}.blocked", payload["actor"], basis, now)
                failure = ConflictError(f"存在司法暂停或身份争议，{STAGE_LABELS[point]}被阻断", context=basis)
            else:
                required_status, next_status = STAGE_FLOW[point]
                file_id = self._require_file(repo, case_id, payload.get("file_code"), expected_kind)
                repo.update_status(case_id, next_status, now)
                basis = {"file_id": file_id, "from_status": required_status, "to_status": next_status,
                         "new_plot_code": case["new_plot_code"], "right_version": case["right_version"]}
                repo.insert_decision(case_id, point, "approved", {}, basis, payload["actor"], payload["idempotency_key"], now)
                MortuaryRepository(connection).event("relocation_case", case_id, f"{point}.completed", payload["actor"], basis, now)
                if point == "old_plot_close":
                    connection.execute("UPDATE burial_rights SET status='relocated',version=version+1,updated_at=? WHERE id=?", (now, case["right_id"]))
                result_case = self.get_case(case_id, repo)
        if failure is not None:
            raise failure
        return result_case

    def reset_site(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """放弃已选新墓位，案件回到同意审定通过状态，可重新选址。"""
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            if case["status"] != "site_selected":
                raise ConflictError("只有已确认新墓位但尚未交接的案件可以恢复重选", context={"status": case["status"]})
            basis = {"released_plot_code": case["new_plot_code"], "reason": payload["reason"]}
            repo.update_status(case_id, "consent_approved", now, clear_new_plot=True)
            repo.insert_decision(case_id, "site_reset", "approved", {}, basis, payload["actor"], "", now)
            MortuaryRepository(connection).event("relocation_case", case_id, "site.reset", payload["actor"], basis, now)
            return self.get_case(case_id, repo)

    def withdraw(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """撤回审批：只改变案件状态，亲属、意见、文件、决定全部保留。"""
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = RelocationRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("迁移案件不存在")
            if case["status"] == "withdrawn":
                return case
            if case["status"] not in WITHDRAWABLE:
                raise ConflictError("已进入交接或施工阶段的案件不能撤回，须按异常流程处理", context={"status": case["status"]})
            repo.update_status(case_id, "withdrawn", now, clear_new_plot=(case["new_plot_code"] is not None), withdrawn_at=now)
            basis = {"reason": payload["reason"], "retained": {"kin": True, "opinions": True, "files": True, "decisions": True}}
            repo.insert_decision(case_id, "withdraw", "approved", {}, basis, payload["actor"], "", now)
            MortuaryRepository(connection).event("relocation_case", case_id, "case.withdrawn", payload["actor"], basis, now)
            return self.get_case(case_id, repo)

    # ---------- 决定追溯与批量核查 ----------

    def get_decision(self, case_id: int, decision_id: int) -> dict[str, Any]:
        case = self.repository.case(case_id)
        if case is None:
            raise NotFoundError("迁移案件不存在")
        decisions = self.repository.list_decisions(case_id)
        decision = next((item for item in decisions if item["id"] == decision_id), None)
        if decision is None:
            raise NotFoundError("决定记录不存在")
        decision["dependencies"] = self._decision_dependencies(case, decision)
        decision["point_label"] = STAGE_LABELS.get(decision["point"], decision["point"])
        return decision

    def _decision_dependencies(self, case: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
        """展开每个决定依赖的亲属、文件和权属版本。"""
        repo = self.repository
        kin_ids: set[int] = set()
        file_ids: set[int] = set()
        opinion_ids: set[int] = set()
        basis = decision["basis"]

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"kin_id"} and isinstance(item, int):
                        kin_ids.add(item)
                    elif key == "file_id" and isinstance(item, int):
                        file_ids.add(item)
                    elif key == "opinion_id" and isinstance(item, int):
                        opinion_ids.add(item)
                    else:
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(basis)
        # 同意审定还依赖全部登记亲属及其关系证明。
        if decision["point"] == "consent_review" and decision["result"] == "approved":
            for kin in repo.list_kin(case["id"]):
                kin_ids.add(kin["id"])
                if kin["proof_file_id"]:
                    file_ids.add(kin["proof_file_id"])
        kin_rows = {kin["id"]: kin for kin in repo.list_kin(case["id"])}
        file_rows = {f["id"]: f for f in repo.list_files(case["id"])}
        opinion_rows = {op["id"]: op for op in repo.list_opinions(case["id"])}
        snapshot = json.loads(case["right_snapshot_json"])
        return {
            "right_version": case["right_version"],
            "snapshot_hash": case["snapshot_hash"],
            "right_snapshot": snapshot,
            "kin": [{"kin_id": kin_id, "relative_name": kin_rows[kin_id]["relative_name"],
                     "relative_identity": kin_rows[kin_id]["relative_identity"],
                     "relationship": kin_rows[kin_id]["relationship"],
                     "proof_file_id": kin_rows[kin_id]["proof_file_id"]}
                    for kin_id in sorted(kin_ids) if kin_id in kin_rows],
            "files": [{"file_id": file_id, "file_code": file_rows[file_id]["file_code"],
                       "kind": file_rows[file_id]["kind"], "title": file_rows[file_id]["title"],
                       "sha256": file_rows[file_id]["sha256"], "status": file_rows[file_id]["status"]}
                      for file_id in sorted(file_ids) if file_id in file_rows],
            "opinions": [{"opinion_id": oid, "kin_id": opinion_rows[oid]["kin_id"],
                          "decision": opinion_rows[oid]["decision"]}
                         for oid in sorted(opinion_ids) if oid in opinion_rows],
        }

    def batch_check(self, project_code: str) -> dict[str, Any]:
        cases = self.repository.project_cases(project_code)
        if not cases:
            raise NotFoundError("项目不存在或没有迁移案件")
        active_cases = {case["id"]: case for case in cases if case["status"] != "withdrawn"}

        # 同一亲属在项目内重复授权。
        consent_by_identity: dict[str, list[dict[str, Any]]] = {}
        for opinion in self.repository.project_opinions(project_code):
            if opinion["decision"] != "consent" or opinion["case_id"] not in active_cases:
                continue
            consent_by_identity.setdefault(opinion["relative_identity"], []).append(opinion)
        duplicate_authorizations = []
        for identity, rows in consent_by_identity.items():
            if len({row["case_id"] for row in rows}) > 1:
                duplicate_authorizations.append({
                    "relative_identity": identity,
                    "relative_name": rows[0]["relative_name"],
                    "records": [{"case_id": row["case_id"], "opinion_id": row["id"],
                                 "duplicate_acknowledged": bool(row["duplicate_acknowledged"])} for row in rows],
                    "needs_confirmation": any(not row["duplicate_acknowledged"] for row in rows),
                })

        # 新墓位冲突（正常情况下唯一索引已阻止，这里做批量巡检）。
        plot_holders: dict[str, list[int]] = {}
        for case in active_cases.values():
            if case["new_plot_code"]:
                plot_holders.setdefault(case["new_plot_code"], []).append(case["id"])
        plot_conflicts = [{"new_plot_code": plot, "case_ids": case_ids}
                          for plot, case_ids in plot_holders.items() if len(case_ids) > 1]
        return {
            "project_code": project_code,
            "case_count": len(cases),
            "active_case_count": len(active_cases),
            "duplicate_authorizations": duplicate_authorizations,
            "new_plot_conflicts": plot_conflicts,
            "blocked_cases": [case["id"] for case in active_cases.values() if self.repository.active_holds(case["id"])],
        }
