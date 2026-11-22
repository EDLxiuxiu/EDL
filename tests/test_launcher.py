import io
import json
import unittest
from unittest.mock import patch

from admissions_app.launcher import open_existing, open_when_ready, server_matches
from admissions_app.repository import MemoryRepository
from admissions_app.web import create_app


class LauncherTests(unittest.TestCase):
    def response(self, value):
        return io.BytesIO(json.dumps(value).encode())

    def test_only_reuses_matching_application_and_mode(self):
        url = "http://127.0.0.1:5000"
        with patch("admissions_app.launcher.urllib.request.urlopen", return_value=self.response(
                {"application": "admissionsim-course-design", "storage": "mysql"})):
            self.assertTrue(server_matches(url, "mysql"))
        for value in ([1, 2], {"application": "other", "storage": "mysql"},
                      {"application": "admissionsim-course-design", "storage": "memory"}):
            with patch("admissions_app.launcher.urllib.request.urlopen", return_value=self.response(value)):
                self.assertFalse(server_matches(url, "mysql"))

    def test_opens_browser_after_ready_and_reuses_running_server(self):
        with patch("admissions_app.launcher.server_matches", return_value=True), patch(
                "admissions_app.launcher.webbrowser.open") as opened:
            open_when_ready("http://127.0.0.1:5000", "mysql")
            opened.assert_called_once_with("http://127.0.0.1:5000")
            opened.reset_mock()
            self.assertTrue(open_existing(5000, "mysql"))
            opened.assert_called_once_with("http://127.0.0.1:5000")

    def test_health_does_not_expose_connection_credentials(self):
        response = create_app(MemoryRepository(), testing=True).test_client().get("/health")
        self.assertEqual(response.json, {"application": "admissionsim-course-design", "storage": "memory"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
