// 당직 일정표 — 화면 로직
//
// 서버가 유일한 진실이다. 화면은 서버가 저장을 확인해 돌려준 값만 반영한다(낙관적 업데이트 없음).
// 저장된 줄 알았는데 DB에는 안 들어간 상태를 만들지 않기 위해서다.
//
// 값 출력은 전부 textContent 로만 한다. 사용자 입력을 innerHTML 에 넣지 않는다(XSS 방어).

"use strict";

const DOW_LABELS = ["일", "월", "화", "수", "목", "금", "토"];
const ORG_CLASSES = ["영기", "소강", "도강", "전산휴무"];
const KEY_STORAGE = "duty.adminKey"; // sessionStorage — 탭을 닫으면 사라진다

const state = {
  byDate: new Map(), // "2026-09-05" → { date, weekday, type, org, person, note }
  viewYear: 0,
  viewMonth: 0, // 0-11
  editMode: false,
  adminLockEnabled: false,
  editingDate: null,
  updatedAt: null,
};

const $ = (sel) => document.querySelector(sel);
const pad = (n) => String(n).padStart(2, "0");
const isoOf = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const todayStr = () => isoOf(new Date());

// ---------------------------------------------------------------- 통신

/** 관리자 키를 헤더에 실어 보낸다. 키는 이 탭의 sessionStorage 에만 있다. */
function authHeaders() {
  const key = sessionStorage.getItem(KEY_STORAGE);
  return key ? { "X-Admin-Key": key } : {};
}

/**
 * API 호출 공통 처리.
 * 서버는 오류를 RFC 7807(problem+json)로 준다. detail 을 그대로 사용자에게 보여준다.
 */
async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { ...(options.headers || {}), ...authHeaders() },
    cache: "no-store",
  });

  if (res.status === 204) return null;

  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }

  if (!res.ok) {
    const detail = (body && body.detail) || "요청을 처리하지 못했습니다.";
    const error = new Error(detail);
    error.status = res.status;
    throw error;
  }
  return body;
}

// ---------------------------------------------------------------- 상태

function rebuildState(payload) {
  state.byDate = new Map();
  for (const list of [payload.weekendDuty, payload.weekdayDuty]) {
    for (const item of list) state.byDate.set(item.date, item);
  }
  state.updatedAt = payload.meta ? payload.meta.updatedAt : null;
}

async function loadSchedule() {
  const payload = await api("/api/schedule");
  rebuildState(payload);
  render();
}

// ---------------------------------------------------------------- 렌더

function render() {
  $("#month-label").textContent = `${state.viewYear}년 ${state.viewMonth + 1}월`;
  renderUpdatedNote();
  renderCalendar();
  renderMobileCalendar();
  renderUpcoming();
}

function renderUpdatedNote() {
  const note = $("#updated-note");
  if (!state.updatedAt) {
    note.textContent = "색상으로 당직 조직과 휴무 일정을 구분합니다.";
    return;
  }
  const when = new Date(state.updatedAt);
  note.textContent = `최종 수정 ${when.getFullYear()}. ${when.getMonth() + 1}. ${when.getDate()}. ${pad(when.getHours())}:${pad(when.getMinutes())}`;
}

/** 한 칸에 들어갈 당직 표시를 만든다. 없으면 null. */
function dutyNodes(entry) {
  if (!entry) return null;
  const nodes = [];

  if (entry.type === "휴일" && entry.org) {
    const tag = document.createElement("div");
    tag.className = `cal-tag org-${ORG_CLASSES.includes(entry.org) ? entry.org : "전산휴무"}`;
    tag.textContent = entry.person ? `${entry.org} · ${entry.person}` : entry.org;
    nodes.push(tag);
  } else if (entry.type === "평일" && entry.person) {
    const tag = document.createElement("div");
    tag.className = "cal-tag weekday-person";
    tag.textContent = entry.person;
    nodes.push(tag);
  }

  if (entry.note) {
    const note = document.createElement("div");
    note.className = "cal-note";
    note.textContent = entry.note;
    nodes.push(note);
  }
  return nodes.length ? nodes : null;
}

