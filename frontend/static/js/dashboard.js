const state = {
  equipment: [],
  search: "",
  statusFilter: "",
  typeFilter: "",
};

// ---------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------

function $(sel) { return document.querySelector(sel); }
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function toast(message, isError) {
  const t = $("#toast");
  t.textContent = message;
  t.classList.toggle("error", !!isError);
  t.classList.add("show");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => t.classList.remove("show"), 3200);
}

function fmtDate(d) {
  return d ? d : "—";
}

function alertBadge(status) {
  if (!status) return "";
  const cls = { "Overdue": "badge-overdue", "Due Soon": "badge-duesoon", "On Track": "badge-ontrack" }[status];
  return `<span class="badge ${cls}">${status.toUpperCase()}</span>`;
}

function statusBadge(status) {
  const cls = status === "Active" ? "badge-active" : "badge-available";
  return `<span class="badge ${cls}">${status}</span>`;
}

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

// ---------------------------------------------------------------
// Clock
// ---------------------------------------------------------------

function tickClock() {
  const now = new Date();
  $("#clock").textContent = now.toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}
setInterval(tickClock, 1000);
tickClock();

// ---------------------------------------------------------------
// Loading + rendering
// ---------------------------------------------------------------

async function loadAll() {
  try {
    const [equipment, alerts, anomalies, fleet] = await Promise.all([
      api("/api/equipment"),
      api("/api/alerts"),
      api("/api/anomalies"),
      api("/api/fleet-summary"),
    ]);
    state.equipment = equipment;
    renderKpis(equipment, alerts.summary, anomalies.summary, fleet);
    renderAlerts(alerts.report);
    renderAnomalies(anomalies.report);
    populateTypeFilter(equipment);
    renderTable();
  } catch (err) {
    toast(err.message, true);
  }
}

function renderKpis(equipment, alertSummary, anomalySummary, fleet) {
  const active = equipment.filter(e => e.status === "Active").length;
  const available = equipment.filter(e => e.status === "Available").length;

  $("#kpi-total").textContent = equipment.length;
  $("#kpi-active").textContent = active;
  $("#kpi-available").textContent = available;
  $("#kpi-overdue").textContent = alertSummary.overdue_count;
  $("#kpi-duesoon").textContent = alertSummary.due_soon_count;
  $("#kpi-anomalies").textContent = anomalySummary.flagged_equipment_count;
  $("#kpi-utilization").textContent = (fleet.fleet_utilization_pct ?? 0) + "%";
}

function renderAlerts(report) {
  const list = $("#alerts-list");
  const urgent = report.filter(r => r.alert_status !== "On Track");
  $("#alerts-count").textContent = urgent.length;

  if (urgent.length === 0) {
    list.innerHTML = `<p class="empty-state">No active rentals need attention.</p>`;
    return;
  }

  list.innerHTML = "";
  urgent.slice(0, 12).forEach(r => {
    const row = el("div", "alert-row");
    const detail = r.alert_status === "Overdue"
      ? `${r.days_overdue} day${r.days_overdue === 1 ? "" : "s"} overdue`
      : `due in ${r.days_until_due} day${r.days_until_due === 1 ? "" : "s"}`;
    row.innerHTML = `
      <div>
        <span class="row-id">${r.equipment_id}</span>
        <div class="row-meta">${r.type} · Site ${r.site_id || "—"} · ${detail}</div>
      </div>
      ${alertBadge(r.alert_status)}
    `;
    row.addEventListener("click", () => openDetail(r.equipment_id));
    list.appendChild(row);
  });
}

function renderAnomalies(report) {
  const list = $("#anomalies-list");
  $("#anomalies-count").textContent = report.length;

  if (report.length === 0) {
    list.innerHTML = `<p class="empty-state">No anomalies detected.</p>`;
    return;
  }

  list.innerHTML = "";
  report.slice(0, 12).forEach(r => {
    const row = el("div", "anomaly-row");
    row.innerHTML = `
      <div>
        <span class="row-id">${r.equipment_id}</span>
        <div class="row-meta">${r.type} · ${r.anomalies.join(", ")}</div>
      </div>
      <span class="badge badge-anomaly">${r.anomaly_count} FLAG${r.anomaly_count === 1 ? "" : "S"}</span>
    `;
    row.addEventListener("click", () => openDetail(r.equipment_id));
    list.appendChild(row);
  });
}

