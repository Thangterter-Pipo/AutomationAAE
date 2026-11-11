import { collectAudit, setPrivacyMasks } from "./scanner.js";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const parameters = new URLSearchParams(location.search);
export const isDevtools = parameters.has("devtools") && Boolean(chrome.devtools?.inspectedWindow);

export async function targetTab() {
  if (isDevtools && chrome.devtools?.inspectedWindow) {
    const id = chrome.devtools.inspectedWindow.tabId;
    return { id, title: "DevTools inspected surface", devtools: true };
  }
  const id = Number(parameters.get("target"));
  if (!Number.isInteger(id) || id <= 0) throw new Error("Mở trang cần kiểm tra, rồi bấm biểu tượng AutomationAAE trên thanh Chrome.");
  const stored = await chrome.storage.session.get("target:" + id);
  const tab = stored["target:" + id];
  if (!tab?.id) throw new Error("Mở trang cần kiểm tra, rồi bấm biểu tượng AutomationAAE trên thanh Chrome.");
  try { return await chrome.tabs.get(tab.id); }
  catch { throw new Error("Tab đã đóng. Mở trang cần kiểm tra và bấm lại biểu tượng extension."); }
}

function devtoolsEval(expression) {
  return new Promise((resolve, reject) => {
    chrome.devtools.inspectedWindow.eval(expression, (result, error) => {
      if (error) reject(new Error("Chrome không cho đọc DOM của bề mặt này. Dùng ảnh chụp hoặc mở đúng DevTools của popup/side panel."));
      else resolve(result);
    });
  });
}

export async function runOnTarget(tab, func, args = []) {
  if (isDevtools) return devtoolsEval(`(${func.toString()})(...${JSON.stringify(args)})`);
  if (!/^https?:\/\//.test(tab.url || "")) {
    throw new Error("Chrome chặn chèn script vào chrome://, Web Store và extension khác. Với extension: Inspect popup/side panel → AAE trong DevTools, hoặc nhập ảnh chụp.");
  }
  try {
    const target = { tabId: tab.id, ...(tab.auditDocumentId ? { documentIds: [tab.auditDocumentId] } : {}) };
    const result = await chrome.scripting.executeScript({ target, func, args, world: "ISOLATED" });
    if (!result[0]?.result) throw new Error("empty");
    if (!result[0].documentId || (tab.auditDocumentId && result[0].documentId !== tab.auditDocumentId)) throw new Error("document changed");
    tab.auditDocumentId = result[0].documentId;
    return result[0].result;
  } catch {
    throw new Error("Không đọc được trang. Trang có thể bị Chrome chặn hoặc đã đổi địa chỉ; bấm lại biểu tượng extension trên trang đích.");
  }
}

export async function captureCurrent(withScreenshot = false) {
  const tab = await targetTab();
  const beforeUrl = tab.url;
  let snapshot;
  if (withScreenshot) {
    if (isDevtools) throw new Error("DevTools không chụp trực tiếp popup/side panel. Hãy nhập ảnh chụp để bổ sung, hoặc dùng quét DOM không ảnh.");
    const previous = (await chrome.tabs.query({ active: true, windowId: tab.windowId }))[0];
    let masked = false;
    try {
      await chrome.tabs.update(tab.id, { active: true });
      await sleep(200);
      const fresh = await chrome.tabs.get(tab.id);
      if (fresh.url !== beforeUrl) throw new Error("Trang đổi địa chỉ trong lúc chụp. Hãy quét lại.");
      // Capture DOM after activating the target; focus handlers can change UI.
      // documentIds pin masks and subsequent checks to this exact document.
      snapshot = await runOnTarget(tab, collectAudit);
      await runOnTarget(tab, setPrivacyMasks, [true]);
      masked = true;
      await sleep(80);
      const active = (await chrome.tabs.query({ active: true, windowId: tab.windowId }))[0];
      if (active?.id !== tab.id) throw new Error("Tab đang xem đã thay đổi; hủy chụp để tránh lấy nhầm trang.");
      snapshot.screenshot = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "jpeg", quality: 80 });
      const after = await chrome.tabs.get(tab.id);
      if (after.url !== beforeUrl) throw new Error("Trang đổi địa chỉ trong lúc chụp; ảnh bị loại bỏ. Quét lại.");
      const activeAfter = (await chrome.tabs.query({ active: true, windowId: tab.windowId }))[0];
      if (activeAfter?.id !== tab.id) throw new Error("Tab đổi trong lúc chụp; ảnh bị loại bỏ để tránh lấy nhầm trang.");
      await runOnTarget(tab, setPrivacyMasks, [false]);
      masked = false;
    } finally {
      if (masked) await runOnTarget(tab, setPrivacyMasks, [false]).catch(() => {});
      if (previous && previous.id !== tab.id) await chrome.tabs.update(previous.id, { active: true }).catch(() => {});
    }
  } else snapshot = await runOnTarget(tab, collectAudit);
  if (!isDevtools && (await chrome.tabs.get(tab.id)).url !== beforeUrl) throw new Error("Trang chuyển địa chỉ trong lúc quét; hãy quét lại để không lẫn bằng chứng.");
  return snapshot;
}

