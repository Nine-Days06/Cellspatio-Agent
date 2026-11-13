const TOKEN_KEY = "cellspatio_review_token";

const GATE_LABELS = {
  qc_review: "质控复核",
  pca_dimensions: "PCA 维度",
  cluster_review: "聚类复核",
  marker_review: "Marker 复核",
  knowledge_qa_review: "知识问答复核",
};

const STATUS_LABELS = {
  pending: "待审",
  needs_changes: "需补充",
  accepted: "已接受",
  rejected: "已拒绝",
  withdrawn: "已撤回",
};

const ACTION_LABELS = {
  submit: "提交",
  domain_pass: "领域通过",
  compliance_pass: "合规通过",
  domain_needs_changes: "领域要求补充",
  compliance_needs_changes: "合规要求补充",
  domain_reject: "领域拒绝",
  compliance_reject: "合规拒绝",
  withdraw: "撤销",
};

const ROLE_LABELS = {
  contributor: "贡献者",
  domain_reviewer: "领域复核者",
  compliance_reviewer: "合规复核者",
};

const CHECK_LABELS = {
  method_complete: "方法与参数完整",
  claim_matches_observation: "结论与观测一致",
  limitations_present: "已写明限制",
  privacy_scan_passed: "隐私扫描通过",
  license_and_attribution_present: "许可与署名完整",
  no_raw_data: "不含原始数据或逐细胞信息",
  small_sample_ok: "小样本已作说明",
};

const state = {
  token: sessionStorage.getItem(TOKEN_KEY),
  user: null,
  role: null,
  checklists: {},
  queue: [],
  selectedId: null,
  selected: null,
  events: [],
  canReview: false,
  canContribute: false,
  view: "review",
  mine: [],
  preview: null,
  revisesId: null,
  withdrawId: null,
};

const view = {
  login: document.querySelector("#login-view"),
  workspace: document.querySelector("#workspace"),
  contribute: document.querySelector("#contribute-view"),
  tabs: document.querySelector("#view-tabs"),
  session: document.querySelector("#session"),
  who: document.querySelector("#who"),
  queue: document.querySelector("#queue-list"),
  count: document.querySelector("#queue-count"),
  detail: document.querySelector("#detail"),
  mine: document.querySelector("#mine-list"),
  mineCount: document.querySelector("#mine-count"),
  contributeDetail: document.querySelector("#contribute-detail"),
  withdrawForm: document.querySelector("#withdraw-form"),
  withdrawTarget: document.querySelector("#withdraw-target"),
  withdrawReason: document.querySelector("#withdraw-reason"),
  toast: document.querySelector("#toast"),
};

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null) node.setAttribute(key, value);
  }
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== false));
  return node;
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function formatTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("zh-CN");
}

function kvTable(data) {
  const list = el("dl", { class: "kv" });
  for (const [key, value] of Object.entries(data || {})) {
    list.append(
      el("dt", { text: key }),
      typeof value === "object" && value !== null
        ? el("dd", {}, el("pre", { text: JSON.stringify(value, null, 2) }))
        : el("dd", { text: formatValue(value) }),
    );
  }
  return list;
}

function toast(message) {
  view.toast.textContent = message;
  view.toast.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => view.toast.classList.add("hidden"), 4000);
}

function clearSession(message) {
  state.token = null;
  state.user = null;
  state.role = null;
  state.checklists = {};
  state.queue = [];
  state.selectedId = null;
  state.selected = null;
  state.events = [];
  state.canReview = false;
  state.canContribute = false;
  state.view = "review";
  state.mine = [];
  state.preview = null;
  state.revisesId = null;
  state.withdrawId = null;
  sessionStorage.removeItem(TOKEN_KEY);
  const url = new URL(location.href);
  url.searchParams.delete("evidence");
  url.searchParams.delete("view");
  history.replaceState(null, "", url);
  view.workspace.classList.add("hidden");
  view.contribute.classList.add("hidden");
  view.tabs.classList.add("hidden");
  view.session.classList.add("hidden");
  view.login.classList.remove("hidden");
  renderQueue();
  renderDetail();
  renderMine();
  renderContribute();
  if (message) document.querySelector("#login-error").textContent = message;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401 && state.token) {
    clearSession("登录已失效，请重新登录。");
    throw new Error("登录已失效");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
  return data;
}

