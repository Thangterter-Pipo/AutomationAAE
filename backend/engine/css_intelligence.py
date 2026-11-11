"""CSS Intelligence Engine: Extracts design tokens, typography scale, CSS cascade overrides, and hardcoded styling."""

import re
from pathlib import Path
from typing import Any, Dict
from urllib.parse import unquote, urljoin, urlparse


CSS_EXTRACTOR_JS = """
(() => {
    const tokens = {};
    const fontFamilies = new Set();
    const fontSizes = new Set();
    const hardcodedColors = new Set();
    const importantRules = [];
    const colorUsage = {};

    // 1. Extract CSS Custom Properties (Tokens)
    try {
        for (const sheet of document.styleSheets) {
            try {
                for (const rule of sheet.cssRules || []) {
                    if (rule.style) {
                        for (let i = 0; i < rule.style.length; i++) {
                            const prop = rule.style[i];
                            if (prop.startsWith('--')) {
                                tokens[prop] = rule.style.getPropertyValue(prop).trim();
                            }
                            if (rule.style.getPropertyPriority(prop) === 'important') {
                                importantRules.push({
                                    selector: rule.selectorText || 'inline',
                                    property: prop,
                                    value: rule.style.getPropertyValue(prop).trim()
                                });
                            }
                        }
                    }
                }
            } catch (e) {
                // Cross-origin stylesheet access blocked
            }
        }
    } catch (e) {}

    // Fallback: Read computed CSS variables from :root / html / body
    const rootStyle = getComputedStyle(document.documentElement);
    const bodyStyle = document.body ? getComputedStyle(document.body) : rootStyle;
    for (const key of Object.keys(tokens)) {
        const computed = rootStyle.getPropertyValue(key).trim() || bodyStyle.getPropertyValue(key).trim();
        if (computed) tokens[key] = computed;
    }

    // 2. Scan visible elements for typography and color usage
    const elements = document.querySelectorAll('body *');
    let sampled = 0;
    for (const el of elements) {
        if (sampled++ > 400) break;
        if (el.matches('script,style,noscript,template') || el.offsetParent === null) continue;

        const cs = getComputedStyle(el);
        if (cs.fontFamily) fontFamilies.add(cs.fontFamily.split(',')[0].replace(/['"]/g, '').trim());
        if (cs.fontSize) fontSizes.add(parseFloat(cs.fontSize));

        const color = cs.color;
        const bg = cs.backgroundColor;
        if (color && color !== 'rgba(0, 0, 0, 0)') {
            colorUsage[color] = (colorUsage[color] || 0) + 1;
        }
        if (bg && bg !== 'rgba(0, 0, 0, 0)') {
            colorUsage[bg] = (colorUsage[bg] || 0) + 1;
        }
    }

    // Categorize tokens
    const colorTokens = {};
    const spacingTokens = {};
    const radiusTokens = {};
    const otherTokens = {};

    for (const [key, val] of Object.entries(tokens)) {
        const lk = key.toLowerCase();
        if (lk.includes('color') || lk.includes('ink') || lk.includes('bg') || lk.includes('border') || lk.includes('line') || lk.includes('red') || lk.includes('green') || lk.includes('blue') || lk.includes('white') || lk.includes('surface')) {
            colorTokens[key] = val;
        } else if (lk.includes('space') || lk.includes('gap') || lk.includes('pad') || lk.includes('margin')) {
            spacingTokens[key] = val;
        } else if (lk.includes('radius') || lk.includes('round')) {
            radiusTokens[key] = val;
        } else {
            otherTokens[key] = val;
        }
    }

    // Vietnamese diacritic font check
    const primaryFont = bodyStyle.fontFamily || '';
    const hasVietnameseSafeFallback = /times new roman|arial|inter|helvetica|system-ui|sans-serif|segoe ui|roboto/i.test(primaryFont);
    const isolatedGeorgiaRisk = /georgia/i.test(primaryFont) && !/times new roman|arial|times/i.test(primaryFont);

    const sortedFontSizes = Array.from(fontSizes).sort((a, b) => a - b);
    const tinyFontCount = sortedFontSizes.filter(s => s < 12).length;

    // Collect stylesheet links and inlines for Python fallback
    const rawLinks = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).map(l => l.getAttribute('href') || l.href);
    const rawInlines = Array.from(document.querySelectorAll('style')).map(s => s.textContent);

    return {
        tokens,
        importantRules,
        rawLinks,
        rawInlines,
        typography: {
            bodyFont: primaryFont,
            fontFamilies: Array.from(fontFamilies).slice(0, 8),
            fontScale: sortedFontSizes,
            tinyFontCount,
            vietnameseSafe: hasVietnameseSafeFallback && !isolatedGeorgiaRisk,
            isolatedGeorgiaRisk
        },
        colorPaletteCount: Object.keys(colorUsage).length
    };
})()
"""


