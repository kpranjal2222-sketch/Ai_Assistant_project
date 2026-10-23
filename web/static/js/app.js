/* =========================================================================
   app.js  --  the browser-side logic for the web UI.

   Responsibilities:
     * switch between views (nav bar)
     * call the FastAPI backend (/api/*) with fetch()
     * render KPIs, tables, charts (Chart.js) and handle uploads
   No framework -- plain JavaScript, one file, easy to read for the report.
   ========================================================================= */

const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// Chart.js palette. It mirrors css/styles.css and is THEME-AWARE: themeColors()
// returns the right set for the current [data-theme], and `C` is refreshed
// whenever the theme changes (see setTheme) so charts recolour to match.
function themeColors() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  return dark
    ? { orange: "#FF5A1F", blue: "#38BDF8", good: "#10B981", crit: "#EF4444",
        grid: "#28323C", ink2: "#94A3B8", surface: "#111820" }
    : { orange: "#E9690C", blue: "#2F6F8F", good: "#3F9D5B", crit: "#DC4B3E",
        grid: "#E7DFD2", ink2: "#57534E", surface: "#FFFFFF" };
}
let C = themeColors();

let charts = {};                   // keep Chart.js instances so we can destroy/rebuild
let pendingDetection = null;       // holds the last analysed photo and review evidence
let pendingZoneFinding = null;
let pendingVideoFinding = null;
let currentView = "landing";       // the view currently on screen (for theme re-render)
let environmentStatesLoaded = false;
let environmentLocations = [];
let selectedEnvironmentLocation = null;
let currentEnvironmentData = null;
let environmentRequestId = 0;
let scenarioRequestId = 0;
let currentUser = null;
let authInitialized = false;

const nativeFetch = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const response = await nativeFetch(input, { credentials: "same-origin", ...init });
  const requestUrl = new URL(typeof input === "string" ? input : input.url, window.location.href);
  if (
    response.status === 401
    && authInitialized
    && requestUrl.pathname.startsWith("/api/")
    && !requestUrl.pathname.startsWith("/api/auth/")
  ) {
    showAuthenticationGate("Your session expired. Sign in again to continue.");
  }
  return response;
};

async function initializeAuthentication() {
  const gate = $("#authGate");
  try {
    const response = await fetch("/api/auth/status");
    if (!response.ok) throw await responseError(response);
    const status = await response.json();
    if (status.authenticated && status.user) {
      enableAuthenticatedApp(status.user);
      return;
    }
    showAuthenticationGate(
      status.configured
        ? "Sign in to access project records and review tools."
        : "Set up the first account to secure this project workspace.",
      !status.configured,
      status.setup_token_required,
    );
  } catch (error) {
    gate.hidden = false;
    $("#authGateTitle").textContent = "Could not verify access";
    $("#authGateIntro").textContent = "The authentication service could not be reached.";
    $("#authMessage").textContent = error.message;
    $("#authMessage").style.color = "var(--critical)";
  }
}

function showAuthenticationGate(message, setup = false, setupTokenRequired = false) {
  const gate = $("#authGate");
  currentUser = null;
  $("#signedInUser").classList.add("hidden");
  $("#signOutButton").classList.add("hidden");
  $("#manageUsersButton").classList.add("hidden");
  $("#userManagementDialog").classList.add("hidden");
  gate.hidden = false;
  $("#authGateTitle").textContent = setup ? "Create the first administrator" : "Secure project access";
  $("#authGateIntro").textContent = message;
  $("#authLoginForm").classList.toggle("hidden", setup);
  $("#authSetupForm").classList.toggle("hidden", !setup);
  $("#authSetupTokenLabel").classList.toggle("hidden", !setupTokenRequired);
  $("#authSetupToken").classList.toggle("hidden", !setupTokenRequired);
  $("#authSetupToken").required = setupTokenRequired;
  $("#authMessage").textContent = "";
  $("#authMessage").style.color = "var(--ink-2)";
  authInitialized = true;
}

function enableAuthenticatedApp(user) {
  currentUser = user;
  authInitialized = true;
  $("#authGate").hidden = true;
  $("#signedInUser").textContent = `${user.display_name} · ${user.role}`;
  $("#signedInUser").classList.remove("hidden");
  $("#signOutButton").classList.remove("hidden");
  $("#manageUsersButton").classList.toggle("hidden", user.role !== "admin");
  showView("landing");
}

async function submitAuthForm(form, endpoint) {
  const message = $("#authMessage");
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  message.textContent = "Please wait…";
  message.style.color = "var(--ink-2)";
  try {
    const payload = Object.fromEntries(new FormData(form));
    const headers = { "Content-Type": "application/json" };
    if (endpoint.endsWith("/setup") && payload.setup_token) {
      headers["X-Setup-Token"] = payload.setup_token;
    }
    delete payload.setup_token;
    const response = await fetch(endpoint, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw await responseError(response);
    const result = await response.json();
    form.reset();
    message.textContent = "";
    enableAuthenticatedApp(result.user);
  } catch (error) {
    message.textContent = error.message;
    message.style.color = "var(--critical)";
  } finally {
    button.disabled = false;
  }
}

$("#authLoginForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitAuthForm(event.currentTarget, "/api/auth/login");
});
$("#authSetupForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitAuthForm(event.currentTarget, "/api/auth/setup");
});
$("#signOutButton").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/auth/logout", { method: "POST" });
    if (!response.ok) throw await responseError(response);
    currentUser = null;
    showAuthenticationGate("Sign in to access project records and review tools.");
  } catch (error) {
    console.error("Could not end the session:", error);
  }
});

// --------------------------------------------------------------------- nav
function showView(name) {
  if (currentView === "sentinel" && name !== "sentinel") {
    stopSentinel();
  }
  if (currentView === "attendance" && name !== "attendance") {
    stopAttendanceCamera();
    clearAttendanceTemporary();
  }
  currentView = name;
  $$(".view").forEach((v) => v.classList.remove("active"));
  $(`#view-${name}`).classList.add("active");
  $$(".nav-link").forEach((l) => l.classList.toggle("active", l.dataset.view === name));
  window.scrollTo(0, 0);
  // (re)load data for the view we're entering
  if (name === "landing") loadLanding();
  if (name === "sentinel") loadSentinel();
  if (name === "map") loadBlueprintMap();
  if (name === "tickets") loadTickets();
  if (name === "air-quality") loadAirQuality();
  if (name === "dashboard") loadDashboard();
  if (name === "attendance") loadAttendance();
  if (name === "log") loadLog();
  if (name === "tracker") loadTasks();
  if (name === "reports") loadReportKpis();
  lucide.createIcons();
}
window.showView = showView;

// Nav links, the logo, the hero CTAs and the feature cards all carry data-view.
$$(".nav-link, .nav-logo, .feature-card, [data-view]").forEach((l) =>
  l.addEventListener("click", () => {
    if (l.classList.contains("nav-link")) closeDrawer();
    if (l.dataset.view) showView(l.dataset.view);
  })
);
$$(".feature-card").forEach((card) => {
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      showView(card.dataset.view);
    }
  });
});
const heroOpenBtn = $("#heroOpen");
if (heroOpenBtn) heroOpenBtn.addEventListener("click", () => showView("dashboard"));
const heroDetectBtn = $("#heroDetect");
if (heroDetectBtn) heroDetectBtn.addEventListener("click", () => showView("detect"));

function openDrawer() {
  const drawer = $("#drawer");
  const backdrop = $("#drawerBackdrop");
  if (!drawer || !backdrop) return;
  drawer.classList.add("open");
  backdrop.classList.add("open");
}

function closeDrawer() {
  const drawer = $("#drawer");
  const backdrop = $("#drawerBackdrop");
  if (!drawer || !backdrop) return;
  drawer.classList.remove("open");
  backdrop.classList.remove("open");
}

$("#menuToggle").addEventListener("click", openDrawer);
const menuCloseBtn = $("#menuClose");
if (menuCloseBtn) {
  menuCloseBtn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeDrawer();
  });
  menuCloseBtn.onclick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeDrawer();
  };
}
$("#drawerBackdrop").addEventListener("click", closeDrawer);
$("#refreshAirQuality").addEventListener("click", () => {
  if (selectedEnvironmentLocation) loadSelectedEnvironmentLocation();
  else loadAirQuality();
});
$("#environmentLocationForm").addEventListener("submit", (event) => {
  event.preventDefault();
  searchEnvironmentLocations(false);
});
$("#loadEnvironmentLocation").addEventListener("click", loadSelectedEnvironmentLocation);
$("#environmentLocationSelect").addEventListener("change", () => {
  const location = environmentLocations[Number($("#environmentLocationSelect").value)];
  $("#loadEnvironmentLocation").disabled = !location;
  if (location) clearEnvironmentData();
});
$("#environmentState").addEventListener("change", () => {
  environmentLocations = [];
  selectedEnvironmentLocation = null;
  $("#environmentLocationSelect").innerHTML = `<option value="">Search for a district or city</option>`;
  $("#findEnvironmentLocations").disabled = false;
  $("#loadEnvironmentLocation").disabled = true;
  clearEnvironmentData();
});
$("#scenarioActivity").addEventListener("change", refreshConstructionScenario);
$("#scenarioMitigation").addEventListener("change", refreshConstructionScenario);
$("#manageUsersButton").addEventListener("click", () => {
  $("#userManagementDialog").classList.remove("hidden");
  loadManagedUsers();
});
$("#closeUserManagement").addEventListener("click", () => {
  $("#userManagementDialog").classList.add("hidden");
});
$("#userManagementDialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.classList.add("hidden");
});
$("#createUserForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = $("#userManagementMessage");
  try {
    const payload = Object.fromEntries(new FormData(form));
    const response = await fetch("/api/auth/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw await responseError(response);
    form.reset();
    message.textContent = "Account created. Share the temporary password directly with the user.";
    message.style.color = "var(--good)";
    await loadManagedUsers();
  } catch (error) {
    message.textContent = error.message;
    message.style.color = "var(--critical)";
  }
});
$("#managedUsers").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-toggle-user]");
  if (!button) return;
  try {
    const response = await fetch(`/api/auth/users/${button.dataset.toggleUser}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: button.dataset.active !== "true" }),
    });
    if (!response.ok) throw await responseError(response);
    $("#userManagementMessage").textContent = "Account status updated.";
    $("#userManagementMessage").style.color = "var(--good)";
    await loadManagedUsers();
  } catch (error) {
    $("#userManagementMessage").textContent = error.message;
    $("#userManagementMessage").style.color = "var(--critical)";
  }
});

async function loadManagedUsers() {
  try {
    const data = await getJSON("/api/auth/users");
    $("#managedUsers").innerHTML = `
      <div class="managed-user-row managed-user-heading"><span>Account</span><span>Role</span><span>Status</span><span>Action</span></div>
      ${data.users.map((user) => `<div class="managed-user-row">
        <span><strong>${escapeHtml(user.display_name)}</strong><small>${escapeHtml(user.username)}</small></span>
        <span>${escapeHtml(user.role)}</span>
        <span>${user.is_active ? "Active" : "Disabled"}</span>
        <button class="btn-ghost" type="button" data-toggle-user="${user.id}" data-active="${user.is_active}" ${user.id === currentUser.id ? "disabled" : ""}>
          ${user.is_active ? "Disable" : "Enable"}
        </button>
      </div>`).join("")}
    `;
  } catch (error) {
    $("#managedUsers").textContent = `Could not load accounts: ${error.message}`;
  }
}
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
});

// ------------------------------------------------------------------- theme
// Light/dark is stored in localStorage and applied to <html data-theme>.
// The inline script in index.html sets it before paint (no flash); here we
// wire the toggle, keep the sun/moon icon in sync, and recolour the charts.
function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}
function syncThemeIcon() {
  const icon = $("#themeIcon");
  if (icon) icon.setAttribute("data-lucide", currentTheme() === "dark" ? "sun" : "moon");
}
function setTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("cm-theme-v2", t); } catch (e) {}
  C = themeColors();          // refresh chart palette for the new theme
  syncThemeIcon();
  showView(currentView);      // re-render the active view so charts + inline colours update
  lucide.createIcons();
}
$("#themeToggle").addEventListener("click", () =>
  setTheme(currentTheme() === "dark" ? "light" : "dark")
);

// ----------------------------------------------------------------- landing
async function loadLanding() {
  try {
    const [s, risk, zones] = await Promise.all([
      getJSON("/api/stats"),
      getJSON("/api/risk-index").catch(() => null),
      getJSON("/api/zones").catch(() => ({ zones: [] })),
    ]);

    const score = risk && Number.isFinite(Number(risk.score)) ? Math.round(Number(risk.score)) : null;
    const hasRiskData = risk && (risk.open_tickets > 0 || risk.remediated_tickets > 0);

    const topScore = $("#topbarRiskScore");
    if (topScore) topScore.textContent = !hasRiskData || score === null ? "—" : score;
    const topBadge = $("#topbarRiskBadge");
    if (topBadge) {
      const level = !hasRiskData || score === null ? "info" : score >= 85 ? "ok" : score >= 65 ? "warn" : "danger";
      topBadge.className = `hud-pill ${level} hidden md:inline-flex cursor-pointer text-[11px] font-mono`;
    }

    $("#heroStats").innerHTML =
      heroChip(!hasRiskData || score === null ? "—" : `${score}/100`, hasRiskData ? "Ticket risk indicator" : "No ticket data yet", !hasRiskData || score === null ? "" : score >= 85 ? "text-emerald-500" : "text-amber-500") +
      heroChip(pct(s.avg_compliance), "Avg PPE in saved checks", s.avg_compliance >= 80 ? "text-emerald-500" : "") +
      heroChip((zones.zones || []).length, "Configured sample zones", "text-blue-400") +
      heroChip(s.workers, "Workers in saved checks");
    lucide.createIcons();
  } catch (e) {
    $("#heroStats").textContent = `Telemetry preview unavailable: ${e.message}`;
  }
}
function heroChip(n, l, colorClass = "") {
  return `<div class="chip"><span class="n ${colorClass}">${n}</span><span class="l font-mono">${l}</span></div>`;
}

// --------------------------------------------------------------- helpers
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}
async function responseError(response) {
  try {
    const body = await response.json();
    return new Error(body.detail || `Request failed (${response.status}).`);
  } catch (_) {
    return new Error(`Request failed (${response.status}).`);
  }
}
async function dataUrlToImageFile(dataUrl, filename) {
  const response = await fetch(dataUrl);
  const blob = await response.blob();
  return new File([blob], filename, { type: blob.type || "image/jpeg" });
}
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw await responseError(r);
  return r.json();
}

function kpiTile(label, value, opts = {}) {
  const color = opts.color || "var(--ink)";
  return `<div class="card p-4">
    <div class="stat-value" style="color:${color}">${value}</div>
    <div class="stat-label">${label}</div>
  </div>`;
}

function pct(v) { return v === null || v === undefined ? "—" : `${Math.round(v)}%`; }

// ==================================================================== DASHBOARD
async function loadDashboard() {
  try {
    const [stats, insp, tasksResp, riskResp, attendanceResp, preShiftResp] = await Promise.all([
      getJSON("/api/stats"),
      getJSON("/api/inspections"),
      getJSON("/api/tasks"),
      getJSON("/api/risk-index").catch(() => null),
      getJSON("/api/attendance").catch(() => null),
      getJSON("/api/pre-shift").catch(() => null),
    ]);

    // The score summarizes active project tickets; it is not a compliance rating.
    if (riskResp) {
      const hasRiskData = riskResp.open_tickets > 0 || riskResp.remediated_tickets > 0;
      const score = hasRiskData ? Math.round(riskResp.score) : null;
      const scoreEl = $("#dashRiskScore");
      const ratingEl = $("#dashRiskRating");
      const cardEl = $("#oshaRiskCard");
      if (scoreEl) scoreEl.textContent = score === null ? "—" : score;
      if (ratingEl) {
        ratingEl.textContent = hasRiskData ? riskResp.rating : "NO TICKET DATA YET";
        ratingEl.className = !hasRiskData ? "hud-pill info" : score >= 85 ? "hud-pill ok" : score >= 65 ? "hud-pill warn" : "hud-pill danger";
      }
      if (cardEl) {
        cardEl.className = !hasRiskData ? "card p-4 mb-5 border-l-4 border-l-slate-400" :
          `card p-4 mb-5 border-l-4 ${
            score >= 85 ? "border-l-emerald-500" : score >= 65 ? "border-l-amber-500" : "border-l-red-500"
          }`;
      }
      const brk = riskResp.breakdown || {};
      const catEl = $("#dashRiskCatastrophic");
      const critEl = $("#dashRiskCritical");
      const highEl = $("#dashRiskHigh");
      const remEl = $("#dashRiskRemediated");
      if (catEl) catEl.textContent = brk.catastrophic || 0;
      if (critEl) critEl.textContent = brk.critical || 0;
      if (highEl) highEl.textContent = brk.high || 0;
      if (remEl) remEl.textContent = riskResp.remediated_tickets || 0;
    } else {
      const scoreEl = $("#dashRiskScore");
      const ratingEl = $("#dashRiskRating");
      if (scoreEl) scoreEl.textContent = "—";
      if (ratingEl) {
        ratingEl.textContent = "TICKET RISK UNAVAILABLE";
        ratingEl.className = "hud-pill info";
      }
    }
    if (attendanceResp) renderAttendanceRows(attendanceResp.records || []);
    else $("#attendanceTbody").innerHTML = `<tr><td colspan="4" class="text-center p-3 text-red-500">Check-in records unavailable.</td></tr>`;
    renderEnvironment(currentEnvironmentData);
    if (preShiftResp) renderPreShift(preShiftResp);
    else $("#preShiftSummary").textContent = "Checklist unavailable";

    $("#kpis").innerHTML =
      kpiTile("Photos checked", stats.checks) +
      kpiTile("Avg PPE in saved checks", pct(stats.avg_compliance),
        { color: stats.avg_compliance >= 80 ? C.good : C.orange }) +
      kpiTile("Violations", stats.violations, { color: stats.violations ? C.crit : C.good }) +
      kpiTile("Workers seen", stats.workers);

    // Compliance-over-checks line (oldest -> newest so the x-axis reads left to right)
    const rows = [...insp].reverse();
    drawLine("chartCompliance",
      rows.map((_, i) => `#${i + 1}`),
      rows.map((r) => r.compliance),
    );

    // Helmet vs violation donut
    drawDonut("chartDonut", stats.helmet_ok, stats.violations);

    // Recent alerts (last few checks with violations)
    const alerts = insp.filter((r) => r.violations > 0).slice(0, 5);
    $("#alerts").innerHTML = alerts.length
      ? alerts.map((r) =>
          `<div class="flex items-center gap-2">
             <span class="pill pill-bad">
               <i data-lucide="alert-triangle" class="w-3 h-3"></i>${r.violations} violation(s)</span>
             <span style="color:var(--ink-2)">${escapeHtml(r.image_name)} · ${escapeHtml(r.timestamp)}</span>
           </div>`).join("")
      : `<div style="color:var(--ink-2)">No violations recorded.</div>`;

    // Project progress summary
    const tasks = tasksResp.tasks;
    if (!tasks.length) {
      $("#dashProgress").innerHTML = `<span style="color:var(--ink-2)">No tasks yet. Add them on the Progress page.</span>`;
    } else {
      const overall = Math.round(tasks.reduce((s, t) => s + t.progress, 0) / tasks.length);
      const behind = tasks.filter((t) => t.status === "Behind Schedule").length;
      $("#dashProgress").innerHTML =
        `<div class="flex gap-6 mb-3">
           <div><div class="stat-value" style="font-size:1.4rem">${overall}%</div><div class="stat-label">Overall completion</div></div>
           <div><div class="stat-value" style="font-size:1.4rem;color:${behind ? C.crit : C.good}">${behind}</div><div class="stat-label">Behind schedule</div></div>
         </div>
         <div class="bar"><span style="width:${overall}%"></span></div>`;
    }
    lucide.createIcons();
  } catch (error) {
    const message = escapeHtml(error.message);
    $("#kpis").innerHTML = `<div class="text-sm" style="color:var(--critical)">Could not load dashboard data: ${message}</div>`;
    $("#alerts").textContent = "Dashboard data is unavailable.";
    $("#dashProgress").textContent = "Project progress is unavailable.";
  }
}

