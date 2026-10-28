import json
import threading
import unittest
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "dashboard"
GROUPS = [
    {"id": 11, "name": "North Area", "camera_count": 1},
    {"id": 22, "name": "South Area", "camera_count": 1},
    {"id": 33, "name": "Empty Area", "camera_count": 0},
]
CAMERAS = [
    {
        "camera_id": "CAM01", "name": "CAM01", "enabled": True, "connected": True,
        "area_group_id": 11, "area_group_name": "North Area",
        "ip_address": "192.168.1.20",
    },
    {
        "camera_id": "CAM02", "name": "CAM02", "enabled": True, "connected": False,
        "area_group_id": 22, "area_group_name": "South Area",
        "ip_address": "192.168.1.21",
    },
]


class DashboardHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parsed_path = urlparse(path).path
        if parsed_path == "/":
            return str(DASHBOARD_DIR / "index.html")
        if parsed_path.startswith("/static/"):
            return str(DASHBOARD_DIR / parsed_path.removeprefix("/static/"))
        return str(DASHBOARD_DIR / parsed_path.lstrip("/"))

    def log_message(self, format_string, *args):
        pass


class DashboardBrowserWorkflowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), DashboardHandler,
        )
        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True,
        )
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=5)

    async def test_camera_stats_and_power_automate_alert_workflows(self):
        self.power_automate_recipient = None
        status_requests = []
        return_invalid_status_shape = False
        page_errors = []

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            async def mock_api(route):
                request = route.request
                url = urlparse(request.url)
                path = url.path
                query = parse_qs(url.query)
                if path == "/api/auth/me":
                    body = {"auth_required": False, "authenticated": False}
                elif path == "/api/area-groups":
                    body = GROUPS
                elif path == "/api/cameras":
                    body = CAMERAS
                elif path == "/api/stats":
                    body = {
                        "window_hours": int(query.get("window_hours", ["24"])[0]),
                        "total_violations": 12,
                        "hourly": [{
                            "hour": datetime.now(timezone.utc).replace(
                                minute=0, second=0, microsecond=0,
                            ).isoformat(),
                            "count": 8,
                        }],
                        "by_type": [
                            {"violation_type": "no_helmet", "c": 8},
                            {"violation_type": "no_vest", "c": 4},
                        ],
                        "by_area_group": [
                            {"area_group": "North Area", "c": 9},
                            {"area_group": "South Area", "c": 3},
                        ],
                        "by_camera": [
                            {"camera_name": "CAM01", "c": 7},
                            {"camera_name": "CAM02", "c": 5},
                        ],
                    }
                elif path == "/api/rules" and request.method == "GET":
                    body = []
                elif path == "/api/camera-status/history":
                    status_requests.append(query)
                    if return_invalid_status_shape:
                        await route.fulfill(
                            status=200, content_type="application/json", body="[]",
                        )
                        return
                    selected = [
                        camera for camera in CAMERAS
                        if (not query.get("camera_id") or
                            camera["camera_id"] == query["camera_id"][0])
                        and (not query.get("area_group_id") or
                             str(camera["area_group_id"]) == query["area_group_id"][0])
                    ]
                    if query.get("area_group_id") == ["33"]:
                        selected = []
                    cameras = [{
                        "camera_id": camera["camera_id"],
                        "name": camera["name"],
                        "area_group_name": camera["area_group_name"],
                        "enabled": camera["enabled"],
                        "connected": camera["connected"],
                        "uptime_pct": 100 if camera["connected"] else 0,
                        "buckets": [
                            {"start": "2026-10-10T00:00:00+00:00",
                             "end": "2026-10-10T12:00:00+00:00",
                             "state": "online", "uptime_pct": 100},
                            {"start": "2026-10-10T12:00:00+00:00",
                             "end": "2026-10-11T00:00:00+00:00",
                             "state": "offline", "uptime_pct": 0},
                        ],
                    } for camera in selected]
                    body = {
                        "window": query.get("window", ["30d"])[0],
                        "start": "2026-10-10T00:00:00+00:00",
                        "end": "2026-10-11T00:00:00+00:00",
                        "summary": {"uptime_pct": 50, "online": 1, "offline": 1,
                                    "total": len(cameras)},
                        "bucket_count": 2, "cameras": cameras,
                        "logs": [],
                    }
                elif path == "/api/alerts/power-automate/test" and request.method == "POST":
                    self.power_automate_recipient = request.post_data_json["recipient"]
                    body = {"sent": True, "recipient": self.power_automate_recipient}
                elif path == "/api/rules" and request.method == "POST":
                    self.rule_payload = request.post_data_json
                    body = {"id": 4, **self.rule_payload}
                else:
                    body = {}
                await route.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps(body),
                )

            await page.route("**/api/**", mock_api)
            try:
                await page.goto(self.base_url)
                try:
                    await page.wait_for_function(
                        "() => document.querySelectorAll('#cameraStatusGroup option').length >= 4 "
                        "&& document.querySelectorAll('#cameraStatusCamera option').length >= 3",
                    )
                except Exception as error:
                    self.fail(
                        f"Dashboard initialization did not populate camera filters: {error}; "
                        f"browser errors: {page_errors}; "
                        f"page text: {(await page.locator('body').inner_text())[:1200]}"
                    )
                await page.locator('[data-tab="playback"]').click()
                await page.locator("#playbackCamera").select_option("CAM01")
                self.assertEqual(
                    await page.locator("#playbackIp").input_value(),
                    "192.168.1.20",
                )
                await page.locator("#playbackStart").fill("2026-10-13T14:00")
                async with page.expect_request(
                    lambda request: "/api/cameras/CAM01/playback?" in request.url,
                ) as playback_request:
                    await page.locator("#btnStartPlayback").click()
                playback_query = parse_qs(urlparse(
                    (await playback_request.value).url,
                ).query)
                self.assertEqual(playback_query["start"], ["2026-10-13T14:00"])
                self.assertEqual(playback_query["duration_minutes"], ["10"])
                await page.get_by_role("button", name="ANALYTICS").click()
                async with page.expect_response(
                    lambda response: "/api/stats?window_hours=24" in response.url,
                ):
                    await page.locator("#btnRefreshAnalytics").click()
                self.assertEqual(await page.locator("#analyticsTotal").inner_text(), "12")
                self.assertEqual(await page.locator("#analyticsCameras").inner_text(), "1/2")
                self.assertEqual(await page.locator("#analyticsTopArea").inner_text(), "North Area")
                self.assertIn("Missing Helmet", await page.locator("#typeLegend").inner_text())
                self.assertEqual(
                    await page.locator("#cameraRankList .analytics-rank-row").count(),
                    2,
                )
                self.assertGreater(
                    await page.locator("#areaGroupChart .analytics-rank-fill").first.evaluate(
                        "(bar) => bar.getBoundingClientRect().width",
                    ),
                    0,
                )
                canvas_size = await page.locator("#hourlyChart").evaluate(
                    "(canvas) => ({width: canvas.clientWidth, height: canvas.clientHeight})",
                )
                self.assertGreater(canvas_size["width"], 250)
                self.assertGreater(canvas_size["height"], 180)
                async with page.expect_response(
                    lambda response: "/api/stats?window_hours=168" in response.url,
                ):
                    await page.locator("#analyticsRange").select_option("168")
                self.assertEqual(await page.locator("#analyticsPeriodCaption").inner_text(), "Last 7 days")
                await page.set_viewport_size({"width": 390, "height": 844})
                mobile_overflow = await page.locator(".analytics-page").evaluate(
                    "(element) => element.scrollWidth > element.clientWidth",
                )
                self.assertFalse(mobile_overflow)
                await page.set_viewport_size({"width": 1280, "height": 900})
                await page.get_by_role("button", name="CAMERA STATUS").click()

                async with page.expect_response(
                    lambda response: "/api/camera-status/history?" in response.url
                    and "window=24h" in response.url,
                ):
                    await page.locator("#cameraStatusRange").select_option("24h")
                async with page.expect_response(
                    lambda response: "/api/camera-status/history?" in response.url
                    and "area_group_id=11" in response.url,
                ):
                    await page.locator("#cameraStatusGroup").select_option("11")
                async with page.expect_response(
                    lambda response: "/api/camera-status/history?" in response.url
                    and "camera_id=CAM01" in response.url
                    and "area_group_id=11" in response.url,
                ):
                    await page.locator("#cameraStatusCamera").select_option("CAM01")
                self.assertIn("CAM01", await page.locator("#cameraStatusTimeline").inner_text())
                self.assertEqual(status_requests[-1]["camera_id"], ["CAM01"])
                self.assertEqual(status_requests[-1]["area_group_id"], ["11"])

                async with page.expect_response(
                    lambda response: "/api/camera-status/history?" in response.url
                    and "area_group_id=33" in response.url,
                ):
                    await page.locator("#cameraStatusGroup").select_option("33")
                self.assertIn(
                    "no data for selected filters.",
                    (await page.locator("#cameraStatusTimeline").inner_text()).casefold(),
                )

                async with page.expect_response(
                    lambda response: "/api/camera-status/history?" in response.url
                    and "window=30d" in response.url
                    and "area_group_id" not in response.url
                    and "camera_id" not in response.url,
                ):
                    await page.locator("#btnClearCameraStatusFilters").click()

                return_invalid_status_shape = True
                async with page.expect_response(
                    lambda response: "/api/camera-status/history?" in response.url,
                ):
                    await page.get_by_role("button", name="CAMERA STATUS").click()
                await page.get_by_text(
                    "Unexpected camera status response format.",
                    exact=True,
                ).wait_for()

                return_invalid_status_shape = False
                await page.get_by_role("button", name="ALERT DELIVERY").click()
                await page.locator("#powerAutomateTestRecipient").fill("safety@example.test")
                await page.locator("#btnTestPowerAutomate").click()
                await page.get_by_text("Test request accepted", exact=False).wait_for()
                self.assertEqual(
                    self.power_automate_recipient,
                    "safety@example.test",
                )

                await page.get_by_role("button", name="ALERT RULES").click()
                await page.get_by_role("button", name="+ NEW RULE").click()
                await page.locator("#ruleName").fill("Power Automate test rule")
                await page.locator("#chanPowerAutomate").check()
                await page.locator("#ruleEmailRecipients").fill("safety@example.test")
                await page.get_by_role("button", name="SAVE RULE").click()
                self.assertEqual(
                    self.rule_payload["channels"],
                    ["power_automate"],
                )
                self.assertEqual(
                    self.rule_payload["email_recipients"],
                    ["safety@example.test"],
                )
                self.assertEqual(page_errors, [])
            finally:
                await browser.close()

    async def test_editing_camera_preserves_other_live_wall_tiles_and_sockets(self):
        cameras = [
            {
                "camera_id": "CAM01", "name": "CAM01", "enabled": True,
                "connected": True, "worker_running": True,
                "area_group_id": 11, "area_group_name": "North Area",
                "rtsp_url": "rtsp://192.168.1.20/Streaming/Channels/101",
                "required_ppe": ["helmet", "vest"],
            },
            {
                "camera_id": "CAM02", "name": "CAM02", "enabled": True,
                "connected": True, "worker_running": True,
                "area_group_id": 22, "area_group_name": "South Area",
                "rtsp_url": "rtsp://192.168.1.21/Streaming/Channels/101",
                "required_ppe": ["helmet", "vest"],
            },
        ]

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.add_init_script("""
                (() => {
                  window.__cameraWebSockets = [];
                  class TestWebSocket {
                    static CONNECTING = 0;
                    static OPEN = 1;
                    static CLOSING = 2;
                    static CLOSED = 3;
                    constructor(url) {
                      this.url = url;
                      this.readyState = TestWebSocket.OPEN;
                      window.__cameraWebSockets.push(url);
                    }
                    close() {
                      this.readyState = TestWebSocket.CLOSED;
                      if (this.onclose) this.onclose({target: this});
                    }
                  }
                  window.WebSocket = TestWebSocket;
                })();
            """)

            async def mock_api(route):
                request = route.request
                path = urlparse(request.url).path
                if path == "/api/auth/me":
                    body = {"auth_required": False, "authenticated": False}
                elif path == "/api/area-groups":
                    body = GROUPS
                elif path == "/api/cameras":
                    body = cameras
                elif path == "/api/cameras/CAM01" and request.method == "GET":
                    body = cameras[0]
                elif path == "/api/cameras/CAM01" and request.method == "PUT":
                    cameras[0] = {**cameras[0], **request.post_data_json}
                    body = cameras[0]
                elif path == "/api/stats":
                    body = {
                        "window_hours": 24, "total_violations": 0,
                        "hourly": [], "by_type": [], "by_area_group": [],
                        "by_camera": [],
                    }
                elif path == "/api/rules":
                    body = []
                else:
                    body = {}
                await route.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps(body),
                )

            await page.route("**/api/**", mock_api)
            try:
                await page.goto(self.base_url)
                await page.locator("#tile-CAM02").wait_for()
                await page.evaluate(
                    "window.__originalCamera2Tile = document.getElementById('tile-CAM02')",
                )
                socket_count_before = await page.evaluate(
                    "window.__cameraWebSockets.filter(url => url.includes('/ws/video/')).length",
                )

                await page.locator('[data-tab="cameras"]').click()
                await page.locator('[data-edit-cam="CAM01"]').click()
                await page.locator("#camName").fill("CAM01 Updated")
                await page.locator("#cameraForm button[type=submit]").click()
                await page.wait_for_function(
                    "() => !document.getElementById('cameraModalBackdrop').classList.contains('open')",
                )

                tile_was_preserved = await page.evaluate(
                    "window.__originalCamera2Tile === document.getElementById('tile-CAM02')",
                )
                socket_count_after = await page.evaluate(
                    "window.__cameraWebSockets.filter(url => url.includes('/ws/video/')).length",
                )
                self.assertTrue(tile_was_preserved)
                self.assertEqual(socket_count_after, socket_count_before)
                self.assertEqual(
                    await page.locator("#tile-CAM01 .tile-cam-name").inner_text(),
                    "CAM01 Updated",
                )
            finally:
                await browser.close()


if __name__ == "__main__":
    unittest.main()
