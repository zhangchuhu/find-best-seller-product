"use strict";

(() => {
  const BLOCK_TEXT = /(?:captcha|verify you are human|security verification|sign\s*in|log\s*in|region (?:is )?(?:unavailable|unsupported)|not available in your (?:country|region))/i;

  function canonicalProductId(value) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.hostname !== "us.shein.com" || url.port) {
        return null;
      }
      const match = /-p-(\d+)\.html$/i.exec(url.pathname);
      return match ? match[1] : null;
    } catch (_error) {
      return null;
    }
  }

  function normalizeBox(rect, viewport) {
    const scale = Number.isFinite(viewport?.scale) && viewport.scale > 0 ? viewport.scale : 1;
    const viewportWidth = Number(viewport?.width);
    const viewportHeight = Number(viewport?.height);
    if (!(viewportWidth > 0) || !(viewportHeight > 0)) {
      return null;
    }
    const left = Math.max(0, Math.floor(Number(rect.left)));
    const top = Math.max(0, Math.floor(Number(rect.top)));
    const right = Math.min(viewportWidth, Math.ceil(Number(rect.right)));
    const bottom = Math.min(viewportHeight, Math.ceil(Number(rect.bottom)));
    if (![left, top, right, bottom].every(Number.isFinite) || right <= left || bottom <= top) {
      return null;
    }
    return [left, top, right - left, bottom - top].map((value) => Math.round(value * scale));
  }

  function classifyVisibleAd(card) {
    const labels = Array.isArray(card?.visibleLabels) ? card.visibleLabels : [];
    const normalized = labels.map((value) => String(value).trim()).filter(Boolean);
    const adLabels = normalized.filter((value) => /^(?:Sponsored|Ad)$/i.test(value));
    const unknownLabels = normalized.filter((value) => !/^(?:Sponsored|Ad)$/i.test(value));
    const organic = card?.visiblyOrganic === true;
    if (unknownLabels.length || (adLabels.length > 0 && organic) || (adLabels.length === 0 && !organic)) {
      throw new Error("ambiguous ad state");
    }
    return adLabels.length > 0;
  }

  function mergeFirstVisible(cards, prior) {
    if (!Array.isArray(cards) || !Array.isArray(prior)) {
      throw new TypeError("cards and prior must be arrays");
    }
    const merged = prior.map((card) => ({...card}));
    const identities = new Set();
    for (const card of merged) {
      const identity = canonicalProductId(card.url);
      if (!identity || (card.product_id != null && String(card.product_id) !== identity)) {
        throw new Error("product identity disagreement");
      }
      identities.add(identity);
    }
    for (const raw of cards) {
      const identity = canonicalProductId(raw?.url);
      if (!identity || (raw.product_id != null && String(raw.product_id) !== identity)) {
        throw new Error("product identity disagreement");
      }
      if (typeof raw.is_ad !== "boolean") {
        throw new Error("ambiguous ad state");
      }
      if (!identities.has(identity)) {
        merged.push({...raw, product_id: identity, rank: merged.length + 1});
        identities.add(identity);
      }
    }
    return merged;
  }

  function isVisible(element) {
    if (!element || typeof element.getBoundingClientRect !== "function") {
      return false;
    }
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= 0 || rect.right <= 0 || rect.top >= innerHeight || rect.left >= innerWidth) {
      return false;
    }
    const style = getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none" && Number(style.opacity || 1) !== 0;
  }

  function visibleText(element) {
    return isVisible(element) ? (element.innerText || element.textContent || "").trim() : "";
  }

  function detectBlockingWall() {
    const candidates = document.querySelectorAll('[role="dialog"], iframe[title], form, h1, h2, [class*="captcha" i], [id*="captcha" i]');
    for (const element of candidates) {
      const text = `${visibleText(element)} ${element.getAttribute?.("title") || ""}`.trim();
      if (text && BLOCK_TEXT.test(text)) {
        return `Collection stopped: blocking page detected (${text.slice(0, 80)}).`;
      }
    }
    return null;
  }

  function candidateCards() {
    const selectors = [
      "[data-goods-id]", "[data-product-id]", ".S-product-item", ".product-card",
      '[class*="product-card" i]', '[class*="product-item" i]',
    ];
    const result = [];
    const seen = new Set();
    for (const element of document.querySelectorAll(selectors.join(","))) {
      if (!seen.has(element) && isVisible(element)) {
        seen.add(element);
        result.push(element);
      }
    }
    return result.sort((left, right) => {
      const a = left.getBoundingClientRect();
      const b = right.getBoundingClientRect();
      return a.top - b.top || a.left - b.left;
    });
  }

  function cardTitle(card, link, image) {
    const elements = card.querySelectorAll('[class*="title" i], [data-title], h2, h3');
    for (const element of elements) {
      const text = visibleText(element);
      if (text) return text;
    }
    return visibleText(link);
  }

  function adState(card) {
    const markers = card.querySelectorAll('[class*="sponsor" i], [class*="advert" i], [data-ad], [aria-label*="sponsor" i]');
    const labels = [];
    let ambiguousMarker = false;
    for (const marker of markers) {
      const text = visibleText(marker);
      if (!text) continue;
      if (/^(?:Sponsored|Ad)$/i.test(text)) labels.push(text);
      else ambiguousMarker = true;
    }
    for (const element of card.querySelectorAll("*")) {
      const text = visibleText(element);
      if (/^(?:Sponsored|Ad)$/i.test(text) && !labels.includes(text)) labels.push(text);
    }
    return classifyVisibleAd({visibleLabels: ambiguousMarker ? [...labels, "ambiguous"] : labels, visiblyOrganic: labels.length === 0 && !ambiguousMarker});
  }

  function extractSearch(query, prior, limit) {
    const blockingError = detectBlockingWall();
    if (blockingError) return {error: blockingError};
    const viewport = {width: innerWidth, height: innerHeight, scale: devicePixelRatio || 1};
    const visible = [];
    for (const card of candidateCards()) {
      const link = [...card.querySelectorAll('a[href*="-p-"][href*=".html"]')].find(isVisible);
      const image = [...card.querySelectorAll("img")].find(isVisible);
      if (!link || !image) continue;
      const url = link.href;
      const productId = canonicalProductId(url);
      const explicit = card.getAttribute("data-goods-id") || card.getAttribute("data-product-id") || productId;
      const title = cardTitle(card, link, image);
      const bbox = normalizeBox(card.getBoundingClientRect(), viewport);
      if (!title || !productId || !bbox) continue;
      try {
        visible.push({
          query,
          is_ad: adState(card),
          title,
          url,
          product_id: explicit,
          thumbnail_url: image.currentSrc || image.src || null,
          bbox,
        });
      } catch (error) {
        return {error: `Collection stopped: ${error.message}.`};
      }
    }
    try {
      const observations = mergeFirstVisible(visible, prior).slice(0, limit);
      return {page_url: location.href, observations};
    } catch (error) {
      return {error: `Collection stopped: ${error.message}.`};
    }
  }

  function firstVisible(selectors) {
    for (const selector of selectors) {
      const element = [...document.querySelectorAll(selector)].find(isVisible);
      if (element) return element;
    }
    return null;
  }

  function extractDetail(expectedIdentity) {
    const blockingError = detectBlockingWall();
    if (blockingError) return {error: blockingError, access_bbox: [0, 0, Math.max(1, innerWidth), Math.max(1, innerHeight)]};
    const productId = canonicalProductId(location.href);
    const actualIdentity = productId ? `shein-us:${productId}` : null;
    const viewport = {width: innerWidth, height: innerHeight, scale: devicePixelRatio || 1};
    const titleElement = firstVisible(["h1", '[class*="product-title" i]', '[class*="goods-title" i]']);
    const imageElement = firstVisible(['[class*="product" i] img', "main img"]);
    const metricElements = [...document.querySelectorAll("button, a, span, div")].filter((element) => {
      const text = visibleText(element);
      return text.length <= 120 && /(?:\d[\d,.]*\s*(?:reviews?|ratings?)|(?:rating\s*)?[0-5](?:\.\d+)\s*(?:\/\s*5)?)/i.test(text);
    });
    const pageText = metricElements.map(visibleText).join(" | ");
    const reviews = pageText.match(/([\d,.]+)\s*(?:reviews?|ratings?)/i)?.[1] || null;
    const rating = pageText.match(/(?:rating\s*)?([0-5](?:\.\d+)?)\s*(?:\/\s*5)?/i)?.[1] || null;
    const sold = pageText.match(/([\d,.]+\+?\s*sold)/i)?.[1] || null;
    const visualBox = imageElement ? normalizeBox(imageElement.getBoundingClientRect(), viewport) : null;
    const metricsRect = metricElements.reduce((box, element) => {
      const rect = element.getBoundingClientRect();
      if (!box) return {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom};
      return {left: Math.min(box.left, rect.left), top: Math.min(box.top, rect.top), right: Math.max(box.right, rect.right), bottom: Math.max(box.bottom, rect.bottom)};
    }, null);
    return {
      page_url: location.href,
      identity: actualIdentity,
      identity_matches: actualIdentity === expectedIdentity,
      product_id: productId,
      title: titleElement ? visibleText(titleElement) : null,
      category: visibleText(firstVisible(['nav[aria-label*="breadcrumb" i]', '[class*="breadcrumb" i]'])) || null,
      sold_display: sold,
      reviews_display: reviews,
      rating_display: rating,
      visual_bbox: visualBox,
      metrics_bbox: metricsRect ? normalizeBox(metricsRect, viewport) : null,
    };
  }

  function scrollPage() {
    const blockingError = detectBlockingWall();
    if (blockingError) return {error: blockingError};
    const before = scrollY;
    const overlap = Math.floor(window.innerHeight * 0.20);
    const step = Math.max(1, window.innerHeight - overlap);
    window.scrollBy({top: step, left: 0, behavior: "instant"});
    return {before, step, at_end: Math.ceil(scrollY + innerHeight) >= document.documentElement.scrollHeight};
  }

  if (globalThis.__SHEIN_COLLECTOR_TEST__) {
    Object.assign(globalThis.__SHEIN_COLLECTOR_TEST__, {
      canonicalProductId,
      normalizeBox,
      classifyVisibleAd,
      mergeFirstVisible,
    });
  }

  if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message?.type === "SHEIN_COLLECTOR_STATUS") {
        sendResponse({error: detectBlockingWall(), page_url: location.href});
      } else if (message?.type === "SHEIN_COLLECTOR_EXTRACT_SEARCH") {
        sendResponse(extractSearch(message.query, message.prior || [], message.limit));
      } else if (message?.type === "SHEIN_COLLECTOR_SCROLL") {
        sendResponse(scrollPage());
      } else if (message?.type === "SHEIN_COLLECTOR_EXTRACT_DETAIL") {
        sendResponse(extractDetail(message.identity));
      }
      return false;
    });
  }
})();
