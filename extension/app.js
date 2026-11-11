import { captureCurrent, captureResponsive, imageSnapshot, targetTab, isDevtools } from "./capture.js";
import { Settings } from "./settings.js";
import { managementReady, managedExtensions, reloadOtherExtension, enableExtension } from "./reload.js";
import { localReport, markdown } from "./report.js";

const $ = (selector) => document.querySelector(selector);
const state = { capture: null, report: null, selectedSnapshot: 0, stop: false, settings: null, busy: false, savedId: null, referenceImage: null };
const severityOrder = ["critical", "high", "medium", "low", "info"];
function element(tag, className, content) {
  const node = document.createElement(tag); if (className) node.className = className;
  if (content !== undefined) node.textContent = String(content); return node;
}
function resetConsent() { $("#ai-consent").checked = false; $("#image-consent").checked = false; }
function setCapture(capture) {
  if (state.referenceImage && !capture.referenceImage) capture = { ...capture, referenceImage: state.referenceImage };
  state.capture = capture; state.report = localReport(capture); state.selectedSnapshot = 0; state.savedId = null;
  resetConsent(); renderReport(); text("#save-status", "Chưa lưu bản quét này. Lưu vào AutomationAAE/reports hoặc sao chép brief.");
  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
}
async function exclusive(work) {
  if (state.busy) return;
  state.busy = true;
  const controls = [...document.querySelectorAll("button:not(#stop-scan), input, textarea, select")];
  const previous = controls.map((node) => node.disabled);
  controls.forEach((node) => { node.disabled = true; });
  try { await work(); }
  finally { controls.forEach((node, index) => { node.disabled = previous[index]; }); state.busy = false; }
}

function text(selector, value) { const node = $(selector); if (node) node.textContent = value ?? ""; }
function show(selector, visible) { const node = $(selector); if (node) node.hidden = !visible; }
function setError(message) { text("#error", message); show("#error", Boolean(message)); }
function setProgress(message, active = true, canStop = false) {
  text("#progress-text", message); show("#progress", active); show("#stop-scan", canStop);
}
function setStatus(selector, message, tone = "") {
  const node = $(selector); if (!node) return;
  node.textContent = message; node.dataset.tone = tone;
}
function safeUrl(url) {
  try { return new URL(url).origin + new URL(url).pathname; } catch { return "Không xác định"; }
}

async function connectFromToken(token) {
  const settings = await Settings.connect(token);
  state.settings = settings;
  renderSettings(settings);
  const shortToken = token.length > 12 ? token.slice(0, 8) + "…" : token;
  setStatus("#connection-badge", `Đã ghép nối · ${shortToken}`, "ok");
  $("#connection-badge").title = `Mã kết nối: ${token} (Bấm để mở/đổi)`;
  $("#pair-token").value = token;
  const tokenDisplay = $("#pair-token-display");
  if (tokenDisplay) {
    tokenDisplay.hidden = false;
    $("#active-pairing-code").textContent = token;
  }
  setStatus("#pair-status", `Đã ghép nối (mã: ${shortToken}). Chỉ lưu hành trên loopback máy này.`, "ok");
  await refreshTarget();
  await refreshHistory();
}

function renderSettings(settings) {
  $("#base-url").value = settings["ai.base_url"] || "";
  $("#model-id").value = settings["ai.model_id"] || "";
  $("#api-key").value = "";
  $("#clear-key").checked = false;
  $("#language").value = settings["audit.language"] || "vi";
  $("#token-parameter").value = settings["ai.token_parameter"] || "max_tokens";
  $("#max-output-tokens").value = settings["ai.max_output_tokens"] || 3500;
  $("#timeout-seconds").value = settings["ai.timeout_seconds"] || 90;
  text("#key-status", settings["ai.has_api_key"] ? "đã lưu · giữ nguyên nếu để trống" : "chưa lưu");
  const token = settings["bridge.pairing_token"] || Settings.getPairingToken();
  if (token) {
    $("#pair-token").value = token;
    const tokenDisplay = $("#pair-token-display");
    if (tokenDisplay) {
      tokenDisplay.hidden = false;
      $("#active-pairing-code").textContent = token;
    }
  }
}

