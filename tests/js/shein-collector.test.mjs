import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {join} from "node:path";
import test from "node:test";
import vm from "node:vm";
import {webcrypto} from "node:crypto";


const ROOT = new URL("../..", import.meta.url).pathname;
const CONTENT = join(ROOT, "chrome-extension/shein-evidence-collector/content.js");
const COLLECTOR = join(ROOT, "chrome-extension/shein-evidence-collector/collector.js");

function loadClassicScript(path, additions = {}) {
  const exports = {};
  const context = vm.createContext({
    URL,
    console,
    globalThis: {__SHEIN_COLLECTOR_TEST__: exports},
    ...additions,
  });
  vm.runInContext(readFileSync(path, "utf8"), context, {filename: path});
  return exports;
}

test("content script exposes the required pure helpers through the VM hook", () => {
  const api = loadClassicScript(CONTENT);
  for (const name of ["canonicalProductId", "normalizeBox", "classifyVisibleAd", "mergeFirstVisible"]) {
    assert.equal(typeof api[name], "function", `${name} must be exported to the test hook`);
  }
});

test("canonicalProductId accepts only an encoded SHEIN US product identity", () => {
  const {canonicalProductId} = loadClassicScript(CONTENT);
  assert.equal(canonicalProductId("https://us.shein.com/Floral-Dress-p-12345678.html?ref=search"), "12345678");
  assert.equal(canonicalProductId("https://m.shein.com/Floral-Dress-p-12345678.html"), null);
  assert.equal(canonicalProductId("http://us.shein.com/Floral-Dress-p-12345678.html"), null);
  assert.equal(canonicalProductId("https://us.shein.com/pdsearch/dress/"), null);
});

test("normalizeBox clips a rendered box to screenshot pixels", () => {
  const {normalizeBox} = loadClassicScript(CONTENT);
  assert.deepEqual(
    Array.from(normalizeBox({left: -10.2, top: 9.2, right: 110.4, bottom: 90.9}, {width: 100, height: 80, scale: 1})),
    [0, 9, 100, 71],
  );
  assert.deepEqual(
    Array.from(normalizeBox({left: 5, top: 5, right: 25, bottom: 15}, {width: 100, height: 80, scale: 2})),
    [10, 10, 40, 20],
  );
  assert.equal(normalizeBox({left: 120, top: 5, right: 140, bottom: 15}, {width: 100, height: 80}), null);
});

test("classifyVisibleAd accepts explicit visible states and rejects ambiguity", () => {
  const {classifyVisibleAd} = loadClassicScript(CONTENT);
  assert.equal(classifyVisibleAd({visibleLabels: ["Sponsored"], visiblyOrganic: false}), true);
  assert.equal(classifyVisibleAd({visibleLabels: ["Ad"], visiblyOrganic: false}), true);
  assert.equal(classifyVisibleAd({visibleLabels: [], visiblyOrganic: true}), false);
  assert.throws(
    () => classifyVisibleAd({visibleLabels: ["Sponsored"], visiblyOrganic: true}),
    /ambiguous ad state/,
  );
  assert.throws(
    () => classifyVisibleAd({visibleLabels: [], visiblyOrganic: false}),
    /ambiguous ad state/,
  );
});

test("mergeFirstVisible preserves first rank and verbatim visible color and size text", () => {
  const {mergeFirstVisible} = loadClassicScript(CONTENT);
  const prior = [{
    query: "women dress", rank: 1, is_ad: false, title: "First dress", url: "https://us.shein.com/a-p-10001.html",
    product_id: "10001", thumbnail_url: null, bbox: [0, 0, 20, 20],
  }];
  const cards = [{
    query: "women dress", is_ad: true, title: "Royal Blue Curve 3XL Dress", url: "https://us.shein.com/b-p-10002.html?src=one",
    product_id: "10002", thumbnail_url: "https://img.shein.com/b.jpg", bbox: [1, 2, 30, 40],
  }, {
    query: "women dress", is_ad: false, title: "Duplicate first", url: "https://us.shein.com/a-p-10001.html?src=two",
    product_id: "10001", thumbnail_url: null, bbox: [1, 2, 30, 40],
  }];
  const merged = mergeFirstVisible(cards, prior);
  assert.equal(merged.length, 2);
  assert.equal(merged[0].rank, 1);
  assert.equal(merged[1].rank, 2);
  assert.equal(merged[1].title, "Royal Blue Curve 3XL Dress");
  assert.equal(merged[1].url, "https://us.shein.com/b-p-10002.html?src=one");
});

