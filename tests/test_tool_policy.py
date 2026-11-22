import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import fast_transport
import gemphone as gp


class ToolPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fast_transport.patch_gemphone_runtime(gp)

    def setUp(self):
        self.kit = gp.AgentToolkit(
            Path.cwd(), Mock(), Mock(), Mock(),
            gp.ApprovalPolicy(True, True, True), mode="phone",
        )

    def assert_denied(self, name, args):
        handler = Mock()
        with patch.dict(self.kit._dispatch, {name: handler}):
            self.assertIn("rejected", self.kit.execute(name, args).payload)
        handler.assert_not_called()

    def test_mode_is_checked_on_every_call(self):
        for mode, tool in (("chat", "adb_status"), ("phone", "read_file"),
                           ("code", "phone_batch"), ("invalid", "adb_status")):
            with self.subTest(mode=mode):
                self.kit.mode = mode
                self.assert_denied(tool, {})

    def test_background_blocks_sensitive_reads_even_with_autoapproval(self):
        for name, args in (
            ("adb_screenshot", {}), ("adb_ui_dump", {}), ("phone_batch", {}),
            ("phone_comms", {"action": "LIST_SMS"}),
            ("phone_comms", {"action": "search_contacts"}),
            ("phone_notifications", {"action": "list"}),
            ("phone_clipboard", {"action": "get"}),
            ("phone_sensors", {"kind": "location"}),
        ):
            with self.subTest(name=name, args=args):
                self.assert_denied(name, args)

    def test_consent_is_single_use(self):
        self.kit.policy.non_interactive = False
        result = gp.ToolExecutionResult({"text": "sample"})
        with patch("builtins.input", side_effect=["да", "нет"]) as prompt:
            with patch.dict(self.kit._dispatch, {"phone_clipboard": Mock(return_value=result)}):
                self.assertIs(result, self.kit.execute("phone_clipboard", {"action": "get"}))
                self.assert_denied("phone_clipboard", {"action": "get"})
            self.assertEqual(prompt.call_count, 2)

    def test_safe_status_still_runs(self):
        result = gp.ToolExecutionResult({"ok": True})
        with patch.dict(self.kit._dispatch, {"adb_status": Mock(return_value=result)}):
            self.assertIs(result, self.kit.execute("adb_status", {}))
