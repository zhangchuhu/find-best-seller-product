"use strict";

const allowedTargets = new Map();

chrome.action.onClicked.addListener((tab) => {
  if (!Number.isInteger(tab.id) || !Number.isInteger(tab.windowId) || !tab.url?.startsWith("https://us.shein.com/")) {
    return;
  }
  allowedTargets.set(tab.id, tab.windowId);
  const collectorUrl = new URL(chrome.runtime.getURL("collector.html"));
  collectorUrl.searchParams.set("tab", String(tab.id));
  collectorUrl.searchParams.set("window", String(tab.windowId));
  chrome.windows.create({url: collectorUrl.href, type: "popup", width: 520, height: 760});
});

function screenshotBytes(dataUrl) {
  const prefix = "data:image/png;base64,";
  if (!dataUrl.startsWith(prefix)) throw new Error("screenshot capture failed");
  const binary = atob(dataUrl.slice(prefix.length));
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function captureRequestedTab(message, sender) {
  const tabId = message?.tab_id;
  const windowId = message?.window_id;
  const collectorPage = chrome.runtime.getURL("collector.html");
  if (!sender?.url?.startsWith(collectorPage)) {
    throw new Error("capture requester is not the local collector");
  }
  if (!Number.isInteger(tabId) || !Number.isInteger(windowId) || allowedTargets.get(tabId) !== windowId) {
    throw new Error("capture requires an explicit SHEIN action click");
  }
  const tab = await chrome.tabs.get(tabId);
  if (tab.windowId !== windowId || !tab.active || !tab.url?.startsWith("https://us.shein.com/")) {
    throw new Error("the clicked SHEIN tab must remain active");
  }
  const dataUrl = await chrome.tabs.captureVisibleTab(windowId, {format: "png"});
  const bytes = screenshotBytes(dataUrl);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const sha256 = [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
  return {data_url: dataUrl, sha256, page_url: tab.url};
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== "SHEIN_CAPTURE_VISIBLE") return false;
  captureRequestedTab(message, sender).then(sendResponse, (error) => {
    sendResponse({error: error instanceof Error ? error.message : "screenshot capture failed"});
  });
  return true;
});
