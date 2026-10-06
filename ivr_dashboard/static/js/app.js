const state = { period: "all", start: null, end: null, fullStart: null, fullEnd: null, charts: {} };

const COLORS = {
  accent: "#7c5cff", accent2: "#22d3d3", accent3: "#ff5fa2",
  warn: "#ffb547", danger: "#ff5f7a", grid: "rgba(255,255,255,0.06)",
  text: "#9691b8",
};
const PALETTE = [COLORS.accent, COLORS.accent2, COLORS.accent3, COLORS.warn, COLORS.danger, "#5fa8ff"];

Chart.defaults.color = COLORS.text;
Chart.defaults.font.family = "Inter, sans-serif";
Chart.defaults.borderColor = COLORS.grid;

// ---------- Upload ----------
const uploadBtn = document.getElementById("uploadBtn");
const csvInput = document.getElementById("csvInput");
const uploadStatus = document.getElementById("uploadStatus");

uploadBtn.addEventListener("click", () => csvInput.click());
csvInput.addEventListener("change", async () => {
  const file = csvInput.files[0];
  if (!file) return;
  uploadStatus.textContent = "Uploading…";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!data.ok) {
      uploadStatus.textContent = "Error: " + data.error;
      uploadStatus.style.color = "#ff5f7a";
      return;
    }
    uploadStatus.textContent = `${data.filename} · ${data.rows.toLocaleString()} rows`;
    uploadStatus.style.color = "";
    document.getElementById("datasetInfo").textContent = `${data.filename}\n${data.rows.toLocaleString()} rows`;
    state.fullStart = data.start_date;
    state.fullEnd = data.end_date;
    const s = document.getElementById("startDate"), e = document.getElementById("endDate");
    s.min = data.start_date; s.max = data.end_date; s.value = data.start_date;
    e.min = data.start_date; e.max = data.end_date; e.value = data.end_date;
    document.getElementById("emptyState").style.display = "none";
    document.getElementById("dashboardContent").style.display = "block";
    setPeriod("all");
  } catch (err) {
    uploadStatus.textContent = "Upload failed.";
  }
});

// ---------- Period controls ----------
document.querySelectorAll(".seg-btn").forEach(btn => {
  btn.addEventListener("click", () => setPeriod(btn.dataset.period));
});
document.getElementById("applyBtn").addEventListener("click", () => loadDashboard());

function setPeriod(period) {
  state.period = period;
  document.querySelectorAll(".seg-btn").forEach(b => b.classList.toggle("active", b.dataset.period === period));
  const s = document.getElementById("startDate"), e = document.getElementById("endDate");

  if (!state.fullEnd) return;
  const end = new Date(state.fullEnd);
  let start = new Date(state.fullStart);
  const custom = period === "custom";
  s.disabled = !custom; e.disabled = !custom;

  if (period === "all") { start = new Date(state.fullStart); }
  else if (period === "day") { start = new Date(end); }
  else if (period === "week") { start = new Date(end); start.setDate(start.getDate() - 6); }
  else if (period === "month") { start = new Date(end); start.setDate(start.getDate() - 29); }

  if (period !== "custom") {
    const clamped = start < new Date(state.fullStart) ? new Date(state.fullStart) : start;
    s.value = fmt(clamped);
    e.value = state.fullEnd;
  }
  loadDashboard();
}

function fmt(d) { return d.toISOString().slice(0, 10); }

// ---------- Nav tabs ----------
document.querySelectorAll(".nav-item").forEach(item => {
  item.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach(i => i.classList.remove("active"));
    item.classList.add("active");
    const view = item.dataset.view;
    document.querySelectorAll(".view").forEach(v => v.style.display = v.dataset.view === view ? "block" : "none");
  });
});

// ---------- Load + render ----------
async function loadDashboard() {
  const start = document.getElementById("startDate").value;
  const end = document.getElementById("endDate").value;
  const params = new URLSearchParams();
  if (start) params.set("start", start);
  if (end) params.set("end", end);

  const res = await fetch("/api/dashboard?" + params.toString());
  const data = await res.json();
  if (!data.ok) return;
  renderKPIs(data.kpis);
  renderDialStatus(data.dial_status);
  renderDailyTrend(data.daily_trend);
  renderWeekday(data.weekday_volume);
  renderHourly(data.hourly_dist);
  renderDurationHour(data.duration_by_hour);
  renderDurationHist(data.duration_hist);
  renderDtmfEngagement(data.dtmf_engagement);
  renderKeypad(data.keypad);
  renderTalkTime(data.talk_time_buckets);
  renderFailures(data.failure_reasons);
  renderInsights(data.insights);
}

