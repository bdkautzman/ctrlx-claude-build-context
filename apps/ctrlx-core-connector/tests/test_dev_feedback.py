"""Dev-only UI feedback overlay (app/dev_feedback.py): off unless run-local.sh turns it on, never in the snap."""

import http.client
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from app import config, dev_feedback
from app.web_server import WebServer

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
# The dev routes and the static page use none of the app's services: the web server runs without them.
SERVER_ARGS = (None, None, None, None, None)   # store, datalayer, monitor, browser, kvd
NOTE = {"note": "Make the title bigger", "selector": "#app-title", "path": "body > header > div > h1",
        "text": "App", "html": '<h1 id="app-title">', "page": config.URL_PREFIX + "/",
        "rect": {"x": 40, "y": 12, "width": 139, "height": 26, "junk": "x"},
        "viewport": {"width": 1400, "height": 860, "dpr": 1.25, "scrollX": 0, "scrollY": 0},
        "colorScheme": "light", "timestamp": "2026-11-08T19:05:00-06:00", "unknown": "dropped"}


class ConfigGateTest(unittest.TestCase):
    """config.DEV_FEEDBACK_JS: only with APP_DEV_NO_AUTH=1 outside the snap, "0" = off."""

    def feedback_js(self, **env):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("SNAP", "SNAP_DATA", "SNAP_COMMON", "APP_DEV_NO_AUTH", "APP_DEV_FEEDBACK")}
        clean.update(env, PYTHONPATH=SRC)
        out = subprocess.run([sys.executable, "-c", "from app import config; print(repr(config.DEV_FEEDBACK_JS))"],
                             env=clean, capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def test_gate(self):
        self.assertEqual(self.feedback_js(), "''")
        self.assertEqual(self.feedback_js(APP_DEV_FEEDBACK="/k/overlay.js"), "''")       # auth on
        self.assertEqual(self.feedback_js(APP_DEV_NO_AUTH="1", APP_DEV_FEEDBACK="/k/overlay.js"), "'/k/overlay.js'")
        self.assertEqual(self.feedback_js(APP_DEV_NO_AUTH="1", APP_DEV_FEEDBACK="0"), "''")
        with tempfile.TemporaryDirectory() as snap:                                       # inside the snap: never
            self.assertEqual(self.feedback_js(SNAP=snap, SNAP_DATA=snap, SNAP_COMMON=snap, APP_DEV_NO_AUTH="1",
                                              APP_DEV_FEEDBACK="/k/overlay.js"), "''")


class DevFeedbackWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._port = config.DEV_HTTP_PORT
        config.DEV_HTTP_PORT = 0
        cls.web = WebServer(*SERVER_ARGS)
        cls.web.start()
        cls.port = cls.web._server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.web.stop()
        config.DEV_HTTP_PORT = cls._port

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.js = os.path.join(d.name, "overlay.js")
        with open(self.js, "w", encoding="utf-8") as f:
            f.write("/* overlay */")
        self.file = os.path.join(d.name, "dev", "feedback.json")
        for name, value in (("DEV_NO_AUTH", True), ("DEV_FEEDBACK_JS", self.js), ("DEV_FEEDBACK_FILE", self.file)):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def req(self, method, rest, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, config.URL_PREFIX + rest, body=None if body is None else json.dumps(body))
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        if res.getheader("Content-Type", "").startswith("application/json"):
            return res.status, json.loads(raw)
        return res.status, raw

    def stored(self):
        with open(self.file, encoding="utf-8") as f:
            return json.load(f)["items"]

    def test_off_without_overlay_or_without_dev_no_auth(self):
        for no_auth, js in ((True, ""), (False, self.js)):
            with mock.patch.object(config, "DEV_NO_AUTH", no_auth), mock.patch.object(config, "DEV_FEEDBACK_JS", js):
                status, page = self.req("GET", "/")
                self.assertEqual(status, 200)
                self.assertNotIn(b"__dev", page)
                self.assertEqual(self.req("GET", "/__dev/feedback.js")[0], 404)
                self.assertEqual(self.req("POST", "/__dev/feedback", NOTE)[0], 404)
                self.assertFalse(os.path.exists(self.file))

    def test_script_injected_and_served(self):
        status, page = self.req("GET", "/")
        self.assertEqual(status, 200)
        tag = dev_feedback.script_tag().encode()
        self.assertEqual(page.count(tag), 1)
        self.assertLess(page.index(tag), page.lower().rindex(b"</body>"))
        status, js = self.req("GET", "/__dev/feedback.js")
        self.assertEqual((status, js), (200, b"/* overlay */"))
        self.assertTrue(dev_feedback.inject(b"<p>no body").endswith(tag))

    def test_add_list_remove(self):
        status, item = self.req("POST", "/__dev/feedback", NOTE)
        self.assertEqual(status, 201)
        self.assertEqual((item["id"], item["status"], item["note"]), (1, "open", NOTE["note"]))
        self.assertEqual(item["rect"], {"x": 40, "y": 12, "width": 139, "height": 26})
        self.assertEqual(item["viewport"]["dpr"], 1.25)
        self.assertNotIn("unknown", item)
        self.assertEqual(self.req("POST", "/__dev/feedback", dict(NOTE, note="second"))[1]["id"], 2)
        status, doc = self.req("GET", "/__dev/feedback")
        self.assertEqual((status, [i["id"] for i in doc["items"]]), (200, [1, 2]))
        self.assertEqual(self.stored(), doc["items"])
        self.assertEqual(self.req("DELETE", "/__dev/feedback/1")[0], 204)
        self.assertEqual(self.req("DELETE", "/__dev/feedback/1")[0], 404)
        self.assertEqual(self.req("DELETE", "/__dev/feedback/x")[0], 404)
        self.assertEqual([i["id"] for i in self.stored()], [2])

    def test_rejects_bad_notes(self):
        bad = [dict(NOTE, note="  "), {"note": "x"}, dict(NOTE, note="x" * 4001), dict(NOTE, rect=[1, 2]),
               dict(NOTE, selector=5), ["not", "an", "object"]]
        for body in bad:
            status, problem = self.req("POST", "/__dev/feedback", body)
            self.assertEqual(status, 400, body)
            self.assertIn("detail", problem)
        self.assertFalse(os.path.exists(self.file))

    def test_server_time_without_timestamp(self):
        item = self.req("POST", "/__dev/feedback", {"note": "n", "selector": "p"})[1]
        self.assertRegex(item["timestamp"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")

    def test_file_edited_while_running(self):
        """The skill marks notes in the file: the server keeps its fields and counts on from the highest id."""
        os.makedirs(os.path.dirname(self.file))
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "items": [dict(NOTE, id=7, status="done", resolution="bigger h1")]}, f)
        self.assertEqual(self.req("POST", "/__dev/feedback", NOTE)[1]["id"], 8)
        self.assertEqual(self.stored()[0]["resolution"], "bigger h1")
        with open(self.file, "w", encoding="utf-8") as f:
            f.write("{broken")
        status, problem = self.req("GET", "/__dev/feedback")
        self.assertEqual(status, 400)
        self.assertIn("feedback.json", problem["detail"])


if __name__ == "__main__":
    unittest.main()