function renderCalendar() {
  const cal = $("#calendar");
  cal.replaceChildren();

  DOW_LABELS.forEach((label, i) => {
    const el = document.createElement("div");
    el.className = "cal-dow" + (i === 0 ? " sun" : i === 6 ? " sat" : "");
    el.textContent = label;
    cal.appendChild(el);
  });

  const first = new Date(state.viewYear, state.viewMonth, 1);
  const startWeekday = first.getDay();
  const daysInMonth = new Date(state.viewYear, state.viewMonth + 1, 0).getDate();
  const total = Math.ceil((startWeekday + daysInMonth) / 7) * 7;
  const today = todayStr();

  for (let i = 0; i < total; i++) {
    const dayNum = i - startWeekday + 1;
    const cellDate = new Date(state.viewYear, state.viewMonth, dayNum);
    const outside = dayNum < 1 || dayNum > daysInMonth;
    const dateStr = isoOf(cellDate);

    const cell = document.createElement("div");
    cell.className =
      "cal-cell" + (outside ? " outside" : "") + (state.editMode && !outside ? " editable" : "");
    if (dateStr === today) cell.classList.add("is-today");

    const dateEl = document.createElement("div");
    const dow = cellDate.getDay();
    dateEl.className = "cal-date" + (dow === 0 ? " sun" : dow === 6 ? " sat" : "");
    dateEl.textContent = String(cellDate.getDate());
    cell.appendChild(dateEl);

    const nodes = dutyNodes(state.byDate.get(dateStr));
    if (nodes) cell.append(...nodes);

    if (!outside && state.editMode) {
      cell.addEventListener("click", () => openDayModal(dateStr));
    }
    cal.appendChild(cell);
  }
}

function renderMobileCalendar() {
  const list = $("#mobile-calendar");
  list.replaceChildren();

  const daysInMonth = new Date(state.viewYear, state.viewMonth + 1, 0).getDate();
  const today = todayStr();

  for (let day = 1; day <= daysInMonth; day++) {
    const cellDate = new Date(state.viewYear, state.viewMonth, day);
    const dateStr = isoOf(cellDate);
    const dow = cellDate.getDay();

    const row = document.createElement("div");
    row.className =
      "mobile-day" + (dateStr === today ? " is-today" : "") + (state.editMode ? " editable" : "");
    if (state.editMode) row.addEventListener("click", () => openDayModal(dateStr));

    const dateEl = document.createElement("div");
    dateEl.className = "mobile-date" + (dow === 0 ? " sun" : dow === 6 ? " sat" : "");
    const strong = document.createElement("strong");
    strong.textContent = String(day);
    const span = document.createElement("span");
    span.textContent = DOW_LABELS[dow];
    dateEl.append(strong, span);

    const duty = document.createElement("div");
    duty.className = "mobile-duty";
    const nodes = dutyNodes(state.byDate.get(dateStr));
    if (nodes) {
      duty.append(...nodes);
    } else {
      const empty = document.createElement("span");
      empty.className = "mobile-empty";
      empty.textContent = "일정 없음";
      duty.appendChild(empty);
    }

    row.append(dateEl, duty);
    list.appendChild(row);
  }
}

function renderUpcoming() {
  const list = $("#upcoming-list");
  list.replaceChildren();
  const today = todayStr();

  const items = [...state.byDate.values()]
    .filter((e) => e.date >= today && (e.org || e.person))
    .sort((a, b) => a.date.localeCompare(b.date))
    .slice(0, 10);

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "upcoming-empty";
    empty.textContent = "예정된 당직이 없습니다.";
    list.appendChild(empty);
    return;
  }

  for (const item of items) {
    const row = document.createElement("div");
    row.className = "upcoming-row";

    const date = document.createElement("span");
    date.className = "upcoming-date";
    date.textContent = `${item.date} (${item.weekday})`;

    const label = document.createElement("span");
    if (item.type === "휴일") {
      label.textContent = item.person ? `${item.org} · ${item.person}` : item.org;
    } else {
      label.textContent = `평일 · ${item.person}`;
    }

    row.append(date, label);
    if (item.note) {
      const note = document.createElement("span");
      note.className = "cal-note";
      note.textContent = item.note;
      row.appendChild(note);
    }
    list.appendChild(row);
  }
}

