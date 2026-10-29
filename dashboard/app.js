/* ============================================================
  INGPCE Operations Console — client logic
   ============================================================ */

const API = {
  cameras: "/api/cameras", areaGroups: "/api/area-groups", violations: "/api/violations",
  stats: "/api/stats", rules: "/api/rules", syncSeed: "/api/cameras/sync-seed",
};
const state = {
  cameras: [], areaGroups: [], rules: [], wsEventsConnected: false,
  wallVideoSocket: null, wallVideoReconnectTimer: null,
  wallVideoReconnectAttempts: 0,
  wallSubscribedIds: "", wallResubscribeTimer: null,
  wallFrameBuffer: new Map(), wallRenderQueued: false,
  violationGallery: [], violationGalleryIndex: 0, violationGalleryOffset: 0, cameraViewerId: null,
  cameraStatusReport: null,
  cameraStatusAbortController: null, cameraStatusDebounceTimer: null,
  analyticsStats: null, analyticsWindowHours: 24, analyticsResizeTimer: null,
  canManageAlertDelivery: false,
  canReviewFaceMatches: false,
  canManageFaceSettings: false,
  wallColumns: 4,
  playbackActive: false,
  faceFilterTimer: null,
};

document.addEventListener("DOMContentLoaded", async () => {
  startClock();
  const authReady = await initializeAuthentication();
  if (!authReady) return;
  setupTabs();
  await loadAreaGroups();
  await loadCameras();
  connectEventStream();
  await refreshStats();
  await refreshRules();
  setupRuleModal();
  setupLightbox();
  setupViolationsGallery();
  setupFaceMatches();
  setupFaceEnrollment();
  setupCameraViewer();
  setupCameraModal();
  setupGroupsModal();
  setupReconnectAll();
  setupLiveWall();
  setupPlayback();
  setupSyncSeed();
  setupCameraStatusReport();
  setupAnalyticsDashboard();
  setupPowerAutomateTest();
  setInterval(refreshStats, 15000);
  setInterval(refreshCameraStatuses, 8000);
  setInterval(() => {
    if (document.getElementById("tab-camera-status").classList.contains("active")) {
      loadCameraStatusReport();
    }
  }, 60000);
});

async function initializeAuthentication() {
  const overlay = document.getElementById("authOverlay");
  const form = document.getElementById("loginForm");
  const error = document.getElementById("loginError");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: document.getElementById("loginEmail").value,
          password: document.getElementById("loginPassword").value,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      window.location.reload();
    } catch (err) {
      error.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  });

  const response = await fetch("/api/auth/me");
  if (!response.ok) {
    overlay.hidden = false;
    error.textContent = "Could not verify the sign-in session. Retry after checking the server.";
    return false;
  }
  const session = await response.json();
  if (session.auth_required && !session.authenticated) {
    overlay.hidden = false;
    return false;
  }
  if (session.authenticated) {
    installCsrfFetch(session.csrf_token);
    state.canManageAlertDelivery = ["admin", "manager"].includes(session.user.role);
    state.canReviewFaceMatches = ["admin", "manager"].includes(session.user.role);
    state.canManageFaceSettings = session.user.role === "admin";
    document.getElementById("alertDeliveryTab").hidden = !state.canManageAlertDelivery;
    document.getElementById("faceMatchesTab").hidden = !state.canReviewFaceMatches;
    if (session.user.role !== "admin") {
      document.getElementById("btnCleanOldViolations").hidden = true;
    }
    const logout = document.getElementById("logoutBtn");
    logout.hidden = false;
    logout.addEventListener("click", async () => {
      await fetch("/api/auth/logout", { method: "POST" });
      window.location.reload();
    });
  }
  if (!session.auth_required) {
    state.canManageAlertDelivery = true;
    document.getElementById("alertDeliveryTab").hidden = false;
  }
  return true;
}

function installCsrfFetch(csrfToken) {
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, options = {}) => {
    const requestMethod = input instanceof Request ? input.method : "GET";
    const method = (options.method || requestMethod).toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
      headers.set("X-CSRF-Token", csrfToken);
      options = { ...options, headers };
    }
    return originalFetch(input, options);
  };
}

function startClock() {
  const el = document.getElementById("liveClock");
  const tick = () => { el.textContent = new Date().toLocaleTimeString(); };
  tick(); setInterval(tick, 1000);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function formatViolationLabel(violationType) {
  const labels = {
    no_helmet: "Missing Helmet",
    no_vest: "Missing Safety Vest",
    phone_detected: "Phone Detected Near Person",
  };
  if (labels[violationType]) return labels[violationType];
  const item = (violationType || "").replace(/^no_/, "").replace(/_/g, " ");
  return item ? `Missing ${item}` : "Violation";
}

// ---------------------------------------------------------------
// "Sync Default Cameras" button — the fix for cameras missing from
// the frontend (e.g. only 1 of 16 ever made it into the database
// because a previous startup crashed/was interrupted mid-seed).
// Calls POST /api/cameras/sync-seed, which adds any of the 16 cameras
// defined in app/config.py that are STILL missing from the DB, without
// touching any camera that already exists. Safe to click any number
// of times.
// ---------------------------------------------------------------
function setupSyncSeed() {
  const btn = document.getElementById("btnSyncSeed");
  const statusEl = document.getElementById("syncStatus");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "Syncing…";
    statusEl.textContent = "";
    statusEl.style.color = "var(--accent-green)";
    try {
      const res = await fetch(API.syncSeed, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const result = await res.json();
      if (result.cameras_created > 0) {
        statusEl.textContent = `✓ Added ${result.cameras_created} missing camera(s): ${result.newly_created_camera_ids.join(", ")}`;
      } else {
        statusEl.textContent = `✓ All cameras already present (${result.cameras_already_existed} found) - nothing to add.`;
      }
      await loadCameras();
      await loadAreaGroups();
    } catch (e) {
      statusEl.style.color = "var(--accent-red)";
      statusEl.textContent = `✗ Sync failed: ${e.message}`;
      console.error("Sync-seed failed:", e);
    } finally {
      btn.disabled = false;
      btn.textContent = "⟲ Sync Default Cameras";
    }
  });
}

// ---------------------------------------------------------------
// Area Groups
// ---------------------------------------------------------------
async function loadAreaGroups() {
  try { const res = await fetch(API.areaGroups); state.areaGroups = await res.json(); }
  catch (e) { state.areaGroups = []; }
  populateAreaGroupSelects();
}

function populateAreaGroupSelects() {
  const targets = [
    document.getElementById("camAreaGroup"),
    document.getElementById("violationAreaGroupFilter"),
    document.getElementById("cameraStatusGroup"),
    document.getElementById("wallAreaFilter"),
  ];
  targets.forEach((select) => {
    if (!select) return;
    const currentValue = select.value;
    const placeholder = select === document.getElementById("violationAreaGroupFilter")
      ? '<option value="">All Area Groups</option>' : '<option value="">Unassigned</option>';
    const selectPlaceholder = select === document.getElementById("wallAreaFilter")
      ? '<option value="">All areas</option>'
      : select === document.getElementById("cameraStatusGroup")
        ? '<option value="">All groups</option>' : placeholder;
    select.innerHTML = selectPlaceholder;
    state.areaGroups.forEach((g) => {
      const opt = document.createElement("option");
      opt.value = g.id; opt.textContent = g.name; select.appendChild(opt);
    });
    select.value = currentValue || "";
  });
}

function setupGroupsModal() {
  const backdrop = document.getElementById("groupsModalBackdrop");
  document.getElementById("btnManageGroups").addEventListener("click", () => {
    renderGroupsTable(); backdrop.classList.add("open");
  });
  document.getElementById("btnCloseGroups").addEventListener("click", () => backdrop.classList.remove("open"));
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) backdrop.classList.remove("open"); });
  document.getElementById("btnAddGroup").addEventListener("click", async () => {
    const name = document.getElementById("newGroupName").value.trim();
    const description = document.getElementById("newGroupDesc").value.trim();
    if (!name) return;
    await fetch(API.areaGroups, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, description }) });
    document.getElementById("newGroupName").value = ""; document.getElementById("newGroupDesc").value = "";
    await loadAreaGroups(); renderGroupsTable(); await loadCameras();
  });
}

