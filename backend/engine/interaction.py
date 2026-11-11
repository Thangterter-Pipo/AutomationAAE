"""Interaction Testing Engine: Tests dynamic states (hover, focus, expand/collapse, scroll)

and records before -> action -> after evidence traces without destructive mutations.
"""

from typing import Any, Dict, List


INTERACTION_EXTRACTOR_JS = """
(() => {
    // 1. Identify interactive elements and collapsible components
    const focusableSelector = 'button:not([disabled]), a[href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), details > summary, [tabindex="0"]';
    const focusables = Array.from(document.querySelectorAll(focusableSelector)).filter(el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && el.offsetParent !== null;
    });

    const collapsibles = Array.from(document.querySelectorAll('details, [aria-expanded], [role="tab"]')).map(el => {
        return {
            tag: el.tagName.toLowerCase(),
            id: el.id,
            className: el.className,
            isOpen: el.hasAttribute('open') || el.getAttribute('aria-expanded') === 'true',
            summaryText: el.querySelector('summary') ? el.querySelector('summary').textContent.trim() : (el.textContent.trim().slice(0, 30))
        };
    });

    return {
        focusableCount: focusables.length,
        collapsibles: collapsibles.slice(0, 8),
        hasDetails: document.querySelectorAll('details').length > 0,
        hasTabs: document.querySelectorAll('[role="tab"]').length > 0,
        hasModals: document.querySelectorAll('[role="dialog"], dialog').length > 0
    };
})()
"""


