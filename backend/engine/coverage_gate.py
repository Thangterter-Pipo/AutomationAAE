"""Coverage Gate & Evidence-Based Acceptance Matrix:

Computes the evidence-grounded QA Acceptance Matrix and performs component-aware regression diffing.
"""

import re
from typing import Any, Dict, List, Optional


def normalize_selector(selector: str) -> str:
    """Normalize spacing without merging structurally distinct sibling selectors."""
    if not selector:
        return "unknown"
    # Preserve structural indices. Removing them conflates sibling components
    # and may falsely mark a finding as fixed or persistent.
    cleaned = selector
    # Collapse duplicate spaces / arrows
    cleaned = re.sub(r"\s*>\s*", " > ", cleaned).strip()
    return cleaned


def compute_advanced_regression(prev_report: Dict[str, Any], curr_findings: List[Dict[str, Any]], current_snapshots: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Compares current findings against baseline report using rule category,

    normalized component selector, and viewport.
    """
    prev_findings = prev_report.get("findings", [])
    baseline_snapshots = prev_report.get("capture", {}).get("snapshots", [])
    def coverage(snapshots):
        return {(s.get("viewport", {}).get("width"), s.get("viewport", {}).get("height"),
                 bool(s.get("elements")), bool(s.get("screenshot"))) for s in snapshots}
    prior_coverage = coverage(baseline_snapshots)
    current_coverage = coverage(current_snapshots or [])
    coverage_comparable = bool(prior_coverage) and prior_coverage == current_coverage
    if not coverage_comparable:
        return {
            "prev_id": prev_report.get("id", "baseline"),
            "prev_count": len(prev_findings),
            "curr_count": len(curr_findings),
            "resolved_count": 0, "persisting_count": 0, "new_count": 0,
            "requires_retest_count": 0, "resolved": [], "persisting": [],
            "new": [], "requires_retest": [],
            "coverageComparable": False,
            "notComparableReason": "Viewport hoặc mức bằng chứng giữa hai lượt quét khác nhau; bỏ qua thống kê thay đổi lỗi.",
        }

    def make_key(f: Dict[str, Any]) -> str:
        rule_prefix = str(f.get("ruleId") or re.sub(r"-\d+$", "", str(f.get("id", ""))))
        cat = str(f.get("category", "")).lower()
        target_norm = normalize_selector(f.get("target", ""))
        viewport = str(f.get("viewport", "")).strip()
        return f"{rule_prefix}::{cat}::{target_norm}::{viewport}"

    prev_map = {make_key(f): f for f in prev_findings}
    curr_map = {make_key(f): f for f in curr_findings}

    # A missing finding is not a proven fix: missing states, selectors or
    # viewports can make the scanner stop observing the same defect.
    candidates = [f for k, f in prev_map.items() if k not in curr_map]
    persisting = [f for k, f in curr_map.items() if k in prev_map]
    new_issues = [f for k, f in curr_map.items() if k not in prev_map]
    resolved = []

    return {
        "prev_id": prev_report.get("id", "baseline"),
        "prev_count": len(prev_findings),
        "curr_count": len(curr_findings),
        "resolved_count": len(resolved),
        "persisting_count": len(persisting),
        "new_count": len(new_issues),
        "resolved": resolved,
        "requires_retest_count": len(candidates),
        "requires_retest": candidates,
        "coverageComparable": True,
        "persisting": persisting,
        "new": new_issues,
    }


def compute_acceptance_matrix(
    snapshots: List[Dict[str, Any]],
    css_intel: Dict[str, Any],
    interaction: Dict[str, Any],
    runtime: Dict[str, Any],
    regression: Optional[Dict[str, Any]] = None,
    all_findings: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Builds the strict, evidence-grounded QA Acceptance Matrix."""
    findings = all_findings or []
    matrix = []

    # 1. Visual Layout
    expected_viewports = {(1440, 900), (768, 1024), (390, 844)}
    observed_viewports = set()
    complete_viewports = set()
    for snap in snapshots:
        viewport = snap.get("viewport") or {}
        dims = (viewport.get("width"), viewport.get("height"))
        observed_viewports.add(dims)
        if snap.get("elements") and isinstance(snap.get("screenshot"), str) and snap["screenshot"].startswith("data:image/"):
            complete_viewports.add(dims)
    visual_complete = expected_viewports <= complete_viewports
    layout_issues = [f for f in findings if f.get("category") == "layout" or "TARGET" in str(f.get("id", ""))]
    if any(f.get("severity") in ("critical", "high") for f in layout_issues):
        layout_status = "FAIL"
        layout_summary = (f"Phát hiện {len(layout_issues)} lỗi layout, có lỗi nghiêm trọng; "
                          f"độ bao phủ hình ảnh/geometry: {len(expected_viewports & complete_viewports)}/3 viewport.")
    elif not visual_complete:
        layout_status = "UNVERIFIED"
        layout_summary = f"Chỉ có {len(expected_viewports & complete_viewports)}/3 viewport đạt đủ ảnh và geometry. Chưa nghiệm thu visual layout."
    elif layout_issues:
        layout_status = "WARN"
        layout_summary = f"{len(snapshots)} viewports có geometry, {sum(1 for s in snapshots if 'screenshot' in s)} ảnh chụp. {len(layout_issues)} vấn đề kích thước/khoảng cách."
    else:
        layout_status = "PARTIAL"
        layout_summary = "Ba viewport có ảnh và geometry; quét tĩnh không phát hiện lỗi layout nhưng chưa có pixel diff hay nghiệm thu tương tác."

    matrix.append({
        "dimension": "Visual layout",
        "required_evidence": "Screenshot + geometry measurements",
        "status": layout_status,
        "summary": layout_summary
    })

    # Spatial constraints are assessed separately: arbitrary pages need not
    # use graph ports or opt into explicit alignment contracts.
    spatial_issues = [f for f in findings if str(f.get("ruleId") or f.get("id", "")).startswith("SPATIAL-")]
    spatial_counts = [s.get("spatialRelations", {}) for s in snapshots]
    checked = sum(item.get("checked", 0) for item in spatial_counts)
    if any(f.get("severity") in ("critical", "high") for f in spatial_issues):
        spatial_status = "FAIL"
        spatial_summary = f"{len(spatial_issues)} quan hệ hình học sai, có lỗi nghiêm trọng. Đã đo {checked} quan hệ."
    elif spatial_issues:
        spatial_status = "WARN"
        spatial_summary = f"{len(spatial_issues)} quan hệ hình học cần xử lý. Đã đo {checked} quan hệ."
    elif checked > 0:
        spatial_status = "PARTIAL"
        spatial_summary = f"{checked} quan hệ được đo, không thấy sai lệch theo hợp đồng; chưa chứng minh mọi vị trí đều đúng."
    else:
        spatial_status = "UNVERIFIED"
        spatial_summary = "Chưa có quan hệ hình học đủ căn cứ để đo. Cần data-aae-align-with hoặc adapter DOM phù hợp."
    matrix.append({
        "dimension": "Spatial relationships",
        "required_evidence": "Measured parent/child, paired elements, graph port/handle alignment",
        "status": spatial_status,
        "summary": spatial_summary,
    })

    # 2. Typography
    typo_data = css_intel.get("typography", {})
    typo_issues = [f for f in findings if f.get("category") in ("typography", "readability") or "SMALL-TEXT" in str(f.get("id", ""))]
    if not typo_data.get("bodyFont"):
        typo_status = "UNVERIFIED"
        typo_summary = "Không trích xuất được font stack thực tế từ DOM."
    elif any(f.get("severity") in ("critical", "high") for f in typo_issues) or typo_data.get("isolatedGeorgiaRisk"):
        typo_status = "FAIL"
        typo_summary = f"Body font: '{typo_data.get('bodyFont', 'system')[:40]}'. Nguy cơ lỗi Unicode tiếng Việt hoặc cỡ chữ nghiêm trọng."
    elif typo_issues:
        typo_status = "WARN"
        typo_summary = f"Body font: '{typo_data.get('bodyFont', 'system')[:40]}'. Unicode Việt: Hợp lệ. {len(typo_issues)} cảnh báo cỡ chữ nhỏ/readability."
    else:
        typo_status = "PASS"
        typo_summary = f"Body font: '{typo_data.get('bodyFont', 'system')[:40]}'. Font scale đạt chuẩn, Unicode Việt an toàn."

    matrix.append({
        "dimension": "Typography",
        "required_evidence": "Computed font + Vietnamese diacritics + font scale",
        "status": typo_status,
        "summary": typo_summary
    })

    # 3. Design Tokens
    tokens = css_intel.get("tokens", {})
    total_tokens = tokens.get("totalTokens", 0)
    if total_tokens == 0:
        token_status = "UNVERIFIED"
        token_summary = "Không phát hiện CSS custom properties (--*). Không thể kết luận thiếu design system: dự án có thể dùng CSS-in-JS, token từ framework hoặc native style."
    elif total_tokens < 5:
        token_status = "WARN"
        token_summary = f"Chỉ tìm thấy {total_tokens} CSS tokens. Cần chuẩn hóa thêm token màu và khoảng cách."
    else:
        token_status = "PARTIAL"
        token_summary = f"Tìm thấy {total_tokens} CSS tokens ({len(tokens.get('colorTokens', {}))} màu, {len(tokens.get('spacingTokens', {}))} khoảng cách, {len(tokens.get('radiusTokens', {}))} bo góc). {css_intel.get('cascade', {}).get('importantRuleCount', 0)} quy tắc !important."

    matrix.append({
        "dimension": "Design tokens",
        "required_evidence": "CSS custom properties (:root tokens) + cascade inventory",
        "status": token_status,
        "summary": token_summary
    })

    # 4. Hover & Focus (Interaction States)
    focus_trace = interaction.get("focusTrace", [])
    hover_feedback = interaction.get("hoverFeedback", [])
    if not focus_trace:
        state_status = "UNVERIFIED"
        state_summary = "Chưa có bằng chứng duyệt bàn phím (Tab trace rỗng)."
    else:
        missing_focus = [f for f in focus_trace if not f.get("hasVisibleFocus")]
        has_hover_effect = any(h.get("hasHoverEffect") for h in hover_feedback)
        if missing_focus:
            state_status = "FAIL"
            state_summary = f"{len(missing_focus)}/{len(focus_trace)} phần tử focus bị mất viền (:focus-visible). Thử hover {len(hover_feedback)} controls."
        elif has_hover_effect:
            state_status = "PARTIAL"
            state_summary = f"Duyệt {len(focus_trace)} phần tử phím Tab (100% có viền rõ). Thử hover {len(hover_feedback)} nút chính có phản hồi."
        else:
            state_status = "WARN"
            state_summary = f"Duyệt {len(focus_trace)} phần tử phím Tab (100% có viền rõ). Chưa thấy thay đổi style khi hover nút."

    matrix.append({
        "dimension": "Hover & focus",
        "required_evidence": "Focus-visible outline check + hover style diff",
        "status": state_status,
        "summary": state_summary
    })

    # 5. Responsive Design
    overflow_issues = [f for f in findings if "OVERFLOW" in str(f.get("id", "")) or f.get("category") == "responsive"]
    if not expected_viewports <= observed_viewports:
        resp_status = "UNVERIFIED"
        resp_summary = f"Chỉ quét {len(expected_viewports & observed_viewports)}/3 viewport tiêu chuẩn. Chưa đủ bằng chứng responsive."
    elif any(f.get("severity") in ("critical", "high") for f in overflow_issues):
        resp_status = "FAIL"
        resp_summary = f"Đã quét 3 kích thước màn hình. Phát hiện lỗi tràn ngang / vỡ layout nghiêm trọng."
    elif overflow_issues:
        resp_status = "WARN"
        resp_summary = f"Đã quét 3 kích thước màn hình. {len(overflow_issues)} cảnh báo tràn ngang / co giãn."
    else:
        resp_status = "PARTIAL"
        resp_summary = "Ba viewport không báo tràn ngang trong quét tĩnh; chưa đo reflow ở zoom 200%, clipping và touch."

    matrix.append({
        "dimension": "Responsive",
        "required_evidence": "3+ viewports (1440/768/390px) + horizontal overflow test",
        "status": resp_status,
        "summary": resp_summary
    })

    # 6. Keyboard Accessibility (WCAG 2.2 AA)
    a11y_issues = [f for f in findings if f.get("category") in ("accessibility", "forms") or any(k in str(f.get("id", "")) for k in ("CONTRAST", "NAME", "LABEL", "TABINDEX"))]
    if any(f.get("severity") in ("critical", "high") for f in a11y_issues):
        a11y_status = "FAIL"
        a11y_summary = f"Phát hiện {sum(1 for f in a11y_issues if f.get('severity') in ('critical', 'high'))} lỗi accessibility nghiêm trọng (Name/Label/Contrast/Tabindex)."
    elif a11y_issues:
        a11y_status = "WARN"
        a11y_summary = f"{len(a11y_issues)} vấn đề accessibility tự động phát hiện."
    else:
        a11y_status = "UNVERIFIED"
        a11y_summary = "Không có finding trong quét tĩnh; chưa có accessibility tree, kiểm thử toàn bộ thứ tự Tab và focus trap."

    matrix.append({
        "dimension": "Keyboard accessibility",
        "required_evidence": "A11y tree names + Tab order + contrast ratios + form labels",
        "status": a11y_status,
        "summary": a11y_summary
    })

    # 7. Dynamic UI Interaction
    transitions = interaction.get("stateTransitions", [])
    if not transitions:
        inter_status = "UNVERIFIED"
        inter_summary = "Chưa có bằng chứng kiểm thử chuyển trạng thái động."
    elif any(t.get("causedHorizontalOverflow") for t in transitions):
        inter_status = "FAIL"
        inter_summary = "Tương tác đóng/mở thành phần động gây tràn ngang bố cục."
    else:
        has_deep = any(t.get("component") in ("tabs", "modals") for t in transitions)
        inter_status = "PARTIAL"  # One sampled tab/modal is not full journey evidence.
        comp_types = ", ".join(set(t.get("component", "details") for t in transitions))
        inter_summary = f"Đã thử nghiệm {len(transitions)} tương tác ({comp_types}). Không gây tràn bố cục."

    matrix.append({
        "dimension": "Dynamic UI interaction",
        "required_evidence": "Before -> action -> after transition trace (accordions/tabs/dialogs)",
        "status": inter_status,
        "summary": inter_summary
    })

    # 8. Runtime & Diagnostics
    perf = runtime.get("performance", {})
    http_errors = runtime.get("httpErrorCount", 0)
    console_errors = runtime.get("consoleErrorCount", 0)
    if runtime.get("hasCriticalRuntimeError"):
        rt_status = "FAIL"
        rt_summary = f"{console_errors} lỗi console, {http_errors} response HTTP 4xx/5xx, {runtime.get('failedRequestCount', 0)} request mạng thất bại."
    elif console_errors > 0 or http_errors > 0:
        rt_status = "WARN"
        rt_summary = f"{console_errors} cảnh báo console, {http_errors} lỗi HTTP."
    elif not perf or any(perf.get(key) is None for key in ("fcpMs", "ttfbMs", "loadEventMs")):
        rt_status = "UNVERIFIED"
        rt_summary = "Thiếu FCP, TTFB hoặc Navigation Timing; không thể kết luận runtime/performance."
    else:
        rt_status = "PARTIAL"
        lcp_text = f", LCP: {perf.get('lcpMs')}ms" if perf.get('lcpMs') else ""
        cls_text = f", CLS: {perf.get('clsScore')}" if perf.get('clsScore') is not None else ""
        rt_summary = f"0 lỗi console, 0 lỗi HTTP. FCP: {perf.get('fcpMs', 'N/A')}ms{lcp_text}{cls_text}, Load: {perf.get('loadEventMs', 'N/A')}ms."

    matrix.append({
        "dimension": "Runtime & diagnostics",
        "required_evidence": "Console logs + uncaught exceptions + HTTP 4xx/5xx + Web Vitals (FCP/LCP/CLS)",
        "status": rt_status,
        "summary": rt_summary
    })

    # 9. Regression Acceptance Gate
    if regression:
        reg_status = "WARN" if regression.get("coverageComparable") and regression.get("new_count", 0) else "UNVERIFIED"
        matrix.append({
            "dimension": "Regression acceptance",
            "required_evidence": "Component-aware diffing against baseline report",
            "status": reg_status,
            "summary": (regression.get("notComparableReason")
                or f"Đã xác minh sửa: 0; Cần tái kiểm chứng: {regression.get('requires_retest_count', 0)}; Còn quan sát: {regression.get('persisting_count', 0)}; Phát sinh: {regression.get('new_count', 0)}. Chưa đủ bằng chứng xác nhận FIXED.")
        })
    else:
        matrix.append({
            "dimension": "Regression acceptance",
            "required_evidence": "Baseline comparison",
            "status": "UNVERIFIED",
            "summary": "Lượt audit đầu tiên của URL này (chưa có baseline để kiểm chứng hồi quy)."
        })

    return matrix
