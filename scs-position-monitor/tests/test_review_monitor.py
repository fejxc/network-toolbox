import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor


def response(code="1", note="", reviewed_at=None):
    return {"flag": "success", "person": {
        "name": "PRIVATE_NAME_NOT_TO_STORE",
        "vhc10gkBk": {"id": "PRIVATE_APPLICATION_ID"},
        "vhc10gkscBk": {"ahc102": code, "ahc103": note, "ahc104": reviewed_at},
        "vhb11Detail": {"ahb110": "TEST_CODE", "ahb111": "测试职位", "ahd011": "测试单位"},
    }}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.state = Path(directory) / "state.json"
        self.history = Path(directory) / "review_history.csv"
        self.stack.enter_context(patch.object(monitor, "STATE_FILE", self.state))
        self.stack.enter_context(patch.object(monitor, "REVIEW_HISTORY_CSV", self.history))
        self.real_fetch_review = monitor.fetch_review
        self.fetch = self.stack.enter_context(patch.object(monitor, "fetch_review", return_value=(200, response())))
        self.notify = self.stack.enter_context(patch.object(monitor, "notify", return_value=True))
        self.stack.enter_context(patch.object(monitor, "log"))

    def test_official_status_mapping_and_minimal_storage(self):
        for code, label in monitor.REVIEW_STATUSES.items():
            with self.subTest(code=code):
                parsed = monitor.extract_review(response(code))
                self.assertEqual(parsed["status"], label)
                self.assertNotIn("PRIVATE_", json.dumps(parsed))

    def test_first_snapshot_then_no_duplicate(self):
        monitor.save_state({"positions": {}, "updated": "2026-10-20 10:11:12"}, update_time=False)
        monitor.check_review("test_cookie")
        monitor.check_review("test_cookie")
        self.assertEqual(self.notify.call_count, 1)
        self.assertEqual(self.notify.call_args.args[0], "资格审查首次快照")
        self.assertIn("审核状态：待审核", self.notify.call_args.args[1])
        self.assertRegex(self.notify.call_args.args[1], r"采集时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
        self.assertEqual(monitor.load_state()["updated"], "2026-10-20 10:11:12")
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.history.stat().st_mode & 0o777, 0o600)

    def test_status_change_and_note_time_change(self):
        monitor.check_review("test_cookie")
        self.fetch.return_value = (200, response("2", "<b>符合条件</b>", "2026-10-20 10:12:13"))
        monitor.check_review("test_cookie")
        body = self.notify.call_args.args[1]
        self.assertIn("待审核 → 审核通过", body)
        self.assertIn("审核说明：无 → 符合条件", body)
        self.assertIn("审核时间：无 → 2026-10-20 10:12:13", body)
        self.fetch.return_value = (200, response("2", "说明更新", "2026-10-20 10:12:13"))
        monitor.check_review("test_cookie")
        self.assertEqual(self.notify.call_count, 3)
        self.assertIn("符合条件 → 说明更新", self.notify.call_args.args[1])

    def test_failed_notification_retries_same_transition(self):
        monitor.check_review("test_cookie")
        self.fetch.return_value = (200, response("4", "请补充材料"))
        self.notify.return_value = False
        monitor.check_review("test_cookie")
        self.assertEqual(monitor.load_state()["review"]["status_code"], "4")
        self.assertEqual(monitor.load_state()["review_notified"]["status_code"], "1")
        self.notify.return_value = True
        monitor.check_review("test_cookie")
        self.assertIn("待审核 → 退回补充材料", self.notify.call_args.args[1])
        self.assertEqual(monitor.load_state()["review_notified"]["status_code"], "4")
        self.assertEqual(len(self.history.read_text(encoding="utf-8-sig").splitlines()), 3)

    def test_failures_do_not_replace_last_known_state(self):
        monitor.check_review("test_cookie")
        original = self.state.read_bytes()
        for reply in [(503, None), (200, {"flag": "error"}), (200, {"flag": "success", "person": {}})]:
            with self.subTest(reply=reply):
                self.fetch.return_value = reply
                monitor.check_review("test_cookie")
                self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.notify.call_count, 1)

    def test_empty_favorites_still_monitors_review(self):
        with patch.object(monitor, "load_cookie", return_value="test_cookie"), patch.object(
            monitor, "fetch_collection", return_value=(200, {"collectFlag": "1"})
        ):
            monitor.check_once(quiet=True)
        self.assertEqual(monitor.load_state()["review"]["status"], "待审核")
        self.notify.assert_called_once()

    def test_favorites_failure_does_not_skip_review(self):
        with patch.object(monitor, "load_cookie", return_value="test_cookie"), patch.object(
            monitor, "fetch_collection", return_value=(503, None)
        ), self.assertRaises(SystemExit):
            monitor.check_once(quiet=True)
        self.assertEqual(monitor.load_state()["review"]["status"], "待审核")

    def test_missing_status_or_position_is_rejected(self):
        bad = response()
        bad["person"]["vhc10gkscBk"] = {}
        with self.assertRaises(ValueError):
            monitor.extract_review(bad)
        bad = response()
        bad["person"]["vhb11Detail"] = "unexpected"
        with self.assertRaises(ValueError):
            monitor.extract_review(bad)
        bad = response()
        del bad["person"]["vhc10gkscBk"]
        with self.assertRaises(ValueError):
            monitor.extract_review(bad)

    def test_review_only_requests_read_only_endpoint(self):
        with patch.object(monitor, "fetch_person_api") as fetch:
            self.real_fetch_review("test_cookie")
        fetch.assert_called_once_with("test_cookie", monitor.REVIEW_PATH)

    def test_expired_review_session_retries_with_new_cookie(self):
        self.fetch.side_effect = [(401, None), (200, response())]
        with patch.object(monitor, "try_relogin", return_value=True), patch.object(
            monitor, "load_cookie", return_value="refreshed_cookie"
        ):
            monitor.check_review("expired_cookie")
        self.assertEqual(self.fetch.call_args.args, ("refreshed_cookie",))
        self.assertEqual(monitor.load_state()["review"]["status"], "待审核")


if __name__ == "__main__":
    unittest.main()