function renderAttendanceRows(records) {
  const tbody = $("#attendanceTbody");
  if (!tbody) return;
  $("#attendanceSummary").textContent = `${records.length} check-in${records.length === 1 ? "" : "s"} today`;
  tbody.innerHTML = records.length
    ? records.map((record) => `<tr>
        <td>${escapeHtml(record.employee_id)}</td>
        <td>${escapeHtml(record.checked_at)}</td>
        <td><span class="pill ${record.ppe_status === "REVIEW REQUIRED" ? "pill-bad" : "pill-good"}">${escapeHtml(record.ppe_status)}</span></td>
        <td>${escapeHtml(record.findings)}</td>
        <td>${record.ticket_id
          ? `<button type="button" class="attendance-ticket-link" data-ticket-link>${escapeHtml(record.ticket_id)}${record.confirmed_by ? ` · ${escapeHtml(record.confirmed_by)}` : ""}</button>`
          : (record.ppe_status === "REVIEW REQUIRED"
            ? `<button type="button" class="btn-ghost attendance-confirm-button" data-employee-id="${escapeHtml(record.employee_id)}" data-check-date="${escapeHtml(record.check_date)}">Verify in person</button>`
            : `<span style="color:var(--muted)">No action</span>`)}</td>
      </tr>`).join("")
    : `<tr><td colspan="5" class="text-center p-3" style="color:var(--ink-2)">No check-ins recorded today.</td></tr>`;

  tbody.querySelectorAll(".attendance-confirm-button").forEach((button) => {
    button.addEventListener("click", () => openAttendanceConfirmation(button));
  });
  tbody.querySelectorAll("[data-ticket-link]").forEach((button) => {
    button.addEventListener("click", () => showView("tickets"));
  });
}

function renderEnvironment(data) {
  if (!data) {
    $("#environmentReadings").textContent =
      "Choose a district or city in the air-quality view to load regional conditions.";
    $("#environmentAqiValue").textContent = "—";
    $("#environmentAqiCategory").textContent = "No area selected";
    $("#environmentTimestamp").textContent = "—";
    return;
  }
  const air = data.air_quality;
  const weather = data.weather;
  const values = [
    ["PM2.5", air.pm25, "µg/m³"],
    ["PM10", air.pm10, "µg/m³"],
    ["Temperature", weather.temperature_2m, "°C"],
    ["Humidity", weather.relative_humidity_2m, "%"],
  ];
  $("#environmentReadings").innerHTML = values.map(([label, value, unit]) =>
    `<div><div class="stat-value" style="font-size:1.25rem">${escapeHtml(formatNumber(value))}<span class="text-xs font-normal ml-1">${unit}</span></div><div class="stat-label">${label}</div></div>`
  ).join("");
  $("#environmentAqiValue").textContent = air.index === null ? "—" : String(air.index);
  $("#environmentAqiCategory").textContent = air.category;
  $("#environmentAqi").dataset.category = aqiCategoryKey(air.category);
  $("#environmentTimestamp").textContent =
    `${data.location.district}, ${data.location.state} · ${formatTimestamp(data.updated_at, data.timezone, data.utc_offset_seconds)}`;
}

async function loadAirQuality() {
  const requestId = environmentRequestId;
  try {
    await ensureEnvironmentStates();
    if (requestId !== environmentRequestId) return;
    if (!selectedEnvironmentLocation) {
      await searchEnvironmentLocations(true);
      return;
    }
    await loadSelectedEnvironmentLocation();
  } catch (error) {
    showEnvironmentMessage(error.message, true);
  }
}

async function ensureEnvironmentStates() {
  if (environmentStatesLoaded) return;
  const data = await getJSON("/api/environment/states");
  const select = $("#environmentState");
  let preferredState = "Delhi";
  let preferredDistrict = "New Delhi";
  try {
    preferredState = localStorage.getItem("sitesentinel-environment-state") || preferredState;
    preferredDistrict = localStorage.getItem("sitesentinel-environment-district") || preferredDistrict;
  } catch (error) {
    console.warn("Could not read the saved environmental location.", error);
  }
  select.replaceChildren(...data.states.map((state) => new Option(state, state)));
  select.value = data.states.includes(preferredState) ? preferredState : "Delhi";
  $("#environmentDistrict").value = preferredDistrict;
  environmentStatesLoaded = true;
}

async function searchEnvironmentLocations(autoLoadFirst) {
  const state = $("#environmentState").value;
  const query = $("#environmentDistrict").value.trim();
  const button = $("#findEnvironmentLocations");
  clearEnvironmentData();
  const requestId = environmentRequestId;
  button.disabled = true;
  showEnvironmentMessage("Searching locations…");
  try {
    const params = new URLSearchParams({ state, query });
    const data = await getJSON(`/api/environment/locations?${params}`);
    if (requestId !== environmentRequestId) return;
    environmentLocations = data.locations;
    const locationSelect = $("#environmentLocationSelect");
    locationSelect.replaceChildren();
    if (!environmentLocations.length) {
      locationSelect.add(new Option("No matches in this state", ""));
      $("#loadEnvironmentLocation").disabled = true;
      showEnvironmentMessage("No matching locality found in the selected state. Try a nearby city or district.", true);
      return;
    }
    environmentLocations.forEach((location, index) => {
      const label = `${location.district}, ${location.state} · ${location.name}`;
      locationSelect.add(new Option(label, String(index)));
    });
    locationSelect.value = "0";
    selectedEnvironmentLocation = environmentLocations[0];
    $("#loadEnvironmentLocation").disabled = false;
    try {
      localStorage.setItem("sitesentinel-environment-state", state);
      localStorage.setItem("sitesentinel-environment-district", query);
    } catch (error) {
      console.warn("Could not save the selected environmental location.", error);
    }
    if (autoLoadFirst) await loadSelectedEnvironmentLocation();
    else showEnvironmentMessage(`${environmentLocations.length} matching location(s). Select one and load its area data.`);
  } catch (error) {
    if (requestId === environmentRequestId) showEnvironmentMessage(error.message, true);
  } finally {
    if (requestId === environmentRequestId) button.disabled = false;
  }
}

async function loadSelectedEnvironmentLocation() {
  const index = Number($("#environmentLocationSelect").value);
  const location = environmentLocations[index];
  if (!location) {
    showEnvironmentMessage("Search for and select a valid district or city first.", true);
    return;
  }
  selectedEnvironmentLocation = location;
  const button = $("#loadEnvironmentLocation");
  const requestId = ++environmentRequestId;
  button.disabled = true;
  showEnvironmentMessage("Loading current regional air-quality and weather models…");
  try {
    const params = new URLSearchParams({
      state: location.state,
      district: location.district,
      name: location.name,
      latitude: String(location.latitude),
      longitude: String(location.longitude),
      activity: $("#scenarioActivity").value,
      mitigation: $("#scenarioMitigation").value,
    });
    const data = await getJSON(`/api/environment/current?${params}`);
    if (requestId !== environmentRequestId) return;
    currentEnvironmentData = data;
    try {
      localStorage.setItem("sitesentinel-environment-state", location.state);
      localStorage.setItem("sitesentinel-environment-district", location.district);
    } catch (error) {
      console.warn("Could not save the selected environmental location.", error);
    }
    renderAirQuality(currentEnvironmentData);
    renderEnvironment(currentEnvironmentData);
    showEnvironmentMessage("Area model data refreshed. Construction-site values remain a separate what-if scenario.");
  } catch (error) {
    if (requestId === environmentRequestId) showEnvironmentMessage(error.message, true);
  } finally {
    if (requestId === environmentRequestId) button.disabled = false;
  }
}

