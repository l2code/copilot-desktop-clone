"""Local workflow definitions and runs."""

from __future__ import annotations

import os
import subprocess

from activity import ActivityLog
from storage import json_dumps, json_loads, new_id, now_ts, Storage


class WorkflowService:
    def __init__(self, storage: Storage, activity: ActivityLog):
        self.storage = storage
        self.activity = activity

    def list_workflows(self, project_id: str) -> list[dict]:
        rows = self.storage.query(
            """
            SELECT * FROM workflows
            WHERE project_id = ? AND archived_at IS NULL
            ORDER BY updated_at DESC
            """,
            (project_id,),
        )
        for row in rows:
            row["definition"] = json_loads(row.pop("definition_json", None), {})
        return rows

    def save_workflow(self, project_id: str, workflow: dict) -> dict:
        ts = now_ts()
        wid = workflow.get("id") or new_id("wf")
        definition = workflow.get("definition") or {
            "command": workflow.get("command") or "",
            "description": workflow.get("description") or "",
        }
        row = {
            "id": wid,
            "project_id": project_id,
            "name": workflow.get("name") or "Workflow",
            "definition_json": json_dumps(definition),
            "created_at": ts,
            "updated_at": ts,
        }
        self.storage.execute(
            """
            INSERT INTO workflows(id, project_id, name, definition_json, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,
              definition_json=excluded.definition_json,
              updated_at=excluded.updated_at
            """,
            (row["id"], row["project_id"], row["name"], row["definition_json"], row["created_at"], row["updated_at"]),
        )
        row["definition"] = definition
        row.pop("definition_json", None)
        return row

    def run_workflow(self, workflow_id: str, workspace: dict) -> dict:
        workflow = self.storage.query_one(
            "SELECT * FROM workflows WHERE id = ? AND archived_at IS NULL",
            (workflow_id,),
        )
        if not workflow:
            return {"ok": False, "error": "Workflow not found"}
        definition = json_loads(workflow.get("definition_json"), {})
        command = (definition.get("command") or "").strip()
        if not command:
            return {"ok": False, "error": "Workflow has no command"}
        ts = now_ts()
        run_id = new_id("wfr")
        env = os.environ.copy()
        env.update({
            "COPILOT_WORKSPACE_NAME": workspace.get("name") or "",
            "COPILOT_WORKSPACE_PATH": workspace.get("path") or "",
            "COPILOT_ROOT_PATH": workspace.get("path") or "",
            "COPILOT_DEFAULT_BRANCH": workspace.get("base_branch") or "",
            "COPILOT_SCRIPT_TRIGGER": "workflow",
        })
        self.storage.execute(
            """
            INSERT INTO workflow_runs(id, workflow_id, workspace_id, status, output, metadata_json, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, workflow_id, workspace["id"], "running", "", json_dumps({}), ts, ts),
        )
        try:
            cp = subprocess.run(
                command,
                cwd=workspace["path"],
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=int(definition.get("timeout") or 300),
                env=env,
            )
            status = "succeeded" if cp.returncode == 0 else "failed"
            output = cp.stdout[-20000:]
            ok = cp.returncode == 0
        except Exception as e:
            status = "failed"
            output = str(e)
            ok = False
        done = now_ts()
        self.storage.execute(
            "UPDATE workflow_runs SET status = ?, output = ?, updated_at = ? WHERE id = ?",
            (status, output, done, run_id),
        )
        self.activity.add(
            "workflow",
            f"Workflow {status}",
            workflow.get("name"),
            project_id=workspace.get("project_id"),
            workspace_id=workspace.get("id"),
            metadata={"workflow_id": workflow_id, "run_id": run_id},
        )
        return {"ok": ok, "run_id": run_id, "status": status, "output": output}

    def list_workflow_runs(self, workspace_id: str, limit: int = 20) -> list[dict]:
        return self.storage.query(
            """
            SELECT wr.*, w.name AS workflow_name
            FROM workflow_runs wr
            JOIN workflows w ON w.id = wr.workflow_id
            WHERE wr.workspace_id = ?
            ORDER BY wr.created_at DESC
            LIMIT ?
            """,
            (workspace_id, limit),
        )