function setUser(user) {
  state.user = user;
  state.role = user.roles.includes("domain_reviewer")
    ? "domain_reviewer"
    : user.roles.includes("compliance_reviewer")
      ? "compliance_reviewer"
      : null;
  state.canReview = Boolean(state.role);
  state.canContribute = user.roles.includes("contributor");
  const roleText = state.role
    ? ROLE_LABELS[state.role]
    : state.canContribute
      ? ROLE_LABELS.contributor
      : "无角色";
  view.who.textContent = `${user.username}（${roleText}）`;
  view.session.classList.remove("hidden");
}

function renderQueue() {
  view.queue.replaceChildren();
  view.count.textContent = String(state.queue.length);
  if (!state.queue.length) {
    view.queue.append(el("li", { class: "empty", text: state.role ? "当前没有待审提交。" : "" }));
    return;
  }
  for (const item of state.queue) {
    const card = item.card || {};
    view.queue.append(
      el(
        "li",
        {},
        el(
          "button",
          {
            type: "button",
            class: "queue-item",
            "aria-current": String(item.evidence_id === state.selectedId),
            onclick: () => selectEvidence(item.evidence_id),
          },
          el("span", { class: "gate", text: GATE_LABELS[item.gate] || item.gate }),
          el("span", { class: "claim", text: card.claim?.text || item.claim || item.card_id }),
          el("span", { class: "when", text: formatTime(item.submitted_at) }),
        ),
      ),
    );
  }
}

async function loadWorkspace() {
  view.login.classList.add("hidden");
  state.checklists = {};
  state.queue = [];
  state.mine = [];
  if (state.canReview) {
    state.checklists = await api("/api/public/evidence/checklists");
    const queueData = await api("/api/public/evidence/queue");
    state.role = queueData.role;
    state.queue = queueData.queue;
  }
  if (state.canContribute) {
    state.mine = (await api("/api/public/evidence/mine")).submissions;
  }

  const params = new URLSearchParams(location.search);
  const fallback = state.canReview ? "review" : "contribute";
  const preferred = params.get("view") === "contribute" ? "contribute" : "review";
  showView(state.canReview && state.canContribute ? preferred : fallback, { updateUrl: false });
  renderQueue();
  renderMine();
  renderContribute();
  const fromUrl = params.get("evidence");
  if (fromUrl && state.view === "review" && !state.selected) {
    await selectEvidence(fromUrl, { updateUrl: false });
  }
  renderDetail();
}

function showView(name, { updateUrl = true } = {}) {
  state.view = name;
  view.workspace.classList.toggle("hidden", name !== "review");
  view.contribute.classList.toggle("hidden", name !== "contribute");
  view.tabs.classList.toggle("hidden", !(state.canReview && state.canContribute));
  for (const button of view.tabs.querySelectorAll("button[data-view]")) {
    button.setAttribute("aria-current", String(button.dataset.view === name));
  }
  if (updateUrl) {
    const url = new URL(location.href);
    if (name === "contribute") {
      url.searchParams.set("view", "contribute");
      url.searchParams.delete("evidence");
    } else {
      url.searchParams.delete("view");
    }
    history.replaceState(null, "", url);
  }
}

