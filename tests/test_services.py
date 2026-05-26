import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from activity import ActivityLog
from file_service import FileService
from git_service import parse_github_remote_url, parse_gitlab_remote_url, parse_porcelain_v2_z
from gitlab_service import GitLabService
from mcp_gitlab_service import GitLabMCPService
from project_service import ProjectService
from settings_service import SettingsService
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
                service = GitLabService()
                _load_env_file()

                self.assertEqual(service.base_url, "https://devcloud.ubs.net")
                self.assertEqual(os.environ["GITLAB_URL"], "https://devcloud.ubs.net")
                self.assertEqual(os.environ["GITLAB_PROJECT_ID"], "170848")
                self.assertEqual(os.environ["GITLAB_GROUP_ID"], "350440")
                self.assertEqual(os.environ["GITLAB_PERSONAL_ACCESS_TOKEN"], "abc")

    def test_gitlab_api_url_is_normalized_to_host_base(self):
        with patch.dict(os.environ, {"GITLAB_API_URL": "https://devcloud.ubs.net/api/v4"}, clear=True):
            service = GitLabService()
            self.assertEqual(service.base_url, "https://devcloud.ubs.net")
            self.assertEqual(service.env_status()["url_source"], "GITLAB_API_URL")

    def test_gitlab_app_settings_override_environment(self):
        settings = {
            "gitlab_url": "https://devcloud.ubs.net",
            "gitlab_token": "abc",
            "gitlab_auth_type": "bearer",
            "gitlab_project": "170848",
            "gitlab_group": "350440",
        }
        with patch.dict(os.environ, {"GITLAB_URL": "https://gitlab.com", "GITLAB_TOKEN": "env-token"}, clear=True):
            service = GitLabService(settings_getter=lambda: settings)
            status = service.env_status()

            self.assertEqual(status["base_url"], "https://devcloud.ubs.net")
            self.assertEqual(status["url_source"], "app settings")
            self.assertEqual(status["token_source"], "app settings")
            self.assertEqual(status["auth_type"], "bearer")
            self.assertEqual(status["default_project"], "170848")
            self.assertEqual(status["default_group"], "350440")

    def test_gitlab_settings_hide_saved_token(self):
        settings = SettingsService(Storage(":memory:"))
        settings.update_gitlab_settings({
            "url": "https://devcloud.ubs.net",
            "token": "secret",
            "auth_type": "both",
            "project": "170848",
            "group": "350440",
        })
        visible = settings.get_gitlab_settings()

        self.assertEqual(visible["url"], "https://devcloud.ubs.net")
        self.assertEqual(visible["auth_type"], "both")
        self.assertTrue(visible["token_configured"])
        self.assertNotIn("token", visible)

    def test_gitlab_html_error_is_summarized(self):
        raw = "<!DOCTYPE HTML><HTML><HEAD><TITLE>UBS Login</TITLE></HEAD><BODY>very long</BODY></HTML>"
        msg = GitLabService._format_non_json_response(raw, "https://devcloud.ubs.net/api/v4/user")

        self.assertIn("GitLab API returned HTML instead of JSON", msg)
        self.assertIn("UBS Login", msg)
        self.assertNotIn("<!DOCTYPE", msg)

    def test_gitlab_iteration_and_story_filters(self):
        service = GitLabService()
        calls = []

        def fake_request(method, path, body=None, params=None):
            calls.append((method, path, params or {}))
            if path.endswith("/iterations"):
                return {"ok": True, "data": [{"id": 7, "title": "Sprint 12", "state": "current"}]}
            return {"ok": True, "data": []}

        service._request = fake_request
        iterations = service.list_group_iterations("350440")
        issues = service.list_group_issues("350440", iteration_id=7, assignee_username="issacre", labels="story")

        self.assertEqual(iterations["iterations"][0]["id"], 7)
        self.assertEqual(calls[0][1], "/groups/350440/iterations")
        self.assertEqual(calls[1][2]["iteration_id"], 7)
        self.assertEqual(calls[1][2]["assignee_username"], "issacre")
        self.assertEqual(calls[1][2]["labels"], "story")
        self.assertEqual(issues["issues"], [])

    def test_gitlab_mcp_config_selects_gitlab_server(self):
        with tempfile.TemporaryDirectory() as d:
            config = os.path.join(d, "mcp-config.json")
            with open(config, "w", encoding="utf-8") as f:
                json.dump({
                    "mcpServers": {
                        "Other": {"command": "other"},
                        "GitLab-MCP": {
                            "args": ["launcher.cmd"],
                            "env": {"GITLAB_API_URL": "https://devcloud.ubs.net/api/v4"},
                        },
                    }
                }, f)

            service = GitLabMCPService(settings_getter=lambda: {"gitlab_mcp_config": config})
            path, name, server = service._server_config()
            command, args = service._command(server)

            self.assertEqual(path, config)
            self.assertEqual(name, "GitLab-MCP")
            self.assertEqual(command, "launcher.cmd")
            self.assertEqual(args, [])

    def test_gitlab_mcp_copilot_config_includes_selected_server(self):
        with tempfile.TemporaryDirectory() as d:
            config = os.path.join(d, "mcp-config.json")
            with open(config, "w", encoding="utf-8") as f:
                json.dump({
                    "mcpServers": {
                        "GitLab-MCP": {
                            "args": ["launcher.cmd"],
                            "env": {"GITLAB_API_URL": "https://from-config/api/v4"},
                            "tools": ["*"],
                        },
                    }
                }, f)

            service = GitLabMCPService(settings_getter=lambda: {
                "gitlab_mcp_config": config,
                "gitlab_mcp_server": "GitLab-MCP",
                "gitlab_token": "secret",
                "gitlab_url": "https://devcloud.ubs.net",
                "gitlab_project": "170848",
                "gitlab_group": "350440",
            })
            cfg = service.copilot_server_config()
            server = cfg["mcp_servers"]["GitLab-MCP"]

            self.assertEqual(server["command"], "launcher.cmd")
            self.assertEqual(server["args"], [])
            self.assertEqual(server["tools"], ["*"])
            self.assertEqual(server["env"]["GITLAB_API_URL"], "https://devcloud.ubs.net/api/v4")
            self.assertEqual(server["env"]["GITLAB_PROJECT_ID"], "170848")
            self.assertEqual(server["env"]["GITLAB_GROUP_ID"], "350440")
            self.assertEqual(server["env"]["GITLAB_PERSONAL_ACCESS_TOKEN"], "secret")

    def test_gitlab_mcp_wraps_windows_cmd_launchers(self):
        service = GitLabMCPService(settings_getter=lambda: {})
        with patch("mcp_gitlab_service.os.name", "nt"):
            command, args = service._command({"args": [r"C:\devpod\gitlab-mcp-launcher.cmd"]})

        self.assertTrue(command.endswith("cmd.exe") or command == "cmd.exe")
        self.assertEqual(args[:2], ["/c", r"C:\devpod\gitlab-mcp-launcher.cmd"])

    def test_gitlab_mcp_current_iteration_uses_iteration_issue_filter(self):
        calls = []

        class FakeClient:
            def call_tool(self, name, arguments):
                calls.append((name, arguments))
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({"issues": [{"iid": 1196, "title": "Sprint story"}]}),
                    }]
                }

        tools = [{
            "name": "list_issues",
            "inputSchema": {"properties": {"iteration_id": {}, "scope": {}, "state": {}, "per_page": {}}},
        }]
        service = GitLabMCPService(settings_getter=lambda: {"gitlab_project": "170848"})

        issues = service._issues_for_current_iteration(FakeClient(), tools, "350440", "249590", {"state": "opened"})

        self.assertEqual(issues[0]["iid"], 1196)
        self.assertEqual(calls[0][0], "list_issues")
        self.assertEqual(calls[0][1]["iteration_id"], "249590")
        self.assertEqual(calls[0][1]["scope"], "all")
        self.assertNotIn("group_id", calls[0][1])

    def test_copilot_backend_lists_app_tools(self):
        from copilot_backend import CopilotBackend

        class FakeToolsRpc:
            async def list(self, request):
                return SimpleNamespace(tools=[])

        backend = CopilotBackend()
        backend.client = SimpleNamespace(rpc=SimpleNamespace(tools=FakeToolsRpc()))
        backend.tools = [SimpleNamespace(name="gitlab_current_sprint", description="List sprint stories")]

        tools = asyncio.run(backend.list_tools())

        self.assertEqual(tools[0]["name"], "gitlab_current_sprint")
        self.assertEqual(tools[0]["server"], "app")


if __name__ == "__main__":
    unittest.main()
