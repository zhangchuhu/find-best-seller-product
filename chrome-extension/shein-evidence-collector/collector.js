"use strict";

(() => {
  const RECORD_ID = /^rec[A-Za-z0-9_-]+$/;
  const DETAIL_KEYS = [
    "identity", "status", "reason", "detail_url", "product_id", "title", "category",
    "sold_display", "reviews_display", "rating_display", "match_level", "visual_features",
  ];
  const REJECTION_REASONS = new Set([
    "visual_structure_mismatch", "imagery_ambiguous_or_inaccessible",
    "metric_missing_or_ambiguous", "threshold_failure", "identity_changed",
    "detail_inaccessible",
  ]);

  function canonicalProductId(value) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.hostname !== "us.shein.com" || url.port) return null;
      return /-p-(\d+)\.html$/i.exec(url.pathname)?.[1] || null;
    } catch (_error) {
      return null;
    }
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

  function createSession({taskRecordId, platform, queries, cardLimit, resultLimit}) {
    if (typeof taskRecordId !== "string" || !RECORD_ID.test(taskRecordId)) throw new Error("invalid task record ID");
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
      queryBlocks: queries.map((query) => ({query, observations: []})),
      details: [],
      stopped: false,
    };
  }

  function addSearchObservations(session, {query, cards, pageUrl = null}) {
    const blockIndex = session.queries.indexOf(query);
    if (blockIndex < 0 || (pageUrl !== null && queryFromUrl(pageUrl) !== query)) throw new Error("search page does not match exact Ark query");
    const block = session.queryBlocks[blockIndex];
    if (!Array.isArray(cards) || block.observations.length + cards.length > session.cardLimit) throw new Error("search observations exceed card limit");
    const seen = new Set(block.observations.map((card) => card.product_id));
    const additions = [];
    for (let index = 0; index < cards.length; index += 1) {
      const card = cards[index];
      const productId = canonicalProductId(card?.url);
      const expectedRank = block.observations.length + index + 1;
      if (!productId || String(card.product_id) !== productId) throw new Error("search card identity disagreement");
      if (card.query !== query || card.rank !== expectedRank || typeof card.is_ad !== "boolean") throw new Error("search cards must have contiguous visible ranks and unambiguous ads");
      if (typeof card.title !== "string" || !card.title) throw new Error("search card visible data is incomplete");
      if (seen.has(productId)) throw new Error("duplicate product in one query");
      if (card.thumbnail_url !== null && (typeof card.thumbnail_url !== "string" || !card.thumbnail_url)) throw new Error("search card thumbnail is invalid");
      seen.add(productId);
      additions.push({
        query: card.query,
        rank: card.rank,
        is_ad: card.is_ad,
        title: card.title,
        url: card.url,
        product_id: productId,
        thumbnail_url: card.thumbnail_url,
      });
    }
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
    if (!searchesComplete(session)) throw new Error("complete all three search queries before detail collection");
    const pool = recurringPool(session);
    if (pool.length < 2) throw new Error("at least two recurring identities are required");
    if (qualifyingCount(session) >= session.resultLimit) throw new Error("task result limit has already been reached");
    if (session.details.length >= pool.length) throw new Error("recurring pool is exhausted");
    return pool[session.details.length];
  }

  function addDetailOutcome(session, detail) {
    if (!detail || typeof detail !== "object" || DETAIL_KEYS.some((key) => !(key in detail)) || Object.keys(detail).length !== DETAIL_KEYS.length) {
      throw new Error("detail has an invalid schema");
    }
    const expectedIdentity = nextExpectedIdentity(session);
    if (detail.identity !== expectedIdentity) throw new Error(`detail must follow recurring-pool order; expected ${expectedIdentity}`);
    const expectedId = expectedIdentity.slice("shein-us:".length);
    const detailUrlId = canonicalProductId(detail.detail_url);
    const qualified = detail.status === "qualified" && detail.reason === null;
    const rejected = detail.status === "rejected" && REJECTION_REASONS.has(detail.reason);
    if (!qualified && !rejected) throw new Error("detail status or reason is invalid");
    if (detail.reason === "identity_changed") {
      if (!detailUrlId || detailUrlId === expectedId || detail.product_id !== detailUrlId) throw new Error("detail identity binding failed");
    } else if (detail.reason === "detail_inaccessible") {
      const absent = ["detail_url", "product_id", "title", "category", "sold_display", "reviews_display", "rating_display"];
      if (absent.some((key) => detail[key] !== null)) throw new Error("inaccessible detail cannot claim visible fields");
    } else if (detail.product_id !== expectedId || detailUrlId !== expectedId) {
      throw new Error("detail identity binding failed");
    }
    if (qualified) {
      if (!(["同款", "高度相似", "类似竞品"].includes(detail.match_level)) || !Array.isArray(detail.visual_features) || detail.visual_features.length === 0 || detail.visual_features.some((value) => typeof value !== "string" || !value.trim())) {
        throw new Error("qualified detail requires match evidence");
      }
      if (typeof detail.title !== "string" || !detail.title || typeof detail.reviews_display !== "string" || !detail.reviews_display || typeof detail.rating_display !== "string" || !detail.rating_display) {
        throw new Error("qualified detail requires visible title and metrics");
      }
    } else if (detail.match_level !== null || detail.visual_features !== null) {
      throw new Error("rejected detail cannot contain match evidence");
    }
    const output = {};
    for (const key of DETAIL_KEYS) output[key] = key === "visual_features" && Array.isArray(detail[key]) ? [...detail[key]] : detail[key];
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

  function detailOutcomePlan(expectedIdentity, visible, operator) {
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
    if (visible.blocked) return {...base, reason: "detail_inaccessible"};
    if (!visible.identity_matches) {
      return {...base, reason: "identity_changed", detail_url: visible.page_url, product_id: visible.product_id, title: visible.title,
        category: visible.category, sold_display: visible.sold_display, reviews_display: visible.reviews_display, rating_display: visible.rating_display};
    }
    const status = operator.status;
    return {...base, status, reason: status === "qualified" ? null : operator.reason, detail_url: visible.page_url,
      product_id: visible.product_id, title: visible.title, category: visible.category, sold_display: visible.sold_display,
      reviews_display: visible.reviews_display, rating_display: visible.rating_display,
      match_level: status === "qualified" ? operator.matchLevel : null,
      visual_features: status === "qualified" ? operator.visualFeatures : null};
  }

  const testExports = globalThis.__SHEIN_COLLECTOR_TEST__;
  if (testExports) {
    Object.assign(testExports, {
      createSession, addSearchObservations, addDetailOutcome, buildEvidence,
      serializeEvidence, requireSafeResponse, detailOutcomePlan, nextExpectedIdentity,
    });
  }

  if (typeof document === "undefined" || typeof chrome === "undefined") return;

  const source = new URL(location.href).searchParams;
  const tabId = Number(source.get("tab"));
  let session = null;

  function element(id) { return document.getElementById(id); }
  function setStatus(message) { element("status").textContent = message; }
  function clearError() { element("error").textContent = ""; }
  function stopWithError(error) {
    const message = error instanceof Error ? error.message : String(error);
    element("error").textContent = message.startsWith("Collection stopped:") ? message : `Collection stopped: ${message}`;
    if (session) session.stopped = true;
    for (const button of document.querySelectorAll("#controls button")) button.disabled = true;
  }
  async function sendToPage(message) {
    return requireSafeResponse(await chrome.tabs.sendMessage(tabId, message));
  }
  function delay(milliseconds) { return new Promise((resolve) => setTimeout(resolve, milliseconds)); }

  async function ensureDedicatedWindow() {
    if (Number.isInteger(tabId) && tabId > 0) return true;
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!Number.isInteger(tab?.id) || !tab.url?.startsWith("https://us.shein.com/")) throw new Error("open the target us.shein.com tab before launching the collector");
    const url = new URL(chrome.runtime.getURL("collector.html"));
    url.searchParams.set("tab", String(tab.id));
    await chrome.windows.create({url: url.href, type: "popup", width: 520, height: 760});
    window.close();
    return false;
  }

  ensureDedicatedWindow().then((ready) => {
    if (!ready) return;
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
        setStatus("Ready. Keep the selected SHEIN tab open on the first exact query.");
      } catch (error) { stopWithError(error); }
    });

    element("collect-search").addEventListener("click", async () => {
      try {
        clearError();
        if (!session || session.stopped) throw new Error("start a new collector session");
        const block = session.queryBlocks.find((candidate) => candidate.observations.length < session.cardLimit);
        if (!block) throw new Error("all three search queries are complete");
        while (block.observations.length < session.cardLimit) {
          const result = await sendToPage({type: "SHEIN_COLLECTOR_EXTRACT_SEARCH", query: block.query, prior: block.observations, limit: session.cardLimit});
          if (queryFromUrl(result.page_url) !== block.query) throw new Error("current page does not match the exact Ark query");
          const newCards = result.observations.slice(block.observations.length);
          if (newCards.length) {
            addSearchObservations(session, {query: block.query, cards: newCards, pageUrl: result.page_url});
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

    element("collect-detail").addEventListener("click", async () => {
      try {
        clearError();
        if (!session || session.stopped) throw new Error("start a new collector session");
        const identity = nextExpectedIdentity(session);
        const visible = await sendToPage({type: "SHEIN_COLLECTOR_EXTRACT_DETAIL", identity});
        const selectedStatus = element("detail-status").value;
        const outcome = detailOutcomePlan(identity, visible, {
          status: selectedStatus,
          reason: selectedStatus === "qualified" ? null : element("detail-reason").value || null,
          matchLevel: element("match-level").value || null,
          visualFeatures: element("visual-features").value.split("\n").filter(Boolean),
        });
        addDetailOutcome(session, outcome);
        if (visible.blocked) element("error").textContent = visible.blocking_error;
        const completed = qualifyingCount(session) >= session.resultLimit || session.details.length === recurringPool(session).length;
        setStatus(completed ? `Collected detail ${identity}. Evidence is complete.` : `Collected detail ${identity}. Next required identity: ${nextExpectedIdentity(session)}.`);
      } catch (error) { stopWithError(error); }
    });

    element("export").addEventListener("click", async () => {
      try {
        clearError();
        if (!session || session.stopped) throw new Error("start a new collector session");
        const root = `find-best-seller-product/${session.taskRecordId}/`;
        const jsonUrl = URL.createObjectURL(new Blob([serializeEvidence(session)], {type: "application/json"}));
        await chrome.downloads.download({url: jsonUrl, filename: root + "evidence.json", conflictAction: "overwrite", saveAs: false});
        URL.revokeObjectURL(jsonUrl);
        setStatus("Downloaded evidence.json.");
      } catch (error) { stopWithError(error); }
    });
  }).catch(stopWithError);
})();
