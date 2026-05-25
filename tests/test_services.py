import json
import os
import tempfile
import unittest
from unittest.mock import patch

from activity import ActivityLog
from file_service import FileService
from git_service import parse_github_remote_url, parse_gitlab_remote_url, parse_porcelain_v2_z
from project_service import ProjectService
from session_manager import SessionManager
from storage import Storage
from workspace_service import WorkspaceService


class ServiceTests(unittest.TestCase):
    def make_services(self):
        storage = Storage(":memory:")
        activity = ActivityLog(storage)
        projects = ProjectService(storage)
        workspaces = WorkspaceService(storage)
        sessions = SessionManager(storage, projects, workspaces, activity)
        return storage, activity, projects, workspaces, sessions

    def test_project_workspace_session_conversation_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, projects, workspaces, sessions = self.make_services()
            project = projects.create_project(d)
            workspace = workspaces.ensure_folder_workspace(project["id"], d)
            sessions.save_conversation(
                "c_test",
                "Hello",
                [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
                d,
            )

            convs = sessions.list_conversations()
            self.assertEqual(len(convs), 1)
            self.assertEqual(convs[0]["id"], "c_test")
            self.assertEqual(convs[0]["workspace_id"], workspace["id"])

            conv = sessions.get_conversation("c_test")
            self.assertEqual(conv["cwd"], d)
            self.assertEqual([m["role"] for m in conv["messages"]], ["user", "assistant"])

    def test_legacy_history_migration(self):
        with tempfile.TemporaryDirectory() as d:
            history = os.path.join(d, "history.json")
            with open(history, "w", encoding="utf-8") as f:
                json.dump({
                    "conversations": [{
                        "id": "c_old",
                        "title": "Old chat",
                        "created": 10,
                        "updated": 20,
                        "cwd": d,
                        "messages": [{"role": "user", "content": "legacy"}],
                    }]
                }, f)

            _, _, _, _, sessions = self.make_services()
            res = sessions.migrate_legacy_history(history, d)
            self.assertEqual(res["migrated"], 1)
            conv = sessions.get_conversation("c_old")
            self.assertEqual(conv["title"], "Old chat")
            self.assertEqual(conv["messages"][0]["content"], "legacy")

    def test_remote_url_parser(self):
        cases = [
            ("https://github.com/l2code/copilot-desktop-clone.git", ("l2code", "copilot-desktop-clone")),
            ("git@github.com:l2code/copilot-desktop-clone.git", ("l2code", "copilot-desktop-clone")),
            ("ssh://git@github.com/l2code/copilot-desktop-clone.git", ("l2code", "copilot-desktop-clone")),
            ("https://example.com/l2code/copilot-desktop-clone.git", (None, None)),
        ]
        for url, expected in cases:
            self.assertEqual(parse_github_remote_url(url), expected)

    def test_gitlab_remote_url_parser(self):
        cases = [
            ("https://gitlab.com/group/sub/repo.git", "group/sub/repo"),
            ("git@gitlab.com:group/sub/repo.git", "group/sub/repo"),
            ("ssh://git@gitlab.example.com/group/sub/repo.git", "group/sub/repo"),
            ("https://example.com/group/sub/repo.git", None),
        ]
        self.assertEqual(parse_gitlab_remote_url(cases[0][0], "gitlab.com"), cases[0][1])
        self.assertEqual(parse_gitlab_remote_url(cases[1][0], "gitlab.com"), cases[1][1])
        self.assertEqual(parse_gitlab_remote_url(cases[2][0], "gitlab.example.com"), cases[2][1])
        self.assertEqual(parse_gitlab_remote_url(cases[3][0], "gitlab.com"), cases[3][1])

    def test_porcelain_v2_parser(self):
        raw = "\0".join([
            "# branch.oid abc",
            "1 .M N... 100644 100644 100644 abc abc app.py",
            "? new.txt",
            "u UU N... 100644 100644 100644 100644 abc abc abc conflict.txt",
            "",
        ])
        files = parse_porcelain_v2_z(raw)
        by_path = {f["path"]: f for f in files}
        self.assertEqual(by_path["app.py"]["worktree"], "M")
        self.assertEqual(by_path["new.txt"]["kind"], "untracked")
        self.assertEqual(by_path["conflict.txt"]["kind"], "conflicted")

    def test_file_service_blocks_parent_escape(self):
        with tempfile.TemporaryDirectory() as d:
            service = FileService()
            with open(os.path.join(d, "a.txt"), "w", encoding="utf-8") as f:
                f.write("hello")
            ok = service.read_file(d, "a.txt")
            self.assertTrue(ok["ok"])
            blocked = service.read_file(d, "../outside.txt")
            self.assertFalse(blocked["ok"])

    def test_explicit_env_file_overrides_gitlab_defaults(self):
        from app import _load_env_file

        with tempfile.TemporaryDirectory() as d:
            env_path = os.path.join(d, ".env")
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("GITLAB_PERSONAL_ACCESS_TOKEN=abc\n")
                f.write("GITLAB_URL=https://devcloud.ubs.net\n")
                f.write("GITLAB_PROJECT_ID=170848\n")
                f.write("GITLAB_GROUP_ID=350440\n")

            env = {
                "COPILOT_ENV_FILE": env_path,
                "GITLAB_URL": "https://gitlab.com",
                "GITLAB_PROJECT_ID": "old-project",
            }
            with patch.dict(os.environ, env, clear=True):
                _load_env_file()

                self.assertEqual(os.environ["GITLAB_URL"], "https://devcloud.ubs.net")
                self.assertEqual(os.environ["GITLAB_PROJECT_ID"], "170848")
                self.assertEqual(os.environ["GITLAB_GROUP_ID"], "350440")
                self.assertEqual(os.environ["GITLAB_PERSONAL_ACCESS_TOKEN"], "abc")


if __name__ == "__main__":
    unittest.main()
