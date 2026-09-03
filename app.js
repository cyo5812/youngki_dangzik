// 영기팀 당직 일정표
// 데이터: data/schedule.json (weekendDuty: 토요일/휴일 3팀 순환 당직, weekdayDuty: 영기팀 평일 개별 당직)

const DATA_URL = "data/schedule.json";
const LS_SETTINGS_KEY = "dangzik.github.settings";

const ORG_LIST = ["영기", "소강", "도강", "전산휴무"];
const DOW_LABELS = ["일", "월", "화", "수", "목", "금", "토"];

let schedule = null;
let weekendByDate = new Map();
let weekdayByDate = new Map();
let editMode = false;
let dirty = false;
let viewYear, viewMonth; // viewMonth: 0-11
let currentEditDate = null;

const $ = (sel) => document.querySelector(sel);

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
function pad(n) { return String(n).padStart(2, "0"); }

function rebuildIndices() {
  weekendByDate = new Map(schedule.weekendDuty.map((e) => [e.date, e]));
  weekdayByDate = new Map(schedule.weekdayDuty.map((e) => [e.date, e]));
}

async function loadData() {
  const res = await fetch(DATA_URL, { cache: "no-store" });
  if (!res.ok) throw new Error("일정 데이터를 불러오지 못했습니다.");
  schedule = await res.json();
  rebuildIndices();
}

function setDirty(v) {
  dirty = v;
  $("#dirty-badge").hidden = !v;
}

function showToast(msg, type = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (type ? " " + type : "");
  t.hidden = false;
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => { t.hidden = true; }, 3500);
}

// ---------- Calendar rendering ----------

function renderMonthLabel() {
  $("#month-label").textContent = `${viewYear}년 ${viewMonth + 1}월`;
}

function renderCalendar() {
  renderMonthLabel();
  const cal = $("#calendar");
  cal.innerHTML = "";

  DOW_LABELS.forEach((label, i) => {
    const el = document.createElement("div");
    el.className = "cal-dow" + (i === 0 ? " sun" : i === 6 ? " sat" : "");
    el.textContent = label;
    cal.appendChild(el);
  });

  const firstOfMonth = new Date(viewYear, viewMonth, 1);
  const startWeekday = firstOfMonth.getDay(); // 0=Sun
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
  const daysInPrevMonth = new Date(viewYear, viewMonth, 0).getDate();

  const totalCells = Math.ceil((startWeekday + daysInMonth) / 7) * 7;
  const today = todayStr();

  for (let i = 0; i < totalCells; i++) {
    const dayNum = i - startWeekday + 1;
    let cellDate, outside = false;
    if (dayNum < 1) {
      cellDate = new Date(viewYear, viewMonth - 1, daysInPrevMonth + dayNum);
      outside = true;
    } else if (dayNum > daysInMonth) {
      cellDate = new Date(viewYear, viewMonth + 1, dayNum - daysInMonth);
      outside = true;
    } else {
      cellDate = new Date(viewYear, viewMonth, dayNum);
    }
    const dateStr = `${cellDate.getFullYear()}-${pad(cellDate.getMonth() + 1)}-${pad(cellDate.getDate())}`;
    const dow = cellDate.getDay();

    const cell = document.createElement("div");
    cell.className = "cal-cell" + (outside ? " outside" : "") + (editMode && !outside ? " editable" : "");
    if (dateStr === today) cell.classList.add("is-today");
    cell.dataset.date = dateStr;

    const dateEl = document.createElement("div");
    dateEl.className = "cal-date" + (dow === 0 ? " sun" : dow === 6 ? " sat" : "");
    dateEl.textContent = cellDate.getDate();
    cell.appendChild(dateEl);

    const we = weekendByDate.get(dateStr);
    const wd = weekdayByDate.get(dateStr);

    if (we && we.org) {
      const tag = document.createElement("div");
      tag.className = "cal-tag org-" + we.org;
      tag.textContent = we.org + (we.org === "영기" && we.person ? ` · ${we.person}` : "");
      cell.appendChild(tag);
      if (we.note) {
        const note = document.createElement("div");
        note.className = "cal-note";
        note.textContent = we.note;
        cell.appendChild(note);
      }
    } else if (wd && wd.type === "평일" && wd.person) {
      const tag = document.createElement("div");
      tag.className = "cal-tag weekday-person";
      tag.textContent = wd.person;
      cell.appendChild(tag);
    }

    if (!outside && editMode) {
      cell.addEventListener("click", () => openDayModal(dateStr));
    }

    cal.appendChild(cell);
  }

  renderUpcoming();
}