function renderGroupsTable() {
  const tbody = document.getElementById("groupsTableBody"); tbody.innerHTML = "";
  state.areaGroups.forEach((g) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escapeHtml(g.name)}</td><td>${g.description ? escapeHtml(g.description) : "<span style='color:var(--text-dim)'>—</span>"}</td><td>${g.camera_count ?? 0}</td><td><button class="icon-btn danger" data-delete-group="${g.id}">Delete</button></td>`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("[data-delete-group]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this area group? Cameras assigned to it will become unassigned.")) return;
      await fetch(`${API.areaGroups}/${btn.dataset.deleteGroup}`, { method: "DELETE" });
      await loadAreaGroups(); renderGroupsTable(); await loadCameras();
    }));
}

// ---------------------------------------------------------------
// Cameras: list + Feeds rendering
// ---------------------------------------------------------------
async function loadCameras() {
  const grid = document.getElementById("feedsByGroup");
  try {
    const res = await fetch(API.cameras);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    state.cameras = await res.json();
  } catch (e) {
    console.error("Failed to load cameras:", e);
    state.cameras = [];
    grid.innerHTML = `<div class="error-state">Could not load cameras.<div class="error-detail">${escapeHtml(e.message)}</div></div>`;
    return;
  }

  renderFeedsGroupedByArea();
  renderCamerasTable();
  populateViolationCameraFilter();
  populateWallCameraFilter();
  populatePlaybackCameraSelect();

  const activeCameras = state.cameras.filter((camera) => camera.enabled);
  document.getElementById("statCamerasOnline").textContent =
    `${activeCameras.filter((camera) => camera.connected).length}/${activeCameras.length}`;
  populateCameraStatusCameraFilter();
}

function populateWallCameraFilter() {
  const select = document.getElementById("wallAreaFilter");
  if (!select) return;
  const currentValue = select.value;
  select.innerHTML = '<option value="">All areas</option>';
  state.areaGroups.forEach((group) => {
    const option = document.createElement("option");
    option.value = group.name;
    option.textContent = group.name;
    select.appendChild(option);
  });
  select.value = currentValue || "";
}

function populateCameraStatusCameraFilter() {
  const select = document.getElementById("cameraStatusCamera");
  if (!select) return;
  const currentValue = select.value;
  select.innerHTML = '<option value="">All cameras</option>';
  state.cameras.forEach((camera) => {
    const option = document.createElement("option");
    option.value = camera.camera_id;
    option.textContent = camera.name;
    select.appendChild(option);
  });
  select.value = currentValue || "";
}

function populatePlaybackCameraSelect() {
  const select = document.getElementById("playbackCamera");
  if (!select) return;
  const currentValue = select.value;
  select.innerHTML = '<option value="">Select a camera</option>';
  state.cameras.forEach((camera) => {
    if (!camera.enabled) return;
    const option = document.createElement("option");
    option.value = camera.camera_id;
    option.textContent = camera.name;
    select.appendChild(option);
  });
  select.value = currentValue || "";
  updatePlaybackCameraIp();
}

function updatePlaybackCameraIp() {
  const camera = state.cameras.find(
    (item) => item.camera_id === document.getElementById("playbackCamera").value,
  );
  document.getElementById("playbackIp").value = camera?.ip_address || "";
}

function setupPlayback() {
  const form = document.getElementById("playbackForm");
  const image = document.getElementById("playbackImage");
  const placeholder = document.getElementById("playbackPlaceholder");
  const status = document.getElementById("playbackStatus");
  const stopButton = document.getElementById("btnStopPlayback");
  const startInput = document.getElementById("playbackStart");
  let playbackGeneration = 0;
  const defaultStart = new Date(Date.now() - 10 * 60 * 1000);
  startInput.value = new Date(
    defaultStart.getTime() - defaultStart.getTimezoneOffset() * 60000,
  ).toISOString().slice(0, 16);

  document.getElementById("playbackCamera").addEventListener("change", updatePlaybackCameraIp);

  const stopPlayback = () => {
    playbackGeneration += 1;
    state.playbackActive = false;
    image.onload = null;
    image.onerror = null;
    image.removeAttribute("src");
    image.hidden = true;
    placeholder.hidden = false;
    stopButton.disabled = true;
  };
  stopButton.addEventListener("click", () => {
    stopPlayback();
    status.textContent = "Playback stopped.";
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const cameraId = document.getElementById("playbackCamera").value;
    const start = startInput.value;
    if (!cameraId || !start) return;
    stopPlayback();
    const query = new URLSearchParams({
      start,
      duration_minutes: document.getElementById("playbackDuration").value,
    });
    image.hidden = false;
    placeholder.hidden = true;
    stopButton.disabled = false;
    state.playbackActive = true;
    const generation = ++playbackGeneration;
    image.onload = () => {
      if (playbackGeneration === generation) status.textContent = "Playback is running.";
    };
    image.onerror = () => {
      if (playbackGeneration !== generation) return;
      state.playbackActive = false;
      image.hidden = true;
      stopButton.disabled = true;
      status.textContent = "Playback stopped or unavailable. Check the recording time and NVR.";
    };
    status.textContent = "Connecting to the camera NVR…";
    image.src = `${API.cameras}/${encodeURIComponent(cameraId)}/playback?${query}`;
  });
}

function populateViolationCameraFilter() {
  const select = document.getElementById("violationCameraFilter");
  const currentValue = select.value;
  select.innerHTML = '<option value="">All Cameras</option>';
  state.cameras.forEach((cam) => {
    const opt = document.createElement("option"); opt.value = cam.camera_id; opt.textContent = cam.name; select.appendChild(opt);
  });
  select.value = currentValue || "";
}

function renderFeedsGroupedByArea() {
  const container = document.getElementById("feedsByGroup");
  closeVideoWall();
  container.innerHTML = "";

  const groups = {};
  state.cameras.filter((cam) => cam.enabled).forEach((cam) => {
    const key = cam.area_group_name || "Unassigned";
    if (!groups[key]) groups[key] = [];
    groups[key].push(cam);
  });

  const groupNames = Object.keys(groups).sort((a, b) => {
    if (a === "Unassigned") return 1; if (b === "Unassigned") return -1; return a.localeCompare(b);
  });

  if (!groupNames.length) {
    container.innerHTML = `<div class="event-empty">No active cameras configured. Enable a camera from the Cameras tab.</div>`;
    return;
  }

  groupNames.forEach((groupName) => {
    const cams = groups[groupName];
    const section = document.createElement("div"); section.className = "feed-group";
    section.dataset.area = groupName;
    section.innerHTML = `<div class="feed-group-header"><h4>${escapeHtml(groupName)}</h4><span class="feed-group-count">${cams.length} FEED${cams.length !== 1 ? "S" : ""}</span></div><div class="camera-grid" style="--wall-columns:${state.wallColumns}"></div>`;
    container.appendChild(section);
    const grid = section.querySelector(".camera-grid");

    cams.forEach((cam) => {
      try {
        const tile = buildCameraTile(cam);
        grid.appendChild(tile);
      } catch (err) {
        console.error(`Failed to render tile for camera ${cam && cam.camera_id}:`, err);
        const errorTile = document.createElement("div");
        errorTile.className = "camera-tile";
        errorTile.innerHTML = `<div class="error-state" style="padding:20px;">Failed to render this camera tile.<div class="error-detail">${escapeHtml(String(err))}</div></div>`;
        grid.appendChild(errorTile);
      }
    });
  });
  applyLiveWallFilters();
  connectVideoWall();
}

function setupLiveWall() {
  const grid = document.getElementById("feedsByGroup");
  const areaFilter = document.getElementById("wallAreaFilter");
  const search = document.getElementById("wallCameraSearch");
  areaFilter.addEventListener("change", applyLiveWallFilters);
  search.addEventListener("input", applyLiveWallFilters);
  document.querySelectorAll("[data-wall-columns]").forEach((button) => {
    button.addEventListener("click", () => {
      state.wallColumns = Number(button.dataset.wallColumns);
      document.querySelectorAll("[data-wall-columns]").forEach((item) => {
        const active = item === button;
        item.classList.toggle("active", active);
        item.setAttribute("aria-pressed", String(active));
      });
      grid.querySelectorAll(".camera-grid").forEach((cameraGrid) => {
        cameraGrid.style.setProperty("--wall-columns", state.wallColumns);
      });
    });
  });
  document.getElementById("btnWallFocus").addEventListener("click", toggleWallFocus);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setWallFocus(false);
  });
  updateWallFeedCount();
}

function applyLiveWallFilters() {
  const area = document.getElementById("wallAreaFilter").value;
  const query = document.getElementById("wallCameraSearch").value.trim().toLowerCase();
  document.querySelectorAll("#feedsByGroup .feed-group").forEach((group) => {
    let visibleTiles = 0;
    group.querySelectorAll(".camera-tile").forEach((tile) => {
      const cameraId = tile.id.replace(/^tile-/, "");
      const camera = state.cameras.find((item) => item.camera_id === cameraId);
      const matchesArea = !area || group.dataset.area === area;
      const matchesQuery = !query || `${camera?.name || ""} ${cameraId}`.toLowerCase().includes(query);
      const visible = matchesArea && matchesQuery;
      tile.hidden = !visible;
      if (visible) visibleTiles += 1;
    });
    group.hidden = visibleTiles === 0;
  });
  updateWallFeedCount();
  scheduleWallResubscribe();
}

function updateWallFeedCount() {
  const visible = document.querySelectorAll("#feedsByGroup .camera-tile:not([hidden])").length;
  const online = state.cameras.filter((camera) => camera.enabled && camera.connected).length;
  const count = document.getElementById("wallFeedCount");
  if (count) count.textContent = `${online} ONLINE · ${visible} SHOWN`;
}

function toggleWallFocus() {
  const focused = !document.querySelector(".ops-grid").classList.contains("wall-focus");
  setWallFocus(focused);
}

function setWallFocus(focused) {
  const shell = document.querySelector(".ops-grid");
  const button = document.getElementById("btnWallFocus");
  shell.classList.toggle("wall-focus", focused);
  button.setAttribute("aria-pressed", String(focused));
  button.textContent = focused ? "⛶ EXIT FOCUS" : "⛶ FOCUS";
  document.body.classList.toggle("wall-focus-active", focused);
}

function cameraConnectionLabel(camera) {
  if (camera.connection_state === "live") return "LIVE";
  if (camera.connection_state === "connecting") return "CONNECTING";
  if (camera.connection_state === "failed") return "RETRYING";
  return camera.connected ? "LIVE" : "OFFLINE";
}

function updateCameraTileStatus(camera) {
  const status = document.getElementById(`status-${camera.camera_id}`);
  if (!status) return;
  const label = cameraConnectionLabel(camera);
  status.className = `tile-status ${label === "LIVE" ? "online" : "offline"}`;
  status.querySelector("span:last-child").textContent = label;
  status.title = camera.connection_error
    || (label === "CONNECTING" ? "Connecting to the configured stream." : "");
  const canvas = document.getElementById(`canvas-${camera.camera_id}`);
  const placeholder = document.getElementById(`placeholder-${camera.camera_id}`);
  if (placeholder && (!canvas || !canvas.width)) {
    placeholder.textContent = camera.connection_error
      || (label === "CONNECTING" ? "Connecting…" : "Stream unavailable; retrying…");
  }
}

function buildCameraTile(cam) {
  const tile = document.createElement("div");
  tile.className = "camera-tile";
  tile.tabIndex = 0;
  tile.setAttribute("role", "button");
  tile.setAttribute("aria-label", `Open live view for ${cam.name}`);
  tile.id = `tile-${cam.camera_id}`;
  tile.innerHTML = `
    <div class="feed-wrap">
      <canvas id="canvas-${cam.camera_id}"></canvas>
      <div class="feed-placeholder" id="placeholder-${cam.camera_id}">Connecting…</div>
      <div class="violation-flash" id="flash-${cam.camera_id}"></div>
    </div>
    <div class="tile-footer">
      <div>
        <span class="tile-cam-name">${escapeHtml(cam.name)}</span>
        <span class="tile-cam-zone">${escapeHtml(cam.camera_id)}</span>
      </div>
      <div class="tile-actions">
        <div class="tile-status ${cam.connected ? "online" : "offline"}" id="status-${cam.camera_id}">
          <span class="dot"></span><span>${cameraConnectionLabel(cam)}</span>
        </div>
        <button class="tile-edit-btn" data-edit-tile="${cam.camera_id}" title="Edit camera connection">EDIT</button>
        <button class="tile-reconnect-btn" data-reconnect-tile="${cam.camera_id}" title="Reconnect this camera">↻</button>
      </div>
    </div>
  `;
  updateCameraTileStatus(cam);
  tile.querySelector(`[data-reconnect-tile="${cam.camera_id}"]`).addEventListener("click", (e) => {
    e.stopPropagation();
    reconnectSingleCamera(cam.camera_id);
  });
  tile.querySelector(`[data-edit-tile="${cam.camera_id}"]`).addEventListener("click", (e) => {
    e.stopPropagation();
    openCameraModal(cam.camera_id);
  });
  tile.addEventListener("click", () => openCameraViewer(cam.camera_id));
  tile.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && event.target === tile) {
      event.preventDefault();
      openCameraViewer(cam.camera_id);
    }
  });
  return tile;
}

async function reconnectSingleCamera(cameraId) {
  const btn = document.querySelector(`[data-reconnect-tile="${cameraId}"]`);
  if (btn) { btn.disabled = true; btn.classList.add("spinning"); }
  const placeholder = document.getElementById(`placeholder-${cameraId}`);
  if (placeholder) { placeholder.style.display = "block"; placeholder.textContent = "Reconnecting…"; }
  try {
    await fetch(`${API.cameras}/${cameraId}/refresh`, { method: "POST" });
    await new Promise((r) => setTimeout(r, 1000));
    connectVideoStream(cameraId, true);
    await refreshCameraStatuses();
  } catch (e) {
    console.error(`Reconnect failed for ${cameraId}:`, e);
  } finally {
    if (btn) { btn.disabled = false; btn.classList.remove("spinning"); }
  }
}

function setupReconnectAll() {
  document.getElementById("btnReconnectAll").addEventListener("click", async () => {
    const btn = document.getElementById("btnReconnectAll");
    btn.disabled = true;
    btn.textContent = "Reconnecting all cameras…";
    try {
      for (const cam of state.cameras.filter((camera) => camera.enabled)) {
        fetch(`${API.cameras}/${cam.camera_id}/refresh`, { method: "POST" }).catch(() => {});
        await new Promise((r) => setTimeout(r, 150));
      }
      await new Promise((r) => setTimeout(r, 1500));
      await loadCameras();
    } finally {
      btn.disabled = false;
      btn.textContent = "↻ Reconnect All Cameras";
    }
  });
}

function closeVideoStream(cameraId) {
  closeVideoWall();
}

function connectVideoStream(cameraId, replaceExisting = false) {
  connectVideoWall(replaceExisting);
}

function closeVideoWall() {
  if (state.wallVideoReconnectTimer) {
    clearTimeout(state.wallVideoReconnectTimer);
    state.wallVideoReconnectTimer = null;
  }
  const ws = state.wallVideoSocket;
  state.wallVideoSocket = null;
  state.wallFrameBuffer.clear();
  state.wallSubscribedIds = "";
  if (ws) {
    ws.onclose = null;
    ws.onerror = null;
    ws.onmessage = null;
    try { ws.close(); } catch (error) {
      console.error("Could not close the live wall stream:", error);
    }
  }
}

function visibleWallCameraIds() {
  return state.cameras
    .filter((camera) => camera.enabled)
    .filter((camera) => {
      const tile = document.getElementById(`tile-${camera.camera_id}`);
      return tile && !tile.hidden;
    })
    .map((camera) => camera.camera_id);
}

// Re-check the wall subscription shortly after filters change so quick
// successive searches do not reopen the socket for every keystroke.
function scheduleWallResubscribe() {
  if (state.wallResubscribeTimer) clearTimeout(state.wallResubscribeTimer);
  state.wallResubscribeTimer = setTimeout(() => {
    state.wallResubscribeTimer = null;
    connectVideoWall();
  }, 250);
}

function connectVideoWall(replaceExisting = false) {
  // Subscribe only to tiles that are actually visible on the wall. Hidden
  // tiles (filtered by area or search) would otherwise force the server to
  // keep encoding and pushing frames nobody is watching.
  const cameraIds = visibleWallCameraIds();
  const desiredIds = cameraIds.join(",");
  if (!cameraIds.length) {
    closeVideoWall();
    return;
  }

  const existing = state.wallVideoSocket;
  if (existing && !replaceExisting
      && state.wallSubscribedIds === desiredIds
      && [WebSocket.CONNECTING, WebSocket.OPEN].includes(existing.readyState)) return;
  closeVideoWall();
  state.wallSubscribedIds = desiredIds;

  const proto = location.protocol === "https:" ? "wss" : "ws";
  const params = new URLSearchParams({ camera_ids: cameraIds.join(",") });
  const ws = new WebSocket(`${proto}://${location.host}/ws/video?${params}`);
  ws.binaryType = "arraybuffer";
  state.wallVideoSocket = ws;

  ws.onopen = () => {
    state.wallVideoReconnectAttempts = 0;
  };

  // Coalesced delivery: only the newest frame per camera is buffered and
  // all buffered frames are decoded in one animation-frame pass, so a slow
  // decode drops stale frames instead of falling behind the live scene.
  ws.onmessage = (event) => {
    try {
      if (!(event.data instanceof ArrayBuffer) || event.data.byteLength < 3) return;
      const idLength = new DataView(event.data).getUint16(0);
      if (event.data.byteLength <= idLength + 2) return;
      const cameraId = new TextDecoder().decode(
        new Uint8Array(event.data, 2, idLength),
      );
      if (!document.getElementById(`canvas-${cameraId}`)) return;
      state.wallFrameBuffer.set(cameraId, event.data.slice(idLength + 2));
      if (!state.wallRenderQueued) {
        state.wallRenderQueued = true;
        requestAnimationFrame(renderWallFrames);
      }
    } catch (error) {
      console.warn("Could not buffer a live wall frame:", error);
    }
  };

  ws.onclose = () => {
    if (state.wallVideoSocket !== ws) return;
    state.wallVideoSocket = null;
    cameraIds.forEach((cameraId) => {
      const placeholder = document.getElementById(`placeholder-${cameraId}`);
      if (placeholder) {
        placeholder.style.display = "block";
        placeholder.textContent = "RECONNECTING…";
      }
    });
    state.wallVideoReconnectTimer = setTimeout(() => {
      state.wallVideoReconnectTimer = null;
      connectVideoWall();
    }, Math.min(2000 * (2 ** state.wallVideoReconnectAttempts++), 30000));
  };
  ws.onerror = () => {
    if (state.wallVideoSocket === ws) ws.close();
  };
}

// One decode-and-draw pass per animation frame for every camera that has a
// newer frame, keeping the wall in sync with the newest available image.
async function renderWallFrames() {
  state.wallRenderQueued = false;
  if (!state.wallVideoSocket) return;
  const frames = Array.from(state.wallFrameBuffer.entries());
  state.wallFrameBuffer.clear();
  for (const [cameraId, jpegBytes] of frames) {
    try {
      const tile = document.getElementById(`tile-${cameraId}`);
      if (tile && tile.hidden) continue;
      const bitmap = await createImageBitmap(
        new Blob([jpegBytes], { type: "image/jpeg" }),
      );
      const canvas = document.getElementById(`canvas-${cameraId}`);
      if (canvas) drawBitmapToCanvas(canvas, bitmap);
      if (state.cameraViewerId === cameraId) {
        const viewer = document.getElementById("cameraViewerCanvas");
        const backdrop = document.getElementById("cameraViewerBackdrop");
        if (viewer && backdrop && backdrop.classList.contains("open")) {
          drawBitmapToCanvas(viewer, bitmap);
        }
      }
      bitmap.close();
      const placeholder = document.getElementById(`placeholder-${cameraId}`);
      if (placeholder) placeholder.style.display = "none";
    } catch (error) {
      console.warn("Could not decode a live wall frame:", error);
    }
  }
}

function drawBitmapToCanvas(canvas, bitmap) {
  if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
  }
  const context = canvas._wallContext
    || (canvas._wallContext = canvas.getContext("2d", { alpha: false }));
  context.drawImage(bitmap, 0, 0);
}

async function refreshCameraStatuses() {
  try {
    const res = await fetch(API.cameras);
    if (!res.ok) return;
    const cams = await res.json();
    state.cameras = cams;
    cams.forEach(updateCameraTileStatus);
    document.getElementById("statCamerasOnline").textContent =
      `${cams.filter(c => c.enabled && c.connected).length}/${cams.filter(c => c.enabled).length}`;
    renderCamerasTable();
    applyLiveWallFilters();
  } catch (e) { /* transient network blip - next poll will retry */ }
}