async function refreshTarget() {
  try {
    const tab = await targetTab();
    text("#target-title", tab.title || "Trang đang kiểm tra");
    text("#target-url", tab.url ? safeUrl(tab.url) : "Bề mặt DevTools đang được inspect");
    text("#target-mode", isDevtools ? "DEVTOOLS" : "TAB WEB");
  } catch (error) {
    text("#target-title", "Chưa chọn trang đích");
    text("#target-url", error.message);
    text("#target-mode", isDevtools ? "DEVTOOLS" : "TAB WEB");
  }
}

async function saveAiSettings(event) {
  event.preventDefault();
  setError("");
  try {
    const changes = { "ai.base_url": $("#base-url").value.trim(), "ai.model_id": $("#model-id").value.trim(), "audit.language": $("#language").value,
      "ai.token_parameter": $("#token-parameter").value, "ai.max_output_tokens": Number($("#max-output-tokens").value),
      "ai.timeout_seconds": Number($("#timeout-seconds").value) };
    if ($( "#clear-key").checked) changes["ai.api_key"] = "";
    else if ($( "#api-key").value) changes["ai.api_key"] = $("#api-key").value;
    state.settings = await Settings.saveMany(changes);
    renderSettings(state.settings);
    setStatus("#settings-status", "Đã lưu cấu hình vào SQLite trên máy.", "ok");
  } catch (error) { setStatus("#settings-status", error.message, "error"); }
}

async function loadModels() {
  setError("");
  if (!state.settings) { setStatus("#settings-status", "Ghép nối dịch vụ trước.", "error"); return; }
  if (!window.confirm("Gọi GET /models tới Base URL đã lưu? Gateway có thể ghi nhận lượt gọi; không gửi nội dung trang.")) return;
  try {
    setStatus("#settings-status", "Đang lấy danh sách model…");
    const result = await Settings.request("/api/models", { consent: true });
    const list = $("#model-list"); list.replaceChildren(...result.models.map((model) => { const option = document.createElement("option"); option.value = model; return option; }));
    setStatus("#settings-status", `Đã nạp ${result.models.length} model. Bạn vẫn cần chọn đúng Model ID.`, "ok");
  } catch (error) { setStatus("#settings-status", error.message, "error"); }
}