test("mergeFirstVisible rejects URL/explicit ID disagreement and unknown ad state", () => {
  const {mergeFirstVisible} = loadClassicScript(CONTENT);
  const base = {
    query: "dress", title: "Dress", url: "https://us.shein.com/a-p-10001.html", thumbnail_url: null, bbox: [0, 0, 20, 20],
  };
  assert.throws(() => mergeFirstVisible([{...base, product_id: "99999", is_ad: false}], []), /identity disagreement/);
  assert.throws(() => mergeFirstVisible([{...base, product_id: "10001", is_ad: null}], []), /ambiguous ad state/);
});

function screenshot(id, pageUrl, digest = "a".repeat(64)) {
  return {id, file: `screenshots/${id}.png`, sha256: digest, page_url: pageUrl};
}

function searchCards(query, offset = 0) {
  return Array.from({length: 30}, (_, index) => {
    const productId = index === 0 ? "10001" : String(offset + index + 20000);
    return {
      query,
      rank: index + 1,
      is_ad: index === 1,
      title: index === 0 ? "Blue Petite Dress XS" : `Dress ${productId}`,
      url: `https://us.shein.com/dress-p-${productId}.html?src=visible`,
      product_id: productId,
      thumbnail_url: null,
      bbox: [0, index * 2, 100, 2],
    };
  });
}

function populatedSession(api) {
  const queries = ["mini dress", "puff-sleeve dress", "party dress 中文"];
  const session = api.createSession({taskRecordId: "recTask_123", platform: "shein-us", queries, limit: 30});
  queries.forEach((query, index) => {
    const pageUrl = `https://us.shein.com/pdsearch/${encodeURIComponent(query)}/`;
    api.addSearchFrame(session, {
      query,
      screenshot: screenshot(`query-${index + 1}-frame-001`, pageUrl, String(index + 1).repeat(64)),
      cards: searchCards(query, index * 100),
    });
  });
  return {session, queries};
}

test("collector package has exact root/schema keys and preserves all three query bytes", () => {
  const api = loadClassicScript(COLLECTOR);
  const {session, queries} = populatedSession(api);
  api.addDetailCapture(session, {
    screenshot: screenshot(
      "detail-10001-001",
      "https://us.shein.com/blue-dress-p-10001.html",
      "b".repeat(64),
    ),
    detail: {
      identity: "shein-us:10001",
      status: "qualified",
      reason: null,
      detail_url: "https://us.shein.com/blue-dress-p-10001.html",
      product_id: "10001",
      title: "Blue Petite Dress XS",
      category: "Women / Dresses",
      sold_display: null,
      reviews_display: "245 reviews",
      rating_display: "4.8",
      match_level: "高度相似",
      visual_features: ["square neckline", "puff sleeves"],
    },
    regions: [
      {purpose: "visual", bbox: [0, 0, 50, 60]},
      {purpose: "metrics", bbox: [50, 0, 50, 60]},
    ],
  });
  const evidence = api.buildEvidence(session);
  assert.deepEqual(Object.keys(evidence), ["task_record_id", "platform", "screenshots", "queries", "details"]);
  assert.deepEqual(Array.from(evidence.queries, (block) => block.query), queries);
  assert.deepEqual(Array.from(evidence.queries, (block) => block.observations.length), [30, 30, 30]);
  for (const block of evidence.queries) {
    assert.deepEqual(Array.from(block.observations, (card) => card.rank), Array.from({length: 30}, (_, i) => i + 1));
    assert.ok(block.observations.every((card) => card.query === block.query));
    assert.ok(block.observations.every((card) => Object.keys(card.evidence_ref).join(",") === "screenshot_id,bbox"));
  }
  assert.equal(evidence.queries[0].observations[0].title, "Blue Petite Dress XS");
  assert.deepEqual(Array.from(evidence.details[0].evidence_refs, (ref) => ref.purpose), ["visual", "metrics"]);
  assert.ok(evidence.screenshots.every((entry) => /^[0-9a-f]{64}$/.test(entry.sha256)));
  assert.equal(new Set(evidence.screenshots.map((entry) => entry.id)).size, evidence.screenshots.length);
  assert.equal(new Set(evidence.screenshots.map((entry) => entry.file)).size, evidence.screenshots.length);
  assert.equal(api.serializeEvidence(session), `${JSON.stringify(evidence, null, 2)}\n`);
});