function renderCamerasTable() {
  const tbody = document.getElementById("camerasTableBody"); if (!tbody) return; tbody.innerHTML = "";
  state.cameras.forEach((cam) => {
    try {
      const tr = document.createElement("tr");
      const ppeTags = (cam.required_ppe || []).map(p => `<span class="ppe-tag">${escapeHtml(p)}</span>`).join("");
      tr.innerHTML = `<td><code>${escapeHtml(cam.camera_id)}</code></td><td><button class="camera-name-link" data-view-cam="${cam.camera_id}">${escapeHtml(cam.name)}</button></td><td>${cam.area_group_name ? escapeHtml(cam.area_group_name) : "<span style='color:var(--text-dim)'>Unassigned</span>"}</td><td><div class="ppe-tag-list">${ppeTags}</div></td><td><span class="tile-status ${cam.connected ? "online" : "offline"}"><span class="dot"></span>${cam.connected ? "ONLINE" : "OFFLINE"}</span></td><td><span class="state-pill ${cam.worker_running ? "running" : "stopped"}">${cam.worker_running ? "RUNNING" : "STOPPED"}</span></td><td><button class="icon-btn" data-refresh-cam="${cam.camera_id}">↻ Refresh</button><button class="icon-btn" data-edit-cam="${cam.camera_id}">Edit</button><button class="icon-btn danger" data-delete-cam="${cam.camera_id}">Delete</button></td>`;
      tbody.appendChild(tr);
    } catch (err) {
      console.error(`Failed to render camera table row for ${cam && cam.camera_id}:`, err);
    }
  });
  tbody.querySelectorAll("[data-refresh-cam]").forEach(btn => btn.addEventListener("click", () => refreshCameraConnection(btn.dataset.refreshCam)));
  tbody.querySelectorAll("[data-view-cam]").forEach(btn => btn.addEventListener("click", () => openCameraViewer(btn.dataset.viewCam)));
  tbody.querySelectorAll("[data-edit-cam]").forEach(btn => btn.addEventListener("click", () => openCameraModal(btn.dataset.editCam)));
  tbody.querySelectorAll("[data-delete-cam]").forEach(btn => btn.addEventListener("click", () => deleteCamera(btn.dataset.deleteCam)));
}

async function refreshCameraConnection(cameraId) {
  const btn = document.querySelector(`[data-refresh-cam="${cameraId}"]`);
  if (btn) { btn.textContent = "Refreshing…"; btn.disabled = true; }
  try {
    const response = await fetch(`${API.cameras}/${cameraId}/refresh`, { method: "POST" });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    await new Promise(r => setTimeout(r, 800));
    connectVideoStream(cameraId, true);
    await refreshCameraStatuses();
  } finally { if (btn) { btn.textContent = "↻ Refresh"; btn.disabled = false; } }
}

async function deleteCamera(cameraId) {
  if (!confirm(`Delete camera ${cameraId}? This stops its stream and removes it permanently.`)) return;
  await fetch(`${API.cameras}/${cameraId}`, { method: "DELETE" });
  await loadCameras();
}

// ---------------------------------------------------------------
// Camera Add/Edit Modal
// ---------------------------------------------------------------
function setupCameraModal() {
  const backdrop = document.getElementById("cameraModalBackdrop");
  const areaGroup = document.getElementById("camAreaGroup");
  const setArcFlashSuitDefault = () => {
    const selectedGroup = areaGroup.selectedOptions[0]?.textContent.trim();
    document.getElementById("ppeArcFlashSuit").checked = selectedGroup === "MCC";
  };
  document.getElementById("btnNewCamera").addEventListener("click", () => openCameraModal(null));
  document.getElementById("btnCancelCamera").addEventListener("click", () => backdrop.classList.remove("open"));
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) backdrop.classList.remove("open"); });
  areaGroup.addEventListener("change", setArcFlashSuitDefault);
  document.getElementById("cameraForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const { camera, isEdit } = await saveCamera();
      if (isEdit) {
        updateEditedCamera(camera);
      } else {
        await loadCameras();
      }
    } catch (error) {
      alert(`Failed to save camera: ${error.message}`);
      return;
    }
    backdrop.classList.remove("open");
  });
}

function updateEditedCamera(camera) {
  const index = state.cameras.findIndex((item) => item.camera_id === camera.camera_id);
  if (index < 0) {
    throw new Error(`Camera ${camera.camera_id} is no longer in the camera list`);
  }
  camera.connected ??= state.cameras[index].connected;
  camera.worker_running ??= state.cameras[index].worker_running;
  state.cameras[index] = camera;

  const tile = document.getElementById(`tile-${camera.camera_id}`);
  const groupName = camera.area_group_name || "Unassigned";
  if (!camera.enabled) {
    const oldGroup = tile?.closest(".feed-group");
    closeVideoStream(camera.camera_id);
    tile?.remove();
    if (oldGroup && !oldGroup.querySelector(".camera-tile")) oldGroup.remove();
  } else if (tile) {
    const currentGroup = tile.closest(".feed-group");
    if (currentGroup?.dataset.area !== groupName) {
      tile.remove();
      if (currentGroup && !currentGroup.querySelector(".camera-tile")) {
        currentGroup.remove();
      }
      getOrCreateFeedGroup(groupName).querySelector(".camera-grid").appendChild(tile);
    }
    tile.setAttribute("aria-label", `Open live view for ${camera.name}`);
    tile.querySelector(".tile-cam-name").textContent = camera.name;
    updateCameraTileStatus(camera);
  } else if (camera.enabled) {
    getOrCreateFeedGroup(groupName)
      .querySelector(".camera-grid")
      .appendChild(buildCameraTile(camera));
    connectVideoStream(camera.camera_id);
  }

  document.querySelectorAll("#feedsByGroup .feed-group").forEach((group) => {
    const count = group.querySelectorAll(".camera-tile").length;
    if (!count) {
      group.remove();
      return;
    }
    group.querySelector(".feed-group-count").textContent =
      `${count} FEED${count !== 1 ? "S" : ""}`;
  });
  if (!document.querySelector("#feedsByGroup .feed-group")) {
    document.getElementById("feedsByGroup").innerHTML =
      '<div class="event-empty">No active cameras configured. Enable a camera from the Cameras tab.</div>';
  }

  renderCamerasTable();
  populateViolationCameraFilter();
  populateWallCameraFilter();
  populatePlaybackCameraSelect();
  populateCameraStatusCameraFilter();
  document.getElementById("statCamerasOnline").textContent =
    `${state.cameras.filter((item) => item.enabled && item.connected).length}/${state.cameras.filter((item) => item.enabled).length}`;
  connectVideoWall(true);
  applyLiveWallFilters();
}

function getOrCreateFeedGroup(groupName) {
  const container = document.getElementById("feedsByGroup");
  let group = [...container.querySelectorAll(".feed-group")]
    .find((element) => element.dataset.area === groupName);
  if (group) return group;

  container.querySelector(".event-empty")?.remove();
  group = document.createElement("div");
  group.className = "feed-group";
  group.dataset.area = groupName;
  group.innerHTML = `<div class="feed-group-header"><h4>${escapeHtml(groupName)}</h4><span class="feed-group-count">0 FEEDS</span></div><div class="camera-grid" style="--wall-columns:${state.wallColumns}"></div>`;
  container.appendChild(group);
  return group;
}

async function openCameraModal(cameraId) {
  const backdrop = document.getElementById("cameraModalBackdrop");
  const isEdit = !!cameraId;
  document.getElementById("cameraModalTitle").textContent = isEdit ? `Edit Camera — ${cameraId}` : "Add Camera";
  const idField = document.getElementById("camCameraId"); idField.disabled = isEdit;
  if (isEdit) {
    const res = await fetch(`${API.cameras}/${cameraId}`); const cam = await res.json();
    idField.value = cam.camera_id;
    document.getElementById("camName").value = cam.name;
    document.getElementById("camRtspUrl").value = cam.rtsp_url;
    document.getElementById("camAreaGroup").value = cam.area_group_id || "";
    document.getElementById("camEnabled").checked = cam.enabled;
    const ppe = cam.required_ppe || [];
    document.getElementById("ppeHelmet").checked = ppe.includes("helmet");
    document.getElementById("ppeVest").checked = ppe.includes("vest");
    document.getElementById("ppeArcFlashSuit").checked = ppe.includes("arc_flash_suit");
    document.getElementById("ppeMask").checked = ppe.includes("mask");
    document.getElementById("ppeGloves").checked = ppe.includes("gloves");
    document.getElementById("ppeBoots").checked = ppe.includes("boots");
    document.getElementById("ppeGoggles").checked = ppe.includes("goggles");
  } else {
    document.getElementById("cameraForm").reset(); idField.value = "";
    document.getElementById("ppeHelmet").checked = true; document.getElementById("ppeVest").checked = true;
    document.getElementById("camEnabled").checked = true;
    setArcFlashSuitDefault();
  }
  backdrop.classList.add("open");
}

async function saveCamera() {
  const cameraId = document.getElementById("camCameraId").value.trim();
  const isEdit = document.getElementById("camCameraId").disabled;
  const requiredPpe = [];
  if (document.getElementById("ppeHelmet").checked) requiredPpe.push("helmet");
  if (document.getElementById("ppeVest").checked) requiredPpe.push("vest");
  if (document.getElementById("ppeArcFlashSuit").checked) requiredPpe.push("arc_flash_suit");
  if (document.getElementById("ppeMask").checked) requiredPpe.push("mask");
  if (document.getElementById("ppeGloves").checked) requiredPpe.push("gloves");
  if (document.getElementById("ppeBoots").checked) requiredPpe.push("boots");
  if (document.getElementById("ppeGoggles").checked) requiredPpe.push("goggles");
  const areaGroupVal = document.getElementById("camAreaGroup").value;
  if (isEdit) {
    const payload = { name: document.getElementById("camName").value, rtsp_url: document.getElementById("camRtspUrl").value, area_group_id: areaGroupVal ? parseInt(areaGroupVal, 10) : null, required_ppe: requiredPpe, enabled: document.getElementById("camEnabled").checked };
    const res = await fetch(`${API.cameras}/${cameraId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) {
      const error = await res.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${res.status}`);
    }
    return { camera: await res.json(), isEdit: true };
  } else {
    const payload = { camera_id: cameraId, name: document.getElementById("camName").value, rtsp_url: document.getElementById("camRtspUrl").value, area_group_id: areaGroupVal ? parseInt(areaGroupVal, 10) : null, required_ppe: requiredPpe, enabled: document.getElementById("camEnabled").checked };
    const res = await fetch(API.cameras, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) {
      const error = await res.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${res.status}`);
    }
    return { camera: await res.json(), isEdit: false };
  }
}

// ---------------------------------------------------------------
// Live Violation Event Feed (WebSocket)
// ---------------------------------------------------------------
function connectEventStream() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/events`);
  const dot = document.getElementById("wsConnDot");
  const status = document.querySelector(".sys-status");
  const setConnectionState = (connected) => {
    state.wsEventsConnected = connected;
    dot.classList.toggle("connected", connected);
    dot.setAttribute("aria-label", `Event stream ${connected ? "connected" : "disconnected"}`);
    status.textContent = connected ? "STREAM ONLINE" : "STREAM OFFLINE";
  };
  ws.onopen = () => setConnectionState(true);
  ws.onclose = () => { setConnectionState(false); setTimeout(connectEventStream, 2000); };
  ws.onerror = () => ws.close();
  ws.onmessage = (msg) => {
    const event = JSON.parse(msg.data);
    if (event.type === "violation") { renderViolationEvent(event); flashCameraTile(event.camera_id); bumpViolationCounter(); }
  };
}

function renderViolationEvent(event) {
  const feed = document.getElementById("eventFeed");
  const empty = feed.querySelector(".empty-state"); if (empty) empty.remove();
  const item = document.createElement("div"); item.className = "event-item";
  const label = formatViolationLabel(event.violation_type);
  const snapshotUrl = event.snapshot_url || (event.violation_id ? `/api/snapshot/${event.violation_id}` : null);
  item.innerHTML = `${snapshotUrl ? `<img class="ev-thumb" src="${snapshotUrl}" alt="violation snapshot" loading="lazy" />` : `<div class="ev-thumb"></div>`}<div class="ev-body"><div class="ev-top"><span class="ev-type">${label.toUpperCase()}</span><span class="ev-time">${event.timestamp || ""}</span></div><div class="ev-meta">${escapeHtml(event.camera_name || event.camera_id)} · ${escapeHtml(event.area_group || event.zone || "")}</div>${event.rules_matched && event.rules_matched.length ? `<div class="ev-rules">⚡ ${escapeHtml(event.rules_matched.join(", "))}</div>` : `<div class="ev-rules" style="color:var(--text-dim)">No matching alert rule</div>`}</div>`;
  if (snapshotUrl) { item.addEventListener("click", () => openLightbox(snapshotUrl, `${event.camera_name || event.camera_id} · ${event.area_group || event.zone || ""} · ${label} · ${event.timestamp || ""}`)); }
  feed.insertBefore(item, feed.firstChild);
  const items = feed.querySelectorAll(".event-item"); if (items.length > 100) items[items.length - 1].remove();
}

function flashCameraTile(cameraId) {
  const flash = document.getElementById(`flash-${cameraId}`); if (!flash) return;
  flash.classList.remove("active"); void flash.offsetWidth; flash.classList.add("active");
}

function bumpViolationCounter() {
  const el = document.getElementById("statViolations24h"); const current = parseInt(el.textContent, 10);
  if (!isNaN(current)) el.textContent = current + 1;
}