async function listManagedExtensions() {
  const choice = $("#reload-extension-select");
  const previous = choice.value;
  const entries = await managedExtensions();
  choice.replaceChildren();
  const empty = document.createElement("option");
  empty.value = ""; empty.textContent = entries.length ? "Chọn extension" : "Không tìm thấy extension khác";
  choice.append(empty);
  for (const info of entries) {
    const option = document.createElement("option");
    option.value = info.id;
    option.textContent = `${info.name} · ${info.enabled ? "Đang bật" : "Đang tắt"} · ${info.version}`;
    option.dataset.enabled = String(info.enabled);
    option.dataset.mayDisable = String(info.mayDisable);
    choice.append(option);
  }
  choice.disabled = entries.length === 0;
  if (previous && entries.some(item => item.id === previous)) choice.value = previous;
  updateReloadControls();
  setStatus("#extension-reload-status", `Đã tải ${entries.length} extension. Chỉ reload sau khi bấm nút và xác nhận.`, "ok");
}
function updateReloadControls() {
  const selected = $("#reload-extension-select").selectedOptions[0];
  const isChosen = Boolean(selected?.value);
  $("#reload-selected-extension").disabled = !isChosen || selected?.dataset.enabled !== "true" || selected?.dataset.mayDisable !== "true";
  $("#enable-selected-extension").hidden = !isChosen || selected?.dataset.enabled !== "false";
}
async function grantManagement() {
  try {
    const granted = await managementReady();
    if (!granted) { setStatus("#extension-reload-status", "Chrome chưa cấp quyền quản lý extension.", "error"); return; }
    await listManagedExtensions();
  } catch (error) { setStatus("#extension-reload-status", error.message, "error"); }
}
async function reloadSelected() {
  const choice = $("#reload-extension-select");
  const selected = choice.selectedOptions[0];
  if (!selected?.value || selected.dataset.enabled !== "true" || selected.dataset.mayDisable !== "true") return;
  const label = selected.textContent.split(" · ")[0];
  if (!window.confirm(`Reload "${label}" bằng cách tắt rồi bật lại? Workflow, popup và Side Panel có thể bị gián đoạn. Không nên thực hiện khi đang chạy tác vụ.`)) return;
  try {
    // Pass the user gesture through to chrome.management.setEnabled promptly.
    setStatus("#extension-reload-status", `Đang reload ${label}…`);
    const result = await reloadOtherExtension(selected.value);
    setStatus("#extension-reload-status", result.message, result.status === "reloaded" ? "ok" : "error");
    await listManagedExtensions();
    if (result.status !== "reloaded") setStatus("#extension-reload-status", result.message, "error");
  } catch (error) {
    setStatus("#extension-reload-status", error.message + " Kiểm tra trạng thái tại chrome://extensions.", "error");
    await listManagedExtensions().catch(() => {});
  }
}
async function restoreSelectedExtension() {
  const id = $("#reload-extension-select").value;
  if (!id || !window.confirm("Bật lại extension này bằng Chrome?")) return;
  try {
    const result = await enableExtension(id);
    await listManagedExtensions();
    setStatus("#extension-reload-status", result.status === "enabled" ? "Đã bật lại extension." : "Extension đang bật.", "ok");
  } catch (error) { setStatus("#extension-reload-status", error.message, "error"); }
}

async function runCurrentScan() {
  state.stop = false; setError(""); setProgress("Đang đọc DOM và CSS cục bộ…", true, false);
  try {
    const snapshot = await captureCurrent($("#with-screenshot").checked);
    snapshot.title = snapshot.title || "Trang đang kiểm tra";
    setCapture({ snapshots: [snapshot], objective: $("#objective").value.trim(), source: isDevtools ? "devtools" : "tab", capturedAt: new Date().toISOString() });
    setProgress("Đã quét xong. Hãy xem bằng chứng trước khi gửi AI.", true, false);
  } catch (error) { setProgress("", false); setError(error.message); }
}

async function runResponsiveScan() {
  if (isDevtools) { setError("Trong DevTools, dùng Device Toolbar và quét từng kích thước để giữ đúng phiên inspect."); return; }
  if (!window.confirm("Cho phép AutomationAAE gắn debugger tạm thời để đổi 3 kích thước viewport? Không click, nhập, gửi form hay gửi dữ liệu AI.")) return;
  state.stop = false; setError(""); setProgress("Chuẩn bị đổi viewport…", true, true);
  try {
    const snapshots = await captureResponsive((message) => setProgress(message, true, true), () => state.stop, $("#with-screenshot").checked);
    setCapture({ snapshots, objective: $("#objective").value.trim(), source: "responsive", capturedAt: new Date().toISOString() });
    setProgress("Đã kiểm tra 3 kích thước. Hãy xem bằng chứng trước khi gửi AI.", true, false);
  } catch (error) { setProgress("", false); setError(error.message); }
}

async function importReferenceImage(event) {
  setError("");
  try {
    const file = event.target.files?.[0]; if (!file) return;
    const snapshot = await imageSnapshot(file);
    state.referenceImage = {
      width: snapshot.viewport.width,
      height: snapshot.viewport.height,
      screenshot: snapshot.screenshot,
    };
    text("#reference-image-status", `Đã chọn ảnh mục tiêu ${snapshot.viewport.width} × ${snapshot.viewport.height}. Có thể quét trang hoặc nhập ảnh thực tế để so sánh.`);
    if (state.capture) setCapture({ ...state.capture, referenceImage: state.referenceImage });
    event.target.value = "";
  } catch (error) { setError(error.message); }
}