function renderAirQuality(data) {
  scenarioRequestId += 1;
  const air = data.air_quality;
  const weather = data.weather;
  const units = air.units || {};
  const temperatureUnit = weather.units?.temperature_2m || "°C";
  const humidityUnit = weather.units?.relative_humidity_2m || "%";
  const windUnit = weather.units?.wind_speed_10m || "km/h";
  const precipitationUnit = weather.units?.precipitation || "mm";
  $("#airQualityLocation").textContent =
    `${data.location.district}, ${data.location.state} · ${data.location.name}`;
  $("#airQualityIndex").textContent = air.index === null ? "—" : String(air.index);
  $("#airQualityCategory").textContent = air.category;
  $("#airQualityTimestamp").textContent =
    formatTimestamp(data.updated_at, data.timezone, data.utc_offset_seconds);
  $("#ambientPm25").textContent = `${formatNumber(air.pm25)} ${units.pm2_5 || "µg/m³"}`;
  $("#ambientPm10").textContent = `${formatNumber(air.pm10)} ${units.pm10 || "µg/m³"}`;
  const categoryKey = aqiCategoryKey(air.category);
  $("#airQualityGauge").setAttribute(
    "aria-label",
    `Regional US AQI ${air.index === null ? "unavailable" : air.index}, ${air.category}`
  );
  $("#airQualityMainCard").dataset.category = categoryKey;
  $("#airQualityGauge").dataset.category = categoryKey;
  $("#airQualityGauge").style.setProperty(
    "--aqi-progress",
    `${Math.min(air.index || 0, 500) / 500 * 180}deg`
  );
  $("#weatherUpdatedAt").textContent =
    formatTimestamp(weather.time, data.timezone, data.utc_offset_seconds);
  $("#weatherCondition").textContent = weatherDescription(weather.weather_code);
  $("#weatherTemperature").textContent =
    `${formatNumber(weather.temperature_2m)} ${temperatureUnit}`;
  $("#weatherApparentTemperature").textContent =
    `${formatNumber(weather.apparent_temperature)} ${temperatureUnit}`;
  $("#weatherHumidity").textContent =
    `${formatNumber(weather.relative_humidity_2m, 0)} ${humidityUnit}`;
  $("#weatherWind").textContent =
    `${formatNumber(weather.wind_speed_10m)} ${windUnit}`;
  $("#weatherPrecipitation").textContent =
    `${formatNumber(weather.precipitation)} ${precipitationUnit}`;
  $("#weatherForecast").innerHTML = (weather.forecast || []).map((day) => `
    <div><span>${escapeHtml(formatDate(day.date))}</span>
      <strong>${escapeHtml(formatNumber(day.high))}° / ${escapeHtml(formatNumber(day.low))}°</strong>
      <small>${escapeHtml(formatNumber(day.precipitation))} mm rain</small></div>
  `).join("");
  drawAirQualityChart(data.trend || []);
  renderConstructionScenario(data.construction_scenario);
  $("#scenarioWeatherContext").textContent =
    `Area weather context only: wind ${formatNumber(weather.wind_speed_10m)} ${weather.units?.wind_speed_10m || "km/h"}, precipitation ${formatNumber(weather.precipitation)} ${weather.units?.precipitation || "mm"}. Weather is not used in the site scenario calculation.`;
  lucide.createIcons();
}

async function refreshConstructionScenario() {
  if (!currentEnvironmentData) return;
  const requestId = ++scenarioRequestId;
  const data = currentEnvironmentData;
  const air = currentEnvironmentData.air_quality;
  const params = new URLSearchParams({
    pm25: String(air.pm25),
    pm10: String(air.pm10),
    activity: $("#scenarioActivity").value,
    mitigation: $("#scenarioMitigation").value,
  });
  try {
    const scenario = await getJSON(`/api/environment/scenario?${params}`);
    if (requestId !== scenarioRequestId || data !== currentEnvironmentData) return;
    data.construction_scenario = scenario;
    renderConstructionScenario(scenario);
  } catch (error) {
    if (requestId === scenarioRequestId && data === currentEnvironmentData) {
      showEnvironmentMessage(error.message, true);
    }
  }
}

function renderConstructionScenario(scenario) {
  if (!scenario) return;
  $("#scenarioIndex").textContent = String(scenario.index);
  $("#scenarioCategory").textContent = scenario.category;
  $("#scenarioParticulates").textContent =
    `What-if PM2.5: ${formatNumber(scenario.pm25)} µg/m³ · PM10: ${formatNumber(scenario.pm10)} µg/m³`;
  $("#scenarioCategory").dataset.category = aqiCategoryKey(scenario.category);
}

function clearEnvironmentData() {
  environmentRequestId += 1;
  scenarioRequestId += 1;
  currentEnvironmentData = null;
  selectedEnvironmentLocation = null;
  $("#airQualityLocation").textContent = "Select a matched district or city";
  $("#airQualityIndex").textContent = "—";
  $("#airQualityCategory").textContent = "Select an area";
  $("#airQualityTimestamp").textContent = "—";
  $("#ambientPm25").textContent = "—";
  $("#ambientPm10").textContent = "—";
  $("#airQualityMainCard").dataset.category = "unavailable";
  $("#airQualityGauge").dataset.category = "unavailable";
  $("#airQualityGauge").style.setProperty("--aqi-progress", "0deg");
  $("#weatherUpdatedAt").textContent = "—";
  [
    "#weatherTemperature", "#weatherApparentTemperature", "#weatherHumidity",
    "#weatherWind", "#weatherPrecipitation",
  ].forEach((selector) => { $(selector).textContent = "—"; });
  $("#weatherForecast").replaceChildren();
  $("#scenarioIndex").textContent = "—";
  $("#scenarioCategory").textContent = "Waiting for area data";
  $("#scenarioParticulates").textContent = "Select an area to calculate a what-if.";
  $("#scenarioWeatherContext").textContent = "Weather context appears when an area is loaded.";
  $("#weatherCondition").textContent = "—";
  renderEnvironment(null);
  destroyChart("chartAirQuality");
}

function showEnvironmentMessage(message, isError = false) {
  const element = $("#airQualityMessage");
  element.textContent = message;
  element.style.color = isError ? "var(--critical)" : "var(--ink-2)";
}

function formatNumber(value, digits = 1) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits)
    : "—";
}

function formatTimestamp(value, timezone, utcOffsetSeconds) {
  if (!value) return "—";
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  let date = new Date(value);
  if (parts && timezone && Number.isFinite(utcOffsetSeconds)) {
    date = new Date(Date.UTC(
      Number(parts[1]),
      Number(parts[2]) - 1,
      Number(parts[3]),
      Number(parts[4]),
      Number(parts[5]),
      Number(parts[6] || 0),
    ) - utcOffsetSeconds * 1000);
  }
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, timezone
    ? { timeZone: timezone, dateStyle: "medium", timeStyle: "short" }
    : undefined);
}

function formatDate(value) {
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function weatherDescription(code) {
  const descriptions = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Freezing fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
  };
  return descriptions[code] || "Conditions unavailable";
}

function aqiCategoryKey(category) {
  return category.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-").replace(/-$/, "");
}