// ---------------------------------------------------------------
// Violations Gallery Tab
// ---------------------------------------------------------------
function setupViolationsGallery() {
  document.getElementById("btnRefreshViolations").addEventListener("click", () => loadViolationsGallery());
  document.getElementById("btnLoadMoreViolations").addEventListener("click", () => loadViolationsGallery(true));
  document.getElementById("violationCameraFilter").addEventListener("change", () => loadViolationsGallery());
  document.getElementById("violationAreaGroupFilter").addEventListener("change", () => loadViolationsGallery());
  document.getElementById("violationTypeFilter").addEventListener("change", () => loadViolationsGallery());
  document.getElementById("violationAgeFilter").addEventListener("change", () => loadViolationsGallery());
  document.getElementById("btnCleanOldViolations").addEventListener("click", cleanOldViolations);
  document.getElementById("violationsGallery").addEventListener("click", async (event) => {
    const faceButton = event.target.closest("[data-face-match-open]");
    if (faceButton) {
      event.stopPropagation();
      await showFaceMatchDetail(Number(faceButton.dataset.faceMatchOpen));
      return;
    }

    const faceReviewButton = event.target.closest("[data-face-review]");
    const reassignButton = event.target.closest("[data-face-reassign]");
    const reprocessButton = event.target.closest("[data-face-reprocess]");
    if (faceReviewButton || reassignButton || reprocessButton) {
      event.stopPropagation();
      const button = faceReviewButton || reassignButton || reprocessButton;
      const panel = button.closest("[data-face-match-panel]");
      const incidentId = Number(button.dataset.incidentId);
      const buttons = panel.querySelectorAll("button, select");
      buttons.forEach((item) => { item.disabled = true; });
      try {
        let response;
        if (reprocessButton) {
          response = await fetch(`/api/face/incidents/${incidentId}/reprocess`, {
            method: "POST",
          });
        } else {
          const decision = faceReviewButton
            ? faceReviewButton.dataset.faceReview
            : "Reassigned";
          const payload = { decision };
          if (reassignButton) {
            payload.person_id = Number(panel.querySelector("[data-face-person]").value);
          }
          response = await fetch(`/api/violations/${incidentId}/face-review`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
        }
        if (!response.ok) {
          const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
          throw new Error(error.detail || `HTTP ${response.status}`);
        }
        if (reprocessButton) {
          panel.innerHTML = `<p class="face-match-status">Face match queued for processing.</p>`;
        } else {
          await loadFaceMatchPanel(incidentId, panel);
        }
      } catch (error) {
        panel.insertAdjacentHTML(
          "afterbegin",
          `<p class="face-match-error">${escapeHtml(error.message)}</p>`,
        );
        buttons.forEach((item) => { item.disabled = false; });
      }
      return;
    }

    const button = event.target.closest("[data-review]");
    if (!button) return;
    event.stopPropagation();
    const controls = button.closest(".review-actions");
    controls.querySelectorAll("button").forEach((item) => { item.disabled = true; });
    try {
      const response = await fetch(`/api/violations/${button.dataset.review}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: button.dataset.reviewStatus }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
        throw new Error(error.detail || `HTTP ${response.status}`);
      }
      await loadViolationsGallery();
    } catch (error) {
      alert(`Could not save review: ${error.message}`);
      controls.querySelectorAll("button").forEach((item) => { item.disabled = false; });
    }
  });
}

async function loadViolationsGallery(append = false) {
  const gallery = document.getElementById("violationsGallery");
  const loadMoreButton = document.getElementById("btnLoadMoreViolations");
  if (!append) {
    state.violationGalleryOffset = 0;
    loadMoreButton.hidden = true;
    gallery.innerHTML = `<div class="event-empty">Loading violations…</div>`;
  }

  const cameraId = document.getElementById("violationCameraFilter").value;
  const areaGroupId = document.getElementById("violationAreaGroupFilter").value;
  const violationType = document.getElementById("violationTypeFilter").value;
  const age = document.getElementById("violationAgeFilter").value;

  const offset = append ? state.violationGalleryOffset : 0;
  const params = new URLSearchParams({ limit: "60", offset: String(offset) });
  if (age !== "all") {
    params.set("since_epoch", String(Date.now() / 1000 - Number(age) * 24 * 60 * 60));
  }
  if (cameraId) params.set("camera_id", cameraId);
  if (areaGroupId) params.set("area_group_id", areaGroupId);
  if (violationType) params.set("violation_type", violationType);

  let rows = [];
  try {
    const res = await fetch(`${API.violations}?${params.toString()}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    rows = await res.json();
  } catch (e) {
    if (append) {
      const error = document.createElement("div");
      error.className = "error-state gallery-page-error";
      error.textContent = `Could not load more incidents: ${e.message}`;
      gallery.querySelector(".gallery-page-error")?.remove();
      gallery.appendChild(error);
      return;
    }
    gallery.innerHTML = `
      <div class="error-state">
        Could not load violations.
        <div class="error-detail">${escapeHtml(e.message)}</div>
        <button class="btn-secondary" onclick="loadViolationsGallery()">↻ Retry</button>
      </div>`;
    return;
  }

  gallery.querySelector(".gallery-page-error")?.remove();
  if (!append) {
    state.violationGallery = [];
    gallery.innerHTML = "";
  }
  state.violationGallery = state.violationGallery.concat(rows
    .filter((row) => row.snapshot_url)
    .map((row) => {
      const label = formatViolationLabel(row.violation_type);
      const detectedAt = row.detected_at ? new Date(row.detected_at).toLocaleString() : "";
      return {
        url: row.snapshot_url,
        caption: `${row.camera_name || row.camera_id} · ${row.area_group || row.zone || ""} · ${label} · ${detectedAt}`,
      };
    }));

  if (!rows.length && !append) {
    gallery.innerHTML = `<div class="event-empty">No violations found for this filter.</div>`;
    return;
  }

  state.violationGalleryOffset = offset + rows.length;
  loadMoreButton.hidden = rows.length < 60;
  rows.forEach((row) => {
    try {
      const card = document.createElement("div"); card.className = "violation-card";
      const label = formatViolationLabel(row.violation_type);
      const snapshotUrl = row.snapshot_url;
      const detectedAt = row.detected_at ? new Date(row.detected_at).toLocaleString() : "";
      const reviewStatus = row.review_status || "unreviewed";
      const faceEligible = ["no_helmet", "no_arc_flash_suit", "phone_detected"].includes(row.violation_type);
      const faceMatchStatus = faceEligible && row.face_recognition_enabled
        ? (row.face_match_available
          ? "Face match available"
          : (state.canReviewFaceMatches ? "Face match queued" : ""))
        : "";
      const faceMatchAction = state.canReviewFaceMatches && faceEligible
        ? `<button type="button" class="icon-btn" data-face-match-open="${row.id}">Open Face Match</button>`
        : "";
      card.innerHTML = `<div class="thumb-wrap">${snapshotUrl ? `<img src="${snapshotUrl}" alt="${escapeHtml(label)}" loading="lazy" />` : `<div class="thumb-placeholder">No image</div>`}<span class="thumb-badge">${label.toUpperCase()}</span></div><div class="card-meta"><div class="card-cam">${escapeHtml(row.camera_name || row.camera_id)}</div><div class="card-time">${escapeHtml(row.area_group || row.zone || "")} · ${detectedAt}</div><div class="review-status">${escapeHtml(reviewStatus.replace("_", " "))}</div>${faceMatchStatus ? `<div class="face-match-availability">${faceMatchStatus}${state.canReviewFaceMatches ? ` <button type="button" class="icon-btn" data-face-match-open="${row.id}">View face match</button>` : ""}</div>` : ""}<div class="face-match-panel" data-face-match-panel hidden></div><div class="review-actions">${faceMatchAction}<button type="button" class="icon-btn" data-review="${row.id}" data-review-status="confirmed">Confirm</button><button type="button" class="icon-btn danger" data-review="${row.id}" data-review-status="false_positive">False positive</button>${row.clean_snapshot_url ? `<a class="icon-btn" href="${row.clean_snapshot_url}" target="_blank" rel="noopener">Clean</a>` : ""}${row.video_url ? `<a class="icon-btn" href="${row.video_url}" target="_blank" rel="noopener">Clip</a>` : ""}</div></div>`;
      if (snapshotUrl) {
        card.setAttribute("role", "button");
        card.tabIndex = 0;
        card.addEventListener("click", (event) => {
          if (!event.target.closest("button, a, select")) {
            openViolationLightbox(snapshotUrl);
          }
        });
        card.addEventListener("keydown", (event) => {
          if (event.target.closest("button, a, select")) return;
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openIncident();
          }
        });
      }
      gallery.appendChild(card);
    } catch (err) {
      console.error("Failed to render a violation card:", err);
    }
  });
}

async function loadFaceMatchPanel(incidentId, panel) {
  panel.innerHTML = `<p class="face-match-status">Loading face-match details…</p>`;
  try {
    const response = await fetch(`/api/violations/${incidentId}/face-match`);
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const match = await response.json();
    const candidates = Array.isArray(match.top5_candidates)
      ? match.top5_candidates
      : (Array.isArray(match.top3_candidates) ? match.top3_candidates : []);
    const reviewed = ["Confirmed", "Reassigned"].includes(match.review_decision);
    const hasCandidate = ["MATCHED", "NEEDS_REVIEW"].includes(match.status)
      && match.matched_full_name;
    const heading = hasCandidate
      ? `${reviewed ? "Reviewer-confirmed" : "Suggested candidate"}: ${escapeHtml(match.matched_full_name)}`
      : escapeHtml(match.status || "No match details");
    const similarity = match.similarity !== null && match.similarity !== undefined
      && Number.isFinite(Number(match.similarity))
      ? ` · Similarity ${(Number(match.similarity) * 100).toFixed(1)}%`
      : "";
    const crop = match.face_crop_available
      ? `<img class="face-match-crop" src="/api/violations/${incidentId}/face-crop" alt="Incident face crop" loading="lazy" />`
      : "";
    const candidateList = candidates.length
      ? `<ul class="face-match-candidates">${candidates.map((candidate) =>
        `<li>${escapeHtml(candidate.full_name || "Enrolled candidate")} · ${(Number(candidate.similarity) * 100).toFixed(1)}%</li>`,
      ).join("")}</ul>`
      : "";
    let actions = "";
    if (!reviewed && ["MATCHED", "NEEDS_REVIEW"].includes(match.status)) {
      actions += `<button type="button" class="icon-btn" data-face-review="Confirmed" data-incident-id="${incidentId}">Confirm match</button><button type="button" class="icon-btn danger" data-face-review="Rejected" data-incident-id="${incidentId}">Reject match</button>`;
      actions += `<button type="button" class="icon-btn" data-face-review="Unknown" data-incident-id="${incidentId}">Mark unknown</button>`;
      const enrollmentsResponse = await fetch("/api/face/enrollments");
      if (!enrollmentsResponse.ok) {
        const error = await enrollmentsResponse.json().catch(() => ({ detail: `HTTP ${enrollmentsResponse.status}` }));
        throw new Error(error.detail || `HTTP ${enrollmentsResponse.status}`);
      }
      const enrollments = await enrollmentsResponse.json();
      const activeEnrollments = enrollments.filter((person) => person.active);
      if (activeEnrollments.length) {
        actions += `<div class="face-reassign"><select data-face-person aria-label="Select enrolled person">${activeEnrollments.map((person) =>
          `<option value="${person.person_id}">${escapeHtml(person.full_name)} (${escapeHtml(person.employee_code)})</option>`,
        ).join("")}</select><button type="button" class="icon-btn" data-face-reassign data-incident-id="${incidentId}">Reassign</button></div>`;
      }
    }
    if (["UNKNOWN", "FACE_NOT_VISIBLE", "Error"].includes(match.status)) {
      actions += `<button type="button" class="icon-btn" data-face-reprocess data-incident-id="${incidentId}">Reprocess</button>`;
    }
    const reviewLabel = reviewed ? `Reviewed: ${escapeHtml(match.review_decision)}` : "";
    panel.innerHTML = `<div class="face-match-heading">${heading}${similarity}</div>${reviewLabel ? `<div class="face-match-review">${reviewLabel}</div>` : ""}${crop}${candidateList}${actions ? `<div class="face-match-actions">${actions}</div>` : ""}`;
  } catch (error) {
    panel.innerHTML = `<p class="face-match-error">Could not load face-match details: ${escapeHtml(error.message)}</p>`;
  }
}

function setupFaceMatches() {
  const filterIds = [
    "faceViolationTypeFilter", "faceFromFilter", "faceToFilter",
    "faceCameraFilter", "faceStatusFilter", "faceEmployeeFilter",
    "faceDepartmentFilter", "faceScoreMin", "faceScoreMax",
    "faceSearchFilter", "faceSourceFilter",
  ];
  filterIds.forEach((id) => {
    const element = document.getElementById(id);
    element.addEventListener(
      element.type === "search" ? "input" : "change",
      () => {
        window.clearTimeout(state.faceFilterTimer);
        state.faceFilterTimer = window.setTimeout(loadFaceMatches, 250);
      },
    );
  });
  document.getElementById("btnRefreshFaceMatches").addEventListener(
    "click", loadFaceMatches,
  );
  document.getElementById("btnPrintFaceMatches").addEventListener(
    "click", () => window.print(),
  );
  document.getElementById("faceMatchesBody").addEventListener("click", (event) => {
    const row = event.target.closest("[data-face-match-id]");
    if (row) showFaceMatchDetail(Number(row.dataset.faceMatchId));
  });
  document.getElementById("faceMatchesBody").addEventListener("keydown", (event) => {
    const row = event.target.closest("[data-face-match-id]");
    if (row && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      showFaceMatchDetail(Number(row.dataset.faceMatchId));
    }
  });
  document.getElementById("btnCloseFaceMatchDrawer").addEventListener(
    "click", () => { document.getElementById("faceMatchDrawer").hidden = true; },
  );
  document.getElementById("faceMatchDrawer").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-face-drawer-action]");
    if (!button) return;
    const incidentId = Number(button.dataset.incidentId);
    const action = button.dataset.faceDrawerAction;
    button.disabled = true;
    try {
      let response;
      if (action === "rerun") {
        response = await fetch(`/api/face-matches/${incidentId}/rerun`, {
          method: "POST",
        });
      } else if (action === "history") {
        await showEmployeeHistory(Number(button.dataset.personId));
        button.disabled = false;
        return;
      } else {
        const payload = { decision: action };
        if (action === "Reassigned") {
          payload.person_id = Number(
            document.getElementById("faceDrawerReassign").value,
          );
        }
        response = await fetch(`/api/face-matches/${incidentId}/review`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      if (!response.ok) {
        const error = await response.json().catch(
          () => ({ detail: `HTTP ${response.status}` }),
        );
        throw new Error(error.detail || `HTTP ${response.status}`);
      }
      await Promise.all([showFaceMatchDetail(incidentId), loadFaceMatches()]);
    } catch (error) {
      document.getElementById("faceMatchDrawerContent").insertAdjacentHTML(
        "afterbegin",
        `<p class="face-match-error">${escapeHtml(error.message)}</p>`,
      );
    } finally {
      button.disabled = false;
    }
  });
  document.getElementById("faceAnalyticsTypeFilter").addEventListener(
    "change", loadFaceAnalytics,
  );
  document.getElementById("faceAnalyticsWindow").addEventListener(
    "change", loadFaceAnalytics,
  );
  document.getElementById("btnRefreshFaceAnalytics").addEventListener(
    "click", loadFaceAnalytics,
  );
  document.getElementById("btnRefreshFaceStatus").addEventListener(
    "click", loadFaceRecognitionStatus,
  );
  document.getElementById("btnRefreshFaceEmployees").addEventListener(
    "click", loadFaceEmployees,
  );
  setupFaceHistorian();
  setupFaceMatchSettings();
  loadFaceHistorianCameras();
  document.getElementById("faceEmployeesBody").addEventListener(
    "click", async (event) => {
      const historyButton = event.target.closest("[data-employee-history]");
      if (historyButton) {
        await showEmployeeHistory(Number(historyButton.dataset.employeeHistory));
        return;
      }
      const deactivateButton = event.target.closest("[data-employee-deactivate]");
      const deleteButton = event.target.closest("[data-employee-delete]");
      const button = deactivateButton || deleteButton;
      if (!button) return;
      const personId = Number(button.dataset.employeeDeactivate || button.dataset.employeeDelete);
      if (!window.confirm(
        deleteButton
          ? "Permanently delete this employee's face enrollment and embeddings?"
          : "Opt this employee out and delete enrolled face embeddings?",
      )) return;
      button.disabled = true;
      try {
        const response = await fetch(
          `/api/face/enrollments/${personId}${deleteButton ? "" : "/deactivate"}`,
          { method: deleteButton ? "DELETE" : "POST" },
        );
        if (!response.ok) {
          const error = await response.json().catch(
            () => ({ detail: `HTTP ${response.status}` }),
          );
          throw new Error(error.detail || `HTTP ${response.status}`);
        }
        await loadFaceEmployees();
      } catch (error) {
        window.alert(`Employee privacy action failed: ${error.message}`);
      } finally {
        button.disabled = false;
      }
    },
  );
  document.querySelectorAll("[data-face-subtab]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-face-subtab]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      document.querySelectorAll(".face-subtab-pane").forEach((panel) => {
        const active = panel.id === `face-panel-${button.dataset.faceSubtab}`;
        panel.hidden = !active;
        panel.classList.toggle("active", active);
      });
      if (button.dataset.faceSubtab === "matches") loadFaceMatches();
      if (button.dataset.faceSubtab === "analytics") loadFaceAnalytics();
      if (button.dataset.faceSubtab === "status") loadFaceRecognitionStatus();
      if (button.dataset.faceSubtab === "historian") {
        loadFaceHistorianJobs();
        loadFaceEmployees();
      }
      if (button.dataset.faceSubtab === "employees") loadFaceEmployees();
      if (button.dataset.faceSubtab === "settings") loadFaceMatchSettings();
    });
  });
}

