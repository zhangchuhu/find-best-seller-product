import assert from "node:assert/strict";
import {existsSync, readFileSync} from "node:fs";
import {join} from "node:path";
import test from "node:test";
import vm from "node:vm";

const ROOT = new URL("../..", import.meta.url).pathname;
const EXTENSION = join(ROOT, "chrome-extension/shein-evidence-collector");
const CONTENT = join(EXTENSION, "content.js");
const COLLECTOR = join(EXTENSION, "collector.js");

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

function searchCards(query, offset = 0, recurringCount = 2) {
  return Array.from({length: 30}, (_, index) => {
    const productId = index < recurringCount ? String(10001 + index) : String(offset + index + 20000);
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

function populatedSession(api, {resultLimit = 1, recurringCount = 2} = {}) {
  const queries = ["mini dress", "puff-sleeve dress", "party dress 中文"];
  const session = api.createSession({taskRecordId: "recTask_123", platform: "shein-us", queries, cardLimit: 30, resultLimit});
  queries.forEach((query, index) => {
    api.addSearchObservations(session, {
      query,
      cards: searchCards(query, index * 100, recurringCount),
    });
  });
  return {session, queries};
}

function qualifiedDetail(identity = "shein-us:10001") {
  const productId = identity.slice("shein-us:".length);
  return {
    identity,
    status: "qualified",
    reason: null,
    detail_url: `https://us.shein.com/dress-p-${productId}.html`,
    product_id: productId,
    title: "Blue Petite Dress XS",
    category: "Women / Dresses",
    visual_category_code: "dress",
    sold_display: null,
    reviews_display: "245 reviews",
    rating_display: "4.8",
    match_level: "高度相似",
    visual_features: ["square neckline", "puff sleeves"],
  };
}

test("content script exposes visible DOM helpers", () => {
  const api = loadClassicScript(CONTENT);
  for (const name of ["canonicalProductId", "classifyVisibleAd", "mergeFirstVisible", "extractVisibleMetrics"])
    assert.equal(typeof api[name], "function", `${name} must be exported`);
});

test("canonicalProductId accepts only encoded SHEIN US identities", () => {
  const {canonicalProductId} = loadClassicScript(CONTENT);
  assert.equal(canonicalProductId("https://us.shein.com/Floral-Dress-p-12345678.html?ref=search"), "12345678");
  assert.equal(canonicalProductId("https://m.shein.com/Floral-Dress-p-12345678.html"), null);
  assert.equal(canonicalProductId("http://us.shein.com/Floral-Dress-p-12345678.html"), null);
});

test("mergeFirstVisible preserves first rank and rejects ambiguous identity or ad state", () => {
  const {mergeFirstVisible} = loadClassicScript(CONTENT);
  const prior = [{
    query: "dress", rank: 1, is_ad: false, title: "First", url: "https://us.shein.com/a-p-10001.html",
    product_id: "10001", thumbnail_url: null,
  }];
  const merged = mergeFirstVisible([{
    query: "dress", is_ad: true, title: "Second", url: "https://us.shein.com/b-p-10002.html",
    product_id: "10002", thumbnail_url: null,
  }], prior);
  assert.deepEqual(Array.from(merged, (card) => card.rank), [1, 2]);
  assert.throws(() => mergeFirstVisible([{...prior[0], product_id: "99999"}], []), /identity disagreement/);
  assert.throws(() => mergeFirstVisible([{...prior[0], rank: undefined, is_ad: null}], []), /ambiguous ad state/);
});

test("visible metrics bind only explicitly typed metric text", () => {
  const {extractVisibleMetrics} = loadClassicScript(CONTENT);
  const metrics = extractVisibleMetrics([
    {kind: "reviews", text: "245 reviews", bbox: [10, 10, 100, 20]},
    {kind: "rating", text: "4.8 out of 5", bbox: [130, 10, 90, 20]},
    {kind: "other", text: "$4.70", bbox: [10, 40, 100, 20]},
  ]);
  assert.equal(metrics.reviews_display, "245");
  assert.equal(metrics.rating_display, "4.8");
});

test("reads SHEIN review lower bounds and average rating labels", () => {
  const {extractVisibleMetrics} = loadClassicScript(CONTENT);
  const metrics = extractVisibleMetrics([
    {kind: "reviews", text: "100+ Reviews", bbox: [0, 0, 100, 20]},
    {kind: "rating", text: "Average Rating 4.62 100+ Reviews", bbox: [0, 25, 200, 20]},
  ]);
  assert.equal(metrics.reviews_display, "100+");
  assert.equal(metrics.rating_display, "4.62");
});

test("collector emits only the four-field structured evidence contract", () => {
  const api = loadClassicScript(COLLECTOR);
  const {session, queries} = populatedSession(api);
  api.addDetailOutcome(session, qualifiedDetail());

  const evidence = api.buildEvidence(session);

  assert.deepEqual(Object.keys(evidence), ["task_record_id", "platform", "queries", "details"]);
  assert.deepEqual(Array.from(evidence.queries, (block) => block.query), queries);
  assert.deepEqual(Array.from(evidence.queries, (block) => block.observations.length), [30, 30, 30]);
  assert.ok(evidence.queries.flatMap((block) => block.observations).every((card) =>
    Object.keys(card).join(",") === "query,rank,is_ad,title,url,product_id,thumbnail_url"));
  assert.ok(evidence.details.every((detail) => !Object.hasOwn(detail, "evidence_refs")));
  assert.equal(api.serializeEvidence(session), `${JSON.stringify(evidence, null, 2)}\n`);
});

test("collector rejects incomplete searches, non-recurring details, wrong order, and overrun", () => {
  const api = loadClassicScript(COLLECTOR);
  const queries = ["one", "two", "three"];
  const empty = api.createSession({taskRecordId: "recA", platform: "shein-us", queries, cardLimit: 30, resultLimit: 1});
  assert.throws(() => api.buildEvidence(empty), /30-50/);

  const {session} = populatedSession(api, {resultLimit: 1});
  assert.throws(() => api.addDetailOutcome(session, qualifiedDetail("shein-us:20001")), /recurring/);
  assert.throws(() => api.addDetailOutcome(session, qualifiedDetail("shein-us:10002")), /recurring-pool order/);
  api.addDetailOutcome(session, qualifiedDetail("shein-us:10001"));
  assert.throws(() => api.addDetailOutcome(session, qualifiedDetail("shein-us:10002")), /result limit/);
});

test("structured detail outcomes retain identity and semantic validation", () => {
  const api = loadClassicScript(COLLECTOR);
  const {session} = populatedSession(api, {resultLimit: 2});
  const changed = {
    ...qualifiedDetail(),
    status: "rejected",
    reason: "identity_changed",
    detail_url: "https://us.shein.com/other-p-99999.html",
    product_id: "99999",
    match_level: null,
    visual_features: null,
  };
  api.addDetailOutcome(session, changed);
  assert.equal(session.details[0].identity, "shein-us:10001");
  assert.equal(session.details[0].product_id, "99999");

  const bad = {...qualifiedDetail("shein-us:10002"), match_level: null, visual_features: []};
  assert.throws(() => api.addDetailOutcome(session, bad), /match evidence/);
});

test("collector exports the operator's pants-set category for blazer and full-length pants", () => {
  const api = loadClassicScript(COLLECTOR);
  const {session} = populatedSession(api, {resultLimit: 1});
  const visible = {blocked: false, identity_matches: true, page_url: "https://us.shein.com/dress-p-10001.html", product_id: "10001", title: "Blazer and Pants", category: "Suits", sold_display: null, reviews_display: "500", rating_display: "4.8"};
  const detail = api.detailOutcomePlan("shein-us:10001", visible, {status: "qualified", visualCategoryCode: "womens_pants_set", matchLevel: "类似竞品", visualFeatures: ["blazer", "full length pants"]});
  api.addDetailOutcome(session, detail);
  assert.equal(JSON.parse(api.serializeEvidence(session)).details[0].visual_category_code, "womens_pants_set");
});

test("extension package contains no screenshot capture path and downloads JSON only", () => {
  const manifest = JSON.parse(readFileSync(join(EXTENSION, "manifest.json"), "utf8"));
  const source = ["collector.js", "content.js", "collector.html"]
    .map((name) => readFileSync(join(EXTENSION, name), "utf8"))
    .join("\n");
  assert.equal(existsSync(join(EXTENSION, "background.js")), false);
  assert.equal(manifest.background, undefined);
  assert.equal(manifest.action.default_popup, "collector.html");
  for (const forbidden of ["captureVisibleTab", "SHEIN_CAPTURE_VISIBLE", "screenshot", "evidence_ref", "evidence_refs"])
    assert.equal(source.includes(forbidden), false, `${forbidden} must be absent`);
  assert.equal((source.match(/chrome\.downloads\.download/g) || []).length, 1);
});

test("detail extraction reads the visible product rating button accessible label", () => {
  const box = {left: 10, top: 10, right: 300, bottom: 50, width: 290, height: 40};
  const element = (text, attributes = {}) => ({innerText: text, textContent: text,
    getBoundingClientRect: () => box, getAttribute: name => attributes[name] || null});
  const title = element('Silquee Lace-Paneled Blazer And Sheer Lace Skirt');
  const ratingButton = element('(5 Reviews)', {'aria-label': 'Average Rating 5.00 5 Reviews'});
  const {extractDetail} = loadClassicScript(CONTENT, {
    innerWidth: 1200, innerHeight: 800, devicePixelRatio: 1,
    getComputedStyle: () => ({visibility: 'visible', display: 'block', opacity: '1'}),
    location: {href: 'https://us.shein.com/suit-p-555957730.html'},
    document: {querySelectorAll: selector => {
      if (selector === 'h1') return [title];
      if (selector.includes('[aria-label*="review"') || selector.includes('[aria-label*="rating"')) return [ratingButton];
      return [];
    }},
  });
  const result = extractDetail('shein-us:555957730');
  assert.equal(result.title, 'Silquee Lace-Paneled Blazer And Sheer Lace Skirt');
  assert.equal(result.reviews_display, '5');
  assert.equal(result.rating_display, '5.00');
});

test("review section heading exposes parenthesized count", () => {
  const {extractVisibleMetrics} = loadClassicScript(CONTENT);
  assert.equal(extractVisibleMetrics([{kind: 'reviews', text: 'Customer Reviews (15) View More', bbox: [0,0,100,20]}]).reviews_display, '15');
});