function renderUpcoming() {
  const list = $("#upcoming-list");
  list.innerHTML = "";
  const today = todayStr();

  const items = [];
  for (const e of schedule.weekendDuty) {
    if (e.date >= today && e.org) {
      const label = e.org === "영기" && e.person ? `${e.org} · ${e.person}` : e.org;
      items.push({ date: e.date, weekday: e.weekday, label, note: e.note });
    }
  }
  for (const e of schedule.weekdayDuty) {
    if (e.date >= today && e.type === "평일" && e.person) {
      items.push({ date: e.date, weekday: e.weekday, label: `평일 · ${e.person}`, note: "" });
    }
  }
  items.sort((a, b) => a.date.localeCompare(b.date));
  const upcoming = items.slice(0, 10);

  if (upcoming.length === 0) {
    list.innerHTML = `<div class="upcoming-empty">예정된 당직이 없습니다.</div>`;
    return;
  }

  for (const it of upcoming) {
    const row = document.createElement("div");
    row.className = "upcoming-row";
    row.innerHTML = `<span class="upcoming-date">${it.date} (${it.weekday})</span><span>${it.label}</span>${it.note ? `<span class="cal-note">${it.note}</span>` : ""}`;
    list.appendChild(row);
  }
}

// ---------- Day edit modal ----------

function openDayModal(dateStr) {
  currentEditDate = dateStr;
  const we = weekendByDate.get(dateStr);
  const wd = weekdayByDate.get(dateStr);
  const d = new Date(dateStr + "T00:00:00");
  const dow = DOW_LABELS[d.getDay()];

  $("#day-modal-title").textContent = `${dateStr} (${dow}) 당직 수정`;

  const isHoliday = !!(we && we.org) || (wd && wd.type === "휴일");
  $("#f-type").value = isHoliday ? "휴일" : "평일";
  $("#f-org").value = we ? (we.org || "") : "";
  $("#f-weekend-person").value = we ? (we.person || "") : "";
  $("#f-note").value = we ? (we.note || "") : "";
  $("#f-weekday-person").value = wd ? (wd.person || "") : "";

  toggleModalFields();
  $("#day-modal").showModal();
}

function toggleModalFields() {
  const isHoliday = $("#f-type").value === "휴일";
  $("#weekend-fields").style.display = isHoliday ? "" : "none";
  $("#weekday-fields").style.display = isHoliday ? "none" : "";
}

function applyDayEdit() {
  const dateStr = currentEditDate;
  const d = new Date(dateStr + "T00:00:00");
  const weekday = DOW_LABELS[d.getDay()];
  const type = $("#f-type").value;

  if (type === "휴일") {
    const org = $("#f-org").value;
    const person = $("#f-weekend-person").value.trim();
    const note = $("#f-note").value.trim();
    if (org) {
      const existing = weekendByDate.get(dateStr);
      const entry = { date: dateStr, weekday, type: "휴일", org, person: org === "영기" ? person : "", note };
      if (existing) {
        Object.assign(existing, entry);
      } else {
        schedule.weekendDuty.push(entry);
        schedule.weekendDuty.sort((a, b) => a.date.localeCompare(b.date));
      }
    } else if (weekendByDate.has(dateStr)) {
      schedule.weekendDuty = schedule.weekendDuty.filter((e) => e.date !== dateStr);
    }
    // clear weekday entry's person for this date (holiday = no individual weekday duty)
    const wdExisting = weekdayByDate.get(dateStr);
    if (wdExisting) {
      wdExisting.type = "휴일";
      wdExisting.person = "";
    } else {
      schedule.weekdayDuty.push({ date: dateStr, weekday, type: "휴일", person: "" });
      schedule.weekdayDuty.sort((a, b) => a.date.localeCompare(b.date));
    }
  } else {
    // 평일
    if (weekendByDate.has(dateStr)) {
      schedule.weekendDuty = schedule.weekendDuty.filter((e) => e.date !== dateStr);
    }
    const person = $("#f-weekday-person").value.trim();
    const wdExisting = weekdayByDate.get(dateStr);
    if (wdExisting) {
      wdExisting.type = "평일";
      wdExisting.person = person;
    } else {
      schedule.weekdayDuty.push({ date: dateStr, weekday, type: "평일", person });
      schedule.weekdayDuty.sort((a, b) => a.date.localeCompare(b.date));
    }
  }

  rebuildIndices();
  setDirty(true);
  $("#day-modal").close();
  renderCalendar();
}

function clearDay() {
  const dateStr = currentEditDate;
  schedule.weekendDuty = schedule.weekendDuty.filter((e) => e.date !== dateStr);
  schedule.weekdayDuty = schedule.weekdayDuty.filter((e) => e.date !== dateStr);
  rebuildIndices();
  setDirty(true);
  $("#day-modal").close();
  renderCalendar();
}

// ---------- Edit mode toggle ----------

function toggleEditMode() {
  editMode = !editMode;
  $("#btn-edit-toggle").textContent = editMode ? "수정 모드 끄기" : "수정 모드";
  $("#btn-edit-toggle").classList.toggle("btn-primary", editMode);
  $("#btn-save").hidden = !editMode;
  renderCalendar();
}

// ---------- Settings (GitHub connection) ----------