// ---------------------------------------------------------------- 알림

let toastTimer = null;
function toast(message, kind = "") {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast" + (kind ? ` ${kind}` : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 4000);
}

// ---------------------------------------------------------------- 하루 수정

function openDayModal(dateStr) {
  state.editingDate = dateStr;
  const entry = state.byDate.get(dateStr);
  const dow = DOW_LABELS[new Date(`${dateStr}T00:00:00`).getDay()];

  $("#day-modal-title").textContent = `${dateStr} (${dow}) 당직`;
  $("#f-type").value = entry ? entry.type : "평일";
  $("#f-org").value = entry && entry.org ? entry.org : "";
  $("#f-person").value = entry ? entry.person : "";
  $("#f-note").value = entry ? entry.note : "";

  toggleDayFields();
  $("#day-modal").showModal();
}

/** 평일은 3팀 순환 대상이 아니므로 조직·비고 칸을 감춘다. */
function toggleDayFields() {
  const holiday = $("#f-type").value === "휴일";
  $("#field-org").hidden = !holiday;
  $("#field-note").hidden = !holiday;
}

async function saveDay() {
  const dateStr = state.editingDate;
  const holiday = $("#f-type").value === "휴일";
  const payload = {
    dayType: holiday ? "휴일" : "평일",
    org: holiday ? $("#f-org").value || null : null,
    person: $("#f-person").value,
    note: holiday ? $("#f-note").value : "",
  };

  try {
    const saved = await api(`/api/schedule/${dateStr}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    // 서버가 저장을 확인해 돌려준 값만 반영한다.
    state.byDate.set(saved.date, saved);
    $("#day-modal").close();
    render();
    toast(`${dateStr} 저장했습니다.`, "success");
  } catch (err) {
    handleError(err, "저장하지 못했습니다.");
  }
}

async function clearDay() {
  const dateStr = state.editingDate;
  try {
    await api(`/api/schedule/${dateStr}`, { method: "DELETE" });
    state.byDate.delete(dateStr);
    $("#day-modal").close();
    render();
    toast(`${dateStr} 일정을 비웠습니다.`, "success");
  } catch (err) {
    handleError(err, "비우지 못했습니다.");
  }
}

// ---------------------------------------------------------------- 엑셀 반영

async function runImport() {
  const file = $("#f-file").files[0];
  if (!file) {
    toast("엑셀 파일을 선택해 주세요.", "error");
    return;
  }

  const button = $("#btn-import-run");
  button.classList.add("is-busy");
  try {
    // multipart 가 아니라 파일 바이트를 그대로 본문에 싣는다(서버가 디스크를 쓰지 않도록).
    const result = await api("/api/schedule/import", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    $("#import-modal").close();
    await loadSchedule();
    toast(result.message, "success");
  } catch (err) {
    handleError(err, "엑셀을 반영하지 못했습니다.");
  } finally {
    button.classList.remove("is-busy");
  }
}

// ---------------------------------------------------------------- 변경 이력

async function openHistory() {
  const list = $("#history-list");
  list.replaceChildren();
  $("#history-modal").showModal();

  try {
    const { items } = await api("/api/history?limit=50");
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "history-empty";
      empty.textContent = "아직 변경 이력이 없습니다.";
      list.appendChild(empty);
      return;
    }

    for (const item of items) {
      const row = document.createElement("div");
      row.className = "history-row";

      const badge = document.createElement("span");
      badge.className = `history-action ${item.action}`;
      badge.textContent = item.action;

      const main = document.createElement("div");
      main.className = "history-main";
      const summary = document.createElement("div");
      summary.className = "history-summary";
      summary.textContent = item.summary;
      const meta = document.createElement("div");
      meta.className = "history-meta";
      const when = new Date(item.changedAt);
      meta.textContent = `#${item.id} · ${when.getMonth() + 1}/${when.getDate()} ${pad(when.getHours())}:${pad(when.getMinutes())}`;
      main.append(summary, meta);

      row.append(badge, main);

      if (item.revertable) {
        const revert = document.createElement("button");
        revert.type = "button";
        revert.className = "btn btn-outline btn-mini";
        revert.textContent = "되돌리기";
        revert.addEventListener("click", () => revertChange(item.id, revert));
        row.appendChild(revert);
      }
      list.appendChild(row);
    }
  } catch (err) {
    handleError(err, "이력을 불러오지 못했습니다.");
  }
}

