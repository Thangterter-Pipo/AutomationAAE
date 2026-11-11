"""CLI tool to audit any target URL or local HTML file using the AutomationAAE engine."""

import argparse
import base64
import functools
import hashlib
import json
import os
import re
import sys
import threading
from urllib.parse import unquote, urlsplit
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend.audit import (
    AuditError,
    build_report,
    evaluate,
    sanitize_capture,
    save_report,
)
from backend.engine.image_target import analyze_target_image, compare_target_image, IMAGE_TYPES
from backend.engine import (
    RuntimeCollector,
    analyze_css,
    compute_acceptance_matrix,
    compute_advanced_regression,
    run_interaction_suite,
    audit_spatial_relations,
)
from db.repositories import SettingsRepository

DEFAULT_CHROMIUM = os.environ.get("AAE_CHROMIUM_PATH") or None
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "tablet": {"width": 768, "height": 1024},
    "mobile": {"width": 390, "height": 844},
}


def load_scanner_script():
    scanner_path = ROOT / "extension" / "scanner.js"
    source = scanner_path.read_text(encoding="utf-8")
    cleaned = re.sub(r"\bexport\s+function\s+", "function ", source)
    return f"""
    (() => {{
        {cleaned}
        return collectAudit();
    }})()
    """


def normalize_url_key(url: str) -> str:
    cleaned = url.split("?")[0].rstrip("/")
    # Normalize local loopback ephemeral ports to match across runs
    cleaned = re.sub(r"http://127\.0\.0\.1:\d+", "http://127.0.0.1:LOCAL", cleaned)
    return cleaned


def find_previous_report(target_url: str, source_identity: str = ""):
    reports_dir = ROOT / "reports"
    json_files = sorted(reports_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    target_key = normalize_url_key(target_url)
    for file in json_files:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            snapshots = data.get("capture", {}).get("snapshots", [])
            same_target = any(normalize_url_key(s.get("url", "")) == target_key for s in snapshots)
            if same_target and (not source_identity or data.get("capture", {}).get("source") == source_identity):
                return file, data
        except Exception:
            continue
    return None, None


def compute_regression_diff(prev_data, current_report):
    prev_findings = prev_data.get("findings", [])
    curr_findings = current_report.get("findings", [])

    prev_map = {(f.get("title", "").strip(), f.get("target", "").strip()): f for f in prev_findings}
    curr_map = {(f.get("title", "").strip(), f.get("target", "").strip()): f for f in curr_findings}

    resolved = [f for key, f in prev_map.items() if key not in curr_map]
    new_issues = [f for key, f in curr_map.items() if key not in prev_map]
    persisting = [f for key, f in curr_map.items() if key in prev_map]

    return {
        "prev_id": prev_data.get("id", "previous"),
        "prev_count": len(prev_findings),
        "curr_count": len(curr_findings),
        "resolved": resolved,
        "new": new_issues,
        "persisting": persisting,
    }


def is_port_listening(port=48765) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.1)
        return s.connect_ex(("127.0.0.1", port)) == 0


class QuietHTTPHandler(SimpleHTTPRequestHandler):
    """Serve only the selected HTML and non-sensitive static assets within its root."""

    def __init__(self, *args, allowed_html="", **kwargs):
        self.allowed_html = allowed_html
        super().__init__(*args, **kwargs)

    def log_message(self, *args):
        pass

    def _allowed(self):
        raw = unquote(urlsplit(self.path).path)
        if raw == "/favicon.ico":
            return True
        # Prevent directory listing, dotfiles, traversal, linked directories and
        # accidental exposure of configuration, backups or reports.
        if not raw.startswith("/") or "\\" in raw:
            return False
        parts = raw.lstrip("/").split("/")
        if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
            return False
        root = Path(self.directory).resolve()
        file = root.joinpath(*parts)
        try:
            if not file.is_file() or file.is_symlink() or any(p.is_symlink() for p in file.parents if p != root and root in p.parents):
                return False
            file.resolve().relative_to(root)
        except (ValueError, OSError):
            return False
        if len(parts) == 1 and parts[0] == self.allowed_html:
            return True
        return file.suffix.lower() in {".css", ".js", ".mjs", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".woff", ".woff2", ".ico"}

    def _serve(self, head=False):
        if urlsplit(self.path).path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if not self._allowed():
            self.send_error(404, "Not Found")
            return
        if head:
            return super().do_HEAD()
        return super().do_GET()

    def do_GET(self):
        self._serve()

    def do_HEAD(self):
        self._serve(head=True)


