from __future__ import annotations

import json
import sqlite3
from typing import Any

SCHEMA = r'''
CREATE TABLE IF NOT EXISTS relocation_consent_rules (
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 min_approvals INTEGER NOT NULL DEFAULT 1, max_objections INTEGER NOT NULL DEFAULT 0,
 require_unanimous INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
 created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relocation_cases (
 id INTEGER PRIMARY KEY AUTOINCREMENT, case_no TEXT NOT NULL UNIQUE,
 source_plot_code TEXT NOT NULL,
 right_id INTEGER NOT NULL REFERENCES burial_rights(id),
 right_version_frozen INTEGER NOT NULL, right_snapshot_json TEXT NOT NULL,
 consent_rule_code TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'collecting'
  CHECK(status IN ('collecting','approved','implementing','completed','withdrawn')),
 new_plot_code TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
 created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_relocation_case_plot ON relocation_cases(source_plot_code,status);
CREATE TABLE IF NOT EXISTS relocation_kin_opinions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL REFERENCES relocation_cases(id),
 kin_name TEXT NOT NULL, kin_identity TEXT NOT NULL, relationship TEXT NOT NULL,
 proof_document_no TEXT NOT NULL, proof_verified INTEGER NOT NULL DEFAULT 0,
 proof_verified_by TEXT NOT NULL DEFAULT '', proof_verified_at TEXT,
 stance TEXT NOT NULL CHECK(stance IN ('approve','object')),
 note TEXT NOT NULL DEFAULT '', idempotency_key TEXT NOT NULL,
 submitted_by TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(case_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_relocation_opinion_case ON relocation_kin_opinions(case_id,kin_identity,id);
CREATE TABLE IF NOT EXISTS relocation_decisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL REFERENCES relocation_cases(id),
 decision TEXT NOT NULL CHECK(decision IN ('consent_approved','consent_rejected')),
 rule_code TEXT NOT NULL, rule_snapshot_json TEXT NOT NULL,
 right_version INTEGER NOT NULL, opinion_ids_json TEXT NOT NULL DEFAULT '[]',
 kin_identities_json TEXT NOT NULL DEFAULT '[]', proof_documents_json TEXT NOT NULL DEFAULT '[]',
 reasons_json TEXT NOT NULL DEFAULT '[]', created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_relocation_decision_case ON relocation_decisions(case_id,id);
CREATE TABLE IF NOT EXISTS relocation_holds (
 id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL REFERENCES relocation_cases(id),
 hold_type TEXT NOT NULL CHECK(hold_type IN ('judicial','identity_dispute','payment_dispute')),
 reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','released')),
 raised_by TEXT NOT NULL, raised_at TEXT NOT NULL,
 released_by TEXT NOT NULL DEFAULT '', released_at TEXT, release_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_relocation_hold_case ON relocation_holds(case_id,status);
CREATE TABLE IF NOT EXISTS relocation_steps (
 id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL REFERENCES relocation_cases(id),
 step TEXT NOT NULL CHECK(step IN ('new_plot_confirmed','remains_transferred','construction_completed','old_plot_closed')),
 reference TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
 payload_json TEXT NOT NULL DEFAULT '{}', idempotency_key TEXT NOT NULL,
 recorded_by TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(case_id,step)
);
CREATE TABLE IF NOT EXISTS relocation_batches (
 id INTEGER PRIMARY KEY AUTOINCREMENT, batch_no TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relocation_batch_items (
 batch_id INTEGER NOT NULL REFERENCES relocation_batches(id),
 case_id INTEGER NOT NULL REFERENCES relocation_cases(id),
 added_by TEXT NOT NULL, added_at TEXT NOT NULL,
 PRIMARY KEY(batch_id,case_id)
);
CREATE TABLE IF NOT EXISTS relocation_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, aggregate_type TEXT NOT NULL, aggregate_id TEXT NOT NULL,
 event_type TEXT NOT NULL, actor TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_relocation_event ON relocation_events(aggregate_type,aggregate_id,id);
'''

