const LEGACY_ACTION_KINDS = {
  pass: { requires_checklist: true, requires_reason: false },
  needs_changes: { requires_checklist: false, requires_reason: true },
  reject: { requires_checklist: false, requires_reason: true },
};

function legacyKind(actionId) {
  for (const kind of Object.keys(LEGACY_ACTION_KINDS)) {
    if (actionId === kind || actionId.endsWith(`_${kind}`)) return kind;
  }
  return null;
}

function normalizeAction(action) {
  if (typeof action === "string") {
    const kind = legacyKind(action);
    return {
      id: action,
      kind,
      requires_checklist: kind ? LEGACY_ACTION_KINDS[kind].requires_checklist : false,
      requires_reason: kind ? LEGACY_ACTION_KINDS[kind].requires_reason : false,
    };
  }
  if (!action || typeof action !== "object") return null;
  return {
    id: action.id,
    kind: action.kind,
    requires_checklist: Boolean(action.requires_checklist),
    requires_reason: Boolean(action.requires_reason),
  };
}

export function reviewerRole(roles) {
  const roleSet = new Set(roles || []);
  if (roleSet.has("domain_reviewer")) return "domain_reviewer";
  if (roleSet.has("compliance_reviewer")) return "compliance_reviewer";
  return null;
}

export function noticeFor(review) {
  return review?.blocked_reason || null;
}

export function normalizeActions(actions) {
  return (actions || []).map(normalizeAction).filter(Boolean);
}

export function actionSpec(actions, id) {
  return normalizeActions(actions).find((action) => action.id === id) || null;
}