def run_interaction_suite(page: Any) -> Dict[str, Any]:
    """Executes safe interaction exploration:

    1. Tab keyboard traversal & focus ring visibility.
    2. Hover state verification on primary controls.
    3. Expand/collapse toggle check on <details> or tabs.
    4. Scroll & fixed header behavior check.
    """
    results: Dict[str, Any] = {
        "status": "completed",
        "focusTrace": [],
        "hoverFeedback": [],
        "stateTransitions": [],
        "scrollCheck": {},
        "issues": [],
    }

    try:
        # Step 1: Scan available interactive components
        page.wait_for_timeout(200)
        metadata = page.evaluate(INTERACTION_EXTRACTOR_JS)

        # Step 2: Keyboard Tab Sequence & Focus-Visible Trace (up to 15 tabs)
        focus_trace = []
        missing_focus_rings = []
        for step in range(12):
            page.keyboard.press("Tab")
            active_info = page.evaluate("""() => {
                const el = document.activeElement;
                if (!el || el === document.body) return null;
                const cs = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                const outlineWidth = parseFloat(cs.outlineWidth) || 0;
                const hasVisibleOutline = (cs.outlineStyle !== 'none' && outlineWidth > 0 && cs.outlineColor !== 'transparent');
                const hasBoxShadow = cs.boxShadow && cs.boxShadow !== 'none';
                return {
                    tagName: el.tagName.toLowerCase(),
                    id: el.id || '',
                    className: el.className ? String(el.className).slice(0, 50) : '',
                    role: el.getAttribute('role') || '',
                    hasVisibleFocus: hasVisibleOutline || hasBoxShadow,
                    outline: cs.outline,
                    rect: { width: Math.round(rect.width), height: Math.round(rect.height) }
                };
            }""")
            if active_info:
                focus_trace.append(active_info)
                if not active_info["hasVisibleFocus"]:
                    missing_focus_rings.append(f"{active_info['tagName']}{'#' + active_info['id'] if active_info['id'] else ''}")

        results["focusTrace"] = focus_trace
        if missing_focus_rings:
            results["issues"].append({
                "severity": "medium",
                "category": "accessibility",
                "title": f"{len(missing_focus_rings)} phần tử không hiển thị viền focus (focus-visible)",
                "evidence": f"OBSERVED: Khi dùng phím Tab, các phần tử sau không có outline/box-shadow nhận diện focus: {', '.join(missing_focus_rings[:5])}",
                "recommendation": "Khai báo :focus-visible { outline: 2px solid ...; outline-offset: 2px; } cho tất cả phần tử tương tác.",
                "acceptance": "Mọi phần tử nhận focus bàn phím đều có chỉ báo thị giác rõ ràng."
            })

        # Step 3: Hover feedback on primary buttons and links
        hover_targets = page.evaluate("""() => {
            const targets = Array.from(document.querySelectorAll('button.primary, button:not(.primary), a.brand, a.connection, .text-button'));
            return targets.slice(0, 4).map(el => {
                const cs = window.getComputedStyle(el);
                return {
                    selector: el.id ? '#' + el.id : (el.className ? '.' + el.className.trim().split(/\\s+/)[0] : el.tagName.toLowerCase()),
                    normalBg: cs.backgroundColor,
                    normalColor: cs.color,
                    normalShadow: cs.boxShadow
                };
            });
        }""")

        hover_results = []
        for t in hover_targets:
            sel = t["selector"]
            try:
                locator = page.locator(sel).first
                if locator.is_visible():
                    locator.hover(timeout=1000)
                    page.wait_for_timeout(100)
                    hover_style = page.evaluate("""(sel) => {
                        const el = document.querySelector(sel);
                        if (!el) return null;
                        const cs = window.getComputedStyle(el);
                        return { bg: cs.backgroundColor, color: cs.color, shadow: cs.boxShadow, cursor: cs.cursor };
                    }""", sel)
                    if hover_style:
                        has_change = (hover_style["bg"] != t["normalBg"] or
                                      hover_style["color"] != t["normalColor"] or
                                      hover_style["shadow"] != t["normalShadow"])
                        hover_results.append({
                            "selector": sel,
                            "hasHoverEffect": has_change,
                            "cursor": hover_style["cursor"]
                        })
            except Exception:
                continue
        results["hoverFeedback"] = hover_results

        # Step 4: Expand/Collapse Transitions (Test safe <details> or accordions)
        details_elements = page.locator("details summary").all()
        transitions = []
        if details_elements:
            first_details = details_elements[0]
            try:
                parent = first_details.locator("..")
                initial_open = parent.evaluate("el => el.hasAttribute('open')")
                first_details.click(timeout=1500)
                page.wait_for_timeout(200)
                after_click_open = parent.evaluate("el => el.hasAttribute('open')")
                height_after = parent.evaluate("el => el.getBoundingClientRect().height")
                overflow_after = page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")

                transitions.append({
                    "component": "details_accordion",
                    "action": "toggle_details_summary",
                    "initialOpen": initial_open,
                    "afterClickOpen": after_click_open,
                    "height": round(height_after),
                    "causedHorizontalOverflow": overflow_after
                })
                # Toggle back
                first_details.click(timeout=1500)
                page.wait_for_timeout(100)
            except Exception:
                pass

        # Step 5: Tabs & Panels Switching (Test safe tab selection)
        tabs = page.locator('[role="tab"], .tab-btn').all()
        if len(tabs) >= 2:
            try:
                second_tab = tabs[1]
                initial_selected = second_tab.evaluate("el => el.getAttribute('aria-selected') === 'true' || el.classList.contains('active')")
                second_tab.click(timeout=1500)
                page.wait_for_timeout(200)
                after_selected = second_tab.evaluate("el => el.getAttribute('aria-selected') === 'true' || el.classList.contains('active')")
                transitions.append({
                    "component": "tabs",
                    "action": "switch_tab",
                    "initialSelected": initial_selected,
                    "afterClickSelected": after_selected,
                    "tabSwitched": after_selected != initial_selected
                })
                # Switch back to first tab
                tabs[0].click(timeout=1500)
                page.wait_for_timeout(100)
            except Exception:
                pass

        # Step 6: Dialog / Modal & Esc Key Dismissal Check
        modal_info = page.evaluate("""() => {
            const modals = Array.from(document.querySelectorAll('dialog, [role="dialog"], .modal'));
            const openModals = modals.filter(m => m.open || m.classList.contains('open') || m.classList.contains('active') || window.getComputedStyle(m).display !== 'none');
            return {
                totalModals: modals.length,
                openCount: openModals.length,
                hasBackdrop: document.querySelectorAll('.modal-backdrop, .overlay').length > 0
            };
        }""")
        results["modalCheck"] = modal_info

        # Step 7: Canvas / Graph Node Exploration (Graph & Diagram Support)
        canvas_info = page.evaluate("""() => {
            const canvasEl = document.querySelector('canvas, .react-flow, .flow-canvas, .diagram-container');
            if (!canvasEl) return { hasCanvas: false };
            const rect = canvasEl.getBoundingClientRect();
            const nodes = document.querySelectorAll('.react-flow__node, .canvas-node, .node');
            return {
                hasCanvas: true,
                tagName: canvasEl.tagName.toLowerCase(),
                bounds: { width: Math.round(rect.width), height: Math.round(rect.height) },
                nodeCount: nodes.length,
                isInteractive: canvasEl.matches('canvas') || canvasEl.matches('[tabindex], [data-zoomable]')
            };
        }""")
        results["canvasCheck"] = canvas_info

        # Step 8: Theme Switcher Check
        theme_info = page.evaluate("""() => {
            const btn = document.querySelector('[data-theme-toggle], #theme-toggle, .theme-switch');
            const root = document.documentElement;
            return {
                hasThemeToggle: Boolean(btn),
                initialTheme: root.getAttribute('data-theme') || (root.classList.contains('dark') ? 'dark' : 'light')
            };
        }""")
        results["themeCheck"] = theme_info
        results["stateTransitions"] = transitions

        # Step 9: Scroll & Stacking behavior
        scroll_data = page.evaluate("""() => {
            const initialScroll = window.scrollY;
            window.scrollTo(0, 300);
            const topbar = document.querySelector('.topbar, header');
            let topbarAnchored = false;
            if (topbar) {
                const cs = window.getComputedStyle(topbar);
                topbarAnchored = (cs.position === 'fixed' || cs.position === 'sticky');
            }
            const scrollWidth = document.documentElement.scrollWidth;
            const innerWidth = window.innerWidth;
            window.scrollTo(0, initialScroll);
            return {
                canScroll: document.documentElement.scrollHeight > window.innerHeight,
                topbarAnchored,
                hasOverflowOnScroll: scrollWidth > innerWidth + 4
            };
        }""")
        results["scrollCheck"] = scroll_data

    except Exception as exc:
        results["status"] = "partial_error"
        results["error"] = str(exc)

    return results
