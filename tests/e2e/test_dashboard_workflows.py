import json
import threading
import unittest
from datetime import UTC, datetime, timezone
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
        self.power_automate_ids = []
        gallery_requests = []
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
                if path in {
                    "/api/snapshot/201",
                    "/api/violations/201/evidence/clean",
                    "/api/violations/201/face-crop",
                }:
                    await route.fulfill(
                        status=200, content_type="image/svg+xml",
                        body=(
                            '<svg xmlns="http://www.w3.org/2000/svg" '
                            'width="100" height="80">'
                            '<rect width="100" height="80" fill="#222"/>'
                            '<rect x="10" y="8" width="40" height="64" fill="#777"/>'
                            '<circle cx="24" cy="18" r="7" fill="#eee"/>'
                            '</svg>'
                        ),
                    )
                    return
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
                elif path == "/api/face-matches/201":
                    body = {
                        "incident_id": 201, "person_track_id": 11,
                        "person_number": 1, "violation_type": "no_helmet",
                        "status": "UNKNOWN", "source": "live",
                        "violation_status": "unreviewed",
                        "camera_id": "CAM01", "camera_name": "CAM01",
                        "area_group_name": "North Area",
                        "epoch_time": datetime.now(UTC).timestamp(),
                        "snapshot_url": "/api/snapshot/201",
                        "clean_snapshot_url": "/api/violations/201/evidence/clean",
                        "face_crop_available": True, "face_bbox": [18, 12, 31, 26],
                        "person_bbox": [10, 8, 50, 72],
                        "detection_metadata": {
                            "frame_width": 100, "frame_height": 80,
                            "person_box": [10, 8, 50, 72],
                        },
                        "face_quality": {"quality_score": 0.9},
                        "thresholds": {}, "top5_candidates": [],
                    }
                elif path == "/api/face/enrollments":
                    body = []
                elif path == "/api/violations":
                    body = [
                        {
                            "id": 201, "camera_id": "CAM01", "camera_name": "CAM01",
                            "zone": "North Area", "violation_type": "no_helmet",
                            "detected_at": datetime.now(UTC).isoformat(),
                            "snapshot_url": "/api/snapshot/201",
                            "review_status": "unreviewed",
                        },
                        {
                            "id": 202, "camera_id": "CAM02", "camera_name": "CAM02",
                            "zone": "South Area", "violation_type": "phone_detected",
                            "detected_at": datetime.now(UTC).isoformat(),
                            "snapshot_url": "/api/snapshot/202",
                            "review_status": "unreviewed",
                        },
                    ]
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
                elif path == "/api/alerts/power-automate/status":
                    body = {
                        "configured": True, "sent_count_24h": 2,
                        "failed_count_24h": 0, "pending_count": 0,
                        "dead_count": 0, "recent": [],
                    }
                elif path == "/api/alerts/power-automate/violations":
                    gallery_requests.append(query)
                    rows = [
                        {
                            "id": 101, "camera_id": "CAM01", "camera_name": "CAM01",
                            "zone": "North Area", "violation_type": "no_helmet",
                            "detected_at": datetime.now(timezone.utc).isoformat(),
                            "snapshot_url": "/api/snapshot/101",
                        },
                        {
                            "id": 102, "camera_id": "CAM02", "camera_name": "CAM02",
                            "zone": "South Area", "violation_type": "phone_detected",
                            "detected_at": datetime.now(timezone.utc).isoformat(),
                            "snapshot_url": "/api/snapshot/102",
                        },
                    ]
                    if query.get("violation_type"):
                        rows = [
                            row for row in rows
                            if row["violation_type"] == query["violation_type"][0]
                        ]
                    body = rows
                elif path == "/api/alerts/power-automate/test" and request.method == "POST":
                    self.power_automate_ids = request.post_data_json["violation_ids"]
                    body = {"sent": True, "count": len(self.power_automate_ids)}
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
                await page.wait_for_function(
                    "() => document.querySelector('#areaGroupChart .analytics-rank-fill')"
                    "?.getBoundingClientRect().width > 0",
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
                await page.get_by_role("button", name="CAMERAS & HEALTH").click()

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
                    await page.get_by_role("button", name="CAMERAS & HEALTH").click()
                await page.get_by_text(
                    "Unexpected camera status response format.",
                    exact=True,
                ).wait_for()

                return_invalid_status_shape = False
                await page.get_by_role("button", name="ALERT DELIVERY").click()
                await page.locator('[data-alert-open="101"]').wait_for()
                await page.locator('[data-alert-open="101"]').click()
                self.assertEqual(await page.locator("#alertPosition").inner_text(), "1 of 2")
                await page.locator("#btnAlertNext").click(position={"x": 17, "y": 17})
                self.assertTrue(
                    (await page.locator("#alertPreviewImage").get_attribute("src")).endswith("/102"),
                )
                self.assertEqual(await page.locator("#alertPosition").inner_text(), "2 of 2")
                await page.keyboard.press("ArrowLeft")
                self.assertEqual(await page.locator("#alertPosition").inner_text(), "1 of 2")
                await page.locator("#btnAlertGalleryBack").click()
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.locator('[data-alert-open="101"]').click()
                alert_mobile_layout = await page.locator(
                    ".alert-inspector .incident-inspector-layout",
                ).evaluate(
                    "(element) => ({columns: getComputedStyle(element).gridTemplateColumns, "
                    "width: element.scrollWidth, clientWidth: element.clientWidth})",
                )
                self.assertEqual(len(alert_mobile_layout["columns"].split()), 1)
                self.assertLessEqual(alert_mobile_layout["width"], alert_mobile_layout["clientWidth"])
                await page.locator("#btnAlertGalleryBack").click()
                await page.set_viewport_size({"width": 1280, "height": 900})
                async with page.expect_response(
                    lambda response: "/api/alerts/power-automate/violations?" in response.url,
                ):
                    await page.locator("#alertGalleryType").select_option("phone_detected")
                self.assertEqual(gallery_requests[-1]["violation_type"], ["phone_detected"])
                self.assertEqual(gallery_requests[-1]["limit"], ["500"])
                select_evidence = page.locator('[data-alert-select="102"]')
                await select_evidence.click()
                self.assertEqual(await select_evidence.get_attribute("aria-pressed"), "true")
                await page.locator("#alertGallerySearch").fill("CAM02")
                await page.locator("#alertGallerySort").select_option("oldest")
                self.assertIn("1 selected", await page.locator("#alertGalleryCount").inner_text())
                await page.locator("#btnTestPowerAutomate").click()
                await page.get_by_text("1 selected violation", exact=False).wait_for()
                self.assertEqual(self.power_automate_ids, [102])
                await page.locator("#alertGallerySearch").fill("")
                await page.locator("#btnClearAlertEvidence").click()
                self.assertIn("0 selected", await page.locator("#alertGalleryCount").inner_text())
                self.assertTrue(await page.locator("#btnTestPowerAutomate").is_disabled())

                await page.get_by_role("button", name="ALERT RULES").click()
                await page.get_by_role("button", name="+ NEW RULE").click()
                await page.locator("#ruleName").fill("Power Automate test rule")
                await page.locator('#ruleCameraOptions input[value="CAM01"]').check()
                await page.locator('#ruleCameraOptions input[value="CAM02"]').check()
                await page.locator("#chanPowerAutomate").check()
                await page.locator("#ruleEmailRecipients").fill("safety@example.test")
                await page.get_by_role("button", name="SAVE RULE").click()
                self.assertEqual(
                    self.rule_payload["camera_ids"],
                    ["CAM01", "CAM02"],
                )
                self.assertEqual(
                    self.rule_payload["channels"],
                    ["power_automate"],
                )
                self.assertEqual(
                    self.rule_payload["email_recipients"],
                    ["safety@example.test"],
                )
                await page.locator('[data-tab="violations"]').click()
                await page.locator('[data-incident-select="201"]').wait_for()
                await page.locator('[data-incident-select="201"]').click()
                self.assertEqual(await page.locator("#incidentPosition").inner_text(), "1 of 2")
                await page.locator("#btnIncidentNext").click(position={"x": 17, "y": 17})
                self.assertTrue(
                    (await page.locator("#incidentPreviewImage").get_attribute("src")).endswith("/202"),
                )
                self.assertEqual(await page.locator("#incidentPosition").inner_text(), "2 of 2")
                await page.keyboard.press("ArrowLeft")
                self.assertEqual(await page.locator("#incidentPosition").inner_text(), "1 of 2")
                await page.evaluate("() => showFaceMatchDetail(201, false, false)")
                await page.wait_for_function(
                    "() => document.querySelector('[data-face-person-crop]')"
                    "?.naturalWidth === 44",
                )
                await page.wait_for_function(
                    "() => document.querySelector('[data-face-crop]')"
                    "?.naturalWidth === 100",
                )
                self.assertIn(
                    "v=18-12-31-26",
                    await page.locator("[data-face-crop]").get_attribute("src"),
                )
                evidence_image = page.locator("[data-face-evidence-image]")
                self.assertTrue(
                    (await evidence_image.get_attribute("src")).endswith("/api/snapshot/201"),
                )
                self.assertIn(
                    "person box",
                    (await page.locator("[data-face-person-crop-status]").inner_text()).casefold(),
                )
                self.assertEqual(
                    await page.locator("[data-face-person-crop]").evaluate(
                        "(image) => [image.naturalWidth, image.naturalHeight]",
                    ),
                    [44, 72],
                )
                await page.evaluate("""
                    () => {
                      document.querySelectorAll(".tab-pane").forEach(
                        (pane) => pane.classList.remove("active"),
                      );
                      document.getElementById("tab-face-matches").classList.add("active");
                    }
                """)
                await page.locator("[data-face-frame-toggle]").click()
                self.assertTrue(
                    (await evidence_image.get_attribute("src")).endswith(
                        "/api/violations/201/evidence/clean",
                    ),
                )
                await page.set_viewport_size({"width": 390, "height": 844})
                face_mobile_layout = await page.locator(".face-case-grid").evaluate(
                    "(element) => ({columns: getComputedStyle(element).gridTemplateColumns, "
                    "width: element.scrollWidth, clientWidth: element.clientWidth})",
                )
                self.assertEqual(len(face_mobile_layout["columns"].split()), 1)
                self.assertLessEqual(face_mobile_layout["width"], face_mobile_layout["clientWidth"])
                self.assertEqual(page_errors, [])
            finally:
                await browser.close()

    async def test_incident_face_match_opens_selected_case_in_face_matches_tab(self):
        face_match = {
            "incident_id": 201, "person_track_id": 11, "person_number": 1,
            "violation_type": "no_helmet", "status": "NEEDS_REVIEW",
            "source": "live", "violation_status": "unreviewed",
            "camera_id": "CAM01", "camera_name": "CAM01",
            "area_group_name": "North Area", "epoch_time": 1790000000,
            "snapshot_url": "/api/snapshot/201",
            "clean_snapshot_url": None, "face_crop_available": False,
            "face_bbox": None, "person_bbox": [10, 8, 50, 72],
            "detection_metadata": {
                "frame_width": 100, "frame_height": 80,
                "person_box": [10, 8, 50, 72],
            },
            "face_quality": {}, "thresholds": {}, "top5_candidates": [],
        }

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()

            async def mock_api(route):
                request = route.request
                path = urlparse(request.url).path
                if path == "/api/auth/me":
                    body = {
                        "auth_required": True, "authenticated": True,
                        "csrf_token": "test-token",
                        "user": {"role": "manager", "email": "manager@example.test"},
                    }
                elif path == "/api/area-groups":
                    body = GROUPS
                elif path == "/api/cameras":
                    body = CAMERAS
                elif path == "/api/stats":
                    body = {
                        "total_violations": 0, "hourly": [], "by_type": [],
                        "by_area_group": [], "by_camera": [],
                    }
                elif path == "/api/rules":
                    body = []
                elif path == "/api/violations":
                    body = [{
                        "id": 201, "camera_id": "CAM01", "camera_name": "CAM01",
                        "area_group": "North Area", "violation_type": "no_helmet",
                        "detected_at": datetime.now(UTC).isoformat(),
                        "snapshot_url": "/api/snapshot/201",
                        "review_status": "unreviewed", "detected_people": [],
                        "face_recognition_enabled": True, "face_match_available": True,
                    }]
                elif path == "/api/face/matches":
                    body = [face_match]
                elif path == "/api/face-matches/201":
                    body = face_match
                elif path == "/api/face/enrollments":
                    body = []
                elif path == "/api/snapshot/201":
                    await route.fulfill(
                        status=200, content_type="image/svg+xml",
                        body=(
                            '<svg xmlns="http://www.w3.org/2000/svg" '
                            'width="100" height="80"><rect width="100" '
                            'height="80" fill="#777"/></svg>'
                        ),
                    )
                    return
                else:
                    body = {}
                await route.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps(body),
                )

            await page.route("**/api/**", mock_api)
            try:
                await page.goto(self.base_url)
                await page.locator('[data-tab="violations"]').click()
                await page.locator('[data-incident-select="201"]').wait_for()
                await page.locator('[data-incident-select="201"]').click()
                await page.locator('[data-face-match-open="201"]').click()

                self.assertTrue(
                    await page.locator("#faceMatchesTab").evaluate(
                        "(button) => button.classList.contains('active')",
                    ),
                )
                self.assertTrue(
                    await page.locator("#tab-face-matches").evaluate(
                        "(pane) => pane.classList.contains('active')",
                    ),
                )
                self.assertIn(
                    "INCIDENT #201",
                    await page.locator("#faceMatchDrawerTitle").inner_text(),
                )
                self.assertTrue(
                    await page.locator('[data-face-match-id="201"]').evaluate(
                        "(card) => card.classList.contains('active')",
                    ),
                )
            finally:
                await browser.close()

    async def test_adding_and_editing_camera_preserves_live_wall_tiles_and_sockets(self):
        speed_settings_requests = []
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
                elif path == "/api/cameras" and request.method == "POST":
                    body = request.post_data_json
                    group = next(
                        (item for item in GROUPS
                         if item["id"] == body["area_group_id"]),
                        None,
                    )
                    body.update(
                        connected=False,
                        worker_running=False,
                        area_group_name=group["name"] if group else None,
                    )
                    cameras.append(body)
                elif path == "/api/cameras" and request.method == "GET":
                    body = cameras
                elif path == "/api/cameras/CAM01" and request.method == "GET":
                    body = cameras[0]
                elif path == "/api/cameras/CAM01" and request.method == "PUT":
                    cameras[0] = {**cameras[0], **request.post_data_json}
                    body = cameras[0]
                elif path.endswith("/speed-settings"):
                    speed_settings_requests.append((path, request.method))
                    body = {
                        "camera_id": path.split("/")[-2],
                        "enabled": False,
                        "gate_a": None,
                        "gate_b": None,
                    }
                elif path.endswith("/speed-measurements"):
                    body = [{
                        "id": 44,
                        "measured_at": "2026-11-09T06:34:06Z",
                        "vehicle_type": "car",
                        "speed_kmh": 24.4,
                        "speed_limit_kmh": 15,
                        "speeding": True,
                        "direction": "A-to-B",
                        "snapshot_url": "/api/cameras/CAM01/speed-measurements/44/evidence",
                        "video_url": "/api/cameras/CAM01/speed-measurements/44/evidence?kind=video",
                    }]
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
                self.assertTrue(await page.evaluate(
                    "document.createElement('video').canPlayType('video/webm; codecs=\"vp8\"') !== ''",
                ))

                await page.locator('[data-tab="cameras"]').click()
                await page.locator(
                    '#speedMeasurementsBody a[href$="/evidence"]',
                ).wait_for()
                await page.locator(
                    '#speedMeasurementsBody a[href*="kind=video"]',
                ).wait_for()
                await page.locator('[data-speed-cam="CAM01"]').click()
                await page.get_by_role(
                    "heading", name="CALIBRATE CAM01 SPEED TRACKING",
                ).wait_for()
                self.assertIn(
                    ("/api/cameras/CAM01/speed-settings", "GET"),
                    speed_settings_requests,
                )
                await page.locator("#btnCancelSpeedCalibration").click()

                await page.locator("#btnNewCamera").click()
                await page.wait_for_function(
                    "() => document.getElementById('cameraModalBackdrop').classList.contains('open')",
                )
                await page.locator("#camCameraId").fill("CAM03")
                await page.locator("#camName").fill("CAM03 Added")
                await page.locator("#camRtspUrl").fill(
                    "rtsp://192.168.1.22/Streaming/Channels/101",
                )
                await page.locator("#camAreaGroup").select_option("11")
                async with page.expect_response(
                    lambda response: response.url.endswith("/api/cameras")
                    and response.request.method == "POST",
                ) as add_response:
                    await page.locator("#cameraForm button[type=submit]").click()
                self.assertEqual((await add_response.value).status, 200)
                await page.wait_for_function(
                    "() => !document.getElementById('cameraModalBackdrop').classList.contains('open')",
                )
                self.assertTrue(await page.locator('[data-edit-cam="CAM03"]').count())
                await page.evaluate(
                    "window.__originalCamera2Tile = document.getElementById('tile-CAM02')",
                )
                socket_count_after_add = await page.evaluate(
                    "window.__cameraWebSockets.filter(url => url.includes('/ws/video/')).length",
                )

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
                self.assertEqual(socket_count_after, socket_count_after_add)
                self.assertEqual(
                    await page.locator("#tile-CAM01 .tile-cam-name").inner_text(),
                    "CAM01 Updated",
                )
            finally:
                await browser.close()


if __name__ == "__main__":
    unittest.main()
