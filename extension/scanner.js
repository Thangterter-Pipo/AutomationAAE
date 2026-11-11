// Self-contained: the same function runs via scripting.executeScript and DevTools.
// Never reads values, cookies, storage, network, script bodies or form data.
export function collectAudit() {
  const limit = 4500;
  const maxEvidence = 350;
  const findings = [];
  const elements = [];
  const counts = new Map();
  const viewport = { width: Math.round(innerWidth), height: Math.round(innerHeight) };
  const sensitive = 'input,textarea,[contenteditable]:not([contenteditable="false"]),[data-private],[data-sensitive],[autocomplete="one-time-code"]';
  const clean = (value, max = 180) => String(value || "")
    .replace(/\b(?:sk-|AIza)[A-Za-z0-9_\-]{12,}/g, "[REDACTED]")
    .replace(/\bBearer\s+[\w.\-/+=]{6,}/gi, "Bearer [REDACTED]")
    .replace(/[\w.+\-]+@[\w.\-]+\.[A-Za-z]{2,}/g, "[EMAIL]")
    .replace(/\b\d(?:[ -]?\d){12,18}\b/g, "[NUMBER]")
    .replace(/https?:\/\/[^\s<>"')]+/g, (url) => url.split(/[?#]/)[0].replace(/^(https?:\/\/)[^/@]+@/, "$1[REDACTED]@"))
    .replace(/\s+/g, " ").trim().slice(0, max);
  function visible(element) {
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0 || element.closest('[hidden],[inert],[aria-hidden="true"]')) return false;
    for (let parent = element, depth = 0; parent && depth < 24; parent = parent.parentElement, depth++) {
      const style = getComputedStyle(parent);
      if (style.display === "none" || style.visibility !== "visible" || Number(style.opacity) === 0) return false;
    }
    return true;
  }
  function safeText(element) {
    if (!element || element.closest(sensitive)) return "";
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        return parent && !parent.closest('script,style,noscript,template,[hidden],[aria-hidden="true"],' + sensitive) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    let text = "", node, visited = 0;
    while ((node = walker.nextNode()) && text.length < 260 && visited++ < 50) text += " " + node.nodeValue;
    return clean(text);
  }
  function accessibleName(element) {
    if (element.closest('[data-private],[data-sensitive]')) return "";
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const name = labelledBy.split(/\s+/).slice(0, 8).map((id) => safeText(document.getElementById(id))).join(" ").trim();
      if (name) return name;
    }
    const aria = clean(element.getAttribute("aria-label"));
    if (aria) return aria;
    const labels = element.labels ? [...element.labels].map(safeText).join(" ").trim() : "";
    if (labels) return labels;
    const imageName = [...element.querySelectorAll("img[alt]")].slice(0, 3).map((image) => clean(image.getAttribute("alt"))).join(" ");
    return safeText(element) || imageName || clean(element.getAttribute("title"));
  }
  function selector(element) {
    if (element === document.documentElement) return "html";
    if (element === document.body) return "body";
    const parts = [];
    for (let current = element; current && current !== document.body && parts.length < 5; current = current.parentElement) {
      const tag = current.localName;
      const siblings = current.parentElement ? [...current.parentElement.children].filter((item) => item.localName === tag) : [];
      parts.unshift(tag + (siblings.length > 1 ? `:nth-of-type(${siblings.indexOf(current) + 1})` : ""));
    }
    return (parts.length === 5 && element.parentElement ? "" : "body > ") + parts.join(" > ");
  }
  function issue(rule, severity, category, title, element, evidence, recommendation, acceptance) {
    const count = counts.get(rule) || 0;
    counts.set(rule, count + 1);
    if (count >= 10 || findings.length >= 150) return;
    findings.push({ id: rule + "-" + (count + 1), severity, category, title, target: selector(element),
      evidence, recommendation, acceptance, source: "rule", viewport: `${viewport.width} x ${viewport.height}` });
  }
  const parseColor = (value) => {
    const match = value.match(/^rgba?\(([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)(?:\s*[,/]\s*([\d.]+))?\)$/);
    return match ? [Number(match[1]), Number(match[2]), Number(match[3]), match[4] === undefined ? 1 : Number(match[4])] : null;
  };
  const blend = (fg, bg) => [0, 1, 2].map((index) => fg[index] * fg[3] + bg[index] * (1 - fg[3]));
  const luminance = (rgb) => rgb.map((part) => part / 255).map((part) => part <= 0.04045 ? part / 12.92 : ((part + 0.055) / 1.055) ** 2.4)
    .reduce((total, part, index) => total + part * [0.2126, 0.7152, 0.0722][index], 0);
  function contrastRatio(element, style) {
    const stack = [];
    for (let parent = element; parent; parent = parent.parentElement) {
      const current = getComputedStyle(parent);
      if (current.backgroundImage !== "none" || Number(current.opacity) < 1 || current.mixBlendMode !== "normal" || (current.filter && current.filter !== "none")) return null;
      const bg = parseColor(current.backgroundColor);
      if (!bg) return null;
      stack.unshift(bg);
    }
    let background = [255, 255, 255];
    for (const color of stack) background = blend(color, background);
    const foreground = parseColor(style.color);
    if (!foreground) return null;
    const lightA = luminance(blend(foreground, background)), lightB = luminance(background);
    return (Math.max(lightA, lightB) + 0.05) / (Math.min(lightA, lightB) + 0.05);
  }
  const all = [...document.querySelectorAll("body *")];
  const headings = [];
  let scanCount = 0, shadowCount = 0, contrastSkipped = 0;
  for (const element of all.slice(0, limit)) {
    if (element.matches("script,style,noscript,template") || !visible(element)) continue;
    scanCount++;
    if (element.shadowRoot) shadowCount++;
    if (element.closest('[data-private],[data-sensitive]')) continue;
    const tag = element.localName;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    const interactive = element.matches('button,a[href],input:not([type="hidden"]),select,textarea,[role="button"],[role="link"],[role="textbox"],[role="combobox"],[role="checkbox"],[role="radio"],[role="switch"]');
    const disabled = element.matches(":disabled") || element.getAttribute("aria-disabled") === "true";
    const heading = /^h[1-6]$/.test(tag);
    const text = safeText(element);
    const directText = [...element.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && node.nodeValue.trim());
    let contrast = null;
    if (directText && text && !disabled && !element.closest('svg,canvas') && (!style.textShadow || style.textShadow === "none")) {
      contrast = contrastRatio(element, style);
      const size = parseFloat(style.fontSize);
      const threshold = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700) ? 3 : 4.5;
      if (contrast !== null && contrast < threshold - 0.01) {
        issue("CONTRAST", "high", "accessibility", "Chữ có độ tương phản thấp", element,
          `Tương phản tính từ CSS ${contrast.toFixed(2)}:1; ngưỡng chữ này ${threshold}:1; cỡ ${style.fontSize}. Không tính ảnh nền/chồng lấp.`,
          "Điều chỉnh màu chữ hoặc nền; xác nhận lại trên giao diện render thực tế.", `Đo lại tỷ lệ tương phản tối thiểu ${threshold}:1 ở trạng thái đang xét.`);
      }
      if (contrast === null) contrastSkipped++;
      if (size < 12 && size > 0) issue("SMALL-TEXT", "low", "readability", "Chữ nhỏ cần xem lại", element,
        `Cỡ chữ CSS ${style.fontSize}. Đây là gợi ý khả năng đọc, không phải kết luận vi phạm WCAG.`,
        "Tăng chữ phụ quan trọng hoặc thử zoom 200% và kiểm tra khả năng đọc.", "Nội dung quan trọng đọc rõ ở zoom 200% và không mất chức năng.");
    }
    const name = interactive ? accessibleName(element) : "";
    if (interactive && !disabled) {
      if (!name) issue("NAME", "high", "accessibility", "Điều khiển thiếu tên truy cập", element,
        `Không tìm thấy nhãn qua aria-labelledby, aria-label, label, chữ nút, alt hoặc title trên <${tag}>.`,
        "Bổ sung nhãn hiển thị hoặc tên truy cập mô tả hành động; kiểm tra bằng accessibility tree.",
        "Trình đọc màn hình đọc được tên có ý nghĩa, phù hợp chữ hiển thị (nếu có).");
      if (rect.width < 24 || rect.height < 24) issue("TARGET", "medium", "usability", "Vùng bấm nhỏ hơn 24 CSS px", element,
        `Kích thước ${Math.round(rect.width)} x ${Math.round(rect.height)} px; chưa kiểm tra ngoại lệ khoảng cách hoặc liên kết trong câu.`,
        "Tăng vùng tương tác hoặc xác nhận ngoại lệ/spacing theo WCAG 2.5.8; ưu tiên 44 px cho cảm ứng.",
        "Mục tiêu đạt ít nhất 24 x 24 CSS px hoặc chứng minh được ngoại lệ spacing/inline.");
      if (Number(element.getAttribute("tabindex")) > 0) issue("TABINDEX", "medium", "accessibility", "Thứ tự focus bị ép bằng tabindex dương", element,
        `tabindex=${element.getAttribute("tabindex")}; không chạy thử thứ tự bàn phím.`,
        "Dùng thứ tự DOM hợp lý với tabindex 0/-1 thay cho số dương.", "Dùng Tab và Shift+Tab kiểm tra thứ tự hợp lý, không nhảy bất ngờ.");
      if (element.matches('input:not([type="button"]):not([type="submit"]):not([type="reset"]),select,textarea') && !element.labels?.length && !element.getAttribute("aria-label") && !element.getAttribute("aria-labelledby")) {
        issue("LABEL", "high", "forms", "Trường nhập thiếu nhãn liên kết", element,
          `Không có <label> liên kết hoặc aria-label/aria-labelledby. Không đọc giá trị ô nhập.`,
          "Liên kết label bằng for/id hoặc nhãn ARIA khi không thể hiển thị nhãn.", "Nhãn còn nhận biết được sau khi nhập và được đọc khi focus.");
      }
    }
    if (tag === "img" && !element.hasAttribute("alt") && !element.getAttribute("aria-label") && !element.getAttribute("aria-labelledby") && !["presentation", "none"].includes(element.getAttribute("role"))) {
      issue("ALT", "high", "accessibility", "Ảnh thiếu mô tả thay thế", element,
        "Thẻ img không có alt hay tên ARIA; không đọc đường dẫn ảnh.",
        "Thêm alt có ý nghĩa cho ảnh thông tin; đặt alt rỗng cho ảnh trang trí.", "Ảnh thông tin có tên tương đương; ảnh trang trí không gây nhiễu trình đọc màn hình.");
    }
    if (heading) headings.push({ level: Number(tag[1]), element });
    if (elements.length < maxEvidence && (interactive || heading || tag === "img" || (directText && text))) {
      elements.push({ tag, role: clean(element.getAttribute("role"), 60), name, text: element.matches(sensitive) ? "[INPUT OMITTED]" : text,
        selector: selector(element), size: `${Math.round(rect.width)} x ${Math.round(rect.height)}`,
        fontSize: style.fontSize, contrast: contrast === null ? "not measured" : contrast.toFixed(2) });
    }
  }
  const root = document.documentElement;
  if (!root.lang.trim()) issue("LANG", "medium", "accessibility", "Trang chưa khai báo ngôn ngữ", root,
    "html không có thuộc tính lang hữu ích.", "Đặt lang đúng với ngôn ngữ chính của trang.", "Trình đọc màn hình chọn đúng giọng đọc khi mở trang.");
  if (!document.title.trim()) issue("TITLE", "medium", "navigation", "Trang chưa có tiêu đề", root,
    "document.title đang rỗng.", "Đặt tiêu đề mô tả màn hình/trang hiện tại.", "Tab trình duyệt hiển thị tên duy nhất và có ý nghĩa.");
  if (root.scrollWidth > viewport.width + 4) issue("OVERFLOW", "medium", "responsive", "Nội dung tràn ngang viewport", document.body || root,
    `scrollWidth=${root.scrollWidth}px, viewport=${viewport.width}px. Canvas/bảng có thể cố ý cuộn ngang.`,
    "Tìm phần tử gây tràn; giới hạn cuộn ngang trong vùng chuyên dụng, giữ nội dung và thao tác chính trong màn hình.",
    "Không có cuộn ngang toàn trang ngoài vùng có chủ đích, kể cả khi zoom 200%.");
  for (let i = 1; i < headings.length; i++) {
    if (headings[i].level > headings[i - 1].level + 1) issue("HEADING", "low", "structure", "Cấp heading nhảy bậc", headings[i].element,
      `Từ h${headings[i - 1].level} sang h${headings[i].level} trong thứ tự DOM; cần xem ngữ cảnh.`,
      "Sắp xếp heading theo cấu trúc nội dung, không dùng heading chỉ để đổi cỡ chữ.", "Outline heading thể hiện được quan hệ cha-con hợp lý.");
  }
  // Portable relationship QA, mirrored by backend/engine/spatial_relations.py.
  // Explicit contracts work for any framework; the optional graph adapter
  // detects node port/button/handle displacement without reading user input.
  const pos = el => {
    const r = el.getBoundingClientRect();
    return { x: r.left+r.width/2, y: r.top+r.height/2, left:r.left, right:r.right };
  };
  const isRendered = el => {
    if (!el || !el.isConnected) return false;
    const r=el.getBoundingClientRect(), s=getComputedStyle(el);
    return r.width>0 && r.height>0 && s.display!=="none" && s.visibility!=="hidden"
      && Number(s.opacity)>0 && !el.closest('[hidden],[inert],[aria-hidden="true"]');
  };
  for (const element of [...document.querySelectorAll('[data-aae-align-with]')].slice(0,120)) {
    if (!isRendered(element)) continue;
    let peer = null;
    try { peer=document.querySelector(element.getAttribute('data-aae-align-with')); } catch {}
    if (!isRendered(peer)) continue;
    const a=pos(element), b=pos(peer), axis=element.getAttribute('data-aae-align')||'center-y';
    const diff = ({
      'center':Math.hypot(a.x-b.x,a.y-b.y), 'center-x':Math.abs(a.x-b.x),
      'center-y':Math.abs(a.y-b.y), left:Math.abs(a.left-b.left),right:Math.abs(a.right-b.right),
      top:Math.abs(element.getBoundingClientRect().top-peer.getBoundingClientRect().top),
      bottom:Math.abs(element.getBoundingClientRect().bottom-peer.getBoundingClientRect().bottom)
    })[axis];
    const tolerance=Math.min(30,Math.max(1,Number(element.getAttribute('data-aae-tolerance'))||6));
    if (diff!==undefined && diff>tolerance) issue("SPATIAL-ALIGN","high","layout",
      "Hai thành phần liên quan bị lệch vị trí",element,
      "OBSERVED: Quan hệ "+axis+" lệch "+diff.toFixed(1)+"px; ngưỡng "+tolerance+"px; "+
      selector(peer),"Căn lại hai thành phần theo constraint khai báo.",
      "Quan hệ hình học đo được không vượt ngưỡng ở các viewport.");
  }
  const graphNodeSelector='[data-aae-node],.react-flow__node,.canvas-node,.diagram-node,[data-graph-node]';
  for (const anchor of [...document.querySelectorAll('[data-aae-port-role="anchor"],.single-port-anchor,[class*="port-anchor"]')].slice(0,120)) {
    if (!isRendered(anchor)) continue;
    const node=anchor.closest(graphNodeSelector);
    const button=anchor.querySelector('[data-aae-port-role="button"],button,[role="button"]');
    const handles=[...anchor.querySelectorAll('[data-aae-port-role="handle"],.react-flow__handle,[class*="port-handle"]')].filter(isRendered);
    if (!isRendered(node)||!isRendered(button)||!handles.length) continue;
    const a=pos(button);
    const handle=handles.sort((x,y) => {
      const q=pos(x),r=pos(y);
      return Math.hypot(q.x-a.x,q.y-a.y)-Math.hypot(r.x-a.x,r.y-a.y);
    })[0];
    const h=pos(handle),n=pos(node);
    const mismatch=Math.hypot(a.x-h.x,a.y-h.y);
    if (mismatch>6) issue("SPATIAL-PORT-HANDLE","high","layout",
      "Cổng điều khiển lệch điểm kết nối thật",anchor,
      "OBSERVED: Tâm button và handle lệch "+mismatch.toFixed(1)+"px; handle "+selector(handle)+".",
      "Đồng bộ vị trí nút/cổng với handle; kiểm tra flex order, left/right, transform.",
      "Tâm button và handle lệch không quá 6px ở viewport tương ứng.");
    const cls=String(anchor.className?.baseVal||anchor.className||'').toLowerCase().split(/\\s+/);
    const rawSide=(anchor.getAttribute('data-aae-port-side')||'').toLowerCase();
    const side=['in','input','left'].includes(rawSide)||cls.some(x=>['in','input','left'].includes(x))?'in':
      ['out','output','right'].includes(rawSide)||cls.some(x=>['out','output','right'].includes(x))?'out':null;
    const edgeCenter=cls.includes('single-port-anchor') || anchor.getAttribute('data-aae-layout')==='edge-center';
    if (edgeCenter && side) {
      const edge=Math.abs(a.x-(side==='in'?n.left:n.right));
      const mid=Math.abs(a.y-n.y);
      if (edge>6||mid>6) issue("SPATIAL-PORT-EDGE","medium","layout",
        "Cổng kết nối lệch khỏi cạnh hoặc tâm node",anchor,
        "OBSERVED: lệch cạnh "+edge.toFixed(1)+"px, lệch tâm dọc "+mid.toFixed(1)+"px.",
        "Căn cổng về cạnh "+side+" và tâm node theo ràng buộc.",
        "Tâm cổng không cách cạnh hoặc tâm dọc quá 6px.");
    }
  }
  const limitations = [
    "Kiểm tra quan hệ hình học theo contract hoặc adapter DOM công khai; không suy đoán căn chỉnh của mọi thành phần.",
    "Quét DOM/CSS tĩnh; không click, nhập liệu, gửi form hay kiểm tra luồng người dùng.",
    "Không đọc giá trị input/textarea/contenteditable, script, cookie, storage hay request mạng.",
    "Tên truy cập là phép xấp xỉ; cần xác minh bằng accessibility tree. Không kiểm tra focus trap/keyboard order.",
    "Không đo contrast khi có ảnh nền, gradient, opacity, filter hoặc blend; phép đo không mô phỏng mọi lớp chồng.",
    `Đã quét ${scanCount} phần tử render trong tối đa ${limit}; giữ tối đa ${maxEvidence} bằng chứng và 10 ví dụ mỗi quy tắc.`,
  ];
  if (all.length > limit) limitations.push(`DOM có ${all.length} phần tử; phần sau giới hạn chưa được quét.`);
  if (shadowCount) limitations.push(`${shadowCount} shadow root chưa được đi sâu; nội dung shadow DOM chưa được kiểm tra.`);
  const frames = document.querySelectorAll("iframe,frame").length;
  if (frames) limitations.push(`${frames} iframe/frame chưa được quét.`);
  if (document.querySelector("canvas")) limitations.push("Canvas chỉ được xem bằng ảnh nếu người dùng cung cấp; không có DOM bên trong canvas.");
  if (contrastSkipped) limitations.push(`${contrastSkipped} phần tử chữ bỏ qua phép đo tương phản do nền/hiệu ứng phức tạp.`);
  return { title: clean(document.title, 200), url: location.origin + location.pathname, viewport, kind: "dom",
    document: { lang: root.lang, title: clean(document.title, 200), scrollWidth: root.scrollWidth, scrollHeight: root.scrollHeight,
      elementCount: all.length, scannedCount: scanCount }, elements, findings, limitations };
}

// Temporary masks are reversible and have a fail-safe timeout. No input values read.
export function setPrivacyMasks(enabled) {
  const key = "__automationAaePrivacyMasks";
  if (window[key]) { window[key].cleanup(); delete window[key]; }
  if (!enabled) return { ok: true };
  const host = document.createElement("div");
  host.setAttribute("data-automation-aae-mask", "true");
  host.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647;";
  const shadow = host.attachShadow({ mode: "closed" });
  for (const element of document.querySelectorAll('input,textarea,[contenteditable]:not([contenteditable="false"]),[data-private],[data-sensitive]')) {
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0 || rect.bottom < 0 || rect.top > innerHeight) continue;
    const block = document.createElement("div");
    block.style.cssText = `position:fixed;left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px;background:#292c35;color:#fff;overflow:hidden;font:12px sans-serif;display:grid;place-items:center;border-radius:3px;`;
    block.textContent = "Private field";
    shadow.append(block);
  }
  document.documentElement.append(host);
  const timer = setTimeout(() => { host.remove(); delete window[key]; }, 8000);
  window[key] = { cleanup() { clearTimeout(timer); host.remove(); } };
  return { ok: true, masked: shadow.childElementCount };
}
