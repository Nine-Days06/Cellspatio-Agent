import assert from "node:assert/strict";

import {
  actionSpec,
  normalizeActions,
  noticeFor,
} from "../../src/public_api/static/review-model.js";

assert.equal(
  noticeFor({ blocked_code: "self_review", blocked_reason: "贡献者不得审核自己的提交" }),
  "贡献者不得审核自己的提交",
);
assert.equal(noticeFor({ blocked_code: "no_role" }), null);
assert.equal(noticeFor(null), null);

const serverSpecs = [
  {
    id: "domain_pass",
    kind: "reject",
    requires_checklist: false,
    requires_reason: true,
  },
];
assert.deepEqual(normalizeActions(serverSpecs), serverSpecs);
assert.deepEqual(actionSpec(serverSpecs, "domain_pass"), serverSpecs[0]);
assert.equal(actionSpec(serverSpecs, "missing"), null);

assert.deepEqual(normalizeActions(["domain_pass"]), []);
