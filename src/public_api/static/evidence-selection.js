/**
 * 证据选择状态：每次选择递增 token，只有最新请求可以写入快照。
 * `load(id)` 返回 Promise<{ detail, events, review }>。
 */
export function createEvidenceSelection(load) {
  let token = 0;
  let selectedId = null;
  let snapshot = null;

  return {
    async select(id) {
      const mine = ++token;
      selectedId = id;
      snapshot = null;
      let data;
      try {
        data = await load(id);
      } catch {
        return null;
      }
      if (mine !== token || id !== selectedId) return null;
      snapshot = { id, detail: data.detail, events: data.events, review: data.review };
      return snapshot;
    },
    current() {
      return snapshot;
    },
    clear() {
      token++;
      selectedId = null;
      snapshot = null;
    },
  };
}