async function selectEvidence(evidenceId, { updateUrl = true } = {}) {
  state.selectedId = evidenceId;
  if (updateUrl) {
    const url = new URL(location.href);
    url.searchParams.set("evidence", evidenceId);
    history.replaceState(null, "", url);
  }
  renderQueue();
  try {
    const [detail, events] = await Promise.all([
      api(`/api/public/evidence/${encodeURIComponent(evidenceId)}?unpublished=1`),
      api(`/api/public/evidence/${encodeURIComponent(evidenceId)}/events`),
    ]);
    state.selected = detail;
    state.events = events.events;
    renderDetail();
  } catch (error) {
    toast(error.message);
  }
}

const MAX_CARD_BYTES = 512 * 1024;

function renderMine() {
  view.mine.replaceChildren();
  view.mineCount.textContent = String(state.mine.length);
  if (!state.mine.length) {
    view.mine.append(el("li", { class: "empty", text: "还没有提交。" }));
    return;
  }
  for (const item of state.mine) {
    const actions = el("div", { class: "mine-actions" });
    if (item.status === "needs_changes") {
      actions.append(
        el("button", {
          type: "button",
          text: "修订",
          onclick: () => startRevision(item.evidence_id),
        }),
      );
    }
    if (item.status === "accepted") {
      actions.append(
        el("button", {
          type: "button",
          class: "danger",
          text: "撤回",
          onclick: () => openWithdraw(item.evidence_id),
        }),
      );
    }
    view.mine.append(
      el(
        "li",
        {},
        el(
          "div",
          { class: "queue-item" },
          el("span", { class: "gate", text: GATE_LABELS[item.gate] || item.gate }),
          el("span", { class: "claim", text: item.claim || item.card_id }),
          el("span", {
            class: "when",
            text: `${STATUS_LABELS[item.status] || item.status} · ${formatTime(item.submitted_at)}`,
          }),
          item.latest_reason
            ? el("span", { class: "mine-reason", text: `原因：${item.latest_reason}` })
            : null,
          actions,
        ),
      ),
    );
  }
}

function startRevision(evidenceId) {
  state.revisesId = evidenceId;
  state.preview = null;
  showView("contribute");
  renderContribute();
  toast("请选择修订后的证据卡 JSON。");
}

function openWithdraw(evidenceId) {
  state.withdrawId = evidenceId;
  view.withdrawTarget.textContent = `撤回提交 ${evidenceId}`;
  view.withdrawReason.value = "";
  view.withdrawForm.classList.remove("hidden");
}

async function confirmWithdraw() {
  if (!state.withdrawId) return;
  const reason = view.withdrawReason.value.trim();
  if (!reason) return toast("撤回必须填写原因。");
  try {
    await api(
      `/api/public/evidence/submissions/${encodeURIComponent(state.withdrawId)}/withdraw`,
      { method: "POST", body: JSON.stringify({ reason }) },
    );
    toast("已撤回。");
    state.withdrawId = null;
    view.withdrawForm.classList.add("hidden");
    await loadWorkspace();
  } catch (error) {
    toast(error.message);
  }
}

async function uploadPreview() {
  if (!state.preview) return;
  try {
    const result = await api("/api/public/evidence/submissions", {
      method: "POST",
      body: JSON.stringify({ preview: state.preview, consent: true }),
    });
    toast(`已提交，等待复核：${result.evidence_id}`);
    state.preview = null;
    state.revisesId = null;
    await loadWorkspace();
  } catch (error) {
    toast(error.message);
  }
}

async function loadCardFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (file.size > MAX_CARD_BYTES) return toast("证据卡 JSON 不能超过 512 KB。");
  let card;
  try {
    card = JSON.parse(await file.text());
  } catch {
    return toast("文件不是有效的 JSON。");
  }
  try {
    state.preview = await api("/api/public/evidence/preview", {
      method: "POST",
      body: JSON.stringify({ card, revises_submission_id: state.revisesId }),
    });
    renderContribute();
  } catch (error) {
    toast(error.message);
  }
}