function drawAirQualityChart(history) {
  destroyChart("chartAirQuality");
  charts.chartAirQuality = new Chart($("#chartAirQuality"), {
    type: "line",
    data: {
      labels: history.map((sample) => sample.time.slice(11, 16) || sample.time),
      datasets: [
        {
          label: "PM2.5 (µg/m³)",
          data: history.map((sample) => sample.pm25),
          borderColor: C.orange,
          backgroundColor: C.orange,
          borderWidth: 2,
          pointRadius: 2,
          tension: 0.3,
          yAxisID: "concentration",
        },
        {
          label: "PM10 (µg/m³)",
          data: history.map((sample) => sample.pm10),
          borderColor: C.blue,
          backgroundColor: C.blue,
          borderWidth: 2,
          pointRadius: 2,
          tension: 0.3,
          yAxisID: "concentration",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      plugins: {
        legend: { position: "bottom", labels: { color: C.ink2, usePointStyle: true } },
      },
      scales: {
        concentration: {
          beginAtZero: true,
          title: { display: true, text: "Model concentration (µg/m³)", color: C.ink2 },
          grid: { color: C.grid },
          ticks: { color: C.ink2 },
        },
        x: { grid: { display: false }, ticks: { color: C.ink2, maxTicksLimit: 6 } },
      },
    },
  });
}

function renderPreShift(data) {
  const form = $("#preShiftForm");
  const checklist = data.checklist;
  form.querySelectorAll('input[type="checkbox"]').forEach((input) => {
    input.checked = checklist ? Boolean(checklist[input.name]) : false;
  });
  const checkedBy = form.elements.namedItem("checked_by");
  if (checkedBy) checkedBy.value = checklist?.checked_by || "";
  const complete = data.completed_items === data.total_items && data.total_items > 0;
  $("#preShiftSummary").textContent = checklist
    ? `${data.completed_items}/${data.total_items} checks${complete ? " complete" : " done"}`
    : "Not recorded";
}

async function loadAttendance() {
  try {
    const data = await getJSON("/api/attendance");
    renderAttendanceRows(data.records || []);
  } catch (error) {
    $("#attendanceTbody").innerHTML = `<tr><td colspan="5" class="text-center p-3 text-red-500">Could not load records: ${escapeHtml(error.message)}</td></tr>`;
  }
}

function openAttendanceConfirmation(button) {
  const form = $("#attendanceConfirmForm");
  form.reset();
  $("#attendanceConfirmEmployeeId").value = button.dataset.employeeId;
  $("#attendanceConfirmDate").value = button.dataset.checkDate;
  $("#attendanceConfirmDetails").textContent =
    `Employee ${button.dataset.employeeId}, check-in ${button.dataset.checkDate}. Confirming creates an initial HIGH-severity follow-up ticket with text findings only.`;
  $("#attendanceConfirmMessage").textContent = "";
  $("#attendanceConfirmDialog").showModal();
}

$("#attendanceConfirmCancel").addEventListener("click", () => {
  $("#attendanceConfirmDialog").close();
});

$("#attendanceConfirmForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = $("#attendanceConfirmSubmit");
  const message = $("#attendanceConfirmMessage");
  const body = new FormData();
  body.append("employee_id", $("#attendanceConfirmEmployeeId").value);
  body.append("check_date", $("#attendanceConfirmDate").value);
  body.append("confirmed_in_person", String($("#attendancePhysicallyVerified").checked));
  body.append("confirmed_by", $("#attendanceSupervisorName").value.trim());
  submit.disabled = true;
  message.textContent = "Saving supervisor-confirmed follow-up…";
  message.style.color = "var(--ink-2)";
  try {
    const response = await fetch("/api/attendance/confirm-ticket", { method: "POST", body });
    if (!response.ok) throw await responseError(response);
    const result = await response.json();
    $("#attendanceConfirmDialog").close();
    const status = $("#attendanceActionStatus");
    status.textContent = result.created
      ? `Created incident ticket ${result.ticket_id}. The original check-in photo was not retained.`
      : `This check-in is already linked to incident ticket ${result.ticket_id}.`;
    status.style.color = "var(--good)";
    await loadAttendance();
  } catch (error) {
    message.textContent = `Could not create incident ticket: ${error.message}`;
    message.style.color = "var(--critical)";
  } finally {
    submit.disabled = false;
  }
});

// The browser holds the selected still only until it is submitted or cleared.
let attendancePhoto = null;
let attendanceStream = null;
let attendanceCaptureVersion = 0;

function stopAttendanceCamera() {
  if (attendanceStream) {
    attendanceStream.getTracks().forEach((track) => track.stop());
    attendanceStream = null;
  }
  const video = $("#attendanceVideo");
  if (video) video.srcObject = null;
  const cameraWrap = $("#attendanceCameraWrap");
  if (cameraWrap) cameraWrap.classList.add("hidden");
  const capture = $("#attendanceCapture");
  if (capture) capture.disabled = true;
}

function clearAttendanceTemporary() {
  attendanceCaptureVersion += 1;
  attendancePhoto = null;
  const upload = $("#attendanceUpload");
  if (upload) upload.value = "";
  const fileName = $("#attendanceFileName");
  if (fileName) fileName.textContent = "No photo selected.";
  const annotated = $("#attendanceAnnotated");
  if (annotated) {
    annotated.removeAttribute("src");
    annotated.classList.add("hidden");
  }
  const canvas = $("#attendanceCanvas");
  if (canvas) {
    canvas.width = 0;
    canvas.height = 0;
  }
  const result = $("#attendanceResult");
  if (result) {
    result.textContent = "Temporary screening image cleared. Text attendance record remains saved.";
    result.style.color = "var(--ink-2)";
  }
  stopAttendanceCamera();
}

$("#attendanceStartCamera").addEventListener("click", async () => {
  const message = $("#attendanceMessage");
  try {
    stopAttendanceCamera();
    attendanceStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
    $("#attendanceVideo").srcObject = attendanceStream;
    $("#attendanceCameraWrap").classList.remove("hidden");
    $("#attendanceCapture").disabled = false;
    message.textContent = "Camera ready. Position the worker and capture one photo.";
  } catch (error) {
    message.textContent = `Camera unavailable: ${error.message}. You can upload a photo instead.`;
    message.style.color = "var(--critical)";
  }
});

$("#attendanceCapture").addEventListener("click", () => {
  const video = $("#attendanceVideo");
  if (!video.videoWidth || !video.videoHeight) {
    $("#attendanceMessage").textContent = "Wait for the camera image, then capture.";
    return;
  }
  const canvas = $("#attendanceCanvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  const captureVersion = ++attendanceCaptureVersion;
  canvas.toBlob((blob) => {
    canvas.width = 0;
    canvas.height = 0;
    if (captureVersion !== attendanceCaptureVersion) return;
    if (!blob) {
      $("#attendanceMessage").textContent = "The camera image could not be captured.";
      return;
    }
    attendancePhoto = new File([blob], "worker-checkin.jpg", { type: "image/jpeg" });
    $("#attendanceFileName").textContent = "Camera photo ready for temporary screening.";
    $("#attendanceUpload").value = "";
    $("#attendanceMessage").textContent = "Captured in browser memory; not saved.";
    stopAttendanceCamera();
  }, "image/jpeg", 0.88);
});

$("#attendanceUpload").addEventListener("change", (event) => {
  const file = event.target.files[0] || null;
  attendancePhoto = file;
  $("#attendanceFileName").textContent = file
    ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB, held temporarily`
    : "No photo selected.";
});

$("#attendanceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const employeeId = $("#attendanceEmployeeId").value.trim();
  const message = $("#attendanceMessage");
  if (!attendancePhoto) {
    message.textContent = "Capture or choose a photo first.";
    message.style.color = "var(--critical)";
    return;
  }
  const form = new FormData();
  form.append("employee_id", employeeId);
  form.append("file", attendancePhoto);
  const submit = $("#attendanceSubmit");
  submit.disabled = true;
  message.textContent = "Screening photo and saving text-only attendance...";
  message.style.color = "var(--ink-2)";
  try {
    const response = await fetch("/api/attendance/check-in", { method: "POST", body: form });
    if (!response.ok) throw await responseError(response);
    const data = await response.json();
    attendancePhoto = null;
    $("#attendanceUpload").value = "";
    $("#attendanceFileName").textContent = "Photo cleared from browser input after screening.";
    $("#attendanceAnnotated").src = data.annotated_image;
    $("#attendanceAnnotated").classList.remove("hidden");
    const record = data.record;
    $("#attendanceResult").innerHTML = `<div class="space-y-2">
      <p class="font-semibold">${escapeHtml(record.employee_id)} · ${escapeHtml(record.ppe_status)}</p>
      <p>${escapeHtml(record.findings)}</p>
      <p class="text-xs">Recorded ${escapeHtml(record.checked_at)}. Human supervisor review required; this is not identity verification.</p>
      <p class="text-xs text-emerald-600">Original photo was not retained by the server. Clear the temporary annotated preview when finished.</p>
    </div>`;
    $("#attendanceResult").style.color = "var(--ink)";
    message.textContent = "Check-in recorded. Review the flags in person.";
    message.style.color = "var(--good)";
    await loadAttendance();
  } catch (error) {
    attendancePhoto = null;
    $("#attendanceUpload").value = "";
    $("#attendanceFileName").textContent = "Photo cleared after failed screening.";
    message.textContent = `Check-in failed: ${error.message}`;
    message.style.color = "var(--critical)";
  } finally {
    submit.disabled = false;
  }
});

$("#attendanceClear").addEventListener("click", clearAttendanceTemporary);

$("#preShiftForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = $("#preShiftMessage");
  const body = new FormData();
  const now = new Date();
  const localDate = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  body.append("check_date", localDate);
  ["headcount_ready", "toolbox_talk", "ppe_inspected", "equipment_checked", "access_clear"].forEach((name) => {
    body.append(name, String(form.elements.namedItem(name).checked));
  });
  body.append("checked_by", form.elements.namedItem("checked_by").value.trim());
  try {
    const response = await fetch("/api/pre-shift", { method: "POST", body });
    if (!response.ok) throw await responseError(response);
    message.textContent = "Today's checklist saved.";
    message.style.color = "var(--good)";
    await loadDashboard();
  } catch (error) {
    message.textContent = `Checklist could not be saved: ${error.message}`;
    message.style.color = "var(--critical)";
  }
});

// ==================================================================== CHART HELPERS
function destroyChart(id) { if (charts[id]) { charts[id].destroy(); delete charts[id]; } }

function drawLine(id, labels, data) {
  destroyChart(id);
  charts[id] = new Chart($(`#${id}`), {
    type: "line",
    data: { labels, datasets: [{
      label: "Compliance %", data, borderColor: C.blue, backgroundColor: C.blue,
      borderWidth: 2, pointRadius: 3, tension: .25, fill: false }] },
    options: { responsive: true, plugins: { legend: { display: false } },
      scales: { y: { min: 0, max: 100, grid: { color: C.grid }, ticks: { color: C.ink2 } },
                x: { grid: { display: false }, ticks: { color: C.ink2 } } } },
  });
}

function drawDonut(id, ok, bad) {
  destroyChart(id);
  charts[id] = new Chart($(`#${id}`), {
    type: "doughnut",
    data: { labels: ["Helmet OK", "Violations"],
      datasets: [{ data: [ok, bad], backgroundColor: [C.good, C.crit], borderWidth: 2, borderColor: C.surface }] },
    options: { responsive: true, cutout: "62%",
      plugins: { legend: { position: "bottom", labels: { color: C.ink2, boxWidth: 12 } } } },
  });
}

// ==================================================================== SAFETY DETECTION
function wireDropzone(zoneSel, inputSel, onFile) {
  const zone = $(zoneSel), input = $(inputSel);
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => input.files[0] && onFile(input.files[0]));
  ["dragover", "dragenter"].forEach((e) =>
    zone.addEventListener(e, (ev) => { ev.preventDefault(); zone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((e) =>
    zone.addEventListener(e, () => zone.classList.remove("drag")));
  zone.addEventListener("drop", (ev) => {
    ev.preventDefault();
    if (ev.dataTransfer.files[0]) onFile(ev.dataTransfer.files[0]);
  });
}

const DETECT_EMPTY = "No photo analysed yet.";
let detectFile = null;
let currentPreviewUrl = null;

// Show a thumbnail of the chosen photo INSIDE the dropzone, so it's clear
// which image is selected. Hides the "drop a photo" prompt while shown.
function showDetectPreview(file) {
  if (currentPreviewUrl) URL.revokeObjectURL(currentPreviewUrl);
  currentPreviewUrl = URL.createObjectURL(file);
  const img = $("#detectPreview");
  img.src = currentPreviewUrl;
  img.classList.remove("hidden");
  $("#dropDetectPrompt").classList.add("hidden");
  $("#detectFileName").innerHTML = `${escapeHtml(file.name)} · <span style="color:var(--orange)">click to change</span>`;
  $("#detectFileName").classList.remove("hidden");
}

// Put the dropzone back to its empty "drop a photo" state.
function clearDetectPreview() {
  if (currentPreviewUrl) { URL.revokeObjectURL(currentPreviewUrl); currentPreviewUrl = null; }
  const img = $("#detectPreview");
  img.src = "";
  img.classList.add("hidden");
  $("#detectFileName").classList.add("hidden");
  $("#dropDetectPrompt").classList.remove("hidden");
}

// Reset the whole detection panel (selection + result), ready for the next photo.
function resetDetection(msg) {
  detectFile = null;
  pendingDetection = null;
  $("#fileDetect").value = "";          // let the same file be re-picked
  clearDetectPreview();
  $("#btnDetect").disabled = true;
  $("#btnSaveDetect").disabled = true;
  $("#detectResult").innerHTML = DETECT_EMPTY;
  $("#detectMsg").innerHTML = msg || "";
}

wireDropzone("#dropDetect", "#fileDetect", (f) => {
  if (!["image/jpeg", "image/png", "image/webp"].includes(f.type) ||
      f.size > 10 * 1024 * 1024) {
    detectFile = null;
    pendingDetection = null;
    $("#btnDetect").disabled = true;
    $("#btnSaveDetect").disabled = true;
    $("#detectMsg").innerHTML = `<span style="color:var(--critical)">Choose a JPG, PNG, or WebP image up to 10 MB.</span>`;
    return;
  }
  detectFile = f;
  pendingDetection = null;
  showDetectPreview(f);
  $("#btnDetect").disabled = false;
  $("#btnSaveDetect").disabled = true;      // must analyze again before saving
  $("#detectResult").innerHTML = DETECT_EMPTY;
  $("#detectMsg").innerHTML = "";
});

$("#conf").addEventListener("input", (e) => ($("#confVal").textContent = e.target.value));

$("#btnDetect").addEventListener("click", async () => {
  if (!detectFile) return;
  $("#btnDetect").disabled = true;
  $("#detectMsg").innerHTML = `<span class="spinner spinner-dark"></span> Analyzing…`;
  try {
    const fd = new FormData();
    fd.append("file", detectFile);
    fd.append("conf", $("#conf").value);
    fd.append("save", "false");
    const r = await fetch("/api/detect", { method: "POST", body: fd });
    if (!r.ok) throw await responseError(r);
    const d = await r.json();
    pendingDetection = { file: detectFile, conf: $("#conf").value, annotatedImage: d.annotated_image };
    renderDetection(d);
    $("#btnSaveDetect").disabled = false;
    $("#detectMsg").innerHTML = "";
  } catch (e) {
    $("#detectMsg").innerHTML = `<span style="color:var(--critical)">Error: ${escapeHtml(e.message)}</span>`;
  } finally {
    $("#btnDetect").disabled = false;
  }
});

function renderDetection(d) {
  const s = d.summary;
  const bad = s.violations > 0;
  const inconclusive = s.total === 0;
  const rows = s.boxes.map((b, i) =>
    `<tr><td>#${i + 1}</td>
       <td>${b.violation
         ? `<span class="pill pill-bad">NO Helmet</span>`
         : `<span class="pill pill-good">Helmet OK</span>`}</td>
       <td style="width:40%"><div class="bar"><span style="width:${Math.round(b.confidence*100)}%"></span></div></td>
       <td>${Math.round(b.confidence*100)}%</td></tr>`).join("");

  $("#detectResult").innerHTML = `
    <p class="site-analysis-source">Source: ${escapeHtml(detectFile.name)} · Model classes: Hardhat / NO-Hardhat · Score threshold: ${Math.round(Number($("#conf").value) * 100)}%</p>
    <img src="${d.annotated_image}" class="rounded-lg border mb-3" style="border-color:var(--border);max-width:100%" />
    <div class="grid grid-cols-3 gap-2 mb-3 text-center">
      <div class="card p-2"><div class="stat-value" style="font-size:1.3rem">${s.total}</div><div class="stat-label">Workers</div></div>
      <div class="card p-2"><div class="stat-value" style="font-size:1.3rem;color:${C.good}">${s.helmet_ok}</div><div class="stat-label">Helmet OK</div></div>
      <div class="card p-2"><div class="stat-value" style="font-size:1.3rem;color:${C.crit}">${s.violations}</div><div class="stat-label">Violations</div></div>
    </div>
    <div class="banner ${bad ? "banner-bad" : (inconclusive ? "" : "banner-good")} mb-3">
      <i data-lucide="${bad ? "alert-triangle" : (inconclusive ? "help-circle" : "check-circle")}"></i>
      ${bad ? `${s.violations} helmet violation(s) — compliance ${pct(s.compliance)}`
        : (inconclusive ? "No workers detected — compliance cannot be assessed."
          : `No helmet violations detected — ${pct(s.compliance)} compliance`)}
    </div>
    ${s.boxes.length ? `<table class="data"><thead><tr><th>Worker</th><th>Result</th><th>Confidence</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : ""}
    ${bad ? `<button type="button" class="btn-ghost mt-3" id="draftPhotoTicket"><i data-lucide="clipboard-plus" class="w-4 h-4"></i>Review and draft incident</button>
      <p class="text-xs mt-2" style="color:var(--muted)">Draft only. Verify the result and location before saving; evidence is not attached unless you opt in.</p>` : ""}
  `;
  lucide.createIcons();
  $("#draftPhotoTicket")?.addEventListener("click", async () => {
    if (!pendingDetection) return;
    let evidenceFile = null;
    try {
      evidenceFile = await dataUrlToImageFile(
        pendingDetection.annotatedImage,
        `${pendingDetection.file.name.replace(/\.[^.]+$/, "")}_review.jpg`,
      );
    } catch (error) {
      console.error("Could not prepare photo-review evidence:", error);
    }
    draftReviewedTicket({
      hazardType: "MISSING_PPE",
      description: `Photo review flagged ${s.violations} possible missing-hardhat finding(s) among ${s.total} detected worker(s). Confirm the observation and record the actual location before action.`,
      evidenceFile,
    });
  });
}

$("#btnSaveDetect").addEventListener("click", async () => {
  if (!pendingDetection) return;
  $("#btnSaveDetect").disabled = true;
  $("#detectMsg").innerHTML = `<span class="spinner spinner-dark"></span> Saving…`;
  try {
    const fd = new FormData();
    fd.append("file", pendingDetection.file);
    fd.append("conf", pendingDetection.conf);
    fd.append("save", "true");
    const r = await fetch("/api/detect", { method: "POST", body: fd });
    if (!r.ok) throw await responseError(r);
    // Saved -> clear the selected photo AND the result so the panel is ready
    // for the next check. The annotated image now lives in the Incident Log.
    resetDetection(`<span style="color:${C.good}">Saved to incident log — ready for the next photo.</span>`);
  } catch (e) {
    $("#btnSaveDetect").disabled = false;
    $("#detectMsg").innerHTML = `<span style="color:var(--critical)">Save failed: ${escapeHtml(e.message)}</span>`;
  }
});

// ------------------------------------------------------ restricted-zone mode
let zoneFile = null;
let zoneObjectUrl = null;
let zonePoints = [];
let zoneFinished = false;
let zoneDrawing = false;
let zoneResizeObserver = null;

$("#detectMode").addEventListener("change", (event) => {
  const showZone = event.target.value === "zone";
  $("#helmetModePanel").classList.toggle("hidden", showZone);
  $("#zoneModePanel").classList.toggle("hidden", !showZone);
});

function updateZoneControls() {
  $("#finishZone").disabled = !zoneFile || zoneFinished || zonePoints.length < 3;
  $("#clearZone").disabled = !zoneFile;
  $("#analyzeZone").disabled = !zoneFile || !zoneFinished;
}

function drawZonePolygon() {
  const image = $("#zoneImage");
  const canvas = $("#zoneCanvas");
  if (!image.naturalWidth || !image.clientWidth || !image.clientHeight) return;
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  const width = image.clientWidth;
  const height = image.clientHeight;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const scaleX = width / image.naturalWidth;
  const scaleY = height / image.naturalHeight;
  const points = zonePoints.map(([x, y]) => [x * scaleX, y * scaleY]);

  if (points.length) {
    ctx.beginPath();
    ctx.moveTo(...points[0]);
    points.slice(1).forEach((point) => ctx.lineTo(...point));
    if (zoneFinished) {
      ctx.closePath();
      ctx.fillStyle = "rgba(245, 135, 31, 0.2)";
      ctx.fill();
    }
    ctx.strokeStyle = "#f5871f";
    ctx.lineWidth = 3;
    ctx.stroke();
    points.forEach(([x, y], index) => {
      ctx.beginPath();
      ctx.arc(x, y, 5, 0, Math.PI * 2);
      ctx.fillStyle = zoneFinished ? "#f5871f" : "#fff";
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#f5871f";
      ctx.stroke();
      ctx.fillStyle = "#1c1917";
      ctx.font = "bold 12px Inter, sans-serif";
      ctx.fillText(String(index + 1), x + 7, y - 7);
    });
  }
}

function resetZoneDrawing() {
  zonePoints = [];
  zoneFinished = false;
  zoneDrawing = Boolean(zoneFile);
  $("#zoneResult").textContent = zoneFile
    ? "Draw a restricted zone using at least three points."
    : "Choose an image, draw a zone, and analyze it.";
  $("#zoneMsg").textContent = zoneDrawing ? "Click on the image to place zone points." : "";
  drawZonePolygon();
  updateZoneControls();
}

function chooseZoneFile(file) {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) ||
      file.size > 10 * 1024 * 1024) {
    if (zoneObjectUrl) URL.revokeObjectURL(zoneObjectUrl);
    zoneFile = null;
    pendingZoneFinding = null;
    zoneObjectUrl = null;
    zonePoints = [];
    zoneFinished = false;
    zoneDrawing = false;
    $("#zoneImage").src = "";
    $("#zoneCanvasWrap").classList.add("hidden");
    $("#zoneFileName").textContent = "";
    $("#zoneResult").textContent = "Choose an image, draw a zone, and analyze it.";
    updateZoneControls();
    $("#zoneMsg").innerHTML =
      `<span style="color:var(--critical)">Choose a JPG, PNG, or WebP image up to 10 MB.</span>`;
    return;
  }
  if (zoneObjectUrl) URL.revokeObjectURL(zoneObjectUrl);
  zoneFile = file;
  pendingZoneFinding = null;
  zoneObjectUrl = URL.createObjectURL(file);
  zonePoints = [];
  zoneFinished = false;
  zoneDrawing = true;
  $("#zoneResult").textContent = "Draw a restricted zone using at least three points.";
  $("#zoneMsg").textContent = "Click on the image to place zone points.";
  $("#zoneFileName").textContent = `${file.name} · ${file.size ? "ready" : ""}`;
  $("#zoneImage").onload = () => {
    $("#zoneCanvasWrap").classList.remove("hidden");
    drawZonePolygon();
  };
  $("#zoneImage").onerror = () => {
    zoneFile = null;
    pendingZoneFinding = null;
    zoneDrawing = false;
    $("#zoneCanvasWrap").classList.add("hidden");
    $("#zoneResult").textContent = "Choose an image, draw a zone, and analyze it.";
    $("#zoneMsg").textContent = "The selected file could not be previewed as an image.";
    updateZoneControls();
  };
  $("#zoneImage").src = zoneObjectUrl;
  updateZoneControls();
}

$("#chooseZoneImage").addEventListener("click", () => $("#fileZone").click());
$("#fileZone").addEventListener("change", (event) => {
  if (event.target.files[0]) {
    chooseZoneFile(event.target.files[0]);
    event.target.value = "";
  }
});
$("#zoneConf").addEventListener("input", (event) => {
  $("#zoneConfVal").textContent = event.target.value;
});

$("#zoneCanvas").addEventListener("click", (event) => {
  if (!zoneFile || !zoneDrawing || zoneFinished) return;
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const image = $("#zoneImage");
  const x = (event.clientX - rect.left) * image.naturalWidth / rect.width;
  const y = (event.clientY - rect.top) * image.naturalHeight / rect.height;
  if (zonePoints.length >= 3) {
    const [firstX, firstY] = zonePoints[0];
    const displayedDistance = Math.hypot(
      (x - firstX) * rect.width / image.naturalWidth,
      (y - firstY) * rect.height / image.naturalHeight
    );
    if (displayedDistance <= 14) {
      zoneFinished = true;
      zoneDrawing = false;
      $("#zoneMsg").textContent = "Polygon closed. Ready to analyze.";
      drawZonePolygon();
      updateZoneControls();
      return;
    }
  }
  zonePoints.push([x, y]);
  $("#zoneMsg").textContent = zonePoints.length < 3
    ? `Point ${zonePoints.length} added. Add at least ${3 - zonePoints.length} more.`
    : "Add more points, click the first point, or choose Finish polygon.";
  drawZonePolygon();
  updateZoneControls();
});

$("#finishZone").addEventListener("click", () => {
  if (zonePoints.length < 3) return;
  zoneFinished = true;
  zoneDrawing = false;
  $("#zoneMsg").textContent = "Polygon closed. Ready to analyze.";
  drawZonePolygon();
  updateZoneControls();
});
$("#clearZone").addEventListener("click", resetZoneDrawing);

if ("ResizeObserver" in window) {
  zoneResizeObserver = new ResizeObserver(drawZonePolygon);
  zoneResizeObserver.observe($("#zoneImage"));
} else {
  window.addEventListener("resize", drawZonePolygon);
}

function renderZoneResult(data) {
  const summary = data.summary;
  const persons = summary.persons;
  const noPeople = summary.total_persons === 0;
  const violation = summary.violations > 0;
  const rows = persons.map((person, index) => `
    <tr>
      <td>#${index + 1}</td>
      <td>${person.zone_violation
        ? `<span class="pill pill-bad">Inside zone · violation</span>`
        : `<span class="pill pill-good">Outside zone</span>`}</td>
      <td>${Math.round(person.confidence * 100)}%</td>
    </tr>`).join("");
  $("#zoneResult").innerHTML = `
    <p class="site-analysis-source">Source: ${escapeHtml(zoneFile.name)} · Model: person detector · Score threshold: ${Math.round(Number($("#zoneConf").value) * 100)}%</p>
    <img src="${data.annotated_image}" alt="Annotated restricted-zone detection result"
      class="rounded-lg border mb-3" style="border-color:var(--border);max-width:100%" />
    <div class="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3 text-center">
      <div class="card p-2"><div class="stat-value" style="font-size:1.3rem">${summary.total_persons}</div><div class="stat-label">People</div></div>
      <div class="card p-2"><div class="stat-value" style="font-size:1.3rem;color:${C.crit}">${summary.inside_zone}</div><div class="stat-label">Inside zone</div></div>
      <div class="card p-2"><div class="stat-value" style="font-size:1.3rem;color:${C.good}">${summary.outside_zone}</div><div class="stat-label">Outside zone</div></div>
      <div class="card p-2"><div class="stat-value" style="font-size:1.3rem;color:${C.crit}">${summary.violations}</div><div class="stat-label">Violations</div></div>
    </div>
    <div class="banner ${noPeople ? "" : (violation ? "banner-bad" : "banner-good")} mb-3">
      <i data-lucide="${noPeople ? "help-circle" : (violation ? "alert-triangle" : "check-circle")}"></i>
      ${escapeHtml(summary.message)}
    </div>
    ${rows ? `<table class="data"><thead><tr><th>Person</th><th>Zone result</th><th>Confidence</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
    ${violation ? `<button type="button" class="btn-ghost mt-3" id="draftZoneTicket"><i data-lucide="clipboard-plus" class="w-4 h-4"></i>Review and draft incident</button>
      <p class="text-xs mt-2" style="color:var(--muted)">Draft only. Verify the result and location before saving; evidence is not attached unless you opt in.</p>` : ""}
  `;
  lucide.createIcons();
  $("#draftZoneTicket")?.addEventListener("click", async () => {
    if (!pendingZoneFinding) return;
    let evidenceFile = null;
    try {
      evidenceFile = await dataUrlToImageFile(
        pendingZoneFinding.annotatedImage,
        `${pendingZoneFinding.file.name.replace(/\.[^.]+$/, "")}_zone_review.jpg`,
      );
    } catch (error) {
      console.error("Could not prepare restricted-zone evidence:", error);
    }
    draftReviewedTicket({
      hazardType: "RESTRICTED_ZONE",
      description: `Restricted-zone review flagged ${summary.violations} possible zone entry finding(s) among ${summary.total_persons} detected person(s). Confirm the image, polygon, and site location before action.`,
      evidenceFile,
    });
  });
}

$("#analyzeZone").addEventListener("click", async () => {
  if (!zoneFile || !zoneFinished) return;
  const button = $("#analyzeZone");
  button.disabled = true;
  $("#zoneMsg").innerHTML = `<span class="spinner spinner-dark"></span> Detecting people…`;
  try {
    const form = new FormData();
    form.append("file", zoneFile);
    form.append("polygon", JSON.stringify(zonePoints));
    form.append("conf", $("#zoneConf").value);
    const response = await fetch("/api/detect-zone", { method: "POST", body: form });
    if (!response.ok) throw await responseError(response);
    const data = await response.json();
    pendingZoneFinding = { file: zoneFile, annotatedImage: data.annotated_image };
    renderZoneResult(data);
    $("#zoneMsg").textContent = "";
  } catch (error) {
    $("#zoneMsg").innerHTML =
      `<span style="color:var(--critical)">Error: ${escapeHtml(error.message)}</span>`;
  } finally {
    updateZoneControls();
  }
});

// ==================================================================== VIDEO
let videoFile = null;
let currentVideoUrl = null;

// Show the picked clip's first frame INSIDE the dropzone (the video analog of
// the photo thumbnail), so it's clear which clip is selected.
function showVideoPreview(file) {
  if (currentVideoUrl) URL.revokeObjectURL(currentVideoUrl);
  currentVideoUrl = URL.createObjectURL(file);
  const v = $("#videoPreview");
  v.src = currentVideoUrl + "#t=0.1";   // nudge to ~first frame so it isn't blank
  v.classList.remove("hidden");
  $("#dropVideoPrompt").classList.add("hidden");
  $("#videoFileName").innerHTML = `${escapeHtml(file.name)} · <span style="color:var(--orange)">click to change</span>`;
  $("#videoFileName").classList.remove("hidden");
}

wireDropzone("#dropVideo", "#fileVideo", (f) => {
  pendingVideoFinding = null;
  $("#videoResult").textContent = "No video analysed yet.";
  if (!["video/mp4", "video/quicktime", "video/x-msvideo", "video/x-matroska", "video/webm"].includes(f.type) ||
      f.size > 50 * 1024 * 1024) {
    videoFile = null;
    $("#btnVideo").disabled = true;
    $("#videoMsg").innerHTML = `<span style="color:var(--critical)">Choose a supported video up to 50 MB.</span>`;
    return;
  }
  videoFile = f;
  showVideoPreview(f);
  $("#btnVideo").disabled = false;
  $("#videoMsg").innerHTML = "";
});
$("#sample").addEventListener("input", (e) => ($("#sampleVal").textContent = e.target.value));

$("#btnVideo").addEventListener("click", async () => {
  if (!videoFile) return;
  $("#btnVideo").disabled = true;
  $("#videoMsg").innerHTML = `<span class="spinner spinner-dark"></span> Processing video (this can take a while on CPU)…`;
  try {
    const fd = new FormData();
    fd.append("file", videoFile);
    fd.append("conf", $("#conf").value);
    fd.append("sample_every", $("#sample").value);
    const r = await fetch("/api/detect-video", { method: "POST", body: fd });
    if (!r.ok) throw await responseError(r);
    const d = await r.json();
    const s = d.summary;
    const noWorkers = s.max_workers === 0;
    const hasViolations = s.peak_violations > 0;
    pendingVideoFinding = hasViolations ? { videoUrl: d.video_url, summary: s, sourceName: videoFile.name } : null;
    const confidenceThreshold = Math.round(Number($("#conf").value) * 100);
    $("#videoResult").innerHTML = `
      <p class="site-analysis-source">Source: ${escapeHtml(videoFile.name)} · Model classes: Hardhat / NO-Hardhat · Score threshold: ${confidenceThreshold}% · Sampled every ${escapeHtml($("#sample").value)} frames</p>
      <video id="reviewedVideoEvidence" src="${d.video_url}" controls preload="metadata" class="rounded-lg border mb-3" style="border-color:var(--border);max-width:100%"></video>
      <div class="grid grid-cols-2 gap-2 text-center">
        <div class="card p-2"><div class="stat-value" style="font-size:1.2rem">${s.frames_analysed}</div><div class="stat-label">Frames analysed</div></div>
        <div class="card p-2"><div class="stat-value" style="font-size:1.2rem">${s.max_workers}</div><div class="stat-label">Peak workers</div></div>
        <div class="card p-2"><div class="stat-value" style="font-size:1.2rem;color:${hasViolations ? C.crit : C.ink2}">${s.peak_violations}</div><div class="stat-label">Peak flags / frame</div></div>
        <div class="card p-2"><div class="stat-value" style="font-size:1.2rem">${pct(s.avg_compliance)}</div><div class="stat-label">Avg compliance</div></div>
      </div>
      <div class="banner ${noWorkers ? "" : (hasViolations ? "banner-bad" : "banner-good")} mt-3">
        ${noWorkers
          ? "No workers detected in the sampled frames — PPE status cannot be assessed."
          : (hasViolations
            ? "Potential missing-hardhat flags appear in sampled frames. Review the annotated video in person."
            : "No hardhat flags appeared in sampled frames. This does not prove the full clip is hazard-free.")}
      </div>
      ${hasViolations ? `<button type="button" class="btn-ghost mt-3" id="draftVideoTicket"><i data-lucide="clipboard-plus" class="w-4 h-4"></i>Review and draft incident</button>
        <p class="text-xs mt-2" style="color:var(--muted)">Draft only. Verify the result and location before saving; evidence is not attached unless you opt in.</p>` : ""}`;
    lucide.createIcons();
    $("#draftVideoTicket")?.addEventListener("click", async () => {
      if (!pendingVideoFinding) return;
      let evidenceFile = null;
      try {
        evidenceFile = await captureReviewedVideoFrame($("#reviewedVideoEvidence"));
      } catch (error) {
        console.error("Could not prepare video-review evidence frame:", error);
      }
      draftReviewedTicket({
        hazardType: "MISSING_PPE",
        description: `Video review flagged up to ${s.peak_violations} possible missing-hardhat finding(s) in sampled frames from ${pendingVideoFinding.sourceName}. Confirm the observation and record the actual location before action.`,
        evidenceFile,
      });
    });
    $("#videoMsg").innerHTML = "";
  } catch (e) {
    $("#videoMsg").innerHTML = `<span style="color:var(--critical)">Error: ${escapeHtml(e.message)}</span>`;
  } finally {
    $("#btnVideo").disabled = false;
  }
});

// ==================================================================== INCIDENT LOG
async function loadLog() {
  const table = $("#logTable");
  try {
    const insp = await getJSON("/api/inspections");
    if (!insp.length) {
      table.classList.add("hidden"); $("#logEmpty").textContent = "No inspections saved yet.";
      $("#logEmpty").classList.remove("hidden");
      $("#logImageSelect").innerHTML = ""; $("#logImageView").innerHTML = "";
      return;
    }
    table.classList.remove("hidden"); $("#logEmpty").classList.add("hidden");
    table.querySelector("thead").innerHTML =
      `<tr><th>ID</th><th>Date &amp; time</th><th>Photo</th><th>Workers</th><th>OK</th><th>Viol.</th><th>Compliance</th></tr>`;
    table.querySelector("tbody").innerHTML = insp.map((r) =>
      `<tr><td>${r.id}</td><td>${escapeHtml(r.timestamp)}</td><td>${escapeHtml(r.image_name)}</td>
         <td>${r.total_workers}</td><td>${r.helmet_ok}</td>
         <td style="color:${r.violations ? C.crit : "inherit"}">${r.violations}</td>
         <td><div class="flex items-center gap-2"><div class="bar" style="width:70px"><span style="width:${r.compliance ?? 0}%"></span></div>${pct(r.compliance)}</div></td>
       </tr>`).join("");

    const withImg = insp.filter((r) => r.image_file);
    $("#logImageSelect").innerHTML = withImg.map((r) =>
      `<option value="${escapeHtml(r.image_file)}">#${r.id} · ${escapeHtml(r.image_name)}</option>`).join("");
    const showImg = () => {
      const f = $("#logImageSelect").value;
      $("#logImageView").innerHTML = f
        ? `<img src="/api/detections/${encodeURIComponent(f)}" class="rounded-lg border" style="border-color:var(--border);max-width:100%" />` : "";
    };
    $("#logImageSelect").onchange = showImg;
    showImg();
  } catch (error) {
    table.classList.add("hidden");
    $("#logEmpty").textContent = `Could not load incident history: ${error.message}`;
    $("#logEmpty").classList.remove("hidden");
    $("#logImageSelect").innerHTML = ""; $("#logImageView").innerHTML = "";
  }
}

// ==================================================================== PROGRESS TRACKER
async function loadTasks() {
  const kpi = $("#taskKpis"), table = $("#taskTable");
  try {
    const { tasks, status_colors } = await getJSON("/api/tasks");

    if (!tasks.length) {
      kpi.innerHTML = "";
      table.classList.add("hidden");
      $("#taskEmpty").textContent = "No tasks yet. Add one above or load the sample project.";
      $("#taskEmpty").classList.remove("hidden");
      destroyChart("chartTasks"); $("#gantt").innerHTML = "";
      return;
    }
    table.classList.remove("hidden"); $("#taskEmpty").classList.add("hidden");

    const overall = Math.round(tasks.reduce((s, t) => s + t.progress, 0) / tasks.length);
    const completed = tasks.filter((t) => t.status === "Completed").length;
    const behind = tasks.filter((t) => t.status === "Behind Schedule").length;
    kpi.innerHTML =
      kpiTile("Total tasks", tasks.length) +
      kpiTile("Overall completion", `${overall}%`) +
      kpiTile("Completed", completed, { color: C.good }) +
      kpiTile("Behind schedule", behind, { color: behind ? C.crit : C.good });

  // Completion bar chart (one series; bars colored by status via the status palette)
    destroyChart("chartTasks");
    charts["chartTasks"] = new Chart($("#chartTasks"), {
      type: "bar",
      data: { labels: tasks.map((t) => t.name),
        datasets: [{ label: "% done", data: tasks.map((t) => t.progress),
          backgroundColor: tasks.map((t) => status_colors[t.status] || C.blue),
          borderRadius: 4, borderSkipped: false }] },
      options: { indexAxis: "y", responsive: true,
        plugins: { legend: { display: false } },
        scales: { x: { min: 0, max: 100, grid: { color: C.grid }, ticks: { color: C.ink2 } },
                  y: { grid: { display: false }, ticks: { color: C.ink2 } } } },
    });

  // Gantt-style timeline (pure divs positioned by date; orange "today" line)
    drawGantt(tasks, status_colors);

  // Task table with progress controls
    table.querySelector("thead").innerHTML =
      `<tr><th>Task</th><th>Start</th><th>Planned end</th><th>% done</th><th>Status</th><th></th></tr>`;
    table.querySelector("tbody").innerHTML = tasks.map((t) =>
      `<tr>
         <td>${escapeHtml(t.name)}</td><td>${escapeHtml(t.start_date)}</td><td>${escapeHtml(t.end_date)}</td>
         <td style="width:130px"><div class="flex items-center gap-2"><div class="bar" style="width:60px"><span style="width:${t.progress}%"></span></div>${t.progress}%</div></td>
         <td><span class="pill" style="background:${status_colors[t.status]}22;color:${status_colors[t.status]}">${escapeHtml(t.status)}</span></td>
         <td class="flex gap-1">
           <button class="btn-ghost" style="padding:.2rem .5rem" data-edit="${t.id}" title="Edit task"><i data-lucide="pencil" class="w-3 h-3"></i></button>
           <button class="btn-ghost" style="padding:.2rem .5rem" data-del="${t.id}" title="Delete task"><i data-lucide="trash-2" class="w-3 h-3"></i></button>
         </td>
       </tr>`).join("");
    $$("[data-edit]").forEach((button) => button.addEventListener("click", () => {
      const task = tasks.find((item) => item.id === Number(button.dataset.edit));
      if (!task) return;
      const form = $("#taskForm");
      form.elements.namedItem("name").value = task.name;
      form.elements.namedItem("start_date").value = task.start_date;
      form.elements.namedItem("end_date").value = task.end_date;
      form.elements.namedItem("progress").value = task.progress;
      form.dataset.editingId = task.id;
      $("#taskSubmit").innerHTML = `<i data-lucide="save" class="w-4 h-4"></i>Save changes`;
      $("#cancelTaskEdit").classList.remove("hidden");
      $("#taskMsg").textContent = "";
      form.scrollIntoView({ behavior: "smooth", block: "center" });
      lucide.createIcons();
    }));
    $$("[data-del]").forEach((button) => button.addEventListener("click", async () => {
      try {
        const response = await fetch(`/api/tasks/${button.dataset.del}`, { method: "DELETE" });
        if (!response.ok) throw await responseError(response);
        if ($("#taskForm").dataset.editingId === button.dataset.del) cancelTaskEdit();
        await loadTasks();
      } catch (error) {
        showTaskMessage(error.message, true);
      }
    }));
    lucide.createIcons();
  } catch (error) {
    kpi.innerHTML = "";
    table.classList.add("hidden");
    $("#taskEmpty").textContent = `Could not load tasks: ${error.message}`;
    $("#taskEmpty").classList.remove("hidden");
  }
}

function drawGantt(tasks, colors) {
  const dates = tasks.flatMap((t) => [new Date(t.start_date), new Date(t.end_date)]);
  const min = new Date(Math.min(...dates)), max = new Date(Math.max(...dates));
  const span = (max - min) || 1;
  const posPct = (d) => ((new Date(d) - min) / span) * 100;
  const todayPct = posPct(new Date());

  $("#gantt").innerHTML = `
    <div class="relative">
      ${(todayPct >= 0 && todayPct <= 100)
        ? `<div style="position:absolute;left:${todayPct}%;top:0;bottom:0;width:2px;background:var(--orange);z-index:2"></div>
           <div style="position:absolute;left:${todayPct}%;top:-2px;transform:translateX(-50%);font-size:.65rem;color:var(--orange);font-weight:700">today</div>` : ""}
      <div class="space-y-1 pt-4">
        ${tasks.map((t) => {
          const l = posPct(t.start_date), w = Math.max(2, posPct(t.end_date) - l);
          return `<div class="flex items-center gap-2 text-xs">
            <div style="width:110px;color:var(--ink-2)" class="truncate">${escapeHtml(t.name)}</div>
            <div class="relative flex-1" style="height:16px">
              <div style="position:absolute;left:${l}%;width:${w}%;height:100%;border-radius:4px;background:${colors[t.status] || C.blue};opacity:.85"></div>
            </div></div>`;
        }).join("")}
      </div>
    </div>`;
}

function showTaskMessage(message, isError = false) {
  const msg = $("#taskMsg");
  msg.textContent = message;
  msg.style.color = isError ? "var(--critical)" : "var(--good)";
}

function cancelTaskEdit() {
  const form = $("#taskForm");
  delete form.dataset.editingId;
  form.reset();
  $("#taskSubmit").innerHTML = `<i data-lucide="plus" class="w-4 h-4"></i>Add task`;
  $("#cancelTaskEdit").classList.add("hidden");
  showTaskMessage("");
  lucide.createIcons();
}

$("#cancelTaskEdit").addEventListener("click", cancelTaskEdit);
$("#taskForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const submit = $("#taskSubmit");
  const form = e.currentTarget;
  submit.disabled = true;
  const fd = new FormData(e.target);
  const taskId = form.dataset.editingId;
  try {
    const response = await fetch(taskId ? `/api/tasks/${taskId}` : "/api/tasks", {
      method: taskId ? "PATCH" : "POST",
      body: fd,
    });
    if (!response.ok) throw await responseError(response);
    if (taskId) cancelTaskEdit();
    else form.reset();
    showTaskMessage(taskId ? "Task updated." : "Task added.");
    await loadTasks();
  } catch (error) {
    showTaskMessage(error.message, true);
  } finally {
    submit.disabled = false;
  }
});
$("#btnSample").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/tasks/load-sample", { method: "POST" });
    if (!response.ok) throw await responseError(response);
    showTaskMessage("Sample project tasks added.");
    await loadTasks();
  } catch (error) {
    showTaskMessage(error.message, true);
  }
});
$("#btnClearTasks").addEventListener("click", async () => {
  if (confirm("Delete ALL tasks?")) {
    try {
      const response = await fetch("/api/tasks", { method: "DELETE" });
      if (!response.ok) throw await responseError(response);
      if ($("#taskForm").dataset.editingId) cancelTaskEdit();
      await loadTasks();
    } catch (error) {
      showTaskMessage(error.message, true);
    }
  }
});