function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(LS_SETTINGS_KEY) || "{}");
  } catch {
    return {};
  }
}
function saveSettings(s) {
  localStorage.setItem(LS_SETTINGS_KEY, JSON.stringify(s));
}

function openSettingsModal() {
  const s = loadSettings();
  $("#s-owner").value = s.owner || "";
  $("#s-repo").value = s.repo || "";
  $("#s-branch").value = s.branch || "main";
  $("#s-token").value = s.token || "";
  $("#settings-modal").showModal();
}

function saveSettingsFromModal() {
  const s = {
    owner: $("#s-owner").value.trim(),
    repo: $("#s-repo").value.trim(),
    branch: $("#s-branch").value.trim() || "main",
    token: $("#s-token").value.trim(),
  };
  saveSettings(s);
  $("#settings-modal").close();
  showToast("GitHub 연결 설정을 저장했습니다.", "success");
}

// ---------- Save to GitHub ----------

function utf8ToBase64(str) {
  return btoa(unescape(encodeURIComponent(str)));
}

async function saveToGitHub() {
  const s = loadSettings();
  if (!s.owner || !s.repo || !s.token) {
    showToast("먼저 ⚙ 설정에서 GitHub 저장소 정보와 토큰을 입력하세요.", "error");
    openSettingsModal();
    return;
  }

  const api = `https://api.github.com/repos/${s.owner}/${s.repo}/contents/data/schedule.json`;
  const headers = {
    Authorization: `Bearer ${s.token}`,
    Accept: "application/vnd.github+json",
  };

  $("#btn-save").disabled = true;
  $("#btn-save").textContent = "저장 중...";

  try {
    const getRes = await fetch(`${api}?ref=${encodeURIComponent(s.branch)}`, { headers });
    if (!getRes.ok) {
      throw new Error(`파일 정보를 가져오지 못했습니다 (${getRes.status}). 저장소/브랜치/토큰 권한을 확인하세요.`);
    }
    const getData = await getRes.json();
    const sha = getData.sha;

    schedule.meta = schedule.meta || {};
    schedule.meta.updatedAt = new Date().toISOString();

    const content = utf8ToBase64(JSON.stringify(schedule, null, 2));

    const putRes = await fetch(api, {
      method: "PUT",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({
        message: `당직 일정 수정 (웹에서, ${new Date().toLocaleString("ko-KR")})`,
        content,
        sha,
        branch: s.branch,
      }),
    });

    if (!putRes.ok) {
      const errBody = await putRes.json().catch(() => ({}));
      throw new Error(`저장 실패 (${putRes.status}): ${errBody.message || "알 수 없는 오류"}`);
    }

    setDirty(false);
    showToast("GitHub에 저장했습니다. 배포까지 1분 정도 걸릴 수 있습니다.", "success");
  } catch (err) {
    console.error(err);
    showToast(err.message || "저장 중 오류가 발생했습니다.", "error");
  } finally {
    $("#btn-save").disabled = false;
    $("#btn-save").textContent = "GitHub에 저장";
  }
}

// ---------- Init ----------

function initMonthFromToday() {
  const d = new Date();
  viewYear = d.getFullYear();
  viewMonth = d.getMonth();
}

function wireEvents() {
  $("#btn-prev").addEventListener("click", () => {
    viewMonth -= 1;
    if (viewMonth < 0) { viewMonth = 11; viewYear -= 1; }
    renderCalendar();
  });
  $("#btn-next").addEventListener("click", () => {
    viewMonth += 1;
    if (viewMonth > 11) { viewMonth = 0; viewYear += 1; }
    renderCalendar();
  });
  $("#btn-today").addEventListener("click", () => {
    initMonthFromToday();
    renderCalendar();
  });
  $("#btn-edit-toggle").addEventListener("click", toggleEditMode);
  $("#btn-save").addEventListener("click", saveToGitHub);
  $("#btn-settings").addEventListener("click", openSettingsModal);
  $("#settings-modal").addEventListener("click", (e) => { if (e.target.id === "settings-modal") e.target.close(); });
  $("#day-modal").addEventListener("click", (e) => { if (e.target.id === "day-modal") e.target.close(); });

  $("#f-type").addEventListener("change", toggleModalFields);
  $("#btn-day-save").addEventListener("click", applyDayEdit);
  $("#btn-day-cancel").addEventListener("click", () => $("#day-modal").close());
  $("#btn-day-delete").addEventListener("click", clearDay);
  $("#btn-settings-save").addEventListener("click", saveSettingsFromModal);
  $("#btn-settings-cancel").addEventListener("click", () => $("#settings-modal").close());

  window.addEventListener("beforeunload", (e) => {
    if (dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
}

async function init() {
  initMonthFromToday();
  wireEvents();
  try {
    await loadData();
    renderCalendar();
  } catch (err) {
    console.error(err);
    showToast(err.message, "error");
  }
}

init();
