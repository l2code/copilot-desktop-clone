"""Small, structured Git helpers for workspace-aware app features."""

from __future__ import annotations

import os
import re
import shutil
import subprocess


class GitError(RuntimeError):
    pass


def normalize_path(path: str | None) -> str:
    return os.path.abspath(os.path.expanduser(path or "~"))


def run_git(path: str, args: list[str], timeout: int = 20, check: bool = True) -> subprocess.CompletedProcess:
    git = shutil.which("git")
    if not git:
        raise GitError("Git is not installed or is not on PATH.")
    cwd = normalize_path(path)
    cp = subprocess.run(
        [git, "-C", cwd, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if check and cp.returncode != 0:
        msg = (cp.stderr or cp.stdout or "Git command failed").strip()
        raise GitError(msg)
    return cp


def _ok(cp: subprocess.CompletedProcess, **extra) -> dict:
    return {"ok": cp.returncode == 0, "stdout": cp.stdout, "stderr": cp.stderr, **extra}


def _safe_rel(path: str) -> str:
    value = str(path or "").replace("\\", "/").strip("/")
    if not value or value.startswith("../") or "/../" in value or value == "..":
        raise GitError("Unsafe repository path.")
    return value


def is_git_repo(path: str) -> bool:
    try:
        cp = run_git(path, ["rev-parse", "--is-inside-work-tree"], check=False)
        return cp.returncode == 0 and cp.stdout.strip() == "true"
    except Exception:
        return False


def parse_github_remote_url(url: str | None) -> tuple[str | None, str | None]:
    if not url:
        return None, None
    value = url.strip()
    patterns = [
        r"^https://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$",
        r"^git@github\.com:([^/\s]+)/([^/\s]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$",
    ]
    for pat in patterns:
        m = re.match(pat, value, re.IGNORECASE)
        if m:
            return m.group(1), m.group(2)
    return None, None


def get_remote_url(path: str, remote: str = "origin") -> str | None:
    if not is_git_repo(path):
        return None
    cp = run_git(path, ["remote", "get-url", remote], check=False)
    if cp.returncode != 0:
        return None
    return cp.stdout.strip() or None


def get_current_branch(path: str) -> str | None:
    if not is_git_repo(path):
        return None
    cp = run_git(path, ["branch", "--show-current"], check=False)
    branch = cp.stdout.strip()
    if branch:
        return branch
    cp = run_git(path, ["rev-parse", "--short", "HEAD"], check=False)
    return f"detached:{cp.stdout.strip()}" if cp.returncode == 0 and cp.stdout.strip() else None


def get_default_branch(path: str) -> str | None:
    if not is_git_repo(path):
        return None
    for args in (
        ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        ["config", "--get", "init.defaultBranch"],
    ):
        cp = run_git(path, args, check=False)
        out = cp.stdout.strip()
        if cp.returncode == 0 and out:
            return out.replace("origin/", "")
    for candidate in ("main", "master"):
        cp = run_git(path, ["show-ref", "--verify", f"refs/heads/{candidate}"], check=False)
        if cp.returncode == 0:
            return candidate
    return None


def describe_repository(path: str) -> dict:
    cwd = normalize_path(path)
    remote_url = get_remote_url(cwd)
    owner, repo = parse_github_remote_url(remote_url)
    return {
        "path": cwd,
        "is_repo": is_git_repo(cwd),
        "remote_url": remote_url,
        "owner": owner,
        "repo": repo,
        "current_branch": get_current_branch(cwd),
        "default_branch": get_default_branch(cwd),
    }


def parse_porcelain_v2_z(raw: str) -> list[dict]:
    files = []
    records = [r for r in raw.split("\0") if r]
    i = 0
    while i < len(records):
        rec = records[i]
        tag = rec[:1]
        if tag == "?":
            files.append({"path": rec[2:], "kind": "untracked", "index": "?", "worktree": "?"})
        elif tag == "!":
            files.append({"path": rec[2:], "kind": "ignored", "index": "!", "worktree": "!"})
        elif tag == "1":
            parts = rec.split(" ", 8)
            if len(parts) >= 9:
                xy = parts[1]
                files.append({"path": parts[8], "kind": "changed", "index": xy[:1], "worktree": xy[1:2]})
        elif tag == "2":
            parts = rec.split(" ", 9)
            if len(parts) >= 10:
                xy = parts[1]
                original = records[i + 1] if i + 1 < len(records) else ""
                files.append({
                    "path": parts[9],
                    "originalPath": original,
                    "kind": "renamed",
                    "index": xy[:1],
                    "worktree": xy[1:2],
                })
                i += 1
        elif tag == "u":
            parts = rec.split(" ", 10)
            if len(parts) >= 11:
                xy = parts[1]
                files.append({"path": parts[10], "kind": "conflicted", "index": xy[:1], "worktree": xy[1:2]})
        i += 1
    return files


def get_status(path: str) -> dict:
    cwd = normalize_path(path)
    if not is_git_repo(cwd):
        return {"ok": False, "error": "Not a Git repository", "path": cwd}
    cp = run_git(cwd, ["status", "--porcelain=v2", "-z", "--branch"], check=False)
    if cp.returncode != 0:
        return {"ok": False, "error": (cp.stderr or cp.stdout).strip(), "path": cwd}
    branch = get_current_branch(cwd)
    default_branch = get_default_branch(cwd)
    files = parse_porcelain_v2_z(cp.stdout)
    conflicted = [f for f in files if f["kind"] == "conflicted"]
    return {
        "ok": True,
        "path": cwd,
        "branch": branch,
        "defaultBranch": default_branch,
        "files": files,
        "conflicted": conflicted,
        "dirty": bool(files),
    }


def get_changed_files(path: str) -> dict:
    return get_status(path)


def get_file_diff(path: str, file_path: str, staged: bool = False) -> dict:
    cwd = normalize_path(path)
    rel = _safe_rel(file_path)
    args = ["diff"]
    if staged:
        args.append("--cached")
    args += ["--", rel]
    cp = run_git(cwd, args, check=False, timeout=30)
    return _ok(cp, diff=cp.stdout, path=rel, staged=bool(staged))


def stage_file(path: str, file_path: str) -> dict:
    rel = _safe_rel(file_path)
    cp = run_git(path, ["add", "--", rel], check=False)
    return _ok(cp)


def unstage_file(path: str, file_path: str) -> dict:
    rel = _safe_rel(file_path)
    cp = run_git(path, ["restore", "--staged", "--", rel], check=False)
    if cp.returncode != 0:
        cp = run_git(path, ["reset", "-q", "HEAD", "--", rel], check=False)
    return _ok(cp)


def stage_all(path: str) -> dict:
    cp = run_git(path, ["add", "-A"], check=False)
    return _ok(cp)


def unstage_all(path: str) -> dict:
    cp = run_git(path, ["restore", "--staged", "."], check=False)
    if cp.returncode != 0:
        cp = run_git(path, ["reset", "-q", "HEAD", "--", "."], check=False)
    return _ok(cp)


def discard_file(path: str, file_path: str) -> dict:
    cwd = normalize_path(path)
    rel = _safe_rel(file_path)
    full = os.path.abspath(os.path.join(cwd, rel))
    if not (full == cwd or full.startswith(cwd + os.sep)):
        raise GitError("Unsafe repository path.")
    cp = run_git(cwd, ["restore", "--worktree", "--", rel], check=False)
    if cp.returncode == 0:
        return _ok(cp)
    if os.path.exists(full):
        try:
            if os.path.isdir(full):
                shutil.rmtree(full)
            else:
                os.remove(full)
            return {"ok": True, "stdout": "", "stderr": ""}
        except Exception as e:
            return {"ok": False, "stdout": "", "stderr": str(e)}
    return _ok(cp)


def commit(path: str, summary: str, description: str | None = None) -> dict:
    msg = (summary or "").strip()
    if not msg:
        return {"ok": False, "error": "Commit summary is required."}
    args = ["commit", "-m", msg]
    if description and description.strip():
        args += ["-m", description.strip()]
    cp = run_git(path, args, check=False, timeout=60)
    return _ok(cp)


def list_branches(path: str) -> dict:
    if not is_git_repo(path):
        return {"ok": False, "error": "Not a Git repository", "branches": []}
    cp = run_git(path, [
        "for-each-ref",
        "--format=%(refname:short)%00%(HEAD)%00%(upstream:short)%00%(objectname:short)%00%(committerdate:relative)",
        "refs/heads",
    ], check=False)
    branches = []
    if cp.returncode == 0:
        for line in cp.stdout.splitlines():
            parts = line.split("\0")
            if len(parts) >= 5:
                branches.append({
                    "name": parts[0],
                    "current": parts[1] == "*",
                    "upstream": parts[2],
                    "sha": parts[3],
                    "updated": parts[4],
                })
    return {"ok": cp.returncode == 0, "branches": branches, "stderr": cp.stderr}


def create_branch(path: str, branch_name: str, base_branch: str | None = None, checkout: bool = True) -> dict:
    name = str(branch_name or "").strip()
    if not name or name.startswith("-") or ".." in name:
        return {"ok": False, "error": "Invalid branch name."}
    args = ["checkout", "-b", name]
    if base_branch:
        args.append(base_branch)
    if not checkout:
        args = ["branch", name] + ([base_branch] if base_branch else [])
    cp = run_git(path, args, check=False)
    return _ok(cp)


def checkout_branch(path: str, branch_name: str) -> dict:
    name = str(branch_name or "").strip()
    if not name or name.startswith("-"):
        return {"ok": False, "error": "Invalid branch name."}
    cp = run_git(path, ["checkout", name], check=False)
    return _ok(cp)


def rename_branch(path: str, old_name: str, new_name: str) -> dict:
    old = str(old_name or "").strip()
    new = str(new_name or "").strip()
    if not old or not new or old.startswith("-") or new.startswith("-"):
        return {"ok": False, "error": "Invalid branch name."}
    cp = run_git(path, ["branch", "-m", old, new], check=False)
    return _ok(cp)


def delete_branch(path: str, branch_name: str, force: bool = False) -> dict:
    name = str(branch_name or "").strip()
    if not name or name.startswith("-"):
        return {"ok": False, "error": "Invalid branch name."}
    cp = run_git(path, ["branch", "-D" if force else "-d", name], check=False)
    return _ok(cp)


def fetch(path: str) -> dict:
    cp = run_git(path, ["fetch", "--prune"], check=False, timeout=120)
    return _ok(cp)


def pull(path: str) -> dict:
    cp = run_git(path, ["pull", "--ff-only"], check=False, timeout=120)
    return _ok(cp)


def push(path: str) -> dict:
    cp = run_git(path, ["push"], check=False, timeout=120)
    if cp.returncode != 0 and "no upstream branch" in (cp.stderr or "").lower():
        branch = get_current_branch(path)
        if branch and not branch.startswith("detached:"):
            cp = run_git(path, ["push", "-u", "origin", branch], check=False, timeout=120)
    return _ok(cp)


def get_commit_history(path: str, limit: int = 50) -> dict:
    cp = run_git(path, [
        "log",
        f"-n{max(1, min(int(limit or 50), 200))}",
        "--date=iso",
        "--pretty=format:%H%x00%h%x00%an%x00%ad%x00%s",
    ], check=False)
    commits = []
    if cp.returncode == 0:
        for line in cp.stdout.splitlines():
            parts = line.split("\0")
            if len(parts) >= 5:
                commits.append({
                    "sha": parts[0],
                    "short": parts[1],
                    "author": parts[2],
                    "date": parts[3],
                    "subject": parts[4],
                })
    return {"ok": cp.returncode == 0, "commits": commits, "stderr": cp.stderr}


def get_commit_details(path: str, sha: str) -> dict:
    ref = str(sha or "").strip()
    if not re.match(r"^[A-Fa-f0-9]{6,40}$", ref):
        return {"ok": False, "error": "Invalid commit SHA."}
    cp = run_git(path, ["show", "--stat", "--patch", "--find-renames", ref], check=False, timeout=30)
    return _ok(cp, detail=cp.stdout)


def list_worktrees(path: str) -> dict:
    cp = run_git(path, ["worktree", "list", "--porcelain"], check=False)
    items = []
    current = {}
    if cp.returncode == 0:
        for line in cp.stdout.splitlines():
            if not line:
                if current:
                    items.append(current)
                    current = {}
                continue
            key, _, val = line.partition(" ")
            if key == "worktree":
                current["path"] = val
            elif key == "HEAD":
                current["head"] = val
            elif key == "branch":
                current["branch"] = val.replace("refs/heads/", "")
        if current:
            items.append(current)
    return {"ok": cp.returncode == 0, "worktrees": items, "stderr": cp.stderr}


def create_worktree(path: str, worktree_path: str, branch_name: str, base_branch: str | None = None) -> dict:
    target = normalize_path(worktree_path)
    name = str(branch_name or "").strip()
    if not name or name.startswith("-") or ".." in name:
        return {"ok": False, "error": "Invalid branch name."}
    args = ["worktree", "add", "-b", name, target]
    if base_branch:
        args.append(base_branch)
    cp = run_git(path, args, check=False, timeout=120)
    return _ok(cp, path=target, branch=name)