// ==================================================================== REPORTS
async function loadReportKpis() {
  try {
    const [stats, tasksResp] = await Promise.all([getJSON("/api/stats"), getJSON("/api/tasks")]);
    $("#reportKpis").innerHTML =
      kpiTile("Photos checked", stats.checks) +
      kpiTile("Avg compliance", pct(stats.avg_compliance)) +
      kpiTile("Violations", stats.violations, { color: stats.violations ? C.crit : C.good }) +
      kpiTile("Tasks tracked", tasksResp.tasks.length);
  } catch (error) {
    $("#reportKpis").innerHTML =
      `<div class="text-sm" style="color:var(--critical)">Could not load report data: ${escapeHtml(error.message)}</div>`;
  }
}
$("#btnReport").addEventListener("click", async (event) => {
  event.preventDefault();
  const incl = $("#inclImages").checked;
  const button = $("#btnReport");
  button.setAttribute("aria-disabled", "true");
  $("#reportMsg").textContent = "Generating report…";
  try {
    const response = await fetch(`/api/report.pdf?include_images=${incl}`);
    if (!response.ok) throw await responseError(response);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `construction_safety_report_${new Date().toISOString().slice(0, 10)}.pdf`;
    link.click();
    URL.revokeObjectURL(url);
    $("#reportMsg").textContent = "Report downloaded.";
  } catch (error) {
    $("#reportMsg").textContent = `Could not generate report: ${error.message}`;
    $("#reportMsg").style.color = "var(--critical)";
  } finally {
    button.removeAttribute("aria-disabled");
  }
});