async function importImage(event) {
  setError("");
  try {
    const file = event.target.files?.[0]; if (!file) return;
    const snapshot = await imageSnapshot(file);
    if ($("#append-image").checked && state.capture) {
      if (state.capture.snapshots.length >= 6) throw new Error("Mỗi báo cáo tối đa 6 trạng thái. Bỏ chọn Gộp ảnh để tạo bản mới.");
      setCapture({ ...state.capture, snapshots: [...state.capture.snapshots, snapshot] });
    } else setCapture({ snapshots: [snapshot], objective: $("#objective").value.trim(), source: "user-image", capturedAt: new Date().toISOString() });
    event.target.value = "";
  } catch (error) { setError(error.message); }
}

function renderReport() {
  if (!state.report) { show("#results", false); return; }
  show("#results", true); setStatus("#result-status", state.report.ai.status === "completed" ? "AI REVIEWED" : "LOCAL SCAN", state.report.ai.status === "completed" ? "ok" : "");
  const counts = state.report.counts;
  $("#stats").replaceChildren(...[["high", "HIGH + CRITICAL"], ["medium", "MEDIUM"], ["low", "LOW"], ["info", "INFO"]].map(([key, label]) => {
    const stat = element("div", "stat stat-" + key);
    stat.append(element("span", "stat-number", (counts[key] || 0) + (key === "high" ? counts.critical || 0 : 0)), element("small", "", label));
    return stat;
  }));
  text("#summary", state.report.ai.summary || "Kết quả quét cục bộ.");
  const tabs = $("#snapshot-tabs"); tabs.replaceChildren(...state.capture.snapshots.map((item, index) => {
    const button = document.createElement("button"); button.type = "button"; button.className = `snapshot-tab ${index === state.selectedSnapshot ? "active" : ""}`;
    button.textContent = `${item.viewport.width} × ${item.viewport.height}`; button.setAttribute("aria-pressed", String(index === state.selectedSnapshot));
    button.addEventListener("click", () => { state.selectedSnapshot = index; renderReport(); }); return button;
  }));
  const previews = $("#screenshot-preview"); previews.replaceChildren();
  for (const item of [state.capture.snapshots[state.selectedSnapshot]]) if (item.screenshot) {
    const figure = document.createElement("figure"); const image = document.createElement("img"); image.src = item.screenshot; image.alt = `Ảnh thực tế ${item.viewport.width} x ${item.viewport.height}`;
    figure.append(element("figcaption", "", "Ảnh thực tế"), image); previews.append(figure);
  }
  if (state.capture.referenceImage?.screenshot) {
    const ref = state.capture.referenceImage;
    const figure = document.createElement("figure"), image = document.createElement("img");
    image.src = ref.screenshot; image.alt = `Ảnh thiết kế mục tiêu ${ref.width} x ${ref.height}`;
    figure.append(element("figcaption", "", "Ảnh mục tiêu / mong muốn"), image); previews.append(figure);
  }
  const evidence = { ...state.capture,
    referenceImage: state.capture.referenceImage ? { width: state.capture.referenceImage.width, height: state.capture.referenceImage.height, screenshot: "[HIDDEN FROM TEXT PREVIEW]" } : undefined,
    snapshots: state.capture.snapshots.map(({ screenshot, ...item }) => item) };
  $("#evidence-preview").textContent = JSON.stringify(evidence, null, 2);
  const filter = $("#severity-filter").value; const findings = state.report.findings.filter((item) => filter === "all" || (filter === "high" ? ["high", "critical"].includes(item.severity) : filter === "medium" ? item.severity === "medium" : ["low", "info"].includes(item.severity)));
  const list = $("#findings"); list.replaceChildren(...findings.map((item, index) => {
    const severity = severityOrder.includes(item.severity) ? item.severity : "info";
    const article = element("article", "finding finding-" + severity);
    const main = element("div", "finding-main"), meta = element("div", "finding-meta");
    meta.append(element("span", "severity", severity), element("span", "", item.category), element("span", "", item.source === "ai" ? "AI observation" : "Rule check"), element("span", "", item.viewport));
    const acceptance = element("p", "acceptance"); acceptance.append(element("b", "", "✓ Nghiệm thu: "), String(item.acceptance || ""));
    main.append(meta, element("h4", "", item.title), element("p", "finding-evidence", `Bằng chứng · ${item.target}\n${item.evidence}`), element("p", "finding-recommendation", `Đề xuất · ${item.recommendation}`), acceptance);
    article.append(element("div", "finding-number", String(index + 1).padStart(2, "0")), main);
    return article;
  }));
  if (!findings.length) list.append(element("p", "help", "Không có finding ở bộ lọc này. Không đồng nghĩa đã kiểm tra mọi tiêu chí UI/UX."));
  const limits = $("#limitations"); limits.replaceChildren(...[...state.report.limitations, ...(state.report.ai.notTested || [])].map((item) => { const li = document.createElement("li"); li.textContent = item; return li; }));
  const nextSteps = state.report.ai.nextSteps || [];
  $("#next-steps").replaceChildren(...nextSteps.map((item) => { const li = document.createElement("li"); li.textContent = item; return li; }));
  show("#next-steps-section", nextSteps.length > 0);
  text("#save-report", state.report.ai.status === "completed" && !state.savedId ? "Lưu lại (không gọi AI) ↓" : "Lưu báo cáo ↓");
  $("#analyze").disabled = state.busy;
}