def serve_local_file_ephemeral(file_path: Path):
    file_path = file_path.resolve()
    if not file_path.is_file() or file_path.suffix.lower() not in (".html", ".htm"):
        raise ValueError("Local fixture chỉ hỗ trợ tệp HTML hợp lệ.")
    directory = str(file_path.parent)
    handler = functools.partial(QuietHTTPHandler, directory=directory, allowed_html=file_path.name)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/{file_path.name}"
    return server, thread, url


def auto_detect_target():
    import urllib.request
    for port in [3000, 5173, 8080, 8000]:
        url = f"http://127.0.0.1:{port}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AutomationAAE-Probe"})
            with urllib.request.urlopen(req, timeout=0.2):
                return url
        except Exception:
            continue
    app_html = ROOT / "extension" / "app.html"
    if app_html.is_file():
        return str(app_html)
    return "http://127.0.0.1:48765"


def run_image_audit(target_image: str, objective: str, call_ai: bool = True,
                    output_json: bool = False, output_brief: bool = False):
    """Analyze a screenshot without pretending that page DOM or behavior exists."""
    image = analyze_target_image(target_image)
    settings = SettingsRepository(ROOT / ".data" / "settings.sqlite3")
    secrets = (settings.get("ai.api_key"), settings.get("bridge.pairing_token"))
    raw = {"objective": objective, "source": "image:" + image["sourceHash"],
           "snapshots": [{
               "title": "Target screenshot", "url": "image://local",
               "kind": "image",
               "viewport": {"width": image["width"], "height": image["height"]},
               "document": {}, "elements": [], "findings": [],
               "screenshot": image["screenshot"], "limitations": image["limits"],
           }]}
    sanitized = sanitize_capture(raw, secrets)
    review = None
    if call_ai:
        try:
            review = evaluate(settings, sanitized, include_images=True)
        except AuditError as exc:
            review = {"status": "error", "summary": str(exc), "findings": [],
                      "notTested": ["AI vision analysis not completed."]}
    matrix = [
        {"dimension": "Image visual review",
         "required_evidence": "Supplied image + optional AI vision review",
         "status": "PARTIAL" if review and review.get("status") == "completed" else "UNVERIFIED",
         "summary": "AI đã phân tích ảnh thị giác; chưa có bằng chứng DOM/tương tác." if review and review.get("status") == "completed"
                    else "Đã đọc ảnh, chưa có phân tích thị giác bằng AI."},
        {"dimension": "DOM / behavior / accessibility",
         "required_evidence": "Live browser DOM, keyboard, runtime, multiple states",
         "status": "UNVERIFIED", "summary": "Ảnh tĩnh không kiểm chứng chức năng hay accessibility."},
    ]
    report = build_report(capture=sanitized, ai=review, acceptance_matrix=matrix)
    saved = save_report(ROOT / "reports", report)
    if output_json:
        print(json.dumps({"status": "completed", "mode": "image-only", "id": saved["id"],
            "width": image["width"], "height": image["height"], "findings": report["findings"],
            "acceptanceMatrix": matrix,
            "markdown_path": str(ROOT / "reports" / (saved["id"] + ".md")),
            "json_path": str(ROOT / "reports" / (saved["id"] + ".json"))}, ensure_ascii=False, indent=2))
    elif output_brief:
        print(f"Image-only UI/UX analysis: {image['width']} x {image['height']}\\n" +
              "\\n".join(f"[{f['severity']}] {f['title']}: {f['recommendation']}" for f in report["findings"]) +
              f"\\nReport: {ROOT / 'reports' / (saved['id'] + '.md')}")
    else:
        print(f"[OK] Image-only UI/UX audit: {image['width']}x{image['height']}; AI={report['ai']['status']}")
        print(f"Report: {ROOT / 'reports' / (saved['id'] + '.md')}")
    settings.close()
    return report, saved