// ===========================================================================
// Sample-clip and local-webcam frame review controller
// ===========================================================================
let sentinelActive = false;
let sentinelProcessing = false;
let sentinelLoopId = null;
let activeCam = "cam1";
let webcamStream = null;
let audioCtx = null;
let sirenOsc = null;
let sirenMuted = false;

const CAM_CONFIG = {
  cam1: { name: "SAMPLE CLIP A", src: "/sample_videos/demo_violations_h264.mp4" },
  cam2: { name: "SAMPLE CLIP B", src: "/sample_videos/demo_compliant_h264.mp4" },
  cam3: { name: "REPLAY CLIP A", src: "/sample_videos/demo_violations_h264.mp4" },
  webcam: { name: "LOCAL WEBCAM", src: null },
};

function initAudioContext() {
  if (!audioCtx) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) audioCtx = new AudioContextClass();
  }
  if (audioCtx && audioCtx.state === "suspended") {
    audioCtx.resume();
  }
}

function playIndustrialSiren() {
  if (sirenMuted || sirenOsc) return;
  try {
    initAudioContext();
    if (!audioCtx) return;

    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sawtooth";
    gain.gain.setValueAtTime(0.12, audioCtx.currentTime);

    const lfo = audioCtx.createOscillator();
    lfo.frequency.value = 2.5; // 2.5 sweeps/sec
    const lfoGain = audioCtx.createGain();
    lfoGain.gain.value = 220;
    lfo.connect(lfoGain);
    lfoGain.connect(osc.frequency);
    osc.frequency.setValueAtTime(750, audioCtx.currentTime);

    lfo.start();
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start();
    sirenOsc = { osc, lfo, gain };
  } catch (e) {
    console.warn("Audio siren error:", e);
  }
}

function stopIndustrialSiren() {
  if (sirenOsc) {
    try {
      sirenOsc.osc.stop();
      sirenOsc.lfo.stop();
      sirenOsc.osc.disconnect();
    } catch (e) {}
    sirenOsc = null;
  }
}

function toggleSiren() {
  sirenMuted = !sirenMuted;
  const label = $("#sirenLabel");
  const icon = $("#sirenIcon");
  if (sirenMuted) {
    stopIndustrialSiren();
    if (label) label.textContent = "SIREN: MUTED";
    if (icon) icon.setAttribute("data-lucide", "volume-x");
  } else {
    if (label) label.textContent = "SIREN: ARMED";
    if (icon) icon.setAttribute("data-lucide", "volume-2");
  }
  lucide.createIcons();
}

const toggleSirenBtn = $("#btnToggleSiren");
if (toggleSirenBtn) toggleSirenBtn.addEventListener("click", toggleSiren);

function loadSentinel() {
  loadEvidenceGallery();
  lucide.createIcons();
}

async function loadEvidenceGallery() {
  const grid = $("#evidenceGrid");
  if (!grid) return;
  try {
    const res = await fetch("/api/evidence");
    if (!res.ok) return;
    const data = await res.json();
    if (!data.evidence || data.evidence.length === 0) {
      grid.innerHTML = '<div class="text-[11px] col-span-full italic" style="color:var(--muted)">No captures recorded yet.</div>';
      return;
    }
    grid.innerHTML = data.evidence
      .slice(0, 8)
      .map(
        (e) =>
          `<a href="${e.url}" target="_blank" title="${escapeHtml(e.filename)} (${e.created_at})">
             <img src="${e.url}" class="evidence-thumb" alt="Evidence Snapshot" />
           </a>`
      )
      .join("");
  } catch (err) {
    console.warn("Failed to load evidence gallery:", err);
  }
}