function setupFaceEnrollment() {
  const form = document.getElementById("faceEnrollmentForm");
  const status = document.getElementById("faceEnrollmentStatus");
  const button = document.getElementById("btnSubmitFaceEnrollment");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;

    button.disabled = true;
    status.textContent = "Validating the face photo and saving the employee enrollment…";
    try {
      const response = await fetch("/api/face/enrollments/upload", {
        method: "POST",
        body: new FormData(form),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || `HTTP ${response.status}`);
      }

      const notes = [];
      if (result.accepted === 0 && result.duplicates > 0) {
        notes.push("This photo was already enrolled; the existing enrollment was kept.");
      } else {
        notes.push(`${result.accepted} photo added to the face index.`);
      }
      if (result.rejected > 0) {
        notes.push(`${result.rejected} existing photo(s) need attention.`);
      }
      status.textContent = `Employee ${result.employee_code} saved. ${notes.join(" ")}`;
      form.reset();
    } catch (error) {
      status.textContent = `Enrollment failed: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  });

  const bulkForm = document.getElementById("faceBulkEnrollmentForm");
  bulkForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = document.getElementById("faceBulkEnrollmentStatus");
    const submit = bulkForm.querySelector("button[type=submit]");
    submit.disabled = true;
    status.textContent = "Validating the consented ZIP dataset and importing photos…";
    try {
      const response = await fetch("/api/face/import-upload", {
        method: "POST",
        body: new FormData(bulkForm),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      status.textContent = `Import complete: ${result.people} employees, ${result.accepted} photos accepted, ${result.rejected} rejected.`;
      bulkForm.reset();
      await loadFaceEmployees();
    } catch (error) {
      status.textContent = `Import failed: ${error.message}`;
    } finally {
      submit.disabled = false;
    }
  });
}

function installFaceImageFallbacks(container) {
  container.querySelectorAll("img").forEach((image) => {
    image.addEventListener("error", () => {
      const placeholder = document.createElement("span");
      placeholder.className = `face-image-unavailable ${image.className}`;
      placeholder.setAttribute("role", "img");
      placeholder.setAttribute("aria-label", `${image.alt} unavailable`);
      placeholder.textContent = image.alt.includes("reference") || image.alt.includes("employee")
        ? "Reference photo unavailable"
        : image.alt.includes("crop")
          ? "Face crop unavailable"
          : "Incident image unavailable";
      image.replaceWith(placeholder);
    }, { once: true });
  });
}

async function loadFaceMatches() {
  const body = document.getElementById("faceMatchesBody");
  body.innerHTML = `<tr><td colspan="10" class="empty-state">Loading face matches…</td></tr>`;
  const params = new URLSearchParams({ limit: "100" });
  const values = {
    violation_type: document.getElementById("faceViolationTypeFilter").value,
    date_from: document.getElementById("faceFromFilter").value,
    date_to: document.getElementById("faceToFilter").value,
    camera_id: document.getElementById("faceCameraFilter").value,
    status: document.getElementById("faceStatusFilter").value,
    employee: document.getElementById("faceEmployeeFilter").value,
    department: document.getElementById("faceDepartmentFilter").value,
    score_min: document.getElementById("faceScoreMin").value,
    score_max: document.getElementById("faceScoreMax").value,
    search: document.getElementById("faceSearchFilter").value,
    source: document.getElementById("faceSourceFilter").value,
  };
  Object.entries(values).forEach(([key, value]) => {
    if (!value) return;
    params.set(
      key,
      key === "date_from" || key === "date_to"
        ? new Date(value).toISOString()
        : value,
    );
  });
  try {
    const response = await fetch(`/api/face/matches?${params}`);
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const matches = await response.json();
    body.replaceChildren();
    if (!matches.length) {
      body.innerHTML = `<tr><td colspan="10" class="empty-state">No face matches for this filter.</td></tr>`;
      return;
    }
    matches.forEach((match) => {
      const row = document.createElement("tr");
      row.dataset.faceMatchId = match.incident_id;
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      const fields = [
        match.epoch_time ? new Date(Number(match.epoch_time) * 1000).toLocaleString() : "—",
        `#${match.incident_id}`,
        `<div class="face-list-evidence">${match.snapshot_url
          ? `<img class="face-list-thumbnail" src="${escapeHtml(match.snapshot_url)}" alt="Violation image" loading="lazy" />`
          : `<span class="face-list-placeholder">No image</span>`}<div class="face-list-comparison"><figure><figcaption>INCIDENT</figcaption>${match.face_crop_url
          ? `<img class="face-list-thumbnail face-list-face" src="${escapeHtml(match.face_crop_url)}" alt="Incident face crop" loading="lazy" />`
          : `<span class="face-list-placeholder">No face</span>`}</figure><figure><figcaption>${match.person_id ? "GALLERY" : "TOP CANDIDATE"}</figcaption>${match.gallery_photo_url
          ? `<img class="face-list-thumbnail face-list-gallery" src="${escapeHtml(match.gallery_photo_url)}" alt="Enrolled employee reference photo" loading="lazy" />`
          : `<span class="face-list-placeholder">No reference</span>`}</figure></div></div>`,
        [match.camera_name || match.camera_id || "—", match.area_group_name]
          .filter(Boolean).join(" · "),
        formatViolationLabel(match.violation_type),
        `${match.full_name || "—"}${match.employee_code ? ` (${match.employee_code})` : ""}${match.similarity == null ? "" : ` · ${(Number(match.similarity) * 100).toFixed(1)}%`}`,
        faceMatchDisplayStatus(match),
        match.violation_status || "—",
        match.review_decision || "Pending review",
        match.source || "live",
      ];
      fields.forEach((value, index) => {
        const cell = document.createElement("td");
        if (index === 2) {
          cell.innerHTML = value;
        } else {
          cell.textContent = value;
        }
        if (index === 4) cell.className = "face-violation-badge";
        if (index === 5 && match.similarity != null) {
          cell.title = `Similarity ${(Number(match.similarity) * 100).toFixed(1)}%`;
        }
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    installFaceImageFallbacks(body);
  } catch (error) {
    body.innerHTML = `<tr><td colspan="10" class="face-match-error">Could not load face matches: ${escapeHtml(error.message)}</td></tr>`;
  }
}

function faceMatchDisplayStatus(match) {
  if (["MATCHED", "NEEDS_REVIEW"].includes(match.status) && match.full_name) {
    return ["Confirmed", "Reassigned"].includes(match.review_decision)
      ? `Reviewer-confirmed: ${match.full_name}`
      : `Suggested: ${match.full_name}`;
  }
  return ({
    FACE_NOT_VISIBLE: "Face Not Visible",
    NEEDS_REVIEW: "Needs Review",
    MATCHED: "Matched",
    UNKNOWN: "Unknown",
  })[match.status] || match.status || "Not available";
}

function faceBoxOverlay(box, frameWidth, frameHeight, className) {
  if (!Array.isArray(box) || box.length !== 4 || !frameWidth || !frameHeight) return "";
  const [x1, y1, x2, y2] = box.map(Number);
  if (![x1, y1, x2, y2].every(Number.isFinite) || x2 <= x1 || y2 <= y1) return "";
  return `<span class="face-box-overlay ${className}" style="left:${Math.max(0, x1 / frameWidth * 100)}%;top:${Math.max(0, y1 / frameHeight * 100)}%;width:${Math.min(100, (x2 - x1) / frameWidth * 100)}%;height:${Math.min(100, (y2 - y1) / frameHeight * 100)}%"></span>`;
}

async function showFaceMatchDetail(incidentId) {
  const drawer = document.getElementById("faceMatchDrawer");
  const content = document.getElementById("faceMatchDrawerContent");
  drawer.hidden = false;
  document.getElementById("faceMatchDrawerTitle").textContent = `FACE MATCH · INCIDENT #${incidentId}`;
  content.innerHTML = `<p class="face-match-status">Loading face-match details…</p>`;
  try {
    const response = await fetch(`/api/face-matches/${incidentId}`);
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const match = await response.json();
    const candidates = Array.isArray(match.top5_candidates)
      ? match.top5_candidates
      : (Array.isArray(match.top3_candidates) ? match.top3_candidates : []);
    const metadata = match.detection_metadata || {};
    const frameWidth = Number(metadata.frame_width);
    const frameHeight = Number(metadata.frame_height);
    const originalUrl = match.snapshot_url || match.clean_snapshot_url;
    const original = originalUrl
      ? `<a href="${escapeHtml(originalUrl)}" target="_blank" rel="noopener"><img class="face-detail-original" src="${escapeHtml(originalUrl)}" alt="Original incident image" /></a>`
      : `<div class="thumb-placeholder">Original image unavailable</div>`;
    const overlays = faceBoxOverlay(
      metadata.person_box, frameWidth, frameHeight, "person",
    ) + faceBoxOverlay(match.face_bbox, frameWidth, frameHeight, "face");
    const candidatesMarkup = candidates.length
      ? `<ol class="face-match-candidates">${candidates.map((candidate) =>
        `<li><button type="button" class="link-button" data-face-drawer-action="history" data-person-id="${Number(candidate.person_id)}">${escapeHtml(candidate.full_name || "Enrolled candidate")} (${escapeHtml(candidate.employee_code || "—")})</button> · ${(Number(candidate.similarity) * 100).toFixed(1)}%</li>`,
      ).join("")}</ol>`
      : `<p>No candidates were returned.</p>`;
    let employeePhotos = "";
    const referencePersonId = match.matched_person_id || candidates[0]?.person_id;
    if (referencePersonId) {
      const photosResponse = await fetch(
        `/api/face/employees/${referencePersonId}/photos`,
      );
      if (photosResponse.ok) {
        const photos = await photosResponse.json();
        employeePhotos = photos.slice(0, 5).map((photo, index) =>
          `<figure><img class="face-enrolled-photo" src="${escapeHtml(photo.url)}" alt="Enrolled employee reference photo ${index + 1}" /><figcaption>REFERENCE ${index + 1} · QUALITY ${(Number(photo.quality_score) * 100).toFixed(0)}%</figcaption></figure>`,
        ).join("");
      }
    }
    const incidentFace = `<figure><img class="face-detail-crop" src="/api/violations/${incidentId}/face-crop" alt="Incident face crop" /><figcaption>INCIDENT FACE</figcaption></figure>`;
    const comparison = match.face_crop_available
      ? `<div class="face-detail-photos face-detail-comparison">${incidentFace}${employeePhotos || `<div class="thumb-placeholder">No enrolled reference photo available</div>`}</div>`
      : `<div class="face-detail-photos"><div class="thumb-placeholder">No usable incident face crop</div>${employeePhotos}</div>`;
    const employee = match.matched_full_name
      ? `<div class="face-detail-employee"><strong>${escapeHtml(match.matched_full_name)}</strong> · ${escapeHtml(match.matched_employee_code || "")}<br />${escapeHtml(match.matched_department || "Department unavailable")} · ${escapeHtml(match.matched_designation || "Designation unavailable")}<br /><button type="button" class="link-button" data-face-drawer-action="history" data-person-id="${Number(match.matched_person_id)}">View employee violation history</button></div>`
      : `<p>No employee is assigned as the best match.</p>`;
    const enrollmentResponse = await fetch("/api/face/enrollments");
    const enrollments = enrollmentResponse.ok ? await enrollmentResponse.json() : [];
    const activeEmployees = enrollments.filter((person) => person.active);
    const reassign = activeEmployees.length
      ? `<label>Reassign to <select id="faceDrawerReassign">${activeEmployees.map((person) =>
        `<option value="${person.person_id}">${escapeHtml(person.full_name)} (${escapeHtml(person.employee_code)})</option>`,
      ).join("")}</select></label><button type="button" data-face-drawer-action="Reassigned" data-incident-id="${incidentId}">Reassign</button>`
      : "";
    const status = match.status || "UNKNOWN";
    const score = match.similarity == null ? "—" : `${(Number(match.similarity) * 100).toFixed(1)}%`;
    const thresholds = match.thresholds || {};
    const quality = match.face_quality || {};
    const cropSource = quality.crop_source
      ? String(quality.crop_source).replace(/_head_roi$/, " head region").replace(/_/g, " ")
      : "person region";
    const detectorDetails = quality.face_detector
      ? ` · ${escapeHtml(quality.face_detector)}${quality.face_detector_confidence == null ? "" : ` ${(Number(quality.face_detector_confidence) * 100).toFixed(0)}%`}`
      : "";
    const processSteps = [
      ["01", "PERSON ROI", metadata.person_box ? "Detector bounding box applied" : "Violation region unavailable"],
      ["02", "FACE + QUALITY", match.face_crop_available
        ? `Face crop accepted · ${escapeHtml(cropSource)}${detectorDetails}${quality.quality_score == null ? "" : ` · quality ${(Number(quality.quality_score) * 100).toFixed(0)}%`}`
        : "No usable face detected"],
      ["03", "ARCFACE EMBEDDING", match.face_crop_available ? `${match.model_version || "Model version unavailable"} embedding generated` : "Skipped"],
      ["04", "GALLERY SEARCH", candidates.length ? `${candidates.length} ranked candidate(s)` : "No gallery candidates"],
    ];
    content.innerHTML = `<p class="face-recognition-notice">Automated result only; a human reviewer must make the final identity decision. This result is not a disciplinary action.</p>
      <div class="face-detail-grid"><div><h4>ORIGINAL INCIDENT · ${escapeHtml(formatViolationLabel(match.violation_type))}</h4>
        <div class="face-original-wrap">${original}${overlays}</div>
        <p>${escapeHtml(match.camera_name || match.camera_id || "Camera unavailable")} · ${escapeHtml(match.area_group_name || "Location unavailable")} · ${match.epoch_time ? new Date(Number(match.epoch_time) * 1000).toLocaleString() : "Time unavailable"}</p>
        <p>Incident status: ${escapeHtml(match.violation_status || "—")} · Source: ${escapeHtml(match.source || "live")}</p></div>
        <div><h4>INCIDENT FACE ↔ ${match.matched_person_id ? "ENROLLED GALLERY" : "TOP GALLERY CANDIDATE"}</h4>${comparison}${employee}</div></div>
      <section class="face-process"><h4>RECOGNITION PIPELINE</h4><ol>${processSteps.map(([step, title, detail]) =>
        `<li><span>${step}</span><strong>${title}</strong><small>${escapeHtml(detail)}</small></li>`,
      ).join("")}</ol></section>
      <p class="face-detail-result"><strong>${escapeHtml(status)}</strong> · Similarity ${escapeHtml(score)} · Model ${escapeHtml(match.model_version || "unavailable")} · thresholds ${(Number(thresholds.low_threshold) * 100 || 0).toFixed(0)}% / ${(Number(thresholds.high_threshold) * 100 || 0).toFixed(0)}%</p>
      <h4>TOP CANDIDATES</h4>${candidatesMarkup}
      <div class="face-detail-actions">
        <button type="button" data-face-drawer-action="Confirmed" data-incident-id="${incidentId}">Confirm match</button>
        <button type="button" data-face-drawer-action="Rejected" data-incident-id="${incidentId}">Reject match</button>
        <button type="button" data-face-drawer-action="Unknown" data-incident-id="${incidentId}">Mark unknown</button>
        ${reassign}
        <button type="button" data-face-drawer-action="rerun" data-incident-id="${incidentId}">Re-run matching</button>
        ${match.review_decision ? `<p>Reviewer decision: ${escapeHtml(match.review_decision)} · ${match.reviewed_at ? new Date(match.reviewed_at).toLocaleString() : ""}</p>` : ""}
      </div><div id="faceEmployeeHistory"></div>`;
    installFaceImageFallbacks(content);
  } catch (error) {
    content.innerHTML = `<p class="face-match-error">Could not load face-match details: ${escapeHtml(error.message)}</p>`;
  }
}

async function showEmployeeHistory(personId) {
  const target = document.getElementById("faceEmployeeHistory");
  if (!target) return;
  target.textContent = "Loading employee violation history…";
  try {
    const response = await fetch(`/api/face/employees/${personId}/history`);
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const result = await response.json();
    const rows = result.violations || [];
    target.innerHTML = `<h4>${escapeHtml(result.employee.full_name)} · REPEAT VIOLATIONS: ${rows.length}</h4>
      <div class="status-log-scroll"><table class="ops-table"><thead><tr><th>TIME</th><th>INCIDENT</th><th>VIOLATION</th><th>CAMERA</th><th>MATCH</th><th>INCIDENT STATUS</th></tr></thead><tbody>${rows.map((row) =>
        `<tr><td>${row.epoch_time ? new Date(Number(row.epoch_time) * 1000).toLocaleString() : "—"}</td><td><button type="button" class="link-button" data-face-history-open="${row.incident_id}">#${row.incident_id}</button></td><td>${escapeHtml(formatViolationLabel(row.violation_type))}</td><td>${escapeHtml(row.camera_name || row.camera_id || "—")}</td><td>${escapeHtml(row.status)}</td><td>${escapeHtml(row.violation_status || "—")}</td></tr>`,
      ).join("")}</tbody></table></div>`;
    target.querySelectorAll("[data-face-history-open]").forEach((button) => {
      button.addEventListener("click", () => showFaceMatchDetail(Number(button.dataset.faceHistoryOpen)));
    });
  } catch (error) {
    target.innerHTML = `<p class="face-match-error">${escapeHtml(error.message)}</p>`;
  }
}

async function loadFaceHistorianCameras() {
  const select = document.getElementById("faceHistorianCameras");
  select.replaceChildren();
  state.cameras.forEach((camera) => {
    const id = camera.camera_id || camera.id;
    if (!id) return;
    const option = document.createElement("option");
    option.value = id;
    option.textContent = camera.name || id;
    select.appendChild(option);
  });
}

function setupFaceHistorian() {
  const form = document.getElementById("faceHistorianForm");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = document.getElementById("faceHistorianStatus");
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    status.textContent = "Creating a resumable historian job…";
    try {
      const from = document.getElementById("faceHistorianFrom").value;
      const to = document.getElementById("faceHistorianTo").value;
      const violation_types = [...form.querySelectorAll(
        'input[name="historian_type"]:checked',
      )].map((input) => input.value);
      const payload = {
        date_from: new Date(from).toISOString(),
        date_to: new Date(to).toISOString(),
        camera_ids: [...document.getElementById("faceHistorianCameras").selectedOptions]
          .map((option) => option.value),
        violation_types,
      };
      const response = await fetch("/api/face/historian-jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      status.textContent = `Job #${result.id} created with ${result.total_count} unprocessed images.`;
      await loadFaceHistorianJobs();
    } catch (error) {
      status.textContent = `Could not start historian job: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
  document.getElementById("faceHistorianJobsBody").addEventListener(
    "click", async (event) => {
      const button = event.target.closest("[data-historian-action]");
      if (!button) return;
      button.disabled = true;
      try {
        const response = await fetch(
          `/api/face/historian-jobs/${button.dataset.jobId}/${button.dataset.historianAction}`,
          { method: "POST" },
        );
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
        await loadFaceHistorianJobs();
      } catch (error) {
        document.getElementById("faceHistorianStatus").textContent = error.message;
      } finally {
        button.disabled = false;
      }
    },
  );
  document.getElementById("faceAdHocSearchForm").addEventListener(
    "submit", async (event) => {
      event.preventDefault();
      const output = document.getElementById("faceAdHocResults");
      output.textContent = "Searching the consented gallery…";
      try {
        const formData = new FormData();
        formData.append("image", document.getElementById("faceAdHocImage").files[0]);
        const response = await fetch("/api/face/search", {
          method: "POST", body: formData,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
        const candidates = result.top5_candidates || [];
        const faceQuality = result.face_quality || {};
        const detectorLabel = faceQuality.face_detector
          ? `${escapeHtml(faceQuality.face_detector)}${faceQuality.face_detector_confidence == null ? "" : ` · ${(Number(faceQuality.face_detector_confidence) * 100).toFixed(1)}% confidence`}`
          : "";
        output.innerHTML = `<p>${escapeHtml(result.status)}${result.similarity == null ? "" : ` · ${(Number(result.similarity) * 100).toFixed(1)}%`}</p>
          ${detectorLabel ? `<p>Face detected by ${detectorLabel}</p>` : ""}
          <ol>${candidates.map((candidate) => `<li>${escapeHtml(candidate.full_name)} (${escapeHtml(candidate.employee_code)}) · ${(Number(candidate.similarity) * 100).toFixed(1)}%</li>`).join("")}</ol>`;
      } catch (error) {
        output.textContent = `Search failed: ${error.message}`;
      }
    },
  );
  window.setInterval(() => {
    if (!document.getElementById("face-panel-historian").hidden) loadFaceHistorianJobs();
  }, 4000);
}

async function loadFaceHistorianJobs() {
  const body = document.getElementById("faceHistorianJobsBody");
  try {
    const response = await fetch("/api/face/historian-jobs");
    const jobs = await response.json();
    if (!response.ok) throw new Error(jobs.detail || `HTTP ${response.status}`);
    body.replaceChildren();
    if (!jobs.length) {
      body.innerHTML = `<tr><td colspan="9" class="empty-state">No historian jobs yet.</td></tr>`;
      return;
    }
    jobs.forEach((job) => {
      const row = document.createElement("tr");
      const percentage = job.total_count
        ? Math.round(Number(job.processed_count) * 100 / Number(job.total_count))
        : 100;
      const actions = job.status === "running"
        ? `<button type="button" data-historian-action="pause" data-job-id="${job.id}">Pause</button><button type="button" data-historian-action="cancel" data-job-id="${job.id}">Cancel</button>`
        : (job.status === "paused"
          ? `<button type="button" data-historian-action="resume" data-job-id="${job.id}">Resume</button><button type="button" data-historian-action="cancel" data-job-id="${job.id}">Cancel</button>`
          : "—");
      row.innerHTML = `<td>#${job.id}</td><td>${escapeHtml(job.status)}</td>
        <td><progress max="100" value="${percentage}"></progress> ${job.processed_count}/${job.total_count}</td>
        <td>${job.matched_count}</td><td>${job.needs_review_count}</td>
        <td>${job.unknown_count}</td><td>${job.not_visible_count}</td>
        <td>${job.failed_count}</td><td>${actions}</td>`;
      body.appendChild(row);
    });
  } catch (error) {
    body.innerHTML = `<tr><td colspan="9" class="face-match-error">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function loadFaceEmployees() {
  const body = document.getElementById("faceEmployeesBody");
  try {
    const response = await fetch("/api/face/enrollments");
    const employees = await response.json();
    if (!response.ok) throw new Error(employees.detail || `HTTP ${response.status}`);
    body.replaceChildren();
    employees.forEach((employee) => {
      const row = document.createElement("tr");
      row.innerHTML = `<td>${escapeHtml(employee.employee_code)}</td>
        <td>${escapeHtml(employee.full_name)}</td><td>${escapeHtml(employee.department || "—")}</td>
        <td>${escapeHtml(employee.designation || "—")}</td><td>${employee.embedding_count || 0}</td>
        <td>${employee.consent_recorded ? escapeHtml(employee.consent_date || "Recorded") : "Missing"}</td>
        <td>${employee.active ? "Active" : "Opted out"}</td>
        <td><button type="button" data-employee-history="${employee.person_id}">History</button>
        ${employee.active ? `<button type="button" data-employee-deactivate="${employee.person_id}">Opt out</button>` : ""}
        ${state.canManageFaceSettings ? `<button type="button" data-employee-delete="${employee.person_id}">Delete</button>` : ""}</td>`;
      body.appendChild(row);
    });
  } catch (error) {
    body.innerHTML = `<tr><td colspan="8" class="face-match-error">${escapeHtml(error.message)}</td></tr>`;
  }
}

function setupFaceMatchSettings() {
  const form = document.getElementById("faceSettingsForm");
  const submit = form.querySelector("button[type=submit]");
  if (!state.canManageFaceSettings) submit.hidden = true;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = document.getElementById("faceSettingsStatus");
    submit.disabled = true;
    try {
      const payload = {
        high_threshold: Number(document.getElementById("faceHighThreshold").value),
        low_threshold: Number(document.getElementById("faceLowThreshold").value),
        minimum_margin: Number(document.getElementById("faceMinimumMargin").value),
        quality_min_by_violation: {},
      };
      const response = await fetch("/api/face/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      status.textContent = "Thresholds saved. Subsequent results will record these values.";
    } catch (error) {
      status.textContent = `Could not save settings: ${error.message}`;
    } finally {
      submit.disabled = false;
    }
  });
}