function previewBlock(preview) {
  const card = preview.will_send.card;
  const submission = preview.will_send.submission;
  const passed = preview.admission?.status === "passed";
  const consent = el("input", { type: "checkbox" });
  const upload = el("button", {
    type: "button",
    class: "primary",
    disabled: "",
    text: "同意并上传",
  });
  consent.addEventListener("change", () => {
    upload.disabled = !consent.checked || !passed;
  });
  upload.addEventListener("click", uploadPreview);

  return el(
    "div",
    { class: "preview" },
    el(
      "header",
      { class: "card-head" },
      el(
        "div",
        {},
        el("p", { class: "eyebrow", text: GATE_LABELS[card.gate] || card.gate }),
        el("h1", { text: card.claim.text }),
        el("p", { class: "meta", text: `卡 ${card.card_id} · 将发送内容如下` }),
      ),
      el("span", { class: "badge", text: "预览" }),
    ),
    contextBlock(card),
    section(
      "提交信息",
      kvTable({
        归属账号: submission.contributor_id,
        目标库: submission.target_library,
        修订: submission.revises_submission_id || "—",
        贡献者数: submission.donor_count ?? "—",
        独立复现: submission.independent_replications,
        同意版本: submission.consent_text_version,
      }),
    ),
    section("观察", kvTable(card.observation)),
    section("方法", kvTable(card.method.parameters)),
    Object.keys(card.method.protocol).length
      ? section("协议", el("pre", { text: JSON.stringify(card.method.protocol, null, 2) }))
      : null,
    limitationsSection(card),
    sourcesSection(card),
    section(
      "不会发送",
      el("ul", { class: "tags" }, ...preview.not_sent.map((item) => el("li", { text: item }))),
    ),
    section(
      "准入检查",
      passed
        ? el("p", { class: "muted", text: "通过。" })
        : el(
            "ul",
            { class: "problems" },
            ...preview.admission.problems.map((item) => el("li", { text: item })),
          ),
    ),
    passed
      ? null
      : el("p", { class: "muted", text: "准入未通过，修正证据卡后重新选择。" }),
    el(
      "form",
      { class: "review-form", onsubmit: (event) => event.preventDefault() },
      el("p", { class: "muted", text: preview.consent_text }),
      el("label", { class: "check" }, consent, el("span", { text: "我已阅读并同意上述内容" })),
      el("div", { class: "actions" }, upload),
    ),
  );
}

function renderContribute() {
  view.contributeDetail.replaceChildren();
  if (!state.canContribute) {
    view.contributeDetail.append(
      el("p", { class: "empty", text: "当前账号没有贡献者角色。" }),
    );
    return;
  }
  const picker = el("input", {
    type: "file",
    accept: "application/json,.json",
    onchange: loadCardFile,
  });
  view.contributeDetail.append(
    el(
      "article",
      { class: "card" },
      el(
        "header",
        { class: "card-head" },
        el(
          "div",
          {},
          el("p", { class: "eyebrow", text: "贡献" }),
          el("h1", { text: "上传证据卡" }),
          el("p", {
            class: "muted",
            text: "选择本地导出的证据卡 JSON；只发送聚合字段，原始数据留在本机。",
          }),
        ),
      ),
      state.revisesId
        ? el(
            "p",
            { class: "note" },
            `将修订提交 ${state.revisesId} `,
            el("button", {
              type: "button",
              class: "ghost",
              text: "取消修订",
              onclick: () => {
                state.revisesId = null;
                state.preview = null;
                renderContribute();
              },
            }),
          )
        : null,
      section("选择文件", picker),
      state.preview
        ? previewBlock(state.preview)
        : el("p", { class: "muted", text: "选择文件后显示将发送的内容。" }),
    ),
  );
}

function section(title, ...children) {
  return el("section", {}, el("h2", { text: title }), ...children);
}