// Camera tab selector
$$(".cam-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    $$(".cam-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const camKey = btn.dataset.cam;
    await switchCamera(camKey);
  });
});

async function switchCamera(camKey) {
  activeCam = camKey;
  const cfg = CAM_CONFIG[camKey] || CAM_CONFIG.cam1;
  const nameLabel = $("#activeCamName");
  if (nameLabel) nameLabel.textContent = cfg.name;

  const wasActive = sentinelActive;
  stopSentinel();

  const video = $("#sentinelVideo");
  const webcam = $("#webcamVideo");

  if (camKey === "webcam") {
    try {
      if (video) video.pause();
      webcamStream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 } },
      });
      if (webcam) {
        webcam.srcObject = webcamStream;
        await webcam.play();
      }
    } catch (err) {
      alert("Could not access webcam: " + err.message);
      return;
    }
  } else {
    if (webcamStream) {
      webcamStream.getTracks().forEach((t) => t.stop());
      webcamStream = null;
    }
    if (video) {
      video.src = cfg.src;
      video.load();
    }
  }

  if (wasActive) {
    startSentinel();
  }
}

async function startSentinel() {
  if (sentinelActive) return;
  initAudioContext();
  sentinelActive = true;

  const playBtn = $("#btnSentinelPlay");
  const pauseBtn = $("#btnSentinelPause");
  const snapBtn = $("#btnSentinelSnap");
  const placeholder = $("#sentinelPlaceholder");

  if (playBtn) playBtn.disabled = true;
  if (pauseBtn) pauseBtn.disabled = false;
  if (snapBtn) snapBtn.disabled = false;
  if (placeholder) placeholder.classList.add("hidden");

  const video = activeCam === "webcam" ? $("#webcamVideo") : $("#sentinelVideo");
  if (video && activeCam !== "webcam") {
    if (!video.src) video.src = CAM_CONFIG[activeCam].src;
    try {
      await video.play();
    } catch (e) {
      console.warn("Video play error:", e);
    }
  }

  runSentinelFrameLoop();
}

function pauseSentinel() {
  sentinelActive = false;
  stopIndustrialSiren();
  const playBtn = $("#btnSentinelPlay");
  const pauseBtn = $("#btnSentinelPause");
  if (playBtn) playBtn.disabled = false;
  if (pauseBtn) pauseBtn.disabled = true;

  const video = activeCam === "webcam" ? $("#webcamVideo") : $("#sentinelVideo");
  if (video && activeCam !== "webcam") video.pause();
}

function stopSentinel() {
  pauseSentinel();
  if (webcamStream) {
    webcamStream.getTracks().forEach((t) => t.stop());
    webcamStream = null;
  }
  const statusBadge = $("#sentinelStatusBadge");
  if (statusBadge) {
    statusBadge.className = "hud-pill info";
    statusBadge.innerHTML = '<i data-lucide="circle-pause" class="w-3.5 h-3.5"></i> DEMO FEED IDLE';
  }
  const viewport = $("#sentinelViewport");
  if (viewport) viewport.classList.remove("siren-active");
  lucide.createIcons();
}

const startBtn = $("#btnStartSentinel");
if (startBtn) startBtn.addEventListener("click", startSentinel);

const sentinelPlayBtn = $("#btnSentinelPlay");
if (sentinelPlayBtn) sentinelPlayBtn.addEventListener("click", startSentinel);

const sentinelPauseBtn = $("#btnSentinelPause");
if (sentinelPauseBtn) sentinelPauseBtn.addEventListener("click", pauseSentinel);

// Stream Frame Processing Loop
async function runSentinelFrameLoop() {
  if (!sentinelActive) return;

  const video = activeCam === "webcam" ? $("#webcamVideo") : $("#sentinelVideo");
  const canvas = $("#sentinelCanvas");

  if (video && canvas && video.readyState >= 2 && !sentinelProcessing) {
    sentinelProcessing = true;
    try {
      // Capture current video frame to offscreen canvas
      const vw = video.videoWidth || 640;
      const vh = video.videoHeight || 480;
      const offscreen = document.createElement("canvas");
      offscreen.width = vw;
      offscreen.height = vh;
      const ctx = offscreen.getContext("2d");
      ctx.drawImage(video, 0, 0, vw, vh);

      const blob = await new Promise((resolve) => offscreen.toBlob(resolve, "image/jpeg", 0.75));
      if (blob && sentinelActive) {
        const formData = new FormData();
        formData.append("file", blob, "stream_frame.jpg");
        const confSlider = $("#sentinelConf");
        formData.append("conf", confSlider ? confSlider.value : "0.35");
        formData.append("save_evidence", "true");

        const res = await fetch("/api/sentinel/process-frame", {
          method: "POST",
          body: formData,
        });

        if (res.ok && sentinelActive) {
          const result = await res.json();
          renderSentinelResult(result);
        }
      }
    } catch (err) {
      console.warn("Sentinel loop frame error:", err);
    } finally {
      sentinelProcessing = false;
    }
  }

  if (sentinelActive) {
    // Schedule next frame in ~220ms (smooth ~4-5 FPS on CPU without lag)
    setTimeout(runSentinelFrameLoop, 220);
  }
}

function renderSentinelResult(result) {
  const canvas = $("#sentinelCanvas");
  if (!canvas || !result.annotated_frame) return;

  const img = new Image();
  img.onload = () => {
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0);
  };
  img.src = result.annotated_frame;

  const tel = result.telemetry || {};
  const haz = tel.hazard_summary || {};
  const ppe = haz.ppe || {};
  const fire = haz.fire_smoke || {};

  // Update Telemetry Indicators
  const fpsLabel = $("#liveFps");
  if (fpsLabel) fpsLabel.textContent = `${tel.fps || 0.0} FPS`;

  const workersCount = $("#liveWorkersCount");
  if (workersCount) workersCount.textContent = (tel.tracked_workers || []).length;

  const violationsCount = $("#liveViolationsCount");
  if (violationsCount) violationsCount.textContent = haz.total_violations || 0;

  // Tracked Workers List
  const workersList = $("#trackedWorkersList");
  if (workersList) {
    const list = tel.tracked_workers || [];
    if (list.length === 0) {
      workersList.innerHTML = '<div class="text-[11px] italic" style="color:var(--muted)">No workers detected in this frame — assessment inconclusive.</div>';
    } else {
      workersList.innerHTML = list
        .map((w) => {
          const confidence = Number.isFinite(w.confidence)
            ? `${Math.round(w.confidence * 100)}%`
            : "not available";
          const observation = w.observation_class
            ? escapeHtml(w.observation_class)
            : "No overlapping PPE observation";
          return `<div class="p-1.5 rounded bg-[var(--surface)] border border-[var(--border)] font-mono text-[11px]">
               <div class="flex items-center justify-between gap-2">
                 <span>Worker #${escapeHtml(w.worker_id)}</span>
                 <span class="${w.confirmed_violation ? "text-red-500 font-bold" : "text-emerald-500"}">${w.confirmed_violation ? "TEMPORAL FLAG" : "NO CONFIRMED FLAG"}</span>
               </div>
               <div class="mt-1 text-[10px]" style="color:var(--ink-2)">Observation: ${observation} · Confidence: ${confidence}</div>
             </div>`
        })
        .join("");
    }
  }

  // Siren and Emergency Flashing
  const viewport = $("#sentinelViewport");
  const statusBadge = $("#sentinelStatusBadge");

  if (tel.siren_trigger) {
    playIndustrialSiren();
    if (viewport) viewport.classList.add("siren-active");
    if (statusBadge) {
      statusBadge.className = "hud-pill danger";
      const hazardName = (haz.active_hazards && haz.active_hazards[0]) || "CRITICAL HAZARD DETECTED";
      statusBadge.innerHTML = `<i data-lucide="alert-triangle" class="w-3.5 h-3.5"></i> ALARM: ${escapeHtml(hazardName)}`;
    }
  } else {
    stopIndustrialSiren();
    if (viewport) viewport.classList.remove("siren-active");
    if (statusBadge) {
      statusBadge.className = "hud-pill info";
      statusBadge.innerHTML = (tel.tracked_workers || []).length
        ? '<i data-lucide="scan-line" class="w-3.5 h-3.5"></i> REVIEW RUNNING · NO PPE FLAG CONFIRMED'
        : '<i data-lucide="help-circle" class="w-3.5 h-3.5"></i> NO WORKERS DETECTED · REVIEW INCONCLUSIVE';
    }
  }
  lucide.createIcons();

  // Log to Incident Stream Ticker
  if (tel.siren_trigger || tel.evidence_saved) {
    appendIncidentStreamEvent(haz, tel.evidence_saved);
  }

  // If evidence snapshot was auto-captured, refresh gallery
  if (tel.evidence_saved) {
    loadEvidenceGallery();
  }
}