function renderKPIs(k) {
  const row = document.getElementById("kpiRow");
  row.innerHTML = `
    ${kpiCard("Total Attempts", k.total_attempts.toLocaleString())}
    ${kpiCard("Connected Calls", k.connected.toLocaleString(), `${k.connect_rate}% connect rate`, "good")}
    ${kpiCard("Failed Attempts", k.failed.toLocaleString(), `${k.failure_rate}% failure rate`, "bad")}
    ${kpiCard("DTMF Responses", k.dtmf_responses.toLocaleString(), `${k.dtmf_rate_of_connected}% of connected`)}
    ${kpiCard("Avg Duration", k.avg_duration + "s", `median ${k.median_duration}s`)}
  `;
}
function kpiCard(label, value, sub, cls) {
  return `<div class="kpi-card">
    <div class="kpi-label">${label}</div>
    <div class="kpi-value">${value}</div>
    ${sub ? `<div class="kpi-sub ${cls || ""}">${sub}</div>` : ""}
  </div>`;
}

function destroy(id) { if (state.charts[id]) { state.charts[id].destroy(); } }

function renderDialStatus(rows) {
  destroy("dialStatus");
  const ctx = document.getElementById("chartDialStatus");
  state.charts.dialStatus = new Chart(ctx, {
    type: "doughnut",
    data: { labels: rows.map(r => r.label), datasets: [{ data: rows.map(r => r.value), backgroundColor: PALETTE, borderWidth: 0 }] },
    options: { plugins: { legend: { position: "bottom", labels: { boxWidth: 10, padding: 14, font: { size: 11 } } } } }
  });
}

function renderDailyTrend(rows) {
  destroy("dailyTrend");
  const ctx = document.getElementById("chartDailyTrend");
  state.charts.dailyTrend = new Chart(ctx, {
    type: "line",
    data: {
      labels: rows.map(r => r.date),
      datasets: [
        { label: "Total Attempts", data: rows.map(r => r.attempts), borderColor: COLORS.accent, backgroundColor: "transparent", tension: .35 },
        { label: "Connected", data: rows.map(r => r.connected), borderColor: COLORS.accent2, backgroundColor: "transparent", tension: .35 },
        { label: "DTMF Responses", data: rows.map(r => r.dtmf), borderColor: COLORS.accent3, backgroundColor: "transparent", tension: .35 },
      ]
    },
    options: baseLineOpts()
  });
}

function renderWeekday(rows) {
  destroy("weekday");
  const ctx = document.getElementById("chartWeekday");
  state.charts.weekday = new Chart(ctx, {
    type: "bar",
    data: {
      labels: rows.map(r => r.day),
      datasets: [
        { label: "Total Attempts", data: rows.map(r => r.attempts), backgroundColor: COLORS.accent, borderRadius: 6 },
        { label: "Connected", data: rows.map(r => r.connected), backgroundColor: COLORS.accent2, borderRadius: 6 },
      ]
    },
    options: baseBarOpts()
  });
}

function renderHourly(rows) {
  destroy("hourly");
  const ctx = document.getElementById("chartHourly");
  state.charts.hourly = new Chart(ctx, {
    type: "bar",
    data: {
      labels: rows.map(r => r.hour + ":00"),
      datasets: [
        { label: "Total Dial Attempts", data: rows.map(r => r.attempts), backgroundColor: COLORS.accent, borderRadius: 5 },
        { label: "Connected Calls", data: rows.map(r => r.connected), backgroundColor: COLORS.accent2, borderRadius: 5 },
      ]
    },
    options: baseBarOpts()
  });
}