async function loadFaceMatchSettings() {
  const status = document.getElementById("faceSettingsStatus");
  try {
    const response = await fetch("/api/face/settings");
    const settings = await response.json();
    if (!response.ok) throw new Error(settings.detail || `HTTP ${response.status}`);
    document.getElementById("faceHighThreshold").value = settings.high_threshold;
    document.getElementById("faceLowThreshold").value = settings.low_threshold;
    document.getElementById("faceMinimumMargin").value = settings.minimum_margin;
    status.textContent = `Current values loaded from ${settings.source}. Model: ${settings.model_name || "InsightFace buffalo_l"}.`;
  } catch (error) {
    status.textContent = `Could not load settings: ${error.message}`;
  }
}

async function loadFaceAnalytics() {
  const body = document.getElementById("faceAnalyticsBody");
  body.innerHTML = `<tr><td colspan="5" class="empty-state">Loading confirmed-match analytics…</td></tr>`;
  const params = new URLSearchParams({
    window_hours: document.getElementById("faceAnalyticsWindow").value,
  });
  const type = document.getElementById("faceAnalyticsTypeFilter").value;
  if (type) params.set("violation_type", type);
  try {
    const response = await fetch(`/api/face/analytics?${params}`);
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const records = await response.json();
    body.replaceChildren();
    if (!records.length) {
      body.innerHTML = `<tr><td colspan="5" class="empty-state">No reviewer-confirmed matches for this period.</td></tr>`;
      return;
    }
    records.forEach((record) => {
      const row = document.createElement("tr");
      [
        `${record.full_name} (${record.employee_code})`,
        record.department || "—",
        formatViolationLabel(record.violation_type),
        String(record.confirmed_violations),
        record.last_violation_epoch
          ? new Date(Number(record.last_violation_epoch) * 1000).toLocaleString()
          : "—",
      ].forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        if (index === 2) cell.className = "face-violation-badge";
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
  } catch (error) {
    body.innerHTML = `<tr><td colspan="5" class="face-match-error">Could not load face analytics: ${escapeHtml(error.message)}</td></tr>`;
  }
}

async function loadFaceRecognitionStatus() {
  const container = document.getElementById("faceRecognitionStatus");
  container.textContent = "Loading face-recognition status…";
  try {
    const response = await fetch("/api/face/status");
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const status = await response.json();
    const enabled = document.createElement("p");
    enabled.textContent = `Feature: ${status.feature_enabled ? "Enabled" : "Disabled"}`;
    const types = document.createElement("p");
    const labels = status.enabled_violation_types.map(formatViolationLabel);
    types.textContent = `Eligible types: ${labels.length ? labels.join(", ") : "None configured"}`;
    const reuse = document.createElement("p");
    reuse.textContent = `Same-track result reuse window: ${status.track_reuse_seconds} seconds`;
    const quality = document.createElement("p");
    const qualityEntries = Object.entries(status.quality_min_by_violation || {})
      .map(([type, value]) => `${formatViolationLabel(type)}: ${value}`);
    quality.textContent = `Additional quality minimums: ${qualityEntries.length ? qualityEntries.join(", ") : "None"}`;
    container.replaceChildren(enabled, types, reuse, quality);
  } catch (error) {
    container.textContent = `Could not load face-recognition status: ${error.message}`;
  }
}

// ---------------------------------------------------------------
// Lightbox
// ---------------------------------------------------------------
function setupLightbox() {
  document.getElementById("lightboxClose").addEventListener("click", closeLightbox);
  document.getElementById("lightboxPrev").addEventListener("click", () => moveViolationLightbox(-1));
  document.getElementById("lightboxNext").addEventListener("click", () => moveViolationLightbox(1));
  document.getElementById("lightboxBackdrop").addEventListener("click", (e) => { if (e.target.id === "lightboxBackdrop") closeLightbox(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeLightbox(); closeCameraViewer(); }
    if (document.getElementById("lightboxBackdrop").classList.contains("open")) {
      if (e.key === "ArrowLeft") { e.preventDefault(); moveViolationLightbox(-1); }
      if (e.key === "ArrowRight") { e.preventDefault(); moveViolationLightbox(1); }
    }
    if (document.getElementById("cameraViewerBackdrop").classList.contains("open")) {
      if (e.key === "ArrowLeft") { e.preventDefault(); moveCameraViewer(-1); }
      if (e.key === "ArrowRight") { e.preventDefault(); moveCameraViewer(1); }
    }
  });
}
function openLightbox(imageUrl, caption, index = 0, navigable = false) {
  state.violationGalleryIndex = index;
  const image = document.getElementById("lightboxImage");
  const error = document.getElementById("lightboxImageError");
  image.hidden = false;
  error.hidden = true;
  image.onerror = () => { image.hidden = true; error.hidden = false; };
  image.src = imageUrl;
  document.getElementById("lightboxCaption").textContent = caption || "";
  const hasNavigation = navigable && state.violationGallery.length > 1;
  document.getElementById("lightboxPrev").hidden = !hasNavigation;
  document.getElementById("lightboxNext").hidden = !hasNavigation;
  document.getElementById("lightboxBackdrop").classList.add("open");
}
function openViolationLightbox(imageUrl) {
  const index = state.violationGallery.findIndex((item) => item.url === imageUrl);
  const currentIndex = index >= 0 ? index : 0;
  const item = state.violationGallery[currentIndex];
  openLightbox(imageUrl, item ? item.caption : "", currentIndex, true);
}
function moveViolationLightbox(direction) {
  if (state.violationGallery.length < 2) return;
  const length = state.violationGallery.length;
  const index = (state.violationGalleryIndex + direction + length) % length;
  const item = state.violationGallery[index];
  openLightbox(item.url, item.caption, index);
}
function closeLightbox() {
  document.getElementById("lightboxBackdrop").classList.remove("open");
  document.getElementById("lightboxImage").src = "";
}

function setupCameraViewer() {
  const backdrop = document.getElementById("cameraViewerBackdrop");
  document.getElementById("cameraViewerClose").addEventListener("click", closeCameraViewer);
  document.getElementById("btnViewerFullscreen").addEventListener("click", toggleCameraViewerFullscreen);
  document.addEventListener("fullscreenchange", () => {
    const isFullscreen = document.fullscreenElement === backdrop;
    document.getElementById("btnViewerFullscreen").textContent =
      isFullscreen ? "EXIT FULLSCREEN" : "FULLSCREEN";
  });
  document.getElementById("cameraViewerPrev").addEventListener("click", () => moveCameraViewer(-1));
  document.getElementById("cameraViewerNext").addEventListener("click", () => moveCameraViewer(1));
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) closeCameraViewer();
  });
}
async function toggleCameraViewerFullscreen() {
  const backdrop = document.getElementById("cameraViewerBackdrop");
  try {
    if (document.fullscreenElement === backdrop) await document.exitFullscreen();
    else await backdrop.requestFullscreen();
  } catch (error) {
    console.error("Could not enter camera fullscreen mode:", error);
  }
}
function openCameraViewer(cameraId) {
  const activeCameras = state.cameras.filter((item) => item.enabled);
  const camera = activeCameras.find((item) => item.camera_id === cameraId);
  if (!camera) return;
  state.cameraViewerId = cameraId;
  const canvas = document.getElementById("cameraViewerCanvas");
  const tileCanvas = document.getElementById(`canvas-${cameraId}`);
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (tileCanvas && tileCanvas.width && tileCanvas.height) {
    if (canvas.width !== tileCanvas.width || canvas.height !== tileCanvas.height) {
      canvas.width = tileCanvas.width;
      canvas.height = tileCanvas.height;
    }
    context.drawImage(tileCanvas, 0, 0, canvas.width, canvas.height);
  }
  document.getElementById("cameraViewerCaption").textContent =
    `${camera.name} · ${camera.camera_id} · ${camera.area_group_name || "Unassigned"}`;
  const hasNavigation = activeCameras.length > 1;
  document.getElementById("cameraViewerPrev").hidden = !hasNavigation;
  document.getElementById("cameraViewerNext").hidden = !hasNavigation;
  document.getElementById("cameraViewerBackdrop").classList.add("open");
}
function moveCameraViewer(direction) {
  const activeCameras = state.cameras.filter((camera) => camera.enabled);
  if (!activeCameras.length) return;
  const currentIndex = activeCameras.findIndex((camera) => camera.camera_id === state.cameraViewerId);
  const nextIndex = (currentIndex + direction + activeCameras.length) % activeCameras.length;
  openCameraViewer(activeCameras[nextIndex].camera_id);
}
function closeCameraViewer() {
  const backdrop = document.getElementById("cameraViewerBackdrop");
  if (!backdrop) return;
  if (document.fullscreenElement === backdrop) {
    document.exitFullscreen().catch((error) => {
      console.error("Could not exit camera fullscreen mode:", error);
    });
  }
  backdrop.classList.remove("open");
  state.cameraViewerId = null;
  const canvas = document.getElementById("cameraViewerCanvas");
  if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
}

