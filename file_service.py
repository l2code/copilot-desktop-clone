"""Workspace file listing and search helpers."""

from __future__ import annotations

import os


SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}


class FileService:
    def list_tree(self, base: str, max_entries: int = 500) -> dict:
        root = os.path.abspath(os.path.expanduser(base))
        if not os.path.isdir(root):
            return {"ok": False, "error": "Workspace folder does not exist", "files": []}
        items = []
        for cur, dirs, files in os.walk(root):
            dirs[:] = sorted([d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")])[:80]
            depth = os.path.relpath(cur, root).count(os.sep)
            if depth > 4:
                dirs[:] = []
            for name in sorted(files):
                if name.startswith("."):
                    continue
                full = os.path.join(cur, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                try:
                    size = os.path.getsize(full)
                except Exception:
                    size = 0
                items.append({"path": rel, "name": name, "size": size})
                if len(items) >= max_entries:
                    return {"ok": True, "base": root, "files": items, "truncated": True}
        return {"ok": True, "base": root, "files": items, "truncated": False}

    def search(self, base: str, query: str, max_entries: int = 100) -> dict:
        q = (query or "").lower()
        if not q:
            return self.list_tree(base, max_entries=max_entries)
        tree = self.list_tree(base, max_entries=3000)
        if not tree.get("ok"):
            return tree
        files = [f for f in tree["files"] if q in f["path"].lower()][:max_entries]
        return {"ok": True, "base": tree["base"], "files": files, "truncated": len(files) >= max_entries}

    def read_file(self, base: str, path: str, max_bytes: int = 400000) -> dict:
        root = os.path.abspath(os.path.expanduser(base))
        full = os.path.abspath(os.path.join(root, str(path or "")))
        if not (full == root or full.startswith(root + os.sep)):
            return {"ok": False, "error": "File is outside the workspace"}
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                return {"ok": True, "path": full, "content": f.read(max_bytes)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