STEP_ORDER = ("new_plot_confirmed", "remains_transferred", "construction_completed", "old_plot_closed")

TERMINAL_STATUSES = {"completed", "withdrawn"}


class RelocationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def ensure_schema(self) -> None:
        self.connection.executescript(SCHEMA)

    @staticmethod
    def one(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return None if row is None else dict(row)

    def event(self, kind: str, aggregate_id: int | str, event_type: str, actor: str, payload: dict[str, Any], now: str) -> None:
        self.connection.execute("INSERT INTO relocation_events(aggregate_type,aggregate_id,event_type,actor,payload_json,created_at) VALUES(?,?,?,?,?,?)", (kind, str(aggregate_id), event_type, actor, json.dumps(payload, ensure_ascii=False, sort_keys=True), now))

    def rule(self, rule_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_consent_rules WHERE id=?", (rule_id,)).fetchone())

    def rule_code(self, code: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_consent_rules WHERE code=?", (code,)).fetchone())

    def case(self, case_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_cases WHERE id=?", (case_id,)).fetchone())

    def case_no(self, case_no: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_cases WHERE case_no=?", (case_no,)).fetchone())

    def active_case_for_plot(self, plot_code: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_cases WHERE source_plot_code=? AND status NOT IN ('completed','withdrawn') ORDER BY id DESC LIMIT 1", (plot_code,)).fetchone())

    def opinion(self, opinion_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_kin_opinions WHERE id=?", (opinion_id,)).fetchone())

    def opinion_key(self, case_id: int, key: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_kin_opinions WHERE case_id=? AND idempotency_key=?", (case_id, key)).fetchone())

    def opinions(self, case_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM relocation_kin_opinions WHERE case_id=? ORDER BY id", (case_id,)).fetchall()
        return [dict(row) for row in rows]

    def latest_opinions(self, case_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT o.* FROM relocation_kin_opinions o JOIN (SELECT kin_identity,MAX(id) AS max_id FROM relocation_kin_opinions WHERE case_id=? GROUP BY kin_identity) latest ON latest.max_id=o.id ORDER BY o.id",
            (case_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def decision(self, decision_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_decisions WHERE id=?", (decision_id,)).fetchone())

    def decisions(self, case_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM relocation_decisions WHERE case_id=? ORDER BY id", (case_id,)).fetchall()
        return [dict(row) for row in rows]

    def hold(self, hold_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_holds WHERE id=?", (hold_id,)).fetchone())

    def holds(self, case_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM relocation_holds WHERE case_id=? ORDER BY id", (case_id,)).fetchall()
        return [dict(row) for row in rows]

    def active_holds(self, case_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM relocation_holds WHERE case_id=? AND status='active' ORDER BY id", (case_id,)).fetchall()
        return [dict(row) for row in rows]

    def active_hold_of_type(self, case_id: int, hold_type: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_holds WHERE case_id=? AND hold_type=? AND status='active'", (case_id, hold_type)).fetchone())

    def steps(self, case_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM relocation_steps WHERE case_id=? ORDER BY id", (case_id,)).fetchall()
        return [dict(row) for row in rows]

    def step(self, case_id: int, step: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_steps WHERE case_id=? AND step=?", (case_id, step)).fetchone())

    def batch(self, batch_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_batches WHERE id=?", (batch_id,)).fetchone())

    def batch_no(self, batch_no: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_batches WHERE batch_no=?", (batch_no,)).fetchone())

    def batch_item(self, batch_id: int, case_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM relocation_batch_items WHERE batch_id=? AND case_id=?", (batch_id, case_id)).fetchone())

    def batch_cases(self, batch_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT c.* FROM relocation_batch_items i JOIN relocation_cases c ON c.id=i.case_id WHERE i.batch_id=? ORDER BY i.added_at,i.case_id", (batch_id,)).fetchall()
        return [dict(row) for row in rows]

    def timeline(self, kind: str, aggregate_id: int | str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM relocation_events WHERE aggregate_type=? AND aggregate_id=? ORDER BY id", (kind, str(aggregate_id))).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result