async function revertChange(id, button) {
  button.classList.add("is-busy");
  try {
    const result = await api(`/api/history/${id}/revert`, { method: "POST" });
    $("#history-modal").close();
    await loadSchedule();
    toast(result.message, "success");
  } catch (err) {
    handleError(err, "되돌리지 못했습니다.");
  } finally {
    button.classList.remove("is-busy");
  }
}

// ---------------------------------------------------------------- 관리자 키

/** 401 이 오면 키를 다시 받는다. 서버가 잠금을 켠 경우에만 일어난다. */
function handleError(err, fallback) {
  if (err.status === 401) {
    sessionStorage.removeItem(KEY_STORAGE);
    toast("관리자 키가 필요합니다.", "error");
    $("#key-modal").showModal();
    return;
  }
  toast(err.message || fallback, "error");
}

function saveAdminKey() {
  const key = $("#f-admin-key").value.trim();
  if (!key) {
    toast("관리자 키를 입력해 주세요.", "error");
    return;
  }
  sessionStorage.setItem(KEY_STORAGE, key);
  $("#f-admin-key").value = "";
  $("#key-modal").close();
  toast("관리자 키를 적용했습니다.", "success");
}

// ---------------------------------------------------------------- 수정 모드

function toggleEditMode() {
  state.editMode = !state.editMode;
  const button = $("#btn-edit-toggle");
  button.textContent = state.editMode ? "수정 모드 끄기" : "수정 모드";
  button.classList.toggle("btn-primary", state.editMode);
  $("#btn-import").hidden = !state.editMode;

  if (state.editMode && state.adminLockEnabled && !sessionStorage.getItem(KEY_STORAGE)) {
    $("#key-modal").showModal();
  }
  render();
}

// ---------------------------------------------------------------- 초기화

function moveMonth(delta) {
  const d = new Date(state.viewYear, state.viewMonth + delta, 1);
  state.viewYear = d.getFullYear();
  state.viewMonth = d.getMonth();
  render();
}

function goToday() {
  const now = new Date();
  state.viewYear = now.getFullYear();
  state.viewMonth = now.getMonth();
  render();
}

function wireEvents() {
  $("#btn-prev").addEventListener("click", () => moveMonth(-1));
  $("#btn-next").addEventListener("click", () => moveMonth(1));
  $("#btn-today").addEventListener("click", goToday);
  $("#btn-edit-toggle").addEventListener("click", toggleEditMode);

  $("#f-type").addEventListener("change", toggleDayFields);
  $("#btn-day-save").addEventListener("click", saveDay);
  $("#btn-day-delete").addEventListener("click", clearDay);
  $("#btn-day-cancel").addEventListener("click", () => $("#day-modal").close());

  $("#btn-import").addEventListener("click", () => {
    $("#f-file").value = "";
    $("#import-modal").showModal();
  });
  $("#btn-import-run").addEventListener("click", runImport);
  $("#btn-import-cancel").addEventListener("click", () => $("#import-modal").close());

  $("#btn-history").addEventListener("click", openHistory);
  $("#btn-history-close").addEventListener("click", () => $("#history-modal").close());

  $("#btn-key-save").addEventListener("click", saveAdminKey);
  $("#btn-key-cancel").addEventListener("click", () => $("#key-modal").close());
}

async function init() {
  goToday();
  wireEvents();

  try {
    const config = await api("/api/config");
    state.adminLockEnabled = Boolean(config.adminLockEnabled);
  } catch {
    // 설정을 못 읽어도 조회는 계속 시도한다.
    state.adminLockEnabled = false;
  }

  try {
    await loadSchedule();
  } catch (err) {
    toast(err.message || "일정을 불러오지 못했습니다.", "error");
  }
}

init();