function populateTypeFilter(equipment) {
  const select = $("#filter-type");
  const existing = new Set(Array.from(select.options).map(o => o.value));
  const types = [...new Set(equipment.map(e => e.type))].sort();
  types.forEach(t => {
    if (!existing.has(t)) {
      const opt = el("option", null, t);
      opt.value = t;
      select.appendChild(opt);
    }
  });
}

function renderTable() {
  const tbody = $("#equipment-tbody");
  let rows = state.equipment;

  if (state.statusFilter) rows = rows.filter(r => r.status === state.statusFilter);
  if (state.typeFilter) rows = rows.filter(r => r.type === state.typeFilter);
  if (state.search) {
    const q = state.search.toLowerCase();
    rows = rows.filter(r =>
      [r.equipment_id, r.type, r.site_id, r.last_operator_id].some(v => (v || "").toLowerCase().includes(q))
    );
  }

  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" class="empty-state">No equipment matches these filters.</td></tr>`;
    return;
  }

  tbody.innerHTML = "";
  rows.forEach(r => {
    const tr = el("tr");
    const dueOrOut = r.status === "Active" ? fmtDate(r.expected_due_date) : fmtDate(r.check_out_date);
    const flags = [
      r.alert_status && r.alert_status !== "On Track" ? alertBadge(r.alert_status) : "",
      r.anomaly_count ? `<span class="badge badge-anomaly">${r.anomaly_count} anomaly</span>` : "",
    ].filter(Boolean).join("");

    tr.innerHTML = `
      <td class="eq-id">${r.equipment_id}</td>
      <td>${r.type}</td>
      <td>${r.site_id || "—"}</td>
      <td>${statusBadge(r.status)}</td>
      <td>${fmtDate(r.check_in_date)}</td>
      <td>${dueOrOut}</td>
      <td class="num">${(r.engine_hours_day ?? 0).toFixed ? r.engine_hours_day.toFixed(1) : r.engine_hours_day}</td>
      <td class="num">${(r.idle_hours_day ?? 0).toFixed ? r.idle_hours_day.toFixed(1) : r.idle_hours_day}</td>
      <td>${r.last_operator_id || "—"}</td>
      <td class="flags-cell">${flags || '<span class="text-muted">—</span>'}</td>
      <td>${r.status === "Active"
        ? `<button class="btn btn-ghost btn-small" data-action="checkout" data-id="${r.equipment_id}">Check Out</button>`
        : `<button class="btn btn-ghost btn-small" data-action="reactivate" data-id="${r.equipment_id}">Check In</button>`}
      </td>
    `;
    tr.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      openDetail(r.equipment_id);
    });
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll('button[data-action="checkout"]').forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        await api("/api/checkout", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ equipment_id: btn.dataset.id }),
        });
        toast(`${btn.dataset.id} checked out.`);
        loadAll();
      } catch (err) {
        toast(err.message, true);
      }
    });
  });

  tbody.querySelectorAll('button[data-action="reactivate"]').forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openCheckin(btn.dataset.id);
    });
  });
}

// ---------------------------------------------------------------
// Detail drawer
// ---------------------------------------------------------------