test("collector rejects invalid records, collisions, query substitution, and incomplete ranks", () => {
  const api = loadClassicScript(COLLECTOR);
  for (const bad of ["rec", "../recA", "ABC123", "recA/child", "recé"])
    assert.throws(() => api.createSession({taskRecordId: bad, platform: "shein-us", queries: ["a", "b", "c"], limit: 30}), /record ID/);
  const {session, queries} = populatedSession(api);
  assert.throws(() => api.addSearchFrame(session, {
    query: queries[0],
    screenshot: screenshot("query-1-frame-001", `https://us.shein.com/pdsearch/${queries[0]}/`),
    cards: [],
  }), /collision/);
  const fresh = api.createSession({taskRecordId: "recA", platform: "shein-us", queries, limit: 30});
  assert.throws(() => api.addSearchFrame(fresh, {
    query: queries[0],
    screenshot: screenshot("q1", "https://us.shein.com/pdsearch/wrong/"),
    cards: searchCards(queries[0]),
  }), /query URL/);
  assert.throws(() => api.buildEvidence(api.createSession({taskRecordId: "recA", platform: "shein-us", queries, limit: 30})), /30-50/);
});

test("detail capture requires recurrence, identity binding, and rejection-purpose evidence", () => {
  const api = loadClassicScript(COLLECTOR);
  const {session} = populatedSession(api);
  const base = {
    identity: "shein-us:10001", status: "qualified", reason: null,
    detail_url: "https://us.shein.com/dress-p-10001.html", product_id: "10001", title: "Dress",
    category: null, sold_display: null, reviews_display: "100", rating_display: "4.7",
    match_level: "同款", visual_features: ["A-line silhouette"],
  };
  assert.throws(() => api.addDetailCapture(session, {
    screenshot: screenshot("bad-detail", "https://us.shein.com/dress-p-99999.html"),
    detail: base,
    regions: [{purpose: "visual", bbox: [0, 0, 20, 20]}, {purpose: "metrics", bbox: [20, 0, 20, 20]}],
  }), /identity binding/);
  assert.throws(() => api.addDetailCapture(session, {
    screenshot: screenshot("missing-purpose", base.detail_url), detail: base,
    regions: [{purpose: "visual", bbox: [0, 0, 20, 20]}],
  }), /metrics/);
  assert.throws(() => api.addDetailCapture(session, {
    screenshot: screenshot("not-recurring", "https://us.shein.com/dress-p-20001.html"),
    detail: {...base, identity: "shein-us:20001", detail_url: "https://us.shein.com/dress-p-20001.html", product_id: "20001"},
    regions: [{purpose: "visual", bbox: [0, 0, 20, 20]}, {purpose: "metrics", bbox: [20, 0, 20, 20]}],
  }), /recurring/);
});

test("access-state rejections preserve the recurring identity without fabricating detail fields", () => {
  const api = loadClassicScript(COLLECTOR);
  const {session} = populatedSession(api);
  const rejected = {
    identity: "shein-us:10001", status: "rejected", reason: "identity_changed",
    detail_url: "https://us.shein.com/other-p-99999.html", product_id: "99999", title: "Other item",
    category: null, sold_display: null, reviews_display: null, rating_display: null,
    match_level: null, visual_features: null,
  };
  api.addDetailCapture(session, {
    screenshot: screenshot("identity-changed", rejected.detail_url),
    detail: rejected,
    regions: [{purpose: "access_state", bbox: [0, 0, 100, 20]}],
  });

  const inaccessibleSession = populatedSession(api).session;
  const inaccessible = {
    identity: "shein-us:10001", status: "rejected", reason: "detail_inaccessible",
    detail_url: null, product_id: null, title: null, category: null, sold_display: null,
    reviews_display: null, rating_display: null, match_level: null, visual_features: null,
  };
  api.addDetailCapture(inaccessibleSession, {
    screenshot: screenshot("detail-inaccessible", "https://us.shein.com/unavailable"),
    detail: inaccessible,
    regions: [{purpose: "access_state", bbox: [0, 0, 100, 20]}],
  });
  assert.equal(api.buildEvidence(session).details[0].product_id, "99999");
  assert.equal(api.buildEvidence(inaccessibleSession).details[0].detail_url, null);
});