let lastLoggedEventTime = 0;
function appendIncidentStreamEvent(haz, evidence) {
  const now = Date.now();
  if (now - lastLoggedEventTime < 3500) return; // Debounce ticker spam
  lastLoggedEventTime = now;

  const stream = $("#incidentStream");
  if (!stream) return;

  const timeStr = new Date().toLocaleTimeString();
  const hazardType = (haz.active_hazards && haz.active_hazards[0]) || (evidence && evidence.type) || "Safety Violation";
  const severity = haz.overall_severity || "CRITICAL";

  const item = document.createElement("div");
  item.className = `incident-item ${severity === "CATASTROPHIC" || severity === "CRITICAL" ? "critical" : "warning"}`;
  item.innerHTML = `
    <div class="flex items-center justify-between font-mono text-[11px]">
      <span class="font-bold">${escapeHtml(hazardType)}</span>
      <span style="color:var(--ink-2)">${timeStr}</span>
    </div>
    <div class="text-[11px]" style="color:var(--ink-2)">
      Severity: <span class="font-semibold text-red-500">${severity}</span>
      ${evidence ? `| <a href="${evidence.path ? `/api/evidence/${evidence.filename}` : "#"}" target="_blank" class="underline text-amber-500">View Evidence</a>` : ""}
    </div>
  `;

  if (stream.children.length === 1 && stream.children[0].textContent.includes("Monitoring")) {
    stream.innerHTML = "";
  }
  stream.prepend(item);
  if (stream.children.length > 20) {
    stream.removeChild(stream.lastChild);
  }
}

const sentinelConfSlider = $("#sentinelConf");
if (sentinelConfSlider) {
  sentinelConfSlider.addEventListener("input", () => {
    const val = $("#sentinelConfVal");
    if (val) val.textContent = sentinelConfSlider.value;
  });
}

const sentinelSnapBtn = $("#btnSentinelSnap");
if (sentinelSnapBtn) {
  sentinelSnapBtn.addEventListener("click", () => {
    const canvas = $("#sentinelCanvas");
    if (!canvas) return;
    const link = document.createElement("a");
    link.download = `sentinel_snapshot_${Date.now()}.jpg`;
    link.href = canvas.toDataURL("image/jpeg", 0.9);
    link.click();
  });
}

// ====================================================================
// Illustrative site layout
// ====================================================================
let blueprintZonesData = [];
let selectedMapZone = "";
let sitePlanInitialized = false;

async function loadBlueprintMap() {
  const container = $("#mapZonesList");
  try {
    const res = await fetch("/api/zones");
    if (!res.ok) {
      throw new Error(`Zone request failed (${res.status}).`);
    }
    const data = await res.json();
    blueprintZonesData = (data.zones || []).filter((zone) => Number(zone.is_active) !== 0);
    renderBlueprintZonesList(blueprintZonesData);
  } catch (err) {
    console.error("Failed to load configured zones:", err);
    const countBadge = $("#mapActiveZonesCount");
    if (countBadge) countBadge.textContent = "UNAVAILABLE";
    if (container) {
      container.innerHTML = '<div class="site-map-error">Configured zones could not be loaded. Check the local server and retry.</div>';
    }
    $$("#sitePlan .site-zone").forEach((zone) => zone.classList.add("unconfigured"));
  }
  initSitePlan();
}

function renderBlueprintZonesList(zones) {
  const container = $("#mapZonesList");
  const countBadge = $("#mapActiveZonesCount");
  if (countBadge) countBadge.textContent = `${zones.length} ${zones.length === 1 ? "ZONE" : "ZONES"}`;
  if (!container) return;

  const zonesByCode = new Map(zones.map((zone) => [zone.zone_code, zone]));
  const mappedCodes = new Set(Array.from(
    $$("#sitePlan .site-zone"),
    (node) => node.dataset.zoneCode
  ));
  $$("#sitePlan .site-zone").forEach((node) => {
    const configuredZone = zonesByCode.get(node.dataset.zoneCode);
    const configured = Boolean(configuredZone);
    const riskLevel = normalizeZoneRiskLevel(configuredZone?.risk_level);
    node.classList.toggle("unconfigured", !configured);
    node.setAttribute("data-risk-level", riskLevel);
    node.setAttribute("aria-hidden", String(!configured));
    node.setAttribute("tabindex", configured ? "0" : "-1");
    if (configuredZone) {
      node.setAttribute(
        "aria-label",
        `Select ${configuredZone.zone_code}, ${configuredZone.name}, risk ${configuredZone.risk_level || "unrated"}`
      );
    }
  });

  if (zones.length === 0) {
    selectedMapZone = "";
    const selection = $("#mapZoneSelection");
    if (selection) selection.textContent = "There are no active zones to show.";
    container.innerHTML = '<div class="site-map-empty">No zones are configured yet.</div>';
    return;
  }

  container.innerHTML = zones
    .map((z) => {
      const riskClass = normalizeZoneRiskLevel(z.risk_level);
      return `
        <button type="button" class="site-zone-card" data-zone-code="${escapeHtml(z.zone_code)}" aria-pressed="false">
          <span class="site-zone-card-top">
            <span class="site-zone-card-name"><span class="site-zone-code-badge">${escapeHtml(z.zone_code)}</span>${escapeHtml(z.name)}</span>
            <span class="site-risk-badge risk-${escapeHtml(riskClass)}">${escapeHtml(z.risk_level || "UNRATED")}</span>
          </span>
          <span class="site-zone-card-bottom">
            <span>${escapeHtml(String(z.hazard_type || "").replaceAll("_", " "))}</span>
            <span>${mappedCodes.has(z.zone_code) ? "APPROX. AREA" : "NOT PLACED"}</span>
          </span>
        </button>
      `;
    })
    .join("");

  $$(".site-zone-card").forEach((card) => {
    card.addEventListener("click", () => selectMapZone(card.dataset.zoneCode));
  });
  lucide.createIcons();
  if (selectedMapZone && zones.some((zone) => zone.zone_code === selectedMapZone)) {
    selectMapZone(selectedMapZone);
  } else {
    selectedMapZone = "";
    $$("#sitePlan .site-zone").forEach((zone) => zone.classList.remove("selected"));
  }
}

function normalizeZoneRiskLevel(riskLevel) {
  const normalized = String(riskLevel || "").trim().toLowerCase();
  return ["low", "moderate", "high", "critical", "catastrophic"].includes(normalized)
    ? normalized
    : "unknown";
}

function selectMapZone(zoneCode) {
  selectedMapZone = zoneCode;
  const zone = blueprintZonesData.find((item) => item.zone_code === zoneCode);
  if (!zone) return;
  const zoneLayer = $('[data-map-layer="zones"]');
  if (zoneLayer && !zoneLayer.checked) {
    zoneLayer.checked = true;
    updateSitePlanLayers();
  }

  $$("#sitePlan .site-zone").forEach((node) => {
    const selected = node.dataset.zoneCode === zoneCode;
    node.classList.toggle("selected", selected);
    node.setAttribute("aria-pressed", String(selected));
  });
  $$(".site-zone-card").forEach((card) => {
    const selected = card.dataset.zoneCode === zoneCode;
    card.classList.toggle("selected", selected);
    card.setAttribute("aria-pressed", String(selected));
  });

  const selection = $("#mapZoneSelection");
  if (selection) {
    selection.innerHTML = `<strong>${escapeHtml(zone.zone_code)} selected</strong><span>${escapeHtml(zone.name)} · ${escapeHtml(zone.risk_level || "UNRATED")}</span>`;
  }
}

function openMapSource(cameraId) {
  showView("sentinel");
  switchCamera(cameraId);
}

function initSitePlan() {
  if (sitePlanInitialized) return;
  sitePlanInitialized = true;
  const plan = $("#sitePlan");
  if (!plan) return;

  $$("[data-map-layer]").forEach((input) => {
    input.addEventListener("change", updateSitePlanLayers);
  });
  updateSitePlanLayers();

  plan.querySelectorAll(".site-zone").forEach((node) => {
    const activate = () => selectMapZone(node.dataset.zoneCode);
    node.addEventListener("click", activate);
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
  });
  plan.querySelectorAll(".site-source-node").forEach((node) => {
    const activate = () => openMapSource(node.dataset.cameraId);
    node.addEventListener("click", activate);
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
  });
  $$(".site-source-button").forEach((button) => {
    button.addEventListener("click", () => openMapSource(button.dataset.cameraId));
  });
}

function updateSitePlanLayers() {
  const plan = $("#sitePlan");
  if (!plan) return;
  plan.classList.toggle("hide-zones", !$('[data-map-layer="zones"]').checked);
  plan.classList.toggle("hide-facilities", !$('[data-map-layer="facilities"]').checked);
  plan.classList.toggle("hide-routes", !$('[data-map-layer="routes"]').checked);
  plan.classList.toggle("hide-sources", !$('[data-map-layer="sources"]').checked);
}

// ====================================================================
// Incident ticket management
// ====================================================================
let currentTicketFilter = "ALL";

async function loadTickets(filter = currentTicketFilter) {
  currentTicketFilter = filter;

  // Update tab filter buttons
  $$("#ticketFilterButtons button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.ticketFilter === filter);
  });

  const tbody = $("#ticketsTbody");
  if (!tbody) return;

  try {
    const data = await getJSON("/api/tickets");
    const allTickets = data.tickets || [];

    // Calculate counters
    const total = allTickets.length;
    const open = allTickets.filter((t) => t.status === "OPEN").length;
    const investigating = allTickets.filter((t) => t.status === "INVESTIGATING").length;
    const remediated = allTickets.filter((t) => t.status === "REMEDIATED").length;

    const cntTotal = $("#cntTotalTickets");
    const cntOpen = $("#cntOpenTickets");
    const cntInv = $("#cntInvestigatingTickets");
    const cntRem = $("#cntRemediatedTickets");

    if (cntTotal) cntTotal.textContent = total;
    if (cntOpen) cntOpen.textContent = open;
    if (cntInv) cntInv.textContent = investigating;
    if (cntRem) cntRem.textContent = remediated;

    // Filter tickets
    const tickets = filter === "ALL" ? allTickets : allTickets.filter((t) => t.status === filter);

    if (tickets.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-center p-6 italic" style="color:var(--muted)">No incident tickets matching status: ${escapeHtml(filter)}.</td></tr>`;
      return;
    }

    tbody.innerHTML = tickets
      .map((t) => {
        const isCat = t.severity === "CATASTROPHIC";
        const isCrit = t.severity === "CRITICAL";
        const isHigh = t.severity === "HIGH" || t.severity === "WARNING";
        const sevClass = isCat ? "hud-pill danger animate-pulse" : isCrit ? "hud-pill danger" : isHigh ? "hud-pill warn" : "hud-pill info";

        const statClass = t.status === "OPEN" ? "hud-pill danger" : t.status === "INVESTIGATING" ? "hud-pill warn" : "hud-pill ok";

        let actionHtml = "";
        if (t.status === "OPEN") {
          actionHtml = `
            <button type="button" class="btn-ghost text-[11px] py-1 px-2.5 flex items-center gap-1 font-mono" onclick="handleUpdateTicket('${escapeHtml(t.ticket_id)}', 'INVESTIGATING')">
              <i data-lucide="search" class="w-3 h-3"></i>Investigate
            </button>
          `;
        } else if (t.status === "INVESTIGATING") {
          actionHtml = `
            <button type="button" class="btn-primary text-[11px] py-1 px-2.5 flex items-center gap-1 font-mono" onclick="handleUpdateTicket('${escapeHtml(t.ticket_id)}', 'REMEDIATED')">
              <i data-lucide="check" class="w-3 h-3"></i>Remediate
            </button>
          `;
        } else {
          actionHtml = `
            <div class="flex items-center gap-2">
              <span class="text-emerald-500 font-semibold text-[11px] flex items-center gap-1 font-mono">
                <i data-lucide="check-circle" class="w-3.5 h-3.5"></i>Cleared
              </span>
              <button type="button" class="icon-btn text-stone-500 hover:text-red-500" title="Delete Ticket" onclick="handleDeleteTicket('${escapeHtml(t.ticket_id)}')">
                <i data-lucide="trash-2" class="w-3 h-3"></i>
              </button>
            </div>
          `;
        }

        const evidenceHtml = t.evidence_file
          ? `<a href="/api/evidence/${encodeURIComponent(t.evidence_file)}" target="_blank" class="inline-flex items-center gap-1 text-amber-500 hover:underline font-mono text-[11px]"><i data-lucide="image" class="w-3.5 h-3.5"></i>Snapshot</a>`
          : `<span class="font-mono text-[10px]" style="color:var(--muted)">Live Telemetry</span>`;

        return `
          <tr class="border-b border-[var(--border)] hover:bg-[var(--surface-2)] transition-colors">
            <td><span class="font-mono font-bold text-[11px] px-2 py-0.5 rounded bg-[var(--surface-2)] border border-[var(--border)]">${escapeHtml(t.ticket_id)}</span></td>
            <td class="font-mono text-[11px] whitespace-nowrap">${escapeHtml(t.timestamp)}</td>
            <td><span class="font-bold font-mono text-[11px]">${escapeHtml(t.hazard_type)}</span></td>
            <td><span class="${sevClass} text-[9px] font-mono">${escapeHtml(t.severity)}</span></td>
            <td>
              <div class="font-medium text-[11px]">${escapeHtml(t.zone_name || "General Site Area")}</div>
              <div class="text-[10px]" style="color:var(--muted)">${escapeHtml(t.description || "")}</div>
              ${t.supervisor_notes ? `<div class="text-[10px] italic text-emerald-400 mt-0.5">Notes: ${escapeHtml(t.supervisor_notes)}</div>` : ""}
            </td>
            <td>${evidenceHtml}</td>
            <td><span class="${statClass} text-[9px] font-mono">${escapeHtml(t.status)}</span></td>
            <td>${actionHtml}</td>
          </tr>
        `;
      })
      .join("");

    lucide.createIcons();
  } catch (err) {
    console.error("Failed to load tickets:", err);
    tbody.innerHTML = `<tr><td colspan="8" class="text-center p-4 text-red-500 text-xs">Failed to load incident tickets: ${escapeHtml(err.message)}</td></tr>`;
  }
}

async function handleUpdateTicket(ticketId, newStatus) {
  let notes = "";
  if (newStatus === "INVESTIGATING") {
    notes = prompt("Enter supervisor action note for investigation:", "EHS Officer dispatched to site zone.");
  } else if (newStatus === "REMEDIATED") {
    notes = prompt("Enter corrective action resolution note:", "PPE issued, hazard cleared, and site safety re-verified.");
  }
  if (notes === null) return; // User cancelled prompt

  try {
    const formData = new FormData();
    formData.append("status", newStatus);
    if (notes) formData.append("supervisor_notes", notes);

    const res = await fetch(`/api/tickets/${encodeURIComponent(ticketId)}`, {
      method: "PATCH",
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`Could not update ticket: ${err.detail || "Server error"}`);
      return;
    }
    await loadTickets(currentTicketFilter);
  } catch (err) {
    alert(`Failed to update ticket: ${err.message}`);
  }
}
window.handleUpdateTicket = handleUpdateTicket;

async function handleDeleteTicket(ticketId) {
  if (!confirm(`Are you sure you want to permanently delete ticket ${ticketId}?`)) return;

  try {
    const res = await fetch(`/api/tickets/${encodeURIComponent(ticketId)}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      alert("Failed to delete ticket.");
      return;
    }
    await loadTickets(currentTicketFilter);
  } catch (err) {
    alert(`Failed to delete ticket: ${err.message}`);
  }
}
window.handleDeleteTicket = handleDeleteTicket;
window.switchCamera = switchCamera;

// Wire ticket filter buttons
$$("#ticketFilterButtons button").forEach((btn) => {
  btn.addEventListener("click", () => {
    loadTickets(btn.dataset.ticketFilter);
  });
});

const refreshTicketsBtn = $("#btnRefreshTickets");
if (refreshTicketsBtn) {
  refreshTicketsBtn.addEventListener("click", () => loadTickets(currentTicketFilter));
}

async function captureReviewedVideoFrame(video) {
  if (!video) throw new Error("The reviewed video is not available.");
  if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("The video frame did not load in time.")), 5000);
      video.addEventListener("loadeddata", () => {
        clearTimeout(timeout);
        resolve();
      }, { once: true });
      video.addEventListener("error", () => {
        clearTimeout(timeout);
        reject(new Error("The reviewed video frame could not be loaded."));
      }, { once: true });
    });
  }
  if (!video.videoWidth || !video.videoHeight) {
    throw new Error("The reviewed video has no frame to attach.");
  }
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Could not prepare the video evidence canvas.");
  context.drawImage(video, 0, 0);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
  if (!blob) throw new Error("Could not encode the reviewed video frame.");
  return new File([blob], "reviewed_video_frame.jpg", { type: "image/jpeg" });
}

function draftReviewedTicket({ hazardType, description, evidenceFile }) {
  const form = $("#ticketForm");
  const message = $("#ticketFormMsg");
  form.querySelector('[name="hazard_type"]').value = hazardType;
  form.querySelector('[name="severity"]').value = "HIGH";
  form.querySelector('[name="zone_name"]').value = "Location not recorded";
  form.querySelector('[name="description"]').value = description;

  const evidenceCheckbox = $("#attachTicketEvidence");
  const evidenceInput = $("#ticketEvidenceUpload");
  evidenceCheckbox.checked = false;
  evidenceInput.value = "";
  if (evidenceFile) {
    try {
      const transfer = new DataTransfer();
      transfer.items.add(evidenceFile);
      evidenceInput.files = transfer.files;
    } catch (error) {
      console.error("Could not stage the optional ticket evidence image:", error);
    }
  }

  showView("tickets");
  message.textContent = evidenceFile
    ? "Review draft prepared. Edit the details and explicitly opt in below if you want to attach the image."
    : "Review draft prepared. Edit the details; choose an evidence image below if you want to attach one.";
  message.style.color = "var(--ink-2)";
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

const ticketForm = $("#ticketForm");
if (ticketForm) {
  const evidenceCheckbox = $("#attachTicketEvidence");
  const evidenceInput = $("#ticketEvidenceUpload");
  const syncEvidenceInput = () => {
    evidenceInput.disabled = !evidenceCheckbox.checked;
  };
  evidenceCheckbox.addEventListener("change", syncEvidenceInput);
  syncEvidenceInput();

  ticketForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("#btnCreateTicket");
    const message = $("#ticketFormMsg");
    if (evidenceCheckbox.checked && !evidenceInput.files.length) {
      message.textContent = "Choose an evidence image, or leave the evidence option unchecked.";
      message.style.color = "var(--critical)";
      evidenceInput.focus();
      return;
    }
    button.disabled = true;
    message.textContent = "Saving incident…";
    message.style.color = "var(--ink-2)";

    try {
      const response = await fetch("/api/tickets", {
        method: "POST",
        body: new FormData(ticketForm),
      });
      if (!response.ok) throw await responseError(response);
      const result = await response.json();
      ticketForm.reset();
      syncEvidenceInput();
      message.textContent = `Incident ${result.ticket_id} saved.`;
      message.style.color = "var(--good)";
      await loadTickets("ALL");
    } catch (error) {
      message.textContent = `Could not save incident: ${error.message}`;
      message.style.color = "var(--critical)";
    } finally {
      button.disabled = false;
    }
  });
}

// ==================================================================== INIT
syncThemeIcon();          // match the sun/moon to the theme set before paint
initializeAuthentication();
lucide.createIcons();
