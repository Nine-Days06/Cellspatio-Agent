function normalizeAction(action) {
  if (!action || typeof action !== "object") return null;
  return {
    id: action.id,
    kind: action.kind,
    requires_checklist: Boolean(action.requires_checklist),
    requires_reason: Boolean(action.requires_reason),
  };
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
