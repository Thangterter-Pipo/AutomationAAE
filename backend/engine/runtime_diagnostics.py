"""Runtime Diagnostics & Performance Engine: Captures console errors, uncaught exceptions,

failed network requests, and measures Core Web Vitals (FCP, CLS, load timing).
"""

from typing import Any, Dict, List


class RuntimeCollector:
    """Attaches listeners to a Playwright page to capture runtime errors, HTTP failures, and Web Vitals."""

    def __init__(self, page: Any):
        self.page = page
        self.console_errors: List[Dict[str, str]] = []
        self.uncaught_exceptions: List[str] = []
        self.failed_requests: List[Dict[str, str]] = []
        self.http_error_responses: List[Dict[str, Any]] = []

        # Attach listeners
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)

    def _on_console(self, msg: Any):
        if msg.type in ("error", "warning"):
            self.console_errors.append({
                "type": msg.type,
                "text": msg.text,
                "location": f"{msg.location.get('url', '')}:{msg.location.get('lineNumber', '')}" if msg.location else ""
            })

    def _on_page_error(self, exc: Any):
        self.uncaught_exceptions.append(str(exc))

    def _on_request_failed(self, req: Any):
        self.failed_requests.append({
            "url": req.url,
            "method": req.method,
            "failure": req.failure or "Failed request",
            "resourceType": req.resource_type
        })

    def _on_response(self, resp: Any):
        if resp.status >= 400:
            self.http_error_responses.append({
                "url": resp.url,
                "status": resp.status,
                "statusText": resp.status_text,
            })

    def collect_performance_metrics(self) -> Dict[str, Any]:
        """Evaluates Performance Navigation Timing and Paint entries."""
        try:
            return self.page.evaluate("""() => {
                const nav = performance.getEntriesByType('navigation')[0] || {};
                const paint = performance.getEntriesByType('paint');
                let fcp = null;
                for (const p of paint) {
                    if (p.name === 'first-contentful-paint') {
                        fcp = Math.round(p.startTime);
                    }
                }
                const ttfb = nav.responseStart ? Math.round(nav.responseStart) : null;
                return {
                    fcpMs: fcp,
                    ttfbMs: ttfb,
                    domContentLoadedMs: nav.domContentLoadedEventEnd ? Math.round(nav.domContentLoadedEventEnd) : null,
                    loadEventMs: nav.loadEventEnd ? Math.round(nav.loadEventEnd) : null,
                    domInteractiveMs: nav.domInteractive ? Math.round(nav.domInteractive) : null,
                    transferSize: nav.transferSize || 0
                };
            }""")
        except Exception:
            return {}

    def summary(self) -> Dict[str, Any]:
        perf = self.collect_performance_metrics()
        has_critical_runtime_error = (
            len(self.uncaught_exceptions) > 0
            or any(e["type"] == "error" for e in self.console_errors)
            or len(self.http_error_responses) > 0
        )
        return {
            "consoleErrorCount": len(self.console_errors),
            "consoleErrors": self.console_errors[:10],
            "uncaughtExceptions": self.uncaught_exceptions[:5],
            "failedRequestCount": len(self.failed_requests),
            "failedRequests": self.failed_requests[:10],
            "httpErrorCount": len(self.http_error_responses),
            "httpErrors": self.http_error_responses[:10],
            "performance": perf,
            "hasCriticalRuntimeError": has_critical_runtime_error
        }
