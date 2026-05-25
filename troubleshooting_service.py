"""Local troubleshooting helpers for logs and the app database."""

from __future__ import annotations

import os
import sqlite3

from storage import APP_DIR, Storage


class TroubleshootingService:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.log_dir = os.path.join(APP_DIR, "logs")

    def summary(self) -> dict:
        return {
            "ok": True,
            "app_dir": APP_DIR,
            "db_path": self.storage.db_path,
            "log_dir": self.log_dir,
            "logs": self.list_logs().get("logs", []),
        }

    def list_logs(self) -> dict:
        if not os.path.isdir(self.log_dir):
            return {"ok": True, "logs": []}
        logs = []
        for name in sorted(os.listdir(self.log_dir)):
            path = os.path.join(self.log_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                st = os.stat(path)
            except Exception:
                continue
            logs.append({"name": name, "size": st.st_size, "updated_at": st.st_mtime})
        logs.sort(key=lambda x: x["updated_at"], reverse=True)
        return {"ok": True, "logs": logs[:50]}

    def read_log(self, name: str, max_bytes: int = 200000) -> dict:
        safe = os.path.basename(str(name or ""))
        if not safe:
            return {"ok": False, "error": "Log name is required"}
        path = os.path.abspath(os.path.join(self.log_dir, safe))
        if not path.startswith(os.path.abspath(self.log_dir) + os.sep):
            return {"ok": False, "error": "Invalid log path"}
        try:
            with open(path, "rb") as f:
                raw = f.read(max_bytes)
            return {"ok": True, "name": safe, "content": raw.decode("utf-8", errors="replace")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def query_app_db(self, sql: str, limit: int = 100) -> dict:
        query = (sql or "").strip()
        lowered = query.lower()
        allowed = lowered.startswith("select ") or lowered.startswith("with ") or lowered.startswith("pragma table_info")
        if not allowed or ";" in query.rstrip(";"):
            return {"ok": False, "error": "Only one read-only SELECT/WITH/PRAGMA table_info query is allowed."}
        try:
            conn = sqlite3.connect(f"file:{self.storage.db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                rows = [dict(r) for r in conn.execute(query).fetchmany(max(1, min(int(limit or 100), 500)))]
            finally:
                conn.close()
            return {"ok": True, "rows": rows, "count": len(rows)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