function contextBlock(card) {
  const items = [
    ["工具", `${card.method.tool} ${card.method.tool_version}`],
    ["来源", (card.provenance.source_ids || []).join("、") || "—"],
    ["许可", [card.license.spdx, card.license.attribution].filter(Boolean).join(" · ") || "—"],
    ["隐私", `原始数据 ${card.privacy.contains_raw_data ? "包含" : "不含"} · 扫描 ${card.privacy.pii_scan}`],
    ["绑定", card.subject.binding_sha256.slice(0, 16)],
  ];
  return kvTable(Object.fromEntries(items));
}

function reviewForm() {
  if (!state.role || state.selected.status !== "pending") return null;
  const prefix = state.role === "domain_reviewer" ? "domain" : "compliance";
  if (state.events.some((event) => event.actor_id === state.user.id)) return null;
  if (state.events.some((event) => event.action === `${prefix}_pass`)) return null;
  const required = state.checklists[state.role] || [];
  const boxes = [];
  const checklist = el("fieldset", {}, el("legend", {
    text: `${ROLE_LABELS[state.role]}清单（全部勾选才能通过）`,
  }));
  for (const key of required) {
    const input = el("input", { type: "checkbox", "data-check": key });
    boxes.push(input);
    checklist.append(
      el("label", { class: "check" }, input, el("span", { text: CHECK_LABELS[key] || key })),
    );
  }
  const reason = el("textarea", { placeholder: "要求补充或拒绝时必须填写具体原因" });
  const pass = el("button", { type: "button", class: "pass", disabled: "", text: "通过" });
  const needs = el("button", { type: "button", text: "要求补充" });
  const reject = el("button", { type: "button", class: "danger", text: "拒绝" });

  const update = () => {
    pass.disabled = boxes.some((box) => !box.checked);
  };
  boxes.forEach((box) => box.addEventListener("change", update));

  const submit = async (kind, prefix) => {
    const result = { action: `${prefix}_${kind}` };
    if (kind === "pass") {
      if (boxes.some((box) => !box.checked)) return toast("请先勾选全部清单项。");
      result.checklist = Object.fromEntries(boxes.map((box) => [box.dataset.check, true]));
    } else if (!reason.value.trim()) {
      return toast("要求补充或拒绝必须填写原因。");
    } else {
      result.reason = reason.value.trim();
    }
    const buttons = [pass, needs, reject];
    buttons.forEach((button) => (button.disabled = true));
    const evidenceId = state.selectedId;
    try {
      await api(`/api/public/evidence/submissions/${encodeURIComponent(evidenceId)}/review`, {
        method: "POST",
        body: JSON.stringify(result),
      });
      toast("已记录复核结论。");
      await loadWorkspace();
      await selectEvidence(evidenceId);
    } catch (error) {
      toast(error.message);
    } finally {
      buttons.forEach((button) => (button.disabled = false));
    }
  };

  pass.addEventListener("click", () => submit("pass", prefix));
  needs.addEventListener("click", () => submit("needs_changes", prefix));
  reject.addEventListener("click", () => submit("reject", prefix));

  return el(
    "form",
    { class: "review-form", onsubmit: (event) => event.preventDefault() },
    checklist,
    el("label", {}, el("span", { class: "muted", text: "说明" }), reason),
    el("div", { class: "actions" }, pass, needs, reject),
  );
}

function noticeText() {
  if (!state.role) return "当前账号没有复核角色。";
  if (state.selected.status !== "pending") return "该提交已结束审核。";
  const prefix = state.role === "domain_reviewer" ? "domain" : "compliance";
  if (state.events.some((event) => event.action === `${prefix}_pass`)) {
    return "本角色已签署，等待另一角色复核。";
  }
  if (state.events.some((event) => event.actor_id === state.user.id)) {
    return "你已复核过该提交。";
  }
  return "该提交不在当前角色的待审范围。";
}