async function openDetail(equipmentId) {
  try {
    const data = await api(`/api/equipment/${equipmentId}`);
    $("#detail-title").textContent = equipmentId;
    const eq = data.equipment;

    const grid = `
      <div class="detail-grid">
        <div><span>Type</span>${eq.type}</div>
        <div><span>Status</span>${eq.status}</div>
        <div><span>Site</span>${eq.site_id || "—"}</div>
        <div><span>Operator</span>${eq.last_operator_id || "—"}</div>
        <div><span>Check-In</span>${fmtDate(eq.check_in_date)}</div>
        <div><span>Expected Due</span>${fmtDate(eq.expected_due_date)}</div>
        <div><span>Engine hrs/day</span>${eq.engine_hours_day}</div>
        <div><span>Idle hrs/day</span>${eq.idle_hours_day}</div>
      </div>`;

    const anomalies = data.anomalies.anomalies.length
      ? `<div class="detail-section"><h3>Anomalies</h3>${data.anomalies.anomalies.map(a => `<span class="badge badge-anomaly">${a}</span>`).join(" ")}</div>`
      : "";

    const history = data.checkinout_history.length
      ? `<div class="detail-section"><h3>Check-In / Check-Out History</h3><div class="history-list">${
          data.checkinout_history.map(h => `<div><span>${h.action}</span><span>${h.event_date}</span></div>`).join("")
        }</div></div>`
      : "";

    const usage = data.usage_history.length
      ? `<div class="detail-section"><h3>Recent Usage Log</h3><div class="history-list">${
          data.usage_history.slice(-7).reverse().map(u => `<div><span>${u.log_date}</span><span>engine ${u.engine_hours}h · idle ${u.idle_hours}h</span></div>`).join("")
        }</div></div>`
      : "";

    $("#detail-body").innerHTML = grid + anomalies + history + usage;
    $("#detail-backdrop").classList.add("open");
  } catch (err) {
    toast(err.message, true);
  }
}

$("#detail-close").addEventListener("click", () => $("#detail-backdrop").classList.remove("open"));
$("#detail-backdrop").addEventListener("click", (e) => {
  if (e.target === $("#detail-backdrop")) $("#detail-backdrop").classList.remove("open");
});

// ---------------------------------------------------------------
// Check-in modal
// ---------------------------------------------------------------

function openCheckin(prefillId) {
  $("#checkin-error").textContent = "";
  $("#checkin-form").reset();
  if (prefillId) $("#checkin-form").equipment_id.value = prefillId;
  $("#checkin-backdrop").classList.add("open");
}

$("#btn-checkin").addEventListener("click", () => openCheckin());
$("#checkin-close").addEventListener("click", () => $("#checkin-backdrop").classList.remove("open"));
$("#checkin-cancel").addEventListener("click", () => $("#checkin-backdrop").classList.remove("open"));
$("#checkin-backdrop").addEventListener("click", (e) => {
  if (e.target === $("#checkin-backdrop")) $("#checkin-backdrop").classList.remove("open");
});

$("#checkin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const body = {
    equipment_id: form.equipment_id.value.trim(),
    equipment_type: form.equipment_type.value || undefined,
    site_id: form.site_id.value.trim(),
    operator_id: form.operator_id.value.trim(),
    planned_rental_days: form.planned_rental_days.value,
  };
  try {
    await api("/api/checkin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("#checkin-backdrop").classList.remove("open");
    toast(`${body.equipment_id} checked in.`);
    loadAll();
  } catch (err) {
    $("#checkin-error").textContent = err.message;
  }
});

// ---------------------------------------------------------------
// Simulate day + filters
// ---------------------------------------------------------------

$("#btn-simulate").addEventListener("click", async () => {
  try {
    const result = await api("/api/simulate-day", { method: "POST" });
    toast(`Simulated a day of usage for ${result.count} active asset${result.count === 1 ? "" : "s"}.`);
    loadAll();
  } catch (err) {
    toast(err.message, true);
  }
});

$("#search-input").addEventListener("input", (e) => {
  state.search = e.target.value;
  renderTable();
});
$("#filter-status").addEventListener("change", (e) => {
  state.statusFilter = e.target.value;
  renderTable();
});
$("#filter-type").addEventListener("change", (e) => {
  state.typeFilter = e.target.value;
  renderTable();
});

// ---------------------------------------------------------------
// Init
// ---------------------------------------------------------------

loadAll();
setInterval(loadAll, 30000); // keep badges fresh without a manual refresh
