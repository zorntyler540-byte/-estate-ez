/**
 * Open the Estate-Ez UI in the side panel when the toolbar icon is clicked so it stays visible
 * while you click Save / navigate steps on CT Seller (the action popup would close immediately).
 */
function enableSidePanelOnActionClick() {
  if (!chrome.sidePanel?.setPanelBehavior) return;
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
}

chrome.runtime.onInstalled.addListener(enableSidePanelOnActionClick);
chrome.runtime.onStartup.addListener(enableSidePanelOnActionClick);
