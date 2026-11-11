// Runtime configuration lives only in the SQLite SettingsRepository.
// chrome.storage.session holds the local pairing capability, not AI configuration.
let pairingToken = "";
const cache = new Map();
const BASE = "http://127.0.0.1:48765";

export const Settings = {
  async connect(token) {
    const candidate = String(token || "").trim();
    if (candidate.length < 20) throw new Error("Nhập mã ghép nối từ cửa sổ dịch vụ cục bộ.");
    const result = await this.request("/api/settings", undefined, candidate);
    pairingToken = candidate;
    cache.clear();
    Object.entries(result.settings).forEach(([key, value]) => cache.set(key, value));
    await chrome.storage.session.set({ pairingToken });
    return result.settings;
  },
  async restore() {
    const stored = await chrome.storage.session.get("pairingToken");
    if (stored.pairingToken) return this.connect(stored.pairingToken);
    return null;
  },
  getPairingToken() { return pairingToken; },
  async getBridgePairingToken() {
    try {
      const response = await fetch(BASE + "/api/pairing-token", {
        method: "GET",
        credentials: "omit",
        cache: "no-store",
      });
      if (response.ok) {
        const data = await response.json();
        return data.pairing_token || "";
      }
    } catch {
      // Bridge is not running or unreachable
    }
    return "";
  },
  get(key) { return cache.get(key); },
  async save(key, value, token) { return this.saveMany({ [key]: value }, token); },
  async saveMany(changes, token) {
    const result = await this.request("/api/settings", { changes }, token || pairingToken);
    Object.entries(result.settings).forEach(([key, value]) => cache.set(key, value));
    return result.settings;
  },
  async request(path, body, token = pairingToken) {
    if (!token) throw new Error("Chưa ghép nối dịch vụ cục bộ.");
    if (!/^\/api\/(settings|health|models|analyze|reports)(\/[a-zA-Z0-9-]+)?$/.test(path)) throw new Error("Đường dẫn không được hỗ trợ.");
    const controller = new AbortController();
    // The server has its own shorter provider timeout and single-flight guard.
    const timer = setTimeout(() => controller.abort(), 195000);
    let response;
    try {
      response = await fetch(BASE + path, { method: body ? "POST" : "GET", headers: { "Authorization": "Bearer " + token,
        ...(body ? { "Content-Type": "application/json" } : {}) }, body: body ? JSON.stringify(body) : undefined,
        credentials: "omit", cache: "no-store", redirect: "error", signal: controller.signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Dịch vụ trả HTTP ${response.status}.`);
      return result;
    } catch (error) {
      if (error instanceof TypeError || error.name === "AbortError") throw new Error("Không kết nối được dịch vụ hoặc hết thời gian. Mở start-bridge.cmd; không tự gửi lại yêu cầu AI đang chạy.");
      throw error;
    } finally { clearTimeout(timer); }
  },
};