async function analyzeWithAi() {
  if (!state.capture) return;
  if (!state.settings) { setError("Ghép nối dịch vụ cục bộ trước khi gửi AI."); return; }
  if (!$("#ai-consent").checked) { setError("Cần tick đồng ý gửi bằng chứng văn bản trước khi phân tích AI."); return; }
  const includeImages = $("#image-consent").checked &&
    (state.capture.snapshots.some((item) => item.screenshot) || Boolean(state.capture.referenceImage?.screenshot));
  if (state.capture.snapshots.every((item) => item.kind === "screenshot") && !includeImages) { setError("Báo cáo này chỉ có ảnh. Cần đồng ý gửi ảnh cho model có thị giác, hoặc quét DOM trước."); return; }
  if (!window.confirm(`Gửi ${state.capture.snapshots.length} trạng thái UI${includeImages ? " và ảnh đã xem" : ""} tới model ${state.settings["ai.model_id"] || "chưa chọn"}? Có thể phát sinh chi phí.`)) return;
  setError(""); setProgress("AI đang đọc bằng chứng… không gửi lần thứ hai nếu chờ lâu.", true, false);
  try {
    state.capture.objective = $("#objective").value.trim();
    const outgoing = { ...state.capture, snapshots: state.capture.snapshots.map(({ screenshot, ...item }) => includeImages && screenshot ? { ...item, screenshot } : item) };
    if (!includeImages) delete outgoing.referenceImage;
    const result = await Settings.request("/api/analyze", { capture: outgoing, includeImages, imageConsent: includeImages, consent: true });
    state.report = result.report; state.capture = result.report.capture; state.savedId = result.saved?.id || null;
    resetConsent(); renderReport(); setProgress("AI đã hoàn thành; kiểm tra lại từng nhận xét.", true, false);
    text("#save-status", result.saved ? `Đã lưu cả nhận xét AI và bằng chứng vào reports/${result.saved.markdown}.` : (result.saveError || "AI đã xong nhưng báo cáo chưa được ghi file; hãy lưu lại hoặc tải file."));
    await refreshHistory();
  } catch (error) { setProgress("", false); setError(error.message); }
}

