"""Loopback-only bridge. Run from AutomationAAE: python -m backend.server."""

import argparse
import hmac
import json
import re
import secrets
import threading
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from backend.audit import AuditError, REPORT_ID, build_report, evaluate, list_models, sanitize_capture, save_report
from db.repositories import SettingsRepository

ROOT = Path(__file__).resolve().parents[1]
PORT = 48765
VERSION = "0.1.0"


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, directory=ROOT / ".data", reports=ROOT / "reports", port=PORT):
        self.settings = SettingsRepository(directory / "settings.sqlite3")
        if not self.settings.get("bridge.pairing_token"):
            self.settings.save({"bridge.pairing_token": secrets.token_urlsafe(32)})
        self.reports = reports
        self.operation_lock = threading.Lock()
        self.report_lock = threading.RLock()
        # Completed paid results survive a failed disk write and can be saved
        # again without calling the model. Bound memory and block new paid work
        # when recovery is required instead of silently evicting a result.
        self.pending_reports = {}
        super().__init__(("127.0.0.1", port), Handler)

    def server_close(self):
        super().server_close()
        self.settings.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "AutomationAAE"
    sys_version = ""

    def log_message(self, *args):
        # No paths, request bodies, credentials or provider errors in logs.
        pass

    def _origin(self):
        origin = self.headers.get("Origin", "")
        # Chromium extension pages commonly omit Origin for loopback fetches.
        # The pairing capability remains mandatory; a non-empty Origin must be
        # a Chrome extension origin or a local loopback origin (127.0.0.1:<port>)
        # so ordinary public web pages cannot reuse CORS.
        if not origin:
            return ""
        if re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
            return origin
        if re.fullmatch(r"http://127\.0\.0\.1:\d+", origin):
            return origin
        return None

    def _host_valid(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def _reply(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self._origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self._host_valid() or self._origin() is None:
            self._reply(403, {"error": "Origin không được phép."})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self._origin())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def _authorized(self):
        if not self._host_valid() or self._origin() is None:
            self._reply(403, {"error": "Chỉ extension đã ghép nối được phép truy cập."})
            return False
        expected = "Bearer " + self.server.settings.get("bridge.pairing_token")
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode("utf-8"), expected.encode("utf-8")):
            self._reply(401, {"error": "Mã ghép nối không đúng. Xem cửa sổ dịch vụ trên máy bạn."})
            return False
        return True

    def _body(self):
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Chỉ chấp nhận application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Content-Length không hợp lệ.") from None
        if not 0 < length <= 12_000_000:
            raise ValueError("Yêu cầu rỗng hoặc vượt 12 MB.")
        self.connection.settimeout(20)
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError, OSError):
            raise ValueError("JSON không hợp lệ hoặc đọc quá thời gian.") from None
        if not isinstance(data, dict):
            raise ValueError("Dữ liệu phải là JSON object.")
        return data

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/pairing-token":
            if not self._host_valid() or self._origin() is None:
                self._reply(403, {"error": "Origin không được phép."})
                return
            self._reply(200, {"status": "ok", "pairing_token": self.server.settings.get("bridge.pairing_token")})
            return
        if not self._authorized():
            return
        if path == "/api/health":
            self._reply(200, {"ok": True, "version": VERSION})
        elif path == "/api/settings":
            self._reply(200, {"settings": self.server.settings.public()})
        elif path == "/api/reports":
            entries = {}
            if self.server.reports.exists():
                for item in sorted(self.server.reports.glob("*.json"), reverse=True):
                    if REPORT_ID.fullmatch(item.stem) and item.with_suffix(".md").is_file():
                        entries[item.stem] = {"id": item.stem, "json": item.name, "markdown": item.stem + ".md", "storage": "saved"}
                    if len(entries) >= 50:
                        break
            with self.server.report_lock:
                for report_id in self.server.pending_reports:
                    entries[report_id] = {"id": report_id, "storage": "pending"}
            self._reply(200, {"reports": sorted(entries.values(), key=lambda entry: entry["id"], reverse=True)})
        elif re.fullmatch(r"/api/reports/[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}", path):
            report_id = path.rsplit("/", 1)[1]
            with self.server.report_lock:
                pending = self.server.pending_reports.get(report_id)
            if pending:
                self._reply(200, {"report": pending, "storage": "pending"})
                return
            file = self.server.reports / (report_id + ".json")
            try:
                if not file.is_file() or file.stat().st_size > 10 * 1024 * 1024 or not file.with_suffix(".md").is_file():
                    self._reply(404, {"error": "Không tìm thấy báo cáo đầy đủ."})
                    return
                report = json.loads(file.read_text(encoding="utf-8"))
                if not isinstance(report, dict) or report.get("id") != report_id:
                    raise ValueError()
                self._reply(200, {"report": report, "storage": "saved"})
            except (OSError, ValueError):
                self._reply(500, {"error": "Không đọc được báo cáo. Kiểm tra file và quyền đọc trên máy."})
        else:
            self._reply(404, {"error": "Không có endpoint này."})

    def do_POST(self):
        if not self._authorized():
            return
        try:
            path = urlsplit(self.path).path
            body = self._body()
            if path == "/api/settings":
                changes = body.get("changes")
                if not isinstance(changes, dict) or any(not (key.startswith("ai.") or key == "audit.language") for key in changes):
                    raise ValueError("Không được thay đổi khóa cấu hình này.")
                if not self.server.operation_lock.acquire(blocking=False):
                    self._reply(409, {"error": "AI đang dùng cấu hình hiện tại; đợi hoàn tất rồi thay đổi."})
                    return
                try:
                    self._reply(200, {"settings": self.server.settings.save(changes)})
                finally:
                    self.server.operation_lock.release()
            elif path in {"/api/models", "/api/analyze"}:
                if not self.server.operation_lock.acquire(blocking=False):
                    self._reply(409, {"error": "Một yêu cầu AI đang chạy. Không gửi trùng."})
                    return
                try:
                    if body.get("consent") is not True:
                        raise ValueError("Cần xác nhận trước khi gọi gateway.")
                    if path == "/api/models":
                        self._reply(200, {"models": list_models(self.server.settings)})
                    else:
                        with self.server.report_lock:
                            if len(self.server.pending_reports) >= 5:
                                self._reply(409, {"error": "Có 5 báo cáo AI chưa lưu được. Hãy lưu lại hoặc xuất các báo cáo trong lịch sử trước khi gọi AI mới."})
                                return
                        capture = sanitize_capture(body.get("capture"), (self.server.settings.get("ai.api_key"), self.server.settings.get("bridge.pairing_token")))
                        include = body.get("includeImages") is True
                        if include and body.get("imageConsent") is not True:
                            raise ValueError("Cần đồng ý riêng trước khi gửi ảnh cho gateway.")
                        if not include:
                            for snapshot in capture["snapshots"]:
                                snapshot.pop("screenshot", None)
                        if all(snapshot["kind"] == "screenshot" for snapshot in capture["snapshots"]) and not include:
                            raise ValueError("Chỉ có ảnh nhưng chưa đồng ý gửi ảnh; không có bằng chứng để AI phân tích.")
                        ai = evaluate(self.server.settings, capture, include)
                        report = build_report(capture, ai)
                        with self.server.report_lock:
                            self.server.pending_reports[report["id"]] = report
                        self._reply(200, self._persist_completed_report(report))
                finally:
                    self.server.operation_lock.release()
            elif path == "/api/reports":
                capture = sanitize_capture(body.get("capture"), (self.server.settings.get("ai.api_key"), self.server.settings.get("bridge.pairing_token")))
                report = build_report(capture)
                self._reply(200, {"report": report, "saved": save_report(self.server.reports, report)})
            elif re.fullmatch(r"/api/reports/[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}", path):
                report_id = path.rsplit("/", 1)[1]
                with self.server.report_lock:
                    report = self.server.pending_reports.get(report_id)
                    if report is None:
                        self._reply(404, {"error": "Không còn báo cáo chờ lưu trong phiên dịch vụ này. Kiểm tra lịch sử hoặc file đã xuất; không gọi lại AI để lưu."})
                    else:
                        self._reply(200, self._persist_completed_report(report))
            else:
                self._reply(404, {"error": "Không có endpoint này."})
        except ValueError as error:
            self._reply(400, {"error": str(error)})
        except AuditError as error:
            self._reply(502, {"error": str(error)})
        except (OSError, RuntimeError, sqlite3.Error):
            self._reply(500, {"error": "Không lưu hoặc xử lý được báo cáo. Kiểm tra quyền ghi và dung lượng ổ đĩa."})

    def _persist_completed_report(self, report):
        with self.server.report_lock:
            try:
                saved = save_report(self.server.reports, report)
            except (OSError, ValueError):
                return {"report": report, "saved": None,
                        "saveError": "AI đã hoàn thành nhưng chưa ghi được file. Kết quả đang giữ tạm trong dịch vụ; bấm Lưu lại (không gọi AI) hoặc tải JSON/Markdown trước khi đóng dịch vụ."}
            self.server.pending_reports.pop(report["id"], None)
            return {"report": report, "saved": saved}


def main():
    parser = argparse.ArgumentParser(description="AutomationAAE local bridge (127.0.0.1 only)")
    parser.add_argument("--rotate-pairing", action="store_true", help="Revoke the previous extension pairing code")
    args = parser.parse_args()
    with BridgeServer() as server:
        if args.rotate_pairing:
            server.settings.save({"bridge.pairing_token": secrets.token_urlsafe(32)})
        print("AutomationAAE local bridge — http://127.0.0.1:48765", flush=True)
        print("Ma ghep noi (chi nhap vao AutomationAAE, khong gui trong chat):", flush=True)
        print(server.settings.get("bridge.pairing_token"), flush=True)
        print("API key duoc luu trong SQLite tren may nay; khong ma hoa o dia.", flush=True)
        print("Bao cao: " + str(server.reports), flush=True)
        print("Giu cua so nay mo. Ctrl+C de dung.", flush=True)
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