function eventList() {
  if (!state.events.length) return el("p", { class: "empty", text: "暂无审计事件。" });
  const list = el("ul", { class: "timeline" });
  for (const event of state.events) {
    list.append(
      el(
        "li",
        {},
        el("span", {
          class: "event-head",
          text: `${ACTION_LABELS[event.action] || event.action} · ${ROLE_LABELS[event.role] || event.role}`,
        }),
        el("span", {
          class: "event-meta",
          text: `${event.actor_id} · ${formatTime(event.created_at)}`,
        }),
        event.reason ? el("span", { class: "event-reason", text: `原因：${event.reason}` }) : null,
      ),
    );
  }
  return list;
}

function limitationsSection(card) {
  return section(
    "限制",
    card.limitations.length
      ? el("ul", { class: "plain" }, ...card.limitations.map((item) => el("li", { text: item })))
      : el("p", { class: "muted", text: "未填写限制。" }),
  );
}

function sourcesSection(card) {
  return section(
    "来源",
    card.provenance.source_ids.length
      ? el(
          "ul",
          { class: "tags" },
          ...card.provenance.source_ids.map((id) => el("li", { text: id })),
        )
      : el("p", { class: "muted", text: "未填写来源。" }),
  );
}

function renderDetail() {
  view.detail.replaceChildren();
  const selected = state.selected;
  if (!selected || !selected.card) {
    view.detail.append(el("p", { class: "empty", text: "从左侧选择一份提交查看证据卡。" }));
    return;
  }
  const card = selected.card;
  const header = el(
    "header",
    { class: "card-head" },
    el(
      "div",
      {},
      el("p", { class: "eyebrow", text: GATE_LABELS[card.gate] || card.gate }),
      el("h1", { text: card.claim.text }),
    ),
    el("span", {
      class: `badge ${selected.status}`,
      text: STATUS_LABELS[selected.status] || selected.status,
    }),
  );
  const meta = el("p", {
    class: "meta",
    text: `证据 ${selected.evidence_id} · 卡 ${card.card_id} · 贡献者 ${selected.contributor_id} · 提交 ${formatTime(selected.submitted_at)}`,
  });

  const claim = section(
    "结论",
    el("p", { class: "claim-text", text: card.claim.text }),
    el("p", {
      class: "meta",
      text: `依据：${card.claim.basis}${card.claim.confidence ? ` · 置信度：${card.claim.confidence}` : ""}`,
    }),
  );

  const limitations = limitationsSection(card);
  const sources = sourcesSection(card);

  const form = reviewForm();
  view.detail.append(
    el(
      "article",
      { class: "card" },
      header,
      meta,
      contextBlock(card),
      claim,
      section("观察", kvTable(card.observation)),
      section("方法", kvTable(card.method.parameters)),
      Object.keys(card.method.protocol).length
        ? section("协议", el("pre", { text: JSON.stringify(card.method.protocol, null, 2) }))
        : null,
      limitations,
      sources,
      form || el("p", { class: "muted", text: noticeText() }),
      section("审计事件", eventList()),
    ),
  );
}

document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  document.querySelector("#login-error").textContent = "";
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: document.querySelector("#username").value.trim(),
        password: document.querySelector("#password").value,
      }),
    });
    state.token = data.token;
    sessionStorage.setItem(TOKEN_KEY, data.token);
    setUser(data.user);
    await loadWorkspace();
  } catch (error) {
    document.querySelector("#login-error").textContent = error.message;
  }
});

document.querySelector("#logout").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } finally {
    clearSession("");
  }
});

view.tabs.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (!button) return;
  showView(button.dataset.view);
});

document.querySelector("#withdraw-confirm").addEventListener("click", confirmWithdraw);
document.querySelector("#withdraw-cancel").addEventListener("click", () => {
  state.withdrawId = null;
  view.withdrawForm.classList.add("hidden");
});

async function boot() {
  if (!state.token) {
    clearSession("");
    return;
  }
  try {
    const data = await api("/api/auth/me");
    setUser(data.user);
    await loadWorkspace();
  } catch {
    // 401 已由 api() 切回登录视图
  }
}

boot();
