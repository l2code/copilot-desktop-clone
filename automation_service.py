"""Stored session automations.

This MVP persists automation definitions so the UI and session model are ready
for a scheduler without silently running background jobs.
"""

from __future__ import annotations

from storage import json_dumps, json_loads, new_id, now_ts, Storage


class AutomationService:
    def __init__(self, storage: Storage):
        self.storage = storage

    def list_session_automations(self, session_id: str) -> list[dict]:
        rows = self.storage.query(
            """
            SELECT * FROM session_automations
            WHERE session_id = ? AND archived_at IS NULL
            ORDER BY updated_at DESC
            """,
            (session_id,),
        )
        for row in rows:
            row["schedule"] = json_loads(row.pop("schedule_json", None), {})
        return rows

    def save_session_automation(self, session_id: str, automation: dict) -> dict:
        ts = now_ts()
        aid = automation.get("id") or new_id("auto")
        row = {
            "id": aid,
            "session_id": session_id,
            "name": automation.get("name") or "Automation",
            "prompt": automation.get("prompt") or "",
            "schedule_json": json_dumps(automation.get("schedule") or {}),
            "status": automation.get("status") or "paused",
            "created_at": ts,
            "updated_at": ts,
        }
        self.storage.execute(
            """
            INSERT INTO session_automations
            (id, session_id, name, prompt, schedule_json, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,
              prompt=excluded.prompt,
              schedule_json=excluded.schedule_json,
              status=excluded.status,
              updated_at=excluded.updated_at
            """,
            tuple(row[k] for k in ("id", "session_id", "name", "prompt", "schedule_json", "status", "created_at", "updated_at")),
        )
        row["schedule"] = automation.get("schedule") or {}
        row.pop("schedule_json", None)
        return row

    def delete_session_automation(self, automation_id: str) -> dict:
        ts = now_ts()
        self.storage.execute(
            "UPDATE session_automations SET archived_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, automation_id),
        )
        return {"ok": True}