// ---------------------------------------------------------------
// Analytics dashboard
// ---------------------------------------------------------------
const ANALYTICS_COLORS = ["#e5e5e5", "#c7c7c7", "#a8a8a8", "#8a8a8a", "#6b6b6b", "#d0d0d0", "#999999"];

function setupAnalyticsDashboard() {
  const range = document.getElementById("analyticsRange");
  range.addEventListener("change", () => {
    state.analyticsWindowHours = Number(range.value);
    refreshStats();
  });
  document.getElementById("btnRefreshAnalytics").addEventListener("click", refreshStats);
  window.addEventListener("resize", () => {
    window.clearTimeout(state.analyticsResizeTimer);
    state.analyticsResizeTimer = window.setTimeout(() => {
      if (state.analyticsStats) renderAnalytics(state.analyticsStats);
    }, 120);
  });
}

async function refreshStats() {
  const message = document.getElementById("analyticsMessage");
  const refreshButton = document.getElementById("btnRefreshAnalytics");
  refreshButton.disabled = true;
  message.hidden = true;
  try {
    const res = await fetch(`${API.stats}?window_hours=${state.analyticsWindowHours}`);
    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(error.detail || `HTTP ${res.status}`);
    }
    const stats = await res.json();
    if (!stats || !Number.isFinite(Number(stats.total_violations))
      || !Array.isArray(stats.hourly) || !Array.isArray(stats.by_type)
      || !Array.isArray(stats.by_area_group) || !Array.isArray(stats.by_camera)) {
      throw new Error("Unexpected analytics response format.");
    }
    state.analyticsStats = stats;
    renderAnalytics(stats);
    document.getElementById("statViolations24h").textContent = stats.total_violations ?? "0";
    document.getElementById("statViolationsPeriod").textContent =
      `THREATS ${formatAnalyticsPeriod(state.analyticsWindowHours).toUpperCase()}`;
    document.getElementById("analyticsLastUpdated").textContent =
      `Updated ${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date())}`;
  } catch (error) {
    console.error("stats refresh failed", error);
    message.textContent = `Could not load analytics: ${error.message}`;
    message.hidden = false;
    document.getElementById("analyticsLastUpdated").textContent = "Update failed";
  } finally {
    refreshButton.disabled = false;
  }
}

function formatAnalyticsPeriod(hours) {
  if (hours === 24) return "24H";
  if (hours === 168) return "7D";
  if (hours === 720) return "30D";
  return `${hours}H`;
}

function renderAnalytics(stats) {
  const byType = [...stats.by_type].sort((a, b) => Number(b.c) - Number(a.c));
  const byArea = [...stats.by_area_group].sort((a, b) => Number(b.c) - Number(a.c));
  const byCamera = [...stats.by_camera].sort((a, b) => Number(b.c) - Number(a.c));
  const total = Number(stats.total_violations);
  const topType = byType[0];
  const topArea = byArea[0];
  const enabledCameras = state.cameras.filter((camera) => camera.enabled);
  const onlineCameras = enabledCameras.filter((camera) => camera.connected).length;
  const periodLabel = state.analyticsWindowHours === 24
    ? "Last 24 hours"
    : state.analyticsWindowHours === 168 ? "Last 7 days" : "Last 30 days";

  document.getElementById("analyticsTotal").textContent = new Intl.NumberFormat().format(total);
  document.getElementById("analyticsPeriodCaption").textContent = periodLabel;
  document.getElementById("analyticsCameras").textContent = `${onlineCameras}/${enabledCameras.length}`;
  document.getElementById("analyticsCameraCaption").textContent =
    enabledCameras.length ? `${enabledCameras.length - onlineCameras} offline` : "No enabled cameras";
  document.getElementById("analyticsTopIssue").textContent =
    topType ? formatViolationLabel(topType.violation_type) : "—";
  document.getElementById("analyticsTopIssueCaption").textContent = topType
    ? `${new Intl.NumberFormat().format(Number(topType.c))} incidents · ${total ? Math.round(Number(topType.c) / total * 100) : 0}%`
    : "No incidents recorded";
  document.getElementById("analyticsTopArea").textContent =
    topArea ? (topArea.area_group || "Unassigned") : "—";
  document.getElementById("analyticsTopAreaCaption").textContent = topArea
    ? `${new Intl.NumberFormat().format(Number(topArea.c))} incidents`
    : "No incidents recorded";
  document.getElementById("analyticsDonutTotal").textContent =
    new Intl.NumberFormat().format(total);

  drawHourlyChart(stats.hourly, state.analyticsWindowHours);
  drawTypeChart(byType, total);
  renderAnalyticsRankList("areaGroupChart", byArea, "area_group", "");
  renderAnalyticsRankList("cameraRankList", byCamera, "camera_name", "camera");
}

function drawHourlyChart(hourly, windowHours) {
  const canvas = document.getElementById("hourlyChart");
  const ctx = prepCanvas(canvas);
  const { width: w, height: h } = canvas.getBoundingClientRect();
  const chart = normalizeHourlyData(hourly, windowHours);
  if (!chart.length || !chart.some((item) => item.count > 0)) {
    ctx.fillStyle = "#a0a0a0";
    ctx.font = "12px Segoe UI, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No incidents in this period", w / 2, h / 2);
    return;
  }

  const pad = { top: 12, right: 10, bottom: 34, left: 36 };
  const plotWidth = Math.max(1, w - pad.left - pad.right);
  const plotHeight = Math.max(1, h - pad.top - pad.bottom);
  const maxValue = Math.max(...chart.map((item) => item.count), 1);
  const scaleMax = maxValue <= 4 ? maxValue : Math.ceil(maxValue / 4) * 4;
  const yTickCount = Math.min(4, scaleMax);
  const points = chart.map((item, index) => ({
    x: pad.left + (chart.length === 1 ? plotWidth / 2 : index / (chart.length - 1) * plotWidth),
    y: pad.top + plotHeight - item.count / scaleMax * plotHeight,
    count: item.count,
  }));

  ctx.font = "10px Segoe UI, sans-serif";
  ctx.textAlign = "right";
  for (let tick = 0; tick <= yTickCount; tick += 1) {
    const y = pad.top + plotHeight - tick / yTickCount * plotHeight;
    ctx.strokeStyle = "#666666";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(w - pad.right, y);
    ctx.stroke();
    ctx.fillStyle = "#a0a0a0";
    ctx.fillText(String(Math.round(scaleMax * tick / yTickCount)), pad.left - 8, y + 3);
  }

  const gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotHeight);
  gradient.addColorStop(0, "rgba(220, 220, 220, .2)");
  gradient.addColorStop(1, "rgba(220, 220, 220, .01)");
  ctx.beginPath();
  ctx.moveTo(points[0].x, pad.top + plotHeight);
  points.forEach((point) => ctx.lineTo(point.x, point.y));
  ctx.lineTo(points[points.length - 1].x, pad.top + plotHeight);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();

  ctx.beginPath();
  points.forEach((point, index) => {
    if (index === 0) ctx.moveTo(point.x, point.y);
    else ctx.lineTo(point.x, point.y);
  });
  ctx.strokeStyle = "#e5e5e5";
  ctx.lineWidth = 2.5;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.stroke();

  const tickCount = Math.min(6, chart.length);
  ctx.font = "10px Segoe UI, sans-serif";
  ctx.fillStyle = "#a0a0a0";
  ctx.textAlign = "center";
  for (let tick = 0; tick < tickCount; tick += 1) {
    const index = tickCount === 1 ? 0 : Math.round(tick / (tickCount - 1) * (chart.length - 1));
    const labelDate = chart[index].date;
    const label = windowHours <= 24
      ? labelDate.toLocaleTimeString([], { hour: "numeric" })
      : labelDate.toLocaleDateString([], { month: "short", day: "numeric" });
    ctx.fillText(label, points[index].x, h - 10);
  }
  ctx.textAlign = "left";
}

function normalizeHourlyData(hourly, windowHours) {
  const now = Date.now();
  const lastHour = Math.floor(now / 3600000) * 3600000;
  const buckets = new Map();
  for (const item of hourly) {
    const timestamp = new Date(item.hour).getTime();
    if (Number.isFinite(timestamp)) {
      const hour = Math.floor(timestamp / 3600000) * 3600000;
      buckets.set(hour, (buckets.get(hour) || 0) + Number(item.count || item.c || 0));
    }
  }
  const result = [];
  for (let index = windowHours - 1; index >= 0; index -= 1) {
    const timestamp = lastHour - index * 3600000;
    result.push({ date: new Date(timestamp), count: buckets.get(timestamp) || 0 });
  }
  return result;
}

function drawTypeChart(byType, total) {
  const canvas = document.getElementById("typeChart");
  const ctx = prepCanvas(canvas);
  const { width: w, height: h } = canvas.getBoundingClientRect();
  const radius = Math.max(12, Math.min(w, h) * .39);
  const centerX = w / 2;
  const centerY = h / 2;
  const thickness = Math.max(14, radius * .24);
  let angle = -Math.PI / 2;
  const nonZeroTypes = byType.filter((item) => Number(item.c) > 0);
  if (!total || !nonZeroTypes.length) {
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
    ctx.strokeStyle = "#666666";
    ctx.lineWidth = thickness;
    ctx.stroke();
    document.getElementById("typeLegend").innerHTML =
      '<div class="analytics-empty">No incidents to classify</div>';
    return;
  }
  nonZeroTypes.forEach((item, index) => {
    const slice = Number(item.c) / total * Math.PI * 2;
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius, angle, angle + slice);
    ctx.strokeStyle = ANALYTICS_COLORS[index % ANALYTICS_COLORS.length];
    ctx.lineWidth = thickness;
    ctx.lineCap = "butt";
    ctx.stroke();
    angle += slice;
  });
  document.getElementById("typeLegend").innerHTML = nonZeroTypes.map((item, index) => `
    <div class="analytics-legend-item">
      <span class="analytics-legend-swatch" style="background:${ANALYTICS_COLORS[index % ANALYTICS_COLORS.length]}"></span>
      <span class="analytics-legend-label">${escapeHtml(formatViolationLabel(item.violation_type))}</span>
      <span class="analytics-legend-value">${new Intl.NumberFormat().format(Number(item.c))}</span>
    </div>`).join("");
}

function renderAnalyticsRankList(containerId, rows, labelKey, colorClass) {
  const container = document.getElementById(containerId);
  if (!rows.length) {
    container.innerHTML = '<div class="analytics-empty">No incidents recorded in this period</div>';
    return;
  }
  const maxCount = Math.max(...rows.map((row) => Number(row.c)), 1);
  container.innerHTML = rows.slice(0, 6).map((row) => {
    const count = Number(row.c);
    const label = row[labelKey] || (labelKey === "area_group" ? "Unassigned" : "Unknown camera");
    const width = Math.max(count > 0 ? 2 : 0, count / maxCount * 100);
    return `<div class="analytics-rank-row">
      <span class="analytics-rank-name" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
      <span class="analytics-rank-track"><span class="analytics-rank-fill ${colorClass}" style="width:${width}%"></span></span>
      <span class="analytics-rank-value">${new Intl.NumberFormat().format(count)}</span>
    </div>`;
  }).join("");
}

function prepCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const cssW = rect.width || canvas.parentElement.clientWidth || 300;
  const cssH = rect.height || 220;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  return ctx;
}

// ---------------------------------------------------------------
// Camera status timeline and transition log
// ---------------------------------------------------------------
function setupCameraStatusReport() {
  const range = document.getElementById("cameraStatusRange");
  const group = document.getElementById("cameraStatusGroup");
  const camera = document.getElementById("cameraStatusCamera");
  [range, group, camera].forEach((select) => {
    select.addEventListener("change", scheduleCameraStatusReport);
  });
  document.getElementById("btnRefreshCameraStatus")
    .addEventListener("click", loadCameraStatusReport);
  document.getElementById("btnClearCameraStatusFilters")
    .addEventListener("click", clearCameraStatusFilters);
  document.getElementById("btnExportCameraStatus")
    .addEventListener("click", exportCameraStatusReport);
}

function scheduleCameraStatusReport() {
  window.clearTimeout(state.cameraStatusDebounceTimer);
  state.cameraStatusDebounceTimer = window.setTimeout(loadCameraStatusReport, 250);
}

function clearCameraStatusFilters() {
  document.getElementById("cameraStatusRange").value = "30d";
  document.getElementById("cameraStatusGroup").value = "";
  document.getElementById("cameraStatusCamera").value = "";
  loadCameraStatusReport();
}

async function loadCameraStatusReport() {
  const timeline = document.getElementById("cameraStatusTimeline");
  const message = document.getElementById("cameraStatusMessage");
  if (state.cameraStatusAbortController) state.cameraStatusAbortController.abort();
  const controller = new AbortController();
  state.cameraStatusAbortController = controller;
  const params = new URLSearchParams({
    window: document.getElementById("cameraStatusRange").value,
  });
  const groupId = document.getElementById("cameraStatusGroup").value;
  const cameraId = document.getElementById("cameraStatusCamera").value;
  if (groupId) params.set("area_group_id", groupId);
  if (cameraId) params.set("camera_id", cameraId);
  message.hidden = false;
  message.className = "camera-status-message loading";
  message.textContent = "Loading camera status history…";
  timeline.innerHTML = '<div class="empty-state">Loading camera status history…</div>';
  try {
    const response = await fetch(
      `/api/camera-status/history?${params.toString()}`,
      { signal: controller.signal },
    );
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const report = await response.json();
    if (controller.signal.aborted) return;
    if (!report || !Array.isArray(report.cameras) || !Array.isArray(report.logs)
      || !Number.isInteger(report.bucket_count) || report.bucket_count < 1
      || !report.cameras.every((camera) => camera && Array.isArray(camera.buckets))) {
      throw new Error("Unexpected camera status response format.");
    }
    state.cameraStatusReport = report;
    renderCameraStatusReport();
  } catch (error) {
    if (controller.signal.aborted || error.name === "AbortError") return;
    state.cameraStatusReport = null;
    message.className = "camera-status-message error";
    message.textContent = `Could not load camera status report: ${error.message}`;
    timeline.innerHTML = `<div class="error-state">Could not load camera status report.<div class="error-detail">${escapeHtml(error.message)}</div></div>`;
    document.getElementById("cameraStatusSummary").innerHTML = "";
    document.getElementById("cameraStatusLogBody").innerHTML =
      '<tr><td colspan="4" class="empty-state">Status history unavailable.</td></tr>';
  }
}

function getFilteredCameraStatusReport() {
  const report = state.cameraStatusReport;
  if (!report) return { cameras: [], logs: [] };
  return { cameras: report.cameras, logs: report.logs };
}

function renderCameraStatusReport() {
  const report = state.cameraStatusReport;
  if (!report) return;
  const filtered = getFilteredCameraStatusReport();
  const message = document.getElementById("cameraStatusMessage");
  if (!filtered.cameras.length) {
    message.hidden = false;
    message.className = "camera-status-message empty";
    message.textContent = "No data for selected filters.";
  } else {
    message.hidden = true;
  }
  const knownUptimes = filtered.cameras
    .map((camera) => camera.uptime_pct)
    .filter((value) => value !== null);
  const overallUptime = knownUptimes.length
    ? `${(knownUptimes.reduce((sum, value) => sum + value, 0) / knownUptimes.length).toFixed(1)}%`
    : "—";
  const online = filtered.cameras.filter((camera) => camera.enabled && camera.connected).length;
  const offline = filtered.cameras.filter((camera) => camera.enabled && !camera.connected).length;
  document.getElementById("cameraStatusSummary").innerHTML = `
    <div class="status-summary-card"><span>OVERALL UPTIME</span><strong>${overallUptime}</strong><small>Based on available history</small></div>
    <div class="status-summary-card"><span>CAMERAS ONLINE</span><strong>${online}</strong><small>Currently active streams</small></div>
    <div class="status-summary-card"><span>CAMERAS OFFLINE</span><strong>${offline}</strong><small>Enabled streams needing attention</small></div>`;
  renderCameraStatusTimeline(filtered.cameras, report);
  renderCameraStatusLogs(filtered.logs);
}

function renderCameraStatusTimeline(cameras, report) {
  const container = document.getElementById("cameraStatusTimeline");
  if (!cameras.length) {
    container.innerHTML = '<div class="empty-state">No data for selected filters.</div>';
    return;
  }
  const bucketCount = report.bucket_count;
  const gridStyle = `--status-buckets:${bucketCount}`;
  const labels = cameras[0].buckets.map((bucket) => {
    const date = new Date(bucket.start);
    return report.window === "1h" || report.window === "24h"
      ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : date.toLocaleDateString([], { month: "short", day: "numeric" });
  });
  const header = `<div class="status-timeline-row status-timeline-axis" style="${gridStyle}">
    <span>CAMERA SOURCE</span>${labels.map((label) => `<span>${escapeHtml(label)}</span>`).join("")}<span>UPTIME</span>
  </div>`;
  const rows = cameras.map((camera) => {
    const cells = camera.buckets.map((bucket) => {
      const title = `${new Date(bucket.start).toLocaleString()} — ${bucket.state}${bucket.uptime_pct === null ? "" : ` (${bucket.uptime_pct}% up)`}`;
      return `<span class="status-cell ${bucket.state}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}"></span>`;
    }).join("");
    const uptime = camera.uptime_pct === null ? "—" : `${camera.uptime_pct.toFixed(1)}%`;
    return `<div class="status-timeline-row" style="${gridStyle}">
      <span class="status-camera-name"><strong>${escapeHtml(camera.name)}</strong><small>${escapeHtml(camera.area_group_name)} · ${escapeHtml(camera.camera_id)}</small></span>
      ${cells}<strong class="status-uptime">${uptime}</strong>
    </div>`;
  }).join("");
  container.innerHTML = `${header}${rows}`;
}

function renderCameraStatusLogs(logs) {
  const body = document.getElementById("cameraStatusLogBody");
  document.getElementById("cameraStatusLogCount").textContent = `${logs.length} event${logs.length === 1 ? "" : "s"}`;
  if (!logs.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty-state">No status changes in this date range.</td></tr>';
    return;
  }
  body.innerHTML = logs.map((log) => `<tr>
    <td>${escapeHtml(new Date(log.observed_at).toLocaleString())}</td>
    <td>${escapeHtml(log.camera_name)} <span class="status-log-id">${escapeHtml(log.camera_id)}</span></td>
    <td>${escapeHtml(log.area_group_name)}</td>
    <td><span class="status-log-event ${log.status.toLowerCase()}">${escapeHtml(log.status)}</span></td>
  </tr>`).join("");
}

async function cleanOldViolations() {
  const status = document.getElementById("violationCleanupStatus");
  if (!confirm("Permanently delete all violations and their saved snapshots/clips older than 2 days? This cannot be undone.")) return;
  const button = document.getElementById("btnCleanOldViolations");
  button.disabled = true;
  status.textContent = "Cleaning old incidents…";
  try {
    const response = await fetch("/api/violations/cleanup", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    status.textContent = `${result.violations_deleted} old incident(s) removed; ${result.evidence_deleted} evidence file(s) deleted.`;
    if (result.evidence_errors) {
      status.textContent += ` ${result.evidence_errors} evidence file(s) could not be removed; see server logs.`;
    }
    await loadViolationsGallery();
  } catch (error) {
    status.textContent = `Cleanup failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

function exportCameraStatusReport() {
  const report = state.cameraStatusReport;
  if (!report) return;
  const filtered = getFilteredCameraStatusReport();
  const rows = [["Camera", "Camera ID", "Area Group", "Interval Start", "Interval End", "Status", "Uptime %"]];
  filtered.cameras.forEach((camera) => camera.buckets.forEach((bucket) => {
    rows.push([
      camera.name, camera.camera_id, camera.area_group_name, bucket.start,
      bucket.end, bucket.state, bucket.uptime_pct ?? "",
    ]);
  }));
  const csv = rows.map((row) => row.map((value) =>
    `"${String(value).replace(/"/g, '""')}"`,
  ).join(",")).join("\r\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  link.download = `camera-status-${report.window}-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

// ---------------------------------------------------------------
// Power Automate delivery test
// ---------------------------------------------------------------
function setupPowerAutomateTest() {
  document.getElementById("btnTestPowerAutomate")
    .addEventListener("click", sendPowerAutomateTest);
}

async function sendPowerAutomateTest() {
  const button = document.getElementById("btnTestPowerAutomate");
  const recipient = document.getElementById("powerAutomateTestRecipient");
  const status = document.getElementById("powerAutomateTestStatus");
  if (!recipient.reportValidity()) return;
  button.disabled = true;
  status.textContent = "Sending Power Automate test email…";
  status.className = "loading";
  try {
    const response = await fetch("/api/alerts/power-automate/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recipient: recipient.value.trim() }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    status.textContent = `Test request accepted for ${body.recipient}. Check the mailbox and flow run history.`;
    status.className = "success";
  } catch (error) {
    status.textContent = `Power Automate test failed: ${error.message}`;
    status.className = "error";
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------
function setupTabs() {
  document.querySelector(".ops-grid").classList.add("live-wall-mode");
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mode-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach(c => c.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
      const isLiveWall = btn.dataset.tab === "feeds";
      document.querySelector(".ops-grid").classList.toggle("live-wall-mode", isLiveWall);
      if (!isLiveWall) setWallFocus(false);
      if (btn.dataset.tab !== "playback" && state.playbackActive) {
        document.getElementById("btnStopPlayback").click();
      }
      if (btn.dataset.tab === "analytics") refreshStats();
      if (btn.dataset.tab === "rules") refreshRules();
      if (btn.dataset.tab === "violations") loadViolationsGallery();
      if (btn.dataset.tab === "cameras") renderCamerasTable();
      if (btn.dataset.tab === "camera-status") loadCameraStatusReport();
      if (btn.dataset.tab === "face-matches") loadFaceMatches();
      if (btn.dataset.tab === "playback") populatePlaybackCameraSelect();
    });
  });
}

// ---------------------------------------------------------------
// Alert Rules CRUD
// ---------------------------------------------------------------
async function refreshRules() {
  try {
    const res = await fetch(API.rules);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.rules = await res.json();
  } catch (e) { state.rules = []; }

  document.getElementById("statActiveRules").textContent = state.rules.filter(r => r.enabled).length;
  const tbody = document.getElementById("rulesTableBody"); tbody.innerHTML = "";
  state.rules.forEach((rule) => {
    const tr = document.createElement("tr");
    const channels = (rule.channels || []).map(c => `<span class="chan-pill">${escapeHtml(c)}</span>`).join("");
    tr.innerHTML = `<td>${escapeHtml(rule.name)}</td><td>${rule.camera_id || "<span style='color:var(--text-dim)'>any</span>"}</td><td>${rule.zone || "<span style='color:var(--text-dim)'>any</span>"}</td><td>${rule.violation_type || "<span style='color:var(--text-dim)'>any</span>"}</td><td>${channels}</td><td>${rule.cooldown_seconds}s</td><td><span class="badge ${rule.enabled ? "on" : "off"}">${rule.enabled ? "ENABLED" : "DISABLED"}</span></td><td><button class="icon-btn" data-edit="${rule.id}">Edit</button><button class="icon-btn danger" data-delete="${rule.id}">Delete</button></td>`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("[data-edit]").forEach(btn => btn.addEventListener("click", () => openRuleModal(Number(btn.dataset.edit))));
  tbody.querySelectorAll("[data-delete]").forEach(btn => btn.addEventListener("click", () => deleteRule(Number(btn.dataset.delete))));
}

function setupRuleModal() {
  const backdrop = document.getElementById("ruleModalBackdrop");
  document.getElementById("btnNewRule").addEventListener("click", () => openRuleModal(null));
  document.getElementById("btnCancelRule").addEventListener("click", () => backdrop.classList.remove("open"));
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) backdrop.classList.remove("open"); });
  document.getElementById("ruleForm").addEventListener("submit", async (e) => {
    e.preventDefault(); await saveRule(); backdrop.classList.remove("open"); refreshRules();
  });
}

function openRuleModal(ruleId) {
  const backdrop = document.getElementById("ruleModalBackdrop");
  const rule = ruleId ? state.rules.find(r => r.id === ruleId) : null;
  document.getElementById("ruleModalTitle").textContent = rule ? "Edit Alert Rule" : "New Alert Rule";
  document.getElementById("ruleId").value = rule ? rule.id : "";
  document.getElementById("ruleName").value = rule ? rule.name : "";
  document.getElementById("ruleCameraId").value = rule ? (rule.camera_id || "") : "";
  document.getElementById("ruleZone").value = rule ? (rule.zone || "") : "";
  document.getElementById("ruleViolationType").value = "no_helmet";
  document.getElementById("ruleCooldown").value = rule ? rule.cooldown_seconds : 300;
  document.getElementById("chanPowerAutomate").checked =
    rule ? rule.channels.includes("power_automate") : true;
  document.getElementById("ruleEnabled").checked = rule ? rule.enabled : true;
  document.getElementById("ruleEmailRecipients").value = rule ? (rule.email_recipients || []).join(", ") : "";
  backdrop.classList.add("open");
}

async function saveRule() {
  const id = document.getElementById("ruleId").value;
  const channels = [];
  if (document.getElementById("chanPowerAutomate").checked) {
    channels.push("power_automate");
  }
  const payload = {
    name: document.getElementById("ruleName").value,
    camera_id: document.getElementById("ruleCameraId").value || null,
    zone: document.getElementById("ruleZone").value || null,
    violation_type: document.getElementById("ruleViolationType").value || null,
    cooldown_seconds: parseInt(document.getElementById("ruleCooldown").value, 10),
    channels, enabled: document.getElementById("ruleEnabled").checked,
    email_recipients: document.getElementById("ruleEmailRecipients").value.split(",").map(s => s.trim()).filter(Boolean),
  };
  const url = id ? `${API.rules}/${id}` : API.rules;
  const method = id ? "PUT" : "POST";
  await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}

async function deleteRule(id) {
  if (!confirm("Delete this alert rule?")) return;
  await fetch(`${API.rules}/${id}`, { method: "DELETE" });
  refreshRules();
}