async function saveLocalReport() {
  if (!state.report || !state.settings) { setError("Cần quét và ghép nối dịch vụ trước khi lưu báo cáo."); return; }
  try {
    if (state.savedId) { text("#save-status", `Báo cáo đã lưu: reports/${state.savedId}.md (giữ nguyên kết quả AI).`); return; }
    if (state.report.ai.status === "completed") {
      const result = await Settings.request(`/api/reports/${encodeURIComponent(state.report.id)}`, { retrySave: true });
      state.report = result.report; state.capture = result.report.capture; state.savedId = result.saved?.id || null;
      renderReport();
      text("#save-status", result.saved ? `Đã lưu lại ${result.saved.markdown} mà không gọi lại AI.` : (result.saveError || "Chưa ghi được file; hãy thử lại hoặc tải file."));
      await refreshHistory();
      return;
    }
    state.capture.objective = $("#objective").value.trim();
    const result = await Settings.request("/api/reports", { capture: state.capture });
    state.report = result.report; state.capture = result.report.capture; state.savedId = result.saved?.id || null; resetConsent(); renderReport(); text("#save-status", result.saved ? `Đã lưu ${result.saved.markdown} trong thư mục AutomationAAE/reports trên máy.` : (result.saveError || "Chưa ghi được file; hãy thử lại.")); await refreshHistory();
  } catch (error) { setError(error.message); }
}

