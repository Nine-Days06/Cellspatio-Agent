let token = null;
let onUnauthorized = null;

export class ApiError extends Error {
  constructor(status, detail, code) {
    super(detail || `请求失败（${status}）`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

export function setToken(value) {
  token = value || null;
}

export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler;
}

export async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && token) {
    onUnauthorized?.();
    throw new ApiError(401, data.detail || "登录已失效", data.code);
  }
  if (!response.ok) {
    throw new ApiError(response.status, data.detail, data.code);
  }
  return data;
}

export function vocabulary() {
  return api("/api/public/vocabulary");
}

export function login(credentials) {
  return api("/api/auth/login", { method: "POST", body: JSON.stringify(credentials) });
}

export function logout() {
  return api("/api/auth/logout", { method: "POST" });
}

export function currentUser() {
  return api("/api/auth/me");
}

export function queue() {
  return api("/api/public/evidence/queue");
}

export function mine() {
  return api("/api/public/evidence/mine");
}

export function evidence(evidenceId) {
  return api(`/api/public/evidence/${encodeURIComponent(evidenceId)}?unpublished=1`);
}

export function events(evidenceId) {
  return api(`/api/public/evidence/${encodeURIComponent(evidenceId)}/events`);
}

export function review(evidenceId, result) {
  return api(`/api/public/evidence/submissions/${encodeURIComponent(evidenceId)}/review`, {
    method: "POST",
    body: JSON.stringify(result),
  });
}

export function withdraw(evidenceId, reason) {
  return api(`/api/public/evidence/submissions/${encodeURIComponent(evidenceId)}/withdraw`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export function preview(card, revisesSubmissionId) {
  return api("/api/public/evidence/preview", {
    method: "POST",
    body: JSON.stringify({ card, revises_submission_id: revisesSubmissionId }),
  });
}

export function uploadSubmission(previewCard, consent) {
  return api("/api/public/evidence/submissions", {
    method: "POST",
    body: JSON.stringify({ preview: previewCard, consent }),
  });
}