export async function captureResponsive(onProgress, shouldStop, withScreenshot = false) {
  if (isDevtools) throw new Error("Trong DevTools, dùng Device Toolbar đổi kích thước rồi quét từng trạng thái. Không gắn debugger thứ hai.");
  // Must be called immediately from a click so the Chrome permission prompt is valid.
  const allowed = await chrome.permissions.request({ permissions: ["debugger"] });
  if (!allowed) throw new Error("Chưa cấp quyền debugger; vẫn có thể quét màn hình hiện tại.");
  const tab = await targetTab();
  if (!/^https?:\/\//.test(tab.url || "")) throw new Error("Responsive tự động chỉ hỗ trợ tab web thông thường.");
  const target = { tabId: tab.id };
  let attached = false;
  let primaryError = null;
  const snapshots = [];
  try {
    await chrome.debugger.attach(target, "1.3");
    attached = true;
    for (const size of [{ width: 1440, height: 900 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
      if (shouldStop()) throw new Error("Đã dừng quét. Không có yêu cầu AI được gửi.");
      const fresh = await chrome.tabs.get(tab.id);
      if (fresh.url !== tab.url) throw new Error("Trang chuyển địa chỉ; dừng quét để không lẫn bằng chứng.");
      onProgress(`Đang kiểm tra ${size.width} × ${size.height}…`);
      await chrome.debugger.sendCommand(target, "Emulation.setDeviceMetricsOverride", { ...size, deviceScaleFactor: 1, mobile: false });
      await sleep(400);
      const snapshot = await runOnTarget(tab, collectAudit);
      if (Math.abs(snapshot.viewport.width - size.width) > 2 || Math.abs(snapshot.viewport.height - size.height) > 2) throw new Error("Không xác nhận được kích thước viewport; không đánh dấu responsive thành công.");
      if (withScreenshot) {
        try {
          await runOnTarget(tab, setPrivacyMasks, [true]);
          await sleep(100);
          const image = await chrome.debugger.sendCommand(target, "Page.captureScreenshot", { format: "jpeg", quality: 75, captureBeyondViewport: false });
          if (typeof image?.data !== "string") throw new Error("Chrome không trả ảnh hợp lệ.");
          snapshot.screenshot = "data:image/jpeg;base64," + image.data;
          await runOnTarget(tab, setPrivacyMasks, [false]);
        } finally { await runOnTarget(tab, setPrivacyMasks, [false]).catch(() => {}); }
      }
      if ((await chrome.tabs.get(tab.id)).url !== tab.url) throw new Error("Trang chuyển địa chỉ trong khi quét; không dùng dữ liệu này.");
      snapshot.limitations.push("Đổi viewport bằng debugger, không giả lập cảm ứng hay thiết bị thật; không tự thao tác chức năng.");
      snapshots.push(snapshot);
    }
    if (shouldStop()) throw new Error("Đã dừng quét. Không có yêu cầu AI được gửi.");
    return snapshots;
  } catch (error) {
    if (!attached) throw new Error("Không gắn được debugger. Đóng DevTools/debugger khác của tab hoặc dùng quét màn hình hiện tại.");
    primaryError = error;
    throw error;
  } finally {
    if (attached) {
      let cleanupFailed = false;
      try { await chrome.debugger.sendCommand(target, "Emulation.clearDeviceMetricsOverride"); }
      catch { cleanupFailed = true; }
      try { await chrome.debugger.detach(target); }
      catch { cleanupFailed = true; }
      if (cleanupFailed) {
        const cleanupError = "Không xác nhận được việc khôi phục viewport hoặc tháo debugger. Kiểm tra tab đích và tải lại trang nếu cần; không dùng kết quả quét này.";
        // Preserve the scan failure if cleanup also fails; cleanup-only failure
        // still rejects the scan so uncertain viewport evidence is not used.
        if (primaryError instanceof Error) primaryError.message += " " + cleanupError;
        else throw new Error(cleanupError);
      }
    }
  }
}

export async function imageSnapshot(file) {
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > 4 * 1024 * 1024) {
    throw new Error("Chọn PNG, JPG hoặc WebP không quá 4 MB; không nhận SVG.");
  }
  const data = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Không đọc được ảnh."));
    reader.readAsDataURL(file);
  });
  const image = await createImageBitmap(file);
  const viewport = { width: image.width, height: image.height };
  image.close();
  if (viewport.width > 10000 || viewport.height > 20000) throw new Error("Kích thước ảnh vượt giới hạn.");
  return { title: "Ảnh giao diện do người dùng cung cấp", url: "user-supplied-image", viewport, kind: "screenshot", document: {},
    elements: [], findings: [], screenshot: data,
    limitations: ["Chỉ có ảnh: không có DOM/selector đo được, không xác minh chức năng hay accessibility tree.", "Ảnh nhập không tự che thông tin riêng tư; hãy biên tập ảnh trước khi gửi AI."] };
}