async function copyBrief() {
  if (!state.report) return;
  try { await navigator.clipboard.writeText(markdown(state.report)); text("#save-status", "Đã sao chép brief Markdown. Kiểm tra dữ liệu riêng tư trước khi chia sẻ."); }
  catch { setError("Chrome không cho ghi clipboard. Dùng nút Tải Markdown để lưu file."); }
}
function download(name, body, type) {
  const blob = new Blob([body], { type }); const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function exportReport(kind) {
  if (!state.report) return;
  if (kind === "md") download(`${state.report.id}.md`, markdown(state.report), "text/markdown;charset=utf-8");
  else download(`${state.report.id}.json`, JSON.stringify(state.report, null, 2), "application/json;charset=utf-8");
}

async function refreshHistory() {
  const history = $("#history");
  if (!state.settings) { history.textContent = "Ghép nối để xem báo cáo cục bộ. Không tự đăng lên hay gửi tin nhắn tới Codex."; return; }
  try {
    const result = await Settings.request("/api/reports");
    if (!result.reports.length) { history.textContent = "Chưa có báo cáo đã lưu. Sau khi kiểm tra, bấm Lưu báo cáo."; return; }
    history.replaceChildren(...result.reports.map((item) => { const button = element("button", "history-item"); button.type = "button"; button.append(element("span", "", item.id), element("small", "", item.storage === "pending" ? "CHỜ LƯU · KHÔNG GỌI LẠI AI" : "JSON · Markdown")); button.addEventListener("click", () => exclusive(() => loadReport(item.id))); return button; }));
  } catch (error) { history.textContent = error.message; }
}
async function loadReport(id) {
  try { const result = await Settings.request(`/api/reports/${encodeURIComponent(id)}`); state.report = result.report; state.capture = result.report.capture; state.referenceImage = state.capture.referenceImage || null; state.selectedSnapshot = 0; state.savedId = result.storage === "saved" ? id : null; $("#objective").value = state.capture.objective; resetConsent(); renderReport(); text("#save-status", result.storage === "pending" ? "Báo cáo AI đang giữ tạm trong dịch vụ; bấm Lưu báo cáo để ghi file, không gọi lại AI." : `Báo cáo đã lưu: reports/${id}.md`); }
  catch (error) { setError(error.message); }
}

$("#pair-form").addEventListener("submit", (event) => { event.preventDefault(); const token = $("#pair-token").value; exclusive(async () => { setError(""); try { await connectFromToken(token); } catch (error) { setStatus("#pair-status", error.message, "error"); } }); });
$("#settings-form").addEventListener("submit", (event) => { event.preventDefault(); exclusive(() => saveAiSettings(event)); });
$("#load-models").addEventListener("click", () => exclusive(loadModels));
$("#scan-current").addEventListener("click", () => exclusive(runCurrentScan));
$("#reload-self").addEventListener("click", () => {
  if (window.confirm("Reload AutomationAAE ngay? Tab AAE có thể cần mở lại; dữ liệu quét chưa lưu có thể mất.")) chrome.runtime.reload();
});
$("#grant-management").addEventListener("click", grantManagement);
$("#reload-extension-select").addEventListener("change", updateReloadControls);
$("#reload-selected-extension").addEventListener("click", reloadSelected);
$("#enable-selected-extension").addEventListener("click", restoreSelectedExtension);
$("#scan-responsive").addEventListener("click", () => exclusive(runResponsiveScan));
$("#upload-image").addEventListener("click", () => $("#image-input").click());
$("#upload-reference").addEventListener("click", () => $("#reference-image-input").click());
$("#reference-image-input").addEventListener("change", (event) => exclusive(() => importReferenceImage(event)));
$("#image-input").addEventListener("change", (event) => exclusive(() => importImage(event)));
$("#refresh-target").addEventListener("click", refreshTarget);
$("#stop-scan").addEventListener("click", () => { state.stop = true; setProgress("Đang dừng sau bước an toàn…", true, false); });
$("#analyze").addEventListener("click", () => exclusive(analyzeWithAi));
$("#save-report").addEventListener("click", () => exclusive(saveLocalReport));
$("#copy-report").addEventListener("click", copyBrief);
$("#export-json").addEventListener("click", () => exportReport("json"));
$("#export-md").addEventListener("click", () => exportReport("md"));
$("#severity-filter").addEventListener("change", () => state.report && renderReport());
$("#refresh-history").addEventListener("click", refreshHistory);
$("#connection-badge")?.addEventListener("click", (event) => {
  event.preventDefault();
  const target = $("#connection-details");
  if (target) {
    target.open = true;
    target.scrollIntoView({ behavior: "smooth" });
  }
});
$("#auto-token-btn")?.addEventListener("click", async () => {
  try {
    const token = await Settings.getBridgePairingToken();
    if (token) {
      $("#pair-token").value = token;
      await connectFromToken(token);
    } else {
      setStatus("#pair-status", "Không thấy bridge chạy ở port 48765. Hãy bật start-bridge.cmd.", "error");
    }
  } catch (err) {
    setStatus("#pair-status", err.message, "error");
  }
});
$("#copy-token-btn")?.addEventListener("click", () => {
  const code = $("#active-pairing-code")?.textContent || $("#pair-token")?.value;
  if (code) {
    navigator.clipboard.writeText(code);
    setStatus("#pair-status", "Đã sao chép mã kết nối vào clipboard.", "ok");
  }
});

(async () => {
  if (isDevtools) { $("#connection-details").open = false; $("#target-mode").textContent = "DEVTOOLS"; }
  await refreshTarget();
  let paired = false;
  try {
    const settings = await Settings.restore();
    if (settings) {
      state.settings = settings;
      renderSettings(settings);
      const token = settings["bridge.pairing_token"] || Settings.getPairingToken();
      const shortToken = token.length > 12 ? token.slice(0, 8) + "…" : token;
      setStatus("#connection-badge", `Đã ghép nối · ${shortToken}`, "ok");
      $("#connection-badge").title = `Mã kết nối: ${token} (Bấm để xem/đổi)`;
      if (window.innerWidth <= 620) {
        $("#connection-details").open = false;
        if (settings["ai.base_url"]) $("#ai-details").open = false;
      }
      await refreshTarget();
      await refreshHistory();
      paired = true;
    }
  } catch { /* The pairing form remains usable. */ }

  if (!paired) {
    try {
      const autoToken = await Settings.getBridgePairingToken();
      if (autoToken) {
        $("#pair-token").value = autoToken;
        await connectFromToken(autoToken);
      }
    } catch { /* Bridge not active yet */ }
  }
})();