test("qualified and rejected details cannot export validator-invalid match fields", () => {
  const api = loadClassicScript(COLLECTOR);
  const makeArgs = (detail) => ({
    screenshot: screenshot("detail-shape", "https://us.shein.com/dress-p-10001.html"),
    detail,
    regions: [{purpose: "visual", bbox: [0, 0, 20, 20]}, {purpose: "metrics", bbox: [20, 0, 20, 20]}],
  });
  const base = {
    identity: "shein-us:10001", status: "qualified", reason: null,
    detail_url: "https://us.shein.com/dress-p-10001.html", product_id: "10001", title: "Dress",
    category: null, sold_display: null, reviews_display: "100", rating_display: "4.7",
    match_level: null, visual_features: [],
  };
  assert.throws(() => api.addDetailCapture(populatedSession(api).session, makeArgs(base)), /match evidence/);
  assert.throws(() => api.addDetailCapture(populatedSession(api).session, makeArgs({
    ...base, status: "rejected", reason: "threshold_failure", match_level: "同款", visual_features: ["A-line"],
  })), /rejected detail cannot contain match evidence/);
});

test("blocking responses stop collection with an operator-visible error", () => {
  const api = loadClassicScript(COLLECTOR);
  assert.throws(
    () => api.requireSafeResponse({error: "Collection stopped: CAPTCHA detected."}),
    /Collection stopped: CAPTCHA detected/,
  );
  assert.deepEqual(api.requireSafeResponse({page_url: "https://us.shein.com/pdsearch/dress/"}), {page_url: "https://us.shein.com/pdsearch/dress/"});
});

test("background captures and hashes the active SHEIN tab only after its action click", async () => {
  const listeners = {};
  const calls = [];
  const extensionRoot = "chrome-extension://collector/";
  const chrome = {
    action: {onClicked: {addListener(fn) { listeners.action = fn; }}},
    runtime: {
      getURL(path) { return extensionRoot + path; },
      onMessage: {addListener(fn) { listeners.message = fn; }},
    },
    windows: {create(options) { calls.push(["window", options]); }},
    tabs: {
      async get(id) { return {id, windowId: 3, active: true, url: "https://us.shein.com/pdsearch/dress/"}; },
      async captureVisibleTab(windowId, options) {
        calls.push(["capture", windowId, options]);
        return "data:image/png;base64,AAECAw==";
      },
    },
  };
  vm.runInContext(readFileSync(join(ROOT, "chrome-extension/shein-evidence-collector/background.js"), "utf8"), vm.createContext({
    chrome, URL, crypto: webcrypto, Uint8Array, atob,
  }));

  const send = (message) => new Promise((resolve) => {
    const keepOpen = listeners.message(message, {url: extensionRoot + "collector.html"}, resolve);
    assert.equal(keepOpen, true);
  });
  const denied = await send({type: "SHEIN_CAPTURE_VISIBLE", tab_id: 7, window_id: 3});
  assert.match(denied.error, /action click/);
  assert.equal(calls.filter(([name]) => name === "capture").length, 0);

  listeners.action({id: 7, windowId: 3, url: "https://us.shein.com/pdsearch/dress/"});
  const result = await send({type: "SHEIN_CAPTURE_VISIBLE", tab_id: 7, window_id: 3});
  assert.equal(result.sha256, "054edec1d0211f624fed0cbca9d4f9400b0e491c43742af2c5b0abebf0c990d8");
  assert.equal(result.data_url, "data:image/png;base64,AAECAw==");
  const captureCall = calls.find(([name]) => name === "capture");
  assert.equal(captureCall[1], 3);
  assert.equal(captureCall[2].format, "png");
});
