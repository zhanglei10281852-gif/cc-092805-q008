"""墓位迁移案件的数据访问与结构。

迁移案件要求冻结申请时的权属版本，因此本模块只通过
``right_version``/``right_snapshot_json`` 引用历史快照，业务判断不直接读取
正在变化的权属表。亲属、意见、材料、司法暂停和决定全部追加保存，撤回审批
不会删除任何已收材料。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


SCHEMA = r'''
CREATE TABLE IF NOT EXISTS relocation_consent_configs (
 scope TEXT NOT NULL CHECK(scope IN ('global','project')),
 scope_key TEXT NOT NULL DEFAULT '',
 rule_type TEXT NOT NULL CHECK(rule_type IN ('unanimous','majority','threshold','holder_only')),
 params_json TEXT NOT NULL DEFAULT '{}',
 updated_by TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(scope,scope_key)
);
CREATE TABLE IF NOT EXISTS relocation_cases (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_code TEXT NOT NULL,
 plot_code TEXT NOT NULL,
 right_id INTEGER NOT NULL REFERENCES burial_rights(id),
 right_version INTEGER NOT NULL,
 right_snapshot_json TEXT NOT NULL,
 snapshot_hash TEXT NOT NULL,
 consent_rule_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'applied' CHECK(status IN
   ('applied','consent_approved','site_selected','remains_handed_over','construction_completed','old_plot_closed','withdrawn')),
 new_plot_code TEXT,
 created_by TEXT NOT NULL,
 withdrawn_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(project_code,plot_code)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_relocation_new_plot
 ON relocation_cases(new_plot_code) WHERE new_plot_code IS NOT NULL AND status<>'withdrawn';
CREATE TABLE IF NOT EXISTS relocation_kin (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 case_id INTEGER NOT NULL REFERENCES relocation_cases(id),
 relative_name TEXT NOT NULL,
 relative_identity TEXT NOT NULL,
 relationship TEXT NOT NULL,
 proof_file_id INTEGER REFERENCES relocation_files(id),
 contact TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'registered' CHECK(status IN ('registered','disputed')),
 created_at TEXT NOT NULL,
 UNIQUE(case_id,relative_identity)
);
CREATE TABLE IF NOT EXISTS relocation_files (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 case_id INTEGER NOT NULL REFERENCES relocation_cases(id),
 kin_id INTEGER REFERENCES relocation_kin(id),
 kind TEXT NOT NULL CHECK(kind IN
   ('relationship_proof','consent_letter','judicial_notice','site_record','handover_receipt','completion_report','closure_record','other')),
 file_code TEXT NOT NULL,
 title TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 size_bytes INTEGER NOT NULL DEFAULT 0,
 content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
 status TEXT NOT NULL DEFAULT 'available' CHECK(status IN ('available','voided')),
 uploaded_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(case_id,file_code)
);
CREATE TABLE IF NOT EXISTS relocation_opinions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 case_id INTEGER NOT NULL REFERENCES relocation_cases(id),
 kin_id INTEGER NOT NULL REFERENCES relocation_kin(id),
 decision TEXT NOT NULL CHECK(decision IN ('consent','object','abstain')),
 file_id INTEGER REFERENCES relocation_files(id),
 duplicate_acknowledged INTEGER NOT NULL DEFAULT 0,
 collected_by TEXT NOT NULL,
 decided_at TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_relocation_opinion_case ON relocation_opinions(case_id,kin_id,id);
CREATE TABLE IF NOT EXISTS relocation_holds (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 case_id INTEGER NOT NULL REFERENCES relocation_cases(id),
 hold_type TEXT NOT NULL CHECK(hold_type IN ('judicial_suspension','identity_dispute')),
 kin_id INTEGER REFERENCES relocation_kin(id),
 reference TEXT NOT NULL,
 file_id INTEGER REFERENCES relocation_files(id),
 reason TEXT NOT NULL DEFAULT '',
 raised_by TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','lifted')),
 raised_at TEXT NOT NULL,
 lifted_by TEXT,
 lifted_at TEXT,
 lift_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_relocation_hold_case ON relocation_holds(case_id,status,id);
CREATE TABLE IF NOT EXISTS relocation_decisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 case_id INTEGER NOT NULL REFERENCES relocation_cases(id),
 point TEXT NOT NULL,
 result TEXT NOT NULL CHECK(result IN ('approved','rejected','blocked')),
 rule_json TEXT NOT NULL DEFAULT '{}',
 basis_json TEXT NOT NULL DEFAULT '{}',
 actor TEXT NOT NULL,
 idempotency_key TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_relocation_decision_idem
 ON relocation_decisions(case_id,idempotency_key) WHERE idempotency_key<>'';
CREATE INDEX IF NOT EXISTS idx_relocation_decision_case ON relocation_decisions(case_id,id);
'''


class RelocationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def ensure_schema(self) -> None:
        self.connection.executescript(SCHEMA)

    @staticmethod
    def one(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return None if row is None else dict(row)

    def config(self, scope: str, scope_key: str = "") -> dict[str, Any] | None:
        return self.one(self.connection.execute(
            "SELECT * FROM relocation_consent_configs WHERE scope=? AND scope_key=?",
            (scope, scope_key),
        ).fetchone())

    def upsert_config(self, scope: str, scope_key: str, rule_type: str, params: dict[str, Any], actor: str, now: str) -> None:
        self.connection.execute(
            "INSERT INTO relocation_consent_configs(scope,scope_key,rule_type,params_json,updated_by,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(scope,scope_key) DO UPDATE SET rule_type=excluded.rule_type,params_json=excluded.params_json,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
            (scope, scope_key, rule_type, json.dumps(params, ensure_ascii=False, sort_keys=True), actor, now),
        )

    def case(self, case_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_cases WHERE id=?", (case_id,)).fetchone())

    def case_plot(self, project_code: str, plot_code: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute(
            "SELECT * FROM relocation_cases WHERE project_code=? AND plot_code=?",
            (project_code, plot_code),
        ).fetchone())

    def list_cases(self, project_code: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM relocation_cases"
        clauses = []
        params: list[Any] = []
        if project_code:
            clauses.append("project_code=?")
            params.append(project_code)
        if status:
            clauses.append("status=?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    def insert_case(self, values: dict[str, Any]) -> int:
        cursor = self.connection.execute(
            "INSERT INTO relocation_cases(project_code,plot_code,right_id,right_version,right_snapshot_json,snapshot_hash,consent_rule_json,status,created_by,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,'applied',?,?,?)",
            (values["project_code"], values["plot_code"], values["right_id"], values["right_version"],
             values["right_snapshot_json"], values["snapshot_hash"], values["consent_rule_json"],
             values["created_by"], values["now"], values["now"]),
        )
        return int(cursor.lastrowid)

    def update_status(self, case_id: int, status: str, now: str, *, new_plot_code: str | None = None, clear_new_plot: bool = False, withdrawn_at: str | None = None) -> None:
        if clear_new_plot:
            self.connection.execute(
                "UPDATE relocation_cases SET status=?,new_plot_code=NULL,withdrawn_at=?,updated_at=? WHERE id=?",
                (status, withdrawn_at, now, case_id),
            )
        elif new_plot_code is not None:
            self.connection.execute(
                "UPDATE relocation_cases SET status=?,new_plot_code=?,updated_at=? WHERE id=?",
                (status, new_plot_code, now, case_id),
            )
        else:
            self.connection.execute(
                "UPDATE relocation_cases SET status=?,withdrawn_at=COALESCE(?,withdrawn_at),updated_at=? WHERE id=?",
                (status, withdrawn_at, now, case_id),
            )

    def plot_taken(self, new_plot_code: str, exclude_case_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute(
            "SELECT * FROM relocation_cases WHERE new_plot_code=? AND status<>'withdrawn' AND id<>?",
            (new_plot_code, exclude_case_id),
        ).fetchone())

    def file(self, file_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_files WHERE id=?", (file_id,)).fetchone())

    def file_code(self, case_id: int, file_code: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute(
            "SELECT * FROM relocation_files WHERE case_id=? AND file_code=?",
            (case_id, file_code),
        ).fetchone())

    def insert_file(self, case_id: int, values: dict[str, Any], kin_id: int | None = None) -> int:
        cursor = self.connection.execute(
            "INSERT INTO relocation_files(case_id,kin_id,kind,file_code,title,sha256,size_bytes,content_type,uploaded_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (case_id, kin_id, values["kind"], values["file_code"], values["title"], values["sha256"],
             int(values.get("size_bytes", 0)), values.get("content_type", "application/octet-stream"),
             values["uploaded_by"], values["now"]),
        )
        return int(cursor.lastrowid)

    def list_files(self, case_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM relocation_files WHERE case_id=? ORDER BY id", (case_id,),
        ).fetchall()]

    def kin(self, kin_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_kin WHERE id=?", (kin_id,)).fetchone())

    def insert_kin(self, case_id: int, values: dict[str, Any], proof_file_id: int | None, now: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO relocation_kin(case_id,relative_name,relative_identity,relationship,proof_file_id,contact,status,created_at) "
            "VALUES(?,?,?,?,?,?,'registered',?)",
            (case_id, values["relative_name"], values["relative_identity"], values["relationship"],
             proof_file_id, values.get("contact", ""), now),
        )
        return int(cursor.lastrowid)

    def set_kin_status(self, kin_id: int, status: str) -> None:
        self.connection.execute("UPDATE relocation_kin SET status=? WHERE id=?", (status, kin_id))

    def list_kin(self, case_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM relocation_kin WHERE case_id=? ORDER BY id", (case_id,),
        ).fetchall()]

    def insert_opinion(self, case_id: int, values: dict[str, Any], now: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO relocation_opinions(case_id,kin_id,decision,file_id,duplicate_acknowledged,collected_by,decided_at,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (case_id, values["kin_id"], values["decision"], values.get("file_id"),
             1 if values.get("allow_duplicate") else 0, values["collected_by"], now, now),
        )
        return int(cursor.lastrowid)

    def list_opinions(self, case_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM relocation_opinions WHERE case_id=? ORDER BY id", (case_id,),
        ).fetchall()]

    def insert_hold(self, case_id: int, values: dict[str, Any], now: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO relocation_holds(case_id,hold_type,kin_id,reference,file_id,reason,raised_by,status,raised_at) "
            "VALUES(?,?,?,?,?,?,?,'active',?)",
            (case_id, values["hold_type"], values.get("kin_id"), values["reference"],
             values.get("file_id"), values.get("reason", ""), values["raised_by"], now),
        )
        return int(cursor.lastrowid)

    def hold(self, hold_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_holds WHERE id=?", (hold_id,)).fetchone())

    def lift_hold(self, hold_id: int, actor: str, note: str, now: str) -> None:
        self.connection.execute(
            "UPDATE relocation_holds SET status='lifted',lifted_by=?,lifted_at=?,lift_note=? WHERE id=?",
            (actor, now, note, hold_id),
        )

    def list_holds(self, case_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM relocation_holds WHERE case_id=? ORDER BY id", (case_id,),
        ).fetchall()]

    def active_holds(self, case_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM relocation_holds WHERE case_id=? AND status='active' ORDER BY id", (case_id,),
        ).fetchall()]

    def insert_decision(self, case_id: int, point: str, result: str, rule: dict[str, Any], basis: dict[str, Any], actor: str, idempotency_key: str, now: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO relocation_decisions(case_id,point,result,rule_json,basis_json,actor,idempotency_key,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (case_id, point, result, json.dumps(rule, ensure_ascii=False, sort_keys=True),
             json.dumps(basis, ensure_ascii=False, sort_keys=True), actor, idempotency_key, now),
        )
        return int(cursor.lastrowid)

    def decision_idempotency(self, case_id: int, key: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute(
            "SELECT * FROM relocation_decisions WHERE case_id=? AND idempotency_key=?",
            (case_id, key),
        ).fetchone())

    def list_decisions(self, case_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM relocation_decisions WHERE case_id=? ORDER BY id", (case_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["rule"] = json.loads(item.pop("rule_json"))
            item["basis"] = json.loads(item.pop("basis_json"))
            result.append(item)
        return result

    def project_cases(self, project_code: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM relocation_cases WHERE project_code=? ORDER BY id", (project_code,),
        ).fetchall()]

    def project_opinions(self, project_code: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT o.*,k.relative_name,k.relative_identity FROM relocation_opinions o "
            "JOIN relocation_kin k ON k.id=o.kin_id JOIN relocation_cases c ON c.id=o.case_id "
            "WHERE c.project_code=? ORDER BY o.id", (project_code,),
        ).fetchall()
        return [dict(row) for row in rows]
