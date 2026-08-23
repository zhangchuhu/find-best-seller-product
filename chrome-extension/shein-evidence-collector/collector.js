"use strict";

(() => {
  const RECORD_ID = /^rec[A-Za-z0-9_-]+$/;
  const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
  const SHA256 = /^[0-9a-f]{64}$/;
  const DETAIL_KEYS = [
    "identity", "status", "reason", "detail_url", "product_id", "title", "category",
    "sold_display", "reviews_display", "rating_display", "match_level", "visual_features",
  ];
  const PURPOSES = {
    qualified: ["visual", "metrics"],
    visual_structure_mismatch: ["visual"],
    imagery_ambiguous_or_inaccessible: ["visual"],
    metric_missing_or_ambiguous: ["metrics"],
    threshold_failure: ["metrics"],
    identity_changed: ["access_state"],
    detail_inaccessible: ["access_state"],
  };

  function canonicalProductId(value) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.hostname !== "us.shein.com" || url.port) return null;
      return /-p-(\d+)\.html$/i.exec(url.pathname)?.[1] || null;
    } catch (_error) {
      return null;
    }
  }

  function createSession({taskRecordId, platform, queries, cardLimit, resultLimit}) {
    if (typeof taskRecordId !== "string" || !RECORD_ID.test(taskRecordId)) {
      throw new Error("invalid task record ID");
    }
    if (platform !== "shein-us") throw new Error("platform must be shein-us");
    if (!Array.isArray(queries) || queries.length !== 3 || queries.some((query) => typeof query !== "string" || !query.trim()) || new Set(queries).size !== 3) {
      throw new Error("exactly three distinct Ark queries are required");
    }
    if (!Number.isInteger(cardLimit) || cardLimit < 30 || cardLimit > 50) throw new Error("card limit must be 30-50");
    if (!Number.isInteger(resultLimit) || resultLimit < 1) throw new Error("task result limit must be a positive integer");
    return {
      taskRecordId,
      platform,
      queries: [...queries],
      cardLimit,
      resultLimit,
      screenshots: [],
      queryBlocks: queries.map((query) => ({query, observations: []})),
      details: [],
      screenshotIds: new Set(),
      screenshotFiles: new Set(),
      downloadData: new Map(),
      stopped: false,
    };
  }

  function queryFromUrl(value) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.hostname !== "us.shein.com" || url.port) return null;
      const match = /^\/pdsearch\/(.*)\/$/.exec(url.pathname);
      return match ? decodeURIComponent(match[1]) : null;
    } catch (_error) {
      return null;
    }
  }

  function validateScreenshot(session, raw, kind, query, identity) {
    if (!raw || typeof raw !== "object" || !IDENTIFIER.test(raw.id || "")) throw new Error("invalid screenshot ID");
    if (typeof raw.file !== "string" || !/^screenshots\/[A-Za-z0-9][A-Za-z0-9._-]*\.png$/.test(raw.file)) throw new Error("invalid screenshot file");
    if (!SHA256.test(raw.sha256 || "")) throw new Error("invalid lowercase screenshot SHA-256");
    if (session.screenshotIds.has(raw.id) || session.screenshotFiles.has(raw.file)) throw new Error("screenshot collision");
    let page;
    try { page = new URL(raw.page_url); } catch (_error) { throw new Error("invalid screenshot page URL"); }
    if (page.protocol !== "https:" || page.hostname !== "us.shein.com" || page.port) throw new Error("invalid screenshot page URL");
    return {
      id: raw.id,
      kind,
      file: raw.file,
      sha256: raw.sha256,
      page_url: raw.page_url,
      query,
      identity,
    };
  }

  function registerScreenshot(session, descriptor) {
    session.screenshotIds.add(descriptor.id);
    session.screenshotFiles.add(descriptor.file);
    session.screenshots.push(descriptor);
  }

  function validBox(value) {
    return Array.isArray(value) && value.length === 4 && value.every((part) => Number.isInteger(part)) && value[0] >= 0 && value[1] >= 0 && value[2] > 0 && value[3] > 0;
  }

  function addSearchFrame(session, {query, screenshot, cards}) {
    const blockIndex = session.queries.indexOf(query);
    if (blockIndex < 0 || queryFromUrl(screenshot?.page_url) !== query) throw new Error("search screenshot query URL does not match exact Ark query");
    const descriptor = validateScreenshot(session, screenshot, "search", query, null);
    const block = session.queryBlocks[blockIndex];
    if (!Array.isArray(cards) || block.observations.length + cards.length > session.cardLimit) throw new Error("search frame exceeds card limit");
    const seen = new Set(block.observations.map((card) => card.product_id));
    const additions = [];
    for (let index = 0; index < cards.length; index += 1) {
      const card = cards[index];
      const productId = canonicalProductId(card?.url);
      const expectedRank = block.observations.length + index + 1;
      if (!productId || String(card.product_id) !== productId) throw new Error("search card identity disagreement");
      if (card.query !== query || card.rank !== expectedRank || typeof card.is_ad !== "boolean") throw new Error("search cards must have contiguous visible ranks and unambiguous ads");
      if (typeof card.title !== "string" || !card.title || !validBox(card.bbox)) throw new Error("search card visible evidence is incomplete");
      if (seen.has(productId)) throw new Error("duplicate product in one query");
      seen.add(productId);
      additions.push({
        query: card.query,
        rank: card.rank,
        is_ad: card.is_ad,
        title: card.title,
        url: card.url,
        product_id: productId,
        thumbnail_url: typeof card.thumbnail_url === "string" && card.thumbnail_url ? card.thumbnail_url : null,
        evidence_ref: {screenshot_id: descriptor.id, bbox: [...card.bbox]},
      });
    }
    registerScreenshot(session, descriptor);
    block.observations.push(...additions);
    return block.observations.length;
  }

  function recurringPool(session) {
    const hits = new Map();
    for (const block of session.queryBlocks) {
      for (const card of block.observations) {
        const identity = `shein-us:${card.product_id}`;
        if (!hits.has(identity)) hits.set(identity, {queries: new Set(), organic: [], sponsored: []});
        const value = hits.get(identity);
        value.queries.add(block.query);
        (card.is_ad ? value.sponsored : value.organic).push(card.rank);
      }
    }
    return [...hits]
      .filter(([, value]) => value.queries.size >= 2)
      .sort(([leftIdentity, left], [rightIdentity, right]) => {
        const leftOrganic = left.organic.length ? Math.min(...left.organic) : null;
        const rightOrganic = right.organic.length ? Math.min(...right.organic) : null;
        const leftRank = leftOrganic ?? Math.min(...left.sponsored);
        const rightRank = rightOrganic ?? Math.min(...right.sponsored);
        return right.queries.size - left.queries.size
          || Number(leftOrganic === null) - Number(rightOrganic === null)
          || leftRank - rightRank
          || (leftIdentity < rightIdentity ? -1 : leftIdentity > rightIdentity ? 1 : 0);
      })
      .map(([identity]) => identity);
  }

  function searchesComplete(session) {
    return session.queryBlocks.every((block) => block.observations.length === session.cardLimit);
  }

  function qualifyingCount(session) {
    return session.details.filter((detail) => detail.status === "qualified").length;
  }

  function nextExpectedIdentity(session) {
    if (!searchesComplete(session)) throw new Error("complete all three search queries before detail capture");
    const pool = recurringPool(session);
    if (pool.length < 2) throw new Error("at least two recurring identities are required");
    if (qualifyingCount(session) >= session.resultLimit) throw new Error("task result limit has already been reached");
    if (session.details.length >= pool.length) throw new Error("recurring pool is exhausted");
    return pool[session.details.length];
  }

  function addDetailCapture(session, {screenshot, detail, regions}) {
    if (!detail || typeof detail !== "object" || DETAIL_KEYS.some((key) => !(key in detail)) || Object.keys(detail).length !== DETAIL_KEYS.length) {
      throw new Error("detail has an invalid schema");
    }
    const expectedIdentity = nextExpectedIdentity(session);
    if (detail.identity !== expectedIdentity) throw new Error(`detail must follow recurring-pool order; expected ${expectedIdentity}`);
    const productId = detail.identity.startsWith("shein-us:") ? detail.identity.slice(9) : null;
    const required = detail.status === "qualified" && detail.reason === null ? PURPOSES.qualified : PURPOSES[detail.reason];
    if (!required || (detail.status !== "qualified" && detail.status !== "rejected")) throw new Error("detail status or reason is invalid");
    const detailUrlId = canonicalProductId(detail.detail_url);
    const screenshotUrlId = canonicalProductId(screenshot?.page_url);
    if (!productId) throw new Error("detail screenshot identity binding failed");
    if (detail.reason === "identity_changed") {
      if (!detailUrlId || detailUrlId === productId || detail.product_id !== detailUrlId || screenshotUrlId !== detailUrlId) {
        throw new Error("detail screenshot identity binding failed");
      }
    } else if (detail.reason === "detail_inaccessible") {
      const inaccessibleFields = ["detail_url", "product_id", "title", "category", "sold_display", "reviews_display", "rating_display"];
      if (inaccessibleFields.some((key) => detail[key] !== null)) throw new Error("inaccessible detail cannot claim visible fields");
    } else if (detail.product_id !== productId || detailUrlId !== productId || screenshotUrlId !== productId) {
      throw new Error("detail screenshot identity binding failed");
    }
    if (detail.status === "qualified") {
      if (!["同款", "高度相似", "类似竞品"].includes(detail.match_level) || !Array.isArray(detail.visual_features) || detail.visual_features.length === 0 || detail.visual_features.some((value) => typeof value !== "string" || !value.trim())) {
        throw new Error("qualified detail requires match evidence");
      }
      if (typeof detail.title !== "string" || !detail.title || typeof detail.reviews_display !== "string" || !detail.reviews_display || typeof detail.rating_display !== "string" || !detail.rating_display) {
        throw new Error("qualified detail requires visible title and metrics");
      }
    } else if (detail.match_level !== null || detail.visual_features !== null) {
      throw new Error("rejected detail cannot contain match evidence");
    }
    if (!Array.isArray(regions) || regions.some((region) => !region || !["visual", "metrics", "access_state"].includes(region.purpose) || !validBox(region.bbox))) {
      throw new Error("detail screenshot regions are invalid");
    }
    const present = new Set(regions.map((region) => region.purpose));
    for (const purpose of required) {
      if (!present.has(purpose)) throw new Error(`detail requires ${purpose} screenshot evidence`);
    }
    const descriptor = validateScreenshot(session, screenshot, "detail", null, detail.identity);
    const output = {};
    for (const key of DETAIL_KEYS) {
      output[key] = key === "visual_features" && Array.isArray(detail[key]) ? [...detail[key]] : detail[key];
    }
    output.evidence_refs = regions.map((region) => ({purpose: region.purpose, screenshot_id: descriptor.id, bbox: [...region.bbox]}));
    registerScreenshot(session, descriptor);
    session.details.push(output);
    return output;
  }

  function buildEvidence(session) {
    for (const block of session.queryBlocks) {
      if (block.observations.length < 30 || block.observations.length > 50 || block.observations.length !== session.cardLimit) throw new Error("each query must contain the chosen 30-50 observations");
      if (block.observations.some((card, index) => card.rank !== index + 1)) throw new Error("query ranks must be contiguous");
    }
    const pool = recurringPool(session);
    if (pool.length < 2) throw new Error("at least two recurring identities are required");
    const identities = session.details.map((detail) => detail.identity);
    if (identities.some((identity, index) => identity !== pool[index])) throw new Error("detail outcomes must be an exact recurring-pool prefix in order");
    const qualified = qualifyingCount(session);
    if (qualified > session.resultLimit) throw new Error("detail outcomes continue after the result limit");
    if (qualified === session.resultLimit) {
      const reachedAt = session.details.findIndex((_detail, index) => qualifyingCount({details: session.details.slice(0, index + 1)}) === session.resultLimit);
      if (reachedAt !== session.details.length - 1) throw new Error("detail outcomes continue after the result limit");
    } else if (session.details.length !== pool.length) {
      throw new Error("detail outcomes stopped before the recurring pool was exhausted");
    }
    return {
      task_record_id: session.taskRecordId,
      platform: session.platform,
      screenshots: session.screenshots,
      queries: session.queryBlocks,
      details: session.details,
    };
  }

  function serializeEvidence(session) {
    return `${JSON.stringify(buildEvidence(session), null, 2)}\n`;
  }

  function requireSafeResponse(response) {
    if (!response || typeof response !== "object") throw new Error("Collection stopped: target page did not respond.");
    if (typeof response.error === "string" && response.error) throw new Error(response.error);
    return response;
  }

  function detailCapturePlan(expectedIdentity, visible, operator) {
    const base = {
      identity: expectedIdentity,
      status: "rejected",
      reason: null,
      detail_url: null,
      product_id: null,
      title: null,
      category: null,
      sold_display: null,
      reviews_display: null,
      rating_display: null,
      match_level: null,
      visual_features: null,
    };
    if (visible.blocked) {
      return {detail: {...base, reason: "detail_inaccessible"}, regions: [{purpose: "access_state", bbox: visible.access_bbox}]};
    }
    if (!visible.identity_matches) {
      return {
        detail: {...base, reason: "identity_changed", detail_url: visible.page_url, product_id: visible.product_id, title: visible.title,
          category: visible.category, sold_display: visible.sold_display, reviews_display: visible.reviews_display, rating_display: visible.rating_display},
        regions: [{purpose: "access_state", bbox: visible.access_bbox}],
      };
    }
    const status = operator.status;
    const reason = status === "qualified" ? null : operator.reason;
    const regions = [];
    if (visible.visual_bbox) regions.push({purpose: "visual", bbox: visible.visual_bbox});
    if (visible.metrics_bbox) regions.push({purpose: "metrics", bbox: visible.metrics_bbox});
    return {
      detail: {...base, status, reason, detail_url: visible.page_url, product_id: visible.product_id, title: visible.title,
        category: visible.category, sold_display: visible.sold_display, reviews_display: visible.reviews_display,
        rating_display: visible.rating_display, match_level: status === "qualified" ? operator.matchLevel : null,
        visual_features: status === "qualified" ? operator.visualFeatures : null},
      regions,
    };
  }

  const testExports = globalThis.__SHEIN_COLLECTOR_TEST__;
  if (testExports) {
    Object.assign(testExports, {createSession, addSearchFrame, addDetailCapture, buildEvidence, serializeEvidence, requireSafeResponse, detailCapturePlan, nextExpectedIdentity});
  }

  if (typeof document === "undefined" || typeof chrome === "undefined") return;

  const source = new URL(location.href).searchParams;
  const tabId = Number(source.get("tab"));
  const windowId = Number(source.get("window"));
  let session = null;

  function element(id) { return document.getElementById(id); }
  function setStatus(message) { element("status").textContent = message; }
  function stopWithError(error) {
    const message = error instanceof Error ? error.message : String(error);
    element("error").textContent = message.startsWith("Collection stopped:") ? message : `Collection stopped: ${message}`;
    if (session) session.stopped = true;
    for (const button of document.querySelectorAll("#controls button")) button.disabled = true;
  }
  function clearError() { element("error").textContent = ""; }
  async function sendToPage(message) {
    return requireSafeResponse(await chrome.tabs.sendMessage(tabId, message));
  }
  async function capture() {
    return requireSafeResponse(await chrome.runtime.sendMessage({type: "SHEIN_CAPTURE_VISIBLE", tab_id: tabId, window_id: windowId}));
  }
  function delay(milliseconds) { return new Promise((resolve) => setTimeout(resolve, milliseconds)); }

  element("setup").addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      clearError();
      session = createSession({
        taskRecordId: element("record-id").value,
        platform: element("platform").value,
        queries: [...document.querySelectorAll(".query")].map((input) => input.value),
        cardLimit: Number(element("card-limit").value),
        resultLimit: Number(element("result-limit").value),
      });
      element("setup").hidden = true;
      element("controls").hidden = false;
      setStatus("Ready. Keep the action-clicked SHEIN tab active and open the first exact query URL.");
    } catch (error) { stopWithError(error); }
  });

  element("collect-search").addEventListener("click", async () => {
    try {
      clearError();
      if (!session || session.stopped) throw new Error("start a new collector session");
      const block = session.queryBlocks.find((candidate) => candidate.observations.length < session.cardLimit);
      if (!block) throw new Error("all three search queries are complete");
      let frame = session.screenshots.filter((item) => item.kind === "search" && item.query === block.query).length + 1;
      while (block.observations.length < session.cardLimit) {
        const prior = block.observations.map((card) => ({...card, bbox: card.evidence_ref.bbox}));
        const result = await sendToPage({type: "SHEIN_COLLECTOR_EXTRACT_SEARCH", query: block.query, prior, limit: session.cardLimit});
        const newCards = result.observations.slice(block.observations.length);
        if (newCards.length) {
          const captured = await capture();
          if (captured.page_url !== result.page_url) throw new Error("page changed during screenshot capture");
          const id = `query-${session.queryBlocks.indexOf(block) + 1}-frame-${String(frame).padStart(3, "0")}`;
          addSearchFrame(session, {query: block.query, screenshot: {id, file: `screenshots/${id}.png`, sha256: captured.sha256, page_url: captured.page_url}, cards: newCards});
          session.downloadData.set(`screenshots/${id}.png`, captured.data_url);
          frame += 1;
          setStatus(`${block.query}: ${block.observations.length}/${session.cardLimit} first-visible cards.`);
        }
        if (block.observations.length >= session.cardLimit) break;
        const scroll = await sendToPage({type: "SHEIN_COLLECTOR_SCROLL"});
        if (scroll.at_end && !newCards.length) throw new Error("page ended before 30 visible unique cards were collected");
        await delay(350);
      }
      setStatus(`Completed query ${session.queryBlocks.indexOf(block) + 1}. Navigate the SHEIN tab to the next exact query.`);
    } catch (error) { stopWithError(error); }
  });

  element("capture-detail").addEventListener("click", async () => {
    try {
      clearError();
      if (!session || session.stopped) throw new Error("start a new collector session");
      const identity = nextExpectedIdentity(session);
      const productId = identity.slice(9);
      const visible = await sendToPage({type: "SHEIN_COLLECTOR_EXTRACT_DETAIL", identity});
      const captured = await capture();
      if (captured.page_url !== visible.page_url) throw new Error("page changed during screenshot capture");
      const selectedStatus = element("detail-status").value;
      const reason = selectedStatus === "qualified" ? null : element("detail-reason").value || null;
      const plan = detailCapturePlan(identity, visible, {
        status: selectedStatus,
        reason,
        matchLevel: element("match-level").value || null,
        visualFeatures: element("visual-features").value.split("\n").filter(Boolean),
      });
      const id = `detail-${productId}-${String(session.details.length + 1).padStart(3, "0")}`;
      addDetailCapture(session, {
        screenshot: {id, file: `screenshots/${id}.png`, sha256: captured.sha256, page_url: captured.page_url},
        detail: plan.detail,
        regions: plan.regions,
      });
      session.downloadData.set(`screenshots/${id}.png`, captured.data_url);
      if (visible.blocked) element("error").textContent = visible.blocking_error;
      const completed = qualifyingCount(session) >= session.resultLimit || session.details.length === recurringPool(session).length;
      setStatus(completed ? `Captured detail ${identity}. Evidence is complete and ready to download.` : `Captured detail ${identity}. Next required identity: ${nextExpectedIdentity(session)}.`);
    } catch (error) { stopWithError(error); }
  });

  element("export").addEventListener("click", async () => {
    try {
      clearError();
      if (!session || session.stopped) throw new Error("start a new collector session");
      const root = `find-best-seller-product/${session.taskRecordId}/`;
      for (const descriptor of session.screenshots) {
        const dataUrl = session.downloadData.get(descriptor.file);
        if (!dataUrl) throw new Error("missing screenshot bytes for export");
        await chrome.downloads.download({url: dataUrl, filename: root + descriptor.file, conflictAction: "overwrite", saveAs: false});
      }
      const jsonUrl = URL.createObjectURL(new Blob([serializeEvidence(session)], {type: "application/json"}));
      await chrome.downloads.download({url: jsonUrl, filename: root + "evidence.json", conflictAction: "overwrite", saveAs: false});
      URL.revokeObjectURL(jsonUrl);
      setStatus(`Downloaded ${session.screenshots.length} screenshots and evidence.json.`);
    } catch (error) { stopWithError(error); }
  });
})();