def _categorize_tokens(tokens: Dict[str, str]) -> Dict[str, Any]:
    color_tokens = {}
    spacing_tokens = {}
    radius_tokens = {}
    other_tokens = {}

    for key, val in tokens.items():
        lk = key.lower()
        if any(term in lk for term in ('color', 'ink', 'bg', 'border', 'line', 'red', 'amber', 'blue', 'green', 'white', 'soft', 'paper', 'surface', 'shadow')):
            color_tokens[key] = val
        elif any(term in lk for term in ('space', 'gap', 'pad', 'margin')):
            spacing_tokens[key] = val
        elif any(term in lk for term in ('radius', 'round')):
            radius_tokens[key] = val
        else:
            other_tokens[key] = val

    return {
        "totalTokens": len(tokens),
        "colorTokens": color_tokens,
        "spacingTokens": spacing_tokens,
        "radiusTokens": radius_tokens,
        "otherTokens": other_tokens,
    }


def analyze_css(page: Any) -> Dict[str, Any]:
    """Extracts CSS Intelligence and Token Inventory from a live Playwright page."""
    try:
        data = page.evaluate(CSS_EXTRACTOR_JS)
        tokens = data.get("tokens", {})
        important_rules = data.get("importantRules", [])

        # Fallback for file:// or cross-origin stylesheets where sheet.cssRules is blocked
        raw_links = data.get("rawLinks", [])
        raw_inlines = data.get("rawInlines", [])
        page_url = page.url

        # Parse inline <style>
        for inline_text in raw_inlines:
            for match in re.finditer(r"(--[\w-]+)\s*:\s*([^;{}]+?)(?:;|\})", inline_text or ""):
                tokens[match.group(1).strip()] = match.group(2).strip()

        # Parse linked stylesheets
        for href in raw_links:
            if not href:
                continue
            resolved = urljoin(page_url, href)
            parsed = urlparse(resolved)
            if parsed.scheme == "file":
                raw_path = unquote(parsed.path)
                # Handle Windows file paths like /E:/... -> E:/...
                if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
                    raw_path = raw_path[1:]
                file_path = Path(raw_path)
                if file_path.is_file():
                    try:
                        css_content = file_path.read_text(encoding="utf-8")
                        for match in re.finditer(r"(--[\w-]+)\s*:\s*([^;{}]+?)(?:;|\})", css_content):
                            tokens[match.group(1).strip()] = match.group(2).strip()
                        for match in re.finditer(r"([^;{}]+)\s*:\s*([^;{}]+!important)(?:;|\})", css_content):
                            important_rules.append({
                                "selector": "stylesheet",
                                "property": match.group(1).strip(),
                                "value": match.group(2).strip()
                            })
                    except Exception:
                        pass

        token_summary = _categorize_tokens(tokens)

        return {
            "status": "success",
            "tokens": token_summary,
            "typography": data.get("typography", {}),
            "cascade": {
                "importantRuleCount": len(important_rules),
                "importantRules": important_rules[:15],
            },
            "colorPaletteCount": data.get("colorPaletteCount", 0),
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "tokens": {"totalTokens": 0, "colorTokens": {}, "spacingTokens": {}, "radiusTokens": {}, "otherTokens": {}},
            "typography": {},
            "cascade": {"importantRuleCount": 0, "importantRules": []},
            "colorPaletteCount": 0,
        }
