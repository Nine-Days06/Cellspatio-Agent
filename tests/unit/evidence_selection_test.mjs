import assert from "node:assert/strict";

import { createEvidenceSelection } from "../../src/public_api/static/evidence-selection.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function payload(id) {
  return { detail: { id }, events: [`event-${id}`], review: { id } };
}

{
  const pending = new Map();
  const selection = createEvidenceSelection((id) => {
    const task = deferred();
    pending.set(id, task);
    return task.promise;
  });

  const a = selection.select("A");
  const b = selection.select("B");
  assert.equal(selection.current(), null);

  pending.get("B").resolve(payload("B"));
  assert.deepEqual(await b, { id: "B", ...payload("B") });
  assert.deepEqual(selection.current(), { id: "B", ...payload("B") });

  pending.get("A").resolve(payload("A"));
  assert.equal(await a, null);
  assert.deepEqual(selection.current(), { id: "B", ...payload("B") });
}

{
  const pending = new Map();
  const selection = createEvidenceSelection((id) => {
    const task = deferred();
    pending.set(id, task);
    return task.promise;
  });

  const a = selection.select("A");
  const b = selection.select("B");
  pending.get("A").reject(new Error("A failed"));
  assert.equal(await a, null);
  assert.equal(selection.lastError(), null);
  pending.get("B").resolve(payload("B"));
  assert.deepEqual(await b, { id: "B", ...payload("B") });
  assert.deepEqual(selection.current(), { id: "B", ...payload("B") });
  assert.equal(selection.lastError(), null);
}

{
  const pending = new Map();
  const selection = createEvidenceSelection((id) => {
    const task = deferred();
    pending.set(id, task);
    return task.promise;
  });

  const a = selection.select("A");
  selection.clear();
  pending.get("A").resolve(payload("A"));
  assert.equal(await a, null);
  assert.equal(selection.current(), null);
}

{
  const pending = [];
  const selection = createEvidenceSelection(() => {
    const task = deferred();
    pending.push(task);
    return task.promise;
  });

  const first = selection.select("A");
  const second = selection.select("A");
  pending[0].resolve(payload("stale"));
  assert.equal(await first, null);
  pending[1].resolve(payload("A"));
  assert.deepEqual(await second, { id: "A", ...payload("A") });
  assert.deepEqual(selection.current(), { id: "A", ...payload("A") });
}

{
  const failure = deferred();
  const selection = createEvidenceSelection(() => failure.promise);
  const pending = selection.select("A");
  const error = new Error("failed");
  failure.reject(error);
  assert.equal(await pending, null);
  assert.equal(selection.current(), null);
  assert.equal(selection.lastError(), error);

  selection.clear();
  assert.equal(selection.lastError(), null);
}

{
  const pending = new Map();
  const selection = createEvidenceSelection((id) => {
    const task = deferred();
    pending.set(id, task);
    return task.promise;
  });

  const a = selection.select("A");
  const b = selection.select("B");
  const latest = new Error("B failed");
  pending.get("B").reject(latest);
  assert.equal(await b, null);
  assert.equal(selection.lastError(), latest);

  pending.get("A").reject(new Error("A failed"));
  assert.equal(await a, null);
  assert.equal(selection.lastError(), latest);
}

{
  const attempts = [];
  const selection = createEvidenceSelection(() => {
    const task = deferred();
    attempts.push(task);
    return task.promise;
  });

  const failed = selection.select("A");
  attempts[0].reject(new Error("failed"));
  assert.equal(await failed, null);
  assert.notEqual(selection.lastError(), null);

  const retried = selection.select("A");
  attempts[1].resolve(payload("A"));
  assert.deepEqual(await retried, { id: "A", ...payload("A") });
  assert.equal(selection.lastError(), null);
}