def run_audit(
    target_url: str = None,
    objective: str = "Đánh giá UI/UX toàn diện, bố cục responsive, tương tác động, CSS tokens và accessibility.",
    responsive: bool = True,
    with_screenshot: bool = True,
    call_ai: bool = True,
    interactive: bool = True,
    chromium_path: str = DEFAULT_CHROMIUM,
    cdp_endpoint: str = None,
    output_json: bool = False,
    output_brief: bool = False,
    reference_image: str = None,
):
    if target_url and Path(target_url).suffix.lower() in IMAGE_TYPES and Path(target_url).is_file():
        if reference_image:
            raise ValueError("Chế độ chỉ-ảnh không nhận --reference-image; hãy cung cấp URL/HTML để so sánh.")
        return run_image_audit(target_url, objective, call_ai, output_json, output_brief)
    if reference_image and not with_screenshot:
        raise ValueError("So sánh với ảnh mục tiêu yêu cầu bật chụp screenshot (không dùng --no-screenshot).")
    reference = analyze_target_image(reference_image) if reference_image else None
    if not target_url:
        target_url = auto_detect_target()
        if not output_json and not output_brief:
            print(f"[*] Tự động phát hiện mục tiêu audit: {target_url}", flush=True)

    settings = SettingsRepository(ROOT / ".data" / "settings.sqlite3")
    secrets = (settings.get("ai.api_key"), settings.get("bridge.pairing_token"))

    # Auto-start local bridge daemon if not already listening
    auto_bridge = None
    auto_bridge_thread = None
    if not is_port_listening(48765):
        try:
            from backend.server import BridgeServer
            auto_bridge = BridgeServer()
            auto_bridge_thread = threading.Thread(target=auto_bridge.serve_forever, daemon=True)
            auto_bridge_thread.start()
            if not output_json and not output_brief:
                print("[*] Tự động khởi chạy Local Bridge nền trên cổng 48765...", flush=True)
        except Exception:
            pass

    # Local source identity avoids merging same-named files from different folders.
    source_identity = ""
    if os.path.exists(target_url):
        source_identity = "local:" + hashlib.sha256(os.path.normcase(str(Path(target_url).resolve())).encode("utf-8")).hexdigest()[:32]

    # Serve local file through a dedicated loopback HTTP fixture to preserve strict CORS & TLS
    local_server = None
    local_thread = None
    if os.path.exists(target_url):
        target_path = Path(target_url).resolve()
        local_server, local_thread, target_url = serve_local_file_ephemeral(target_path)

    active_viewports = VIEWPORTS if responsive else {"desktop": VIEWPORTS["desktop"]}
    scanner_code = load_scanner_script()
    snapshots = []
    css_intel = {}
    interaction_data = {}
    runtime_data = {}

    if not output_json and not output_brief:
        print(f"[*] Đang thực thi autonomous audit: {target_url}", flush=True)
    try:
        with sync_playwright() as p:
            if cdp_endpoint:
                if not output_json:
                    print(f"[*] Kết nối browser qua CDP: {cdp_endpoint}", flush=True)
                browser = p.chromium.connect_over_cdp(cdp_endpoint)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
            else:
                if not output_json:
                    print(f"[*] Khởi động Chromium headless (TLS strict)...", flush=True)
                launch_kwargs = {"headless": True}
                if chromium_path:
                    launch_kwargs["executable_path"] = chromium_path
                browser = p.chromium.launch(**launch_kwargs)
                context = browser.new_context(
                    ignore_https_errors=False,
                    java_script_enabled=True,
                )

            is_primary_page = True
            for name, size in active_viewports.items():
                if not output_json and not output_brief:
                    print(f"[*] Quét viewport [{name}]: {size['width']} x {size['height']}...", flush=True)
                page = context.new_page()
                page.set_viewport_size(size)

                runtime_collector = None
                if is_primary_page:
                    runtime_collector = RuntimeCollector(page)

                page.goto(target_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(600)

                # Auto-dismiss gate overlay if present to audit the true workspace
                try:
                    page.evaluate("""() => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        const localBtn = btns.find(b => b.textContent && b.textContent.includes('Cục bộ'));
                        if (localBtn) localBtn.click();
                    }""")
                    page.wait_for_timeout(300)
                except Exception:
                    pass

                if is_primary_page:
                    if not output_json and not output_brief:
                        print("  [+] Thu thập CSS Intelligence (Tokens, font scale, cascade overrides)...", flush=True)
                    css_intel = analyze_css(page)

                    if interactive:
                        if not output_json and not output_brief:
                            print("  [+] Chạy Dynamic Interaction Suite (Tab focus-visible, hover, expand/collapse)...", flush=True)
                        interaction_data = run_interaction_suite(page)
                    else:
                        interaction_data = {"status": "skipped", "focusTrace": [], "hoverFeedback": [], "stateTransitions": []}

                    if runtime_collector:
                        runtime_data = runtime_collector.summary()
                    is_primary_page = False

                # Independent read-only geometry relationships for ordinary UIs
                # and node/port canvases. No click, script injection or data mutation.
                spatial = audit_spatial_relations(page)
                # Collect audit data via scanner.js
                raw_snapshot = page.evaluate(scanner_code)
                snapshot = {
                    "kind": "dom",
                    "title": page.title() or raw_snapshot.get("title") or name,
                    "url": target_url.split("?")[0],
                    "viewport": size,
                    "document": raw_snapshot.get("document", {
                        "lang": page.evaluate("() => document.documentElement.lang || ''"),
                        "title": page.title(),
                    }),
                    "elements": raw_snapshot.get("elements", []),
                    "findings": raw_snapshot.get("findings", []) + spatial.get("findings", []),
                    "spatialRelations": spatial,
                    "limitations": [
                        f"Quét tự động Playwright tại viewport {size['width']}x{size['height']}.",
                        "Quan hệ hình học chỉ đánh giá phần tử nhìn thấy hoặc có thuộc tính ràng buộc; không suy đoán mọi ý định thiết kế.",
                    ],
                }

                if with_screenshot:
                    # Mask sensitive inputs if any
                    page.evaluate("""() => {
                        const selector = 'input,textarea,[contenteditable]:not([contenteditable="false"]),[data-private],[data-sensitive]';
                        document.querySelectorAll(selector).forEach(el => {
                            el.setAttribute('data-aae-orig-style', el.getAttribute('style') || '');
                            el.style.setProperty('filter', 'blur(8px)', 'important');
                        });
                    }""")
                    screenshot_bytes = page.screenshot(type="jpeg", quality=70, full_page=False)
                    # Unmask
                    page.evaluate("""() => {
                        const selector = 'input,textarea,[contenteditable]:not([contenteditable="false"]),[data-private],[data-sensitive]';
                        document.querySelectorAll(selector).forEach(el => {
                            const orig = el.getAttribute('data-aae-orig-style');
                            if (orig) el.setAttribute('style', orig);
                            else el.removeAttribute('style');
                            el.removeAttribute('data-aae-orig-style');
                        });
                    }""")
                    data_uri = "data:image/jpeg;base64," + base64.b64encode(screenshot_bytes).decode("ascii")
                    snapshot["screenshot"] = data_uri
                    snapshot["screenshotKind"] = "jpeg"

                snapshots.append(snapshot)
                page.close()

            browser.close()

    finally:
        if local_server:
            try:
                local_server.shutdown()
                local_server.server_close()
            except Exception:
                pass
        if local_thread:
            try:
                local_thread.join(timeout=1)
            except Exception:
                pass
        if auto_bridge:
            try:
                auto_bridge.shutdown()
                auto_bridge.server_close()
            except Exception:
                pass
        if auto_bridge_thread:
            try:
                auto_bridge_thread.join(timeout=1)
            except Exception:
                pass

    visual_comparison = []
    if reference:
        for snap in snapshots:
            comparison = compare_target_image(reference_image, snap.get("screenshot", ""))
            visual_comparison.append({**comparison, "viewport": snap["viewport"]})
    capture_data = {
        "source": source_identity or "tab",
        "objective": objective,
        "snapshots": snapshots,
        "cssIntelligence": css_intel,
        "interaction": interaction_data,
        "runtimeDiagnostics": runtime_data,
    }
    if reference:
        capture_data["referenceImage"] = {
            "width": reference["width"], "height": reference["height"],
            "screenshot": reference["screenshot"],
        }
        capture_data["visualComparison"] = visual_comparison
    sanitized = sanitize_capture(capture_data, secrets)

    ai_review = None
    if call_ai:
        if not output_json and not output_brief:
            print(f"[*] Gửi dữ liệu bằng chứng tới AI ({settings.get('ai.model_id')})...", flush=True)
        try:
            ai_review = evaluate(settings, sanitized, include_images=with_screenshot)
            if not output_json and not output_brief:
                print(f"[+] AI phân tích thành công! Status: {ai_review.get('status')}", flush=True)
        except AuditError as err:
            if not output_json and not output_brief:
                print(f"[-] AI đánh giá thất bại: {err}", file=sys.stderr)
            ai_review = {"status": "error", "error": str(err), "summary": f"Đánh giá AI thất bại: {err}", "findings": []}

    # Aggregate rule findings and AI findings
    raw_findings = []
    seen = set()
    for s in snapshots:
        vp_str = f"{s['viewport']['width']} x {s['viewport']['height']}"
        for f in s.get("findings", []):
            k = (f.get("title"), f.get("target"), vp_str)
            if k not in seen:
                raw_findings.append({**f, "viewport": vp_str, "source": "rule"})
                seen.add(k)
    if ai_review and ai_review.get("findings"):
        raw_findings.extend(ai_review["findings"])

    # Regression diffing
    prev_file, prev_data = find_previous_report(target_url, source_identity)
    regression_diff = None
    if prev_data:
        regression_diff = compute_advanced_regression(prev_data, raw_findings, snapshots)

    # 9-Dimension Acceptance Matrix
    acceptance_matrix = compute_acceptance_matrix(
        snapshots=snapshots,
        css_intel=css_intel,
        interaction=interaction_data,
        runtime=runtime_data,
        regression=regression_diff,
        all_findings=raw_findings,
    )

    if reference:
        comparable = [x for x in visual_comparison if x.get("status") == "MEASURED"]
        acceptance_matrix.append({
            "dimension": "Target image comparison",
            "required_evidence": "Reference image + viewport-matched screenshot + pixel measurements",
            "status": "PARTIAL" if comparable else "UNVERIFIED",
            "summary": (f"{len(comparable)} screenshot khớp kích thước ảnh mục tiêu; đã đo sai lệch pixel, chưa xác nhận UX hay tính tương đương ngữ nghĩa."
                        if comparable else "Ảnh mục tiêu và screenshot chưa đồng kích thước; chưa tính khác biệt pixel để tránh kết luận sai."),
        })

    # Build and persist report
    report = build_report(
        capture=sanitized,
        ai=ai_review,
        acceptance_matrix=acceptance_matrix,
        css_intel=css_intel,
        interaction=interaction_data,
        runtime=runtime_data,
        regression=regression_diff,
    )
    saved = save_report(ROOT / "reports", report)

    # Output formatting
    if output_json:
        payload = {
            "status": "completed",
            "id": saved["id"],
            "url": target_url,
            "summary": report.get("ai", {}).get("summary", ""),
            "acceptanceMatrix": acceptance_matrix,
            "regression": regression_diff,
            "findings": report.get("findings", []),
            "markdown_path": str(ROOT / "reports" / (saved["id"] + ".md")),
            "json_path": str(ROOT / "reports" / (saved["id"] + ".json")),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif output_brief:
        brief_lines = [
            f"# UI/UX Remediation Brief for Coding Agent",
            f"Target: {target_url}",
            f"Report ID: {saved['id']}",
            "",
            "## Summary",
            report.get("ai", {}).get("summary", ""),
            "",
            "## Bảng nghiệm thu chất lượng UI/UX (Acceptance Matrix)",
            "",
            "| Hạng mục | Trạng thái | Bằng chứng |",
            "| :--- | :---: | :--- |",
        ]
        for row in acceptance_matrix:
            brief_lines.append(f"| {row['dimension']} | **{row['status']}** | {row['summary']} |")
        brief_lines.append("\n## Actionable Findings")
        for f in report.get("findings", []):
            brief_lines.append(f"### [{f.get('severity', '').upper()}] {f.get('title')}")
            brief_lines.append(f"- Selector: `{f.get('target')}`")
            brief_lines.append(f"- Viewport: {f.get('viewport')}")
            brief_lines.append(f"- Evidence: {f.get('evidence')}")
            brief_lines.append(f"- Recommendation: {f.get('recommendation')}")
            brief_lines.append(f"- Acceptance: {f.get('acceptance')}\n")
        print("\n".join(brief_lines))
    else:
        print("\n" + "=" * 70, flush=True)
        print(" BẢNG NGHIỆM THU CHẤT LƯỢNG UI/UX (ACCEPTANCE MATRIX)", flush=True)
        print("=" * 70, flush=True)
        for row in acceptance_matrix:
            mark = "[PASS]" if row["status"] in ("PASS", "FIXED") else f"[{row['status']}]"
            print(f" {mark:<8} {row['dimension']:<24} : {row['summary']}", flush=True)
        print("=" * 70, flush=True)

        if regression_diff:
            print(f"[+] KIỂM CHỨNG HỒI QUY (REGRESSION) so với {regression_diff['prev_id']}:", flush=True)
            if not regression_diff.get("coverageComparable"):
                print(f"  [UNVERIFIED] {regression_diff.get('notComparableReason', 'Phạm vi quét khác baseline.')}", flush=True)
            print(f"  ? Chờ tái kiểm chứng: {regression_diff['requires_retest_count']} vấn đề (chưa xác nhận FIXED)", flush=True)
            for item in regression_diff["requires_retest"][:4]:
                print(f"     - [{item.get('severity', '').upper()}] {item.get('title')} ({item.get('target')})", flush=True)
            print(f"  ⚠ Còn tồn tại:    {regression_diff['persisting_count']} vấn đề", flush=True)
            print(f"  + Mới phát sinh:  {regression_diff['new_count']} vấn đề", flush=True)
            print("=" * 70, flush=True)

        print(f"\n[OK] Đã hoàn thành audit! Báo cáo lưu tại:")
        print(f"  - Markdown: {ROOT / 'reports' / (saved['id'] + '.md')}")
        print(f"  - JSON:     {ROOT / 'reports' / (saved['id'] + '.json')}")

    return report, saved


def main():
    parser = argparse.ArgumentParser(description="AutomationAAE Autonomous Engine for AI Agents")
    parser.add_argument("target", nargs="?", default=None, help="Target URL or local HTML file (auto-detected if omitted)")
    parser.add_argument("--objective", default="Đánh giá UI/UX toàn diện, bố cục responsive, tương tác động và accessibility.", help="Mục tiêu đánh giá")
    parser.add_argument("--no-responsive", action="store_true", help="Chỉ quét desktop (1440x900)")
    parser.add_argument("--no-screenshot", action="store_true", help="Không chụp screenshot gửi AI")
    parser.add_argument("--reference-image", default=None, help="Ảnh giao diện mong muốn PNG/JPEG/WebP để đối chiếu với URL/HTML")
    parser.add_argument("--no-ai", action="store_true", help="Chỉ quét rule DOM cục bộ, không gọi gateway AI")
    parser.add_argument("--no-interactive", action="store_true", help="Bỏ qua bộ kiểm tra tương tác động (hover, tab focus, expand)")
    parser.add_argument("--cdp", default=None, help="Connect over Chrome DevTools Protocol endpoint (e.g. http://127.0.0.1:9222)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON to stdout for AI")
    parser.add_argument("--brief", action="store_true", help="Output developer remediation brief to stdout")
    parser.add_argument("--chromium", default=DEFAULT_CHROMIUM, help="Optional path to Chrome/Chromium executable (defaults to AAE_CHROMIUM_PATH or Playwright Chromium)")
    args = parser.parse_args()

    run_audit(
        target_url=args.target,
        objective=args.objective,
        responsive=not args.no_responsive,
        with_screenshot=not args.no_screenshot,
        call_ai=not args.no_ai,
        interactive=not args.no_interactive,
        chromium_path=args.chromium,
        cdp_endpoint=args.cdp,
        output_json=args.json,
        output_brief=args.brief,
        reference_image=args.reference_image,
    )


if __name__ == "__main__":
    main()
