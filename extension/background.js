chrome.action.onClicked.addListener(async (tab) => {
  if (!Number.isInteger(tab.id)) return;
  // Each workspace has its own target, so a second review cannot retarget the first.
  await chrome.storage.session.set({ ["target:" + tab.id]: { id: tab.id, windowId: tab.windowId } });
  await chrome.tabs.create({ url: chrome.runtime.getURL("app.html") + "?target=" + tab.id });
});