function renderDurationHour(rows) {
  destroy("durHour");
  const ctx = document.getElementById("chartDurationHour");
  state.charts.durHour = new Chart(ctx, {
    data: {
      labels: rows.map(r => r.hour + ":00"),
      datasets: [
        { type: "bar", label: "Connected Calls", data: rows.map(r => r.connected), backgroundColor: "rgba(124,92,255,0.25)", yAxisID: "y1", borderRadius: 4 },
        { type: "line", label: "Mean Duration (s)", data: rows.map(r => r.mean), borderColor: COLORS.accent2, tension: .3, yAxisID: "y" },
        { type: "line", label: "Median Duration (s)", data: rows.map(r => r.median), borderColor: COLORS.accent3, tension: .3, yAxisID: "y" },
      ]
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } },
      scales: {
        x: { grid: { color: COLORS.grid } },
        y: { position: "left", title: { display: true, text: "Seconds" }, grid: { color: COLORS.grid } },
        y1: { position: "right", title: { display: true, text: "Calls" }, grid: { display: false } },
      }
    }
  });
}

function renderDurationHist(rows) {
  destroy("durHist");
  const ctx = document.getElementById("chartDurationHist");
  state.charts.durHist = new Chart(ctx, {
    type: "bar",
    data: { labels: rows.map(r => r.bucket), datasets: [{ label: "Calls", data: rows.map(r => r.count), backgroundColor: COLORS.accent2, borderRadius: 4 }] },
    options: baseBarOpts(true)
  });
}

function renderDtmfEngagement(rows) {
  destroy("dtmfEng");
  const ctx = document.getElementById("chartDtmfEngagement");
  state.charts.dtmfEng = new Chart(ctx, {
    type: "doughnut",
    data: { labels: rows.map(r => r.label), datasets: [{ data: rows.map(r => r.value), backgroundColor: [COLORS.accent3, COLORS.accent], borderWidth: 0 }] },
    options: { plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } } }
  });
}

function renderKeypad(rows) {
  destroy("keypad");
  const ctx = document.getElementById("chartKeypad");
  state.charts.keypad = new Chart(ctx, {
    type: "bar",
    data: { labels: rows.map(r => "Key " + r.key), datasets: [{ label: "Presses", data: rows.map(r => r.count), backgroundColor: COLORS.accent, borderRadius: 6 }] },
    options: baseBarOpts(true)
  });
}

function renderTalkTime(rows) {
  destroy("talkTime");
  const ctx = document.getElementById("chartTalkTime");
  state.charts.talkTime = new Chart(ctx, {
    type: "bar",
    data: { labels: rows.map(r => r.bucket), datasets: [{ label: "Calls", data: rows.map(r => r.count), backgroundColor: COLORS.accent3, borderRadius: 6 }] },
    options: baseBarOpts(true)
  });
}

function renderFailures(rows) {
  destroy("failures");
  const ctx = document.getElementById("chartFailures");
  state.charts.failures = new Chart(ctx, {
    type: "bar",
    data: { labels: rows.map(r => r.reason), datasets: [{ label: "Attempts", data: rows.map(r => r.count), backgroundColor: PALETTE, borderRadius: 6 }] },
    options: baseBarOpts(true)
  });
}

function renderInsights(rows) {
  const el = document.getElementById("insightList");
  el.innerHTML = rows.map(r => `
    <div class="insight-card">
      <span class="insight-tag">${r.tag}</span>
      <h4>${r.title}</h4>
      <p>${r.text}</p>
    </div>
  `).join("");
}

function baseLineOpts() {
  return {
    responsive: true,
    plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } },
    scales: { x: { grid: { color: COLORS.grid } }, y: { grid: { color: COLORS.grid } } }
  };
}
function baseBarOpts(noLegend) {
  return {
    responsive: true,
    plugins: { legend: { display: !noLegend, position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } },
    scales: { x: { grid: { display: false } }, y: { grid: { color: COLORS.grid } } }
  };
}

// ---------- init: check if data already loaded on server ----------
(async function init() {
  const res = await fetch("/api/status");
  const data = await res.json();
  if (data.loaded) {
    uploadStatus.textContent = `${data.filename} · ${data.rows.toLocaleString()} rows`;
    document.getElementById("datasetInfo").textContent = `${data.filename}\n${data.rows.toLocaleString()} rows`;
    state.fullStart = data.start_date;
    state.fullEnd = data.end_date;
    const s = document.getElementById("startDate"), e = document.getElementById("endDate");
    s.min = data.start_date; s.max = data.end_date; s.value = data.start_date;
    e.min = data.start_date; e.max = data.end_date; e.value = data.end_date;
    document.getElementById("emptyState").style.display = "none";
    document.getElementById("dashboardContent").style.display = "block";
    setPeriod("all");
  }
})();
