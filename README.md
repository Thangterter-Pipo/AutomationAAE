# AutomationAAE — AI UI/UX Reviewer

**🌐 Language / Ngôn ngữ:** **English** · [Tiếng Việt](./README.vi.md)

A Chrome Extension (MV3), local loopback bridge, and Playwright-powered CLI engine to audit web pages and extension UIs with concrete DOM/geometry/visual evidence and export structured Markdown/JSON briefs for coding agents.

## Features

- **Chrome Extension Review Studio (`extension/`)**:
  - Single-viewport scan and 3-viewport responsive scan (`1440×900`, `768×1024`, `390×844`) with optional debugger attachment.
  - DevTools panel (**AAE · UI/UX**) for inspecting popups, side panels, and other extension surfaces safely.
  - Built-in screenshot masking over `input`, `textarea`, `[contenteditable]`, `[data-private]`, and `[data-sensitive]` elements.
  - **Image-target review**: import a standalone screenshot for visual review, or supply a desired reference image to compare against a live capture.
  - **Safe extension reload**: reload AutomationAAE itself or opt-in via Chrome's `management` permission to disable/re-enable another installed extension.
  - Explicit user consent gates before sending any text or image evidence to an AI gateway.
- **Autonomous CLI Audit (`run_audit.py`)**:
  - Works on live URLs, local HTML fixtures (served over a restricted ephemeral loopback HTTP server), or standalone PNG/JPEG/WebP screenshots.
  - **CSS Intelligence** (`backend/engine/css_intelligence.py`): design tokens (`--*`), typography scale, cascade overrides.
  - **Dynamic Interaction Suite** (`backend/engine/interaction.py`): keyboard `Tab` focus-visible trace, hover feedback, `<details>` expand/collapse, modal/canvas/scroll checks.
  - **Spatial Relations Engine** (`backend/engine/spatial_relations.py`): checks geometric alignment via `data-aae-align-with` / `data-aae-port-role` contracts and React Flow / diagram port-handle heuristics.
  - **9-Dimension Acceptance Matrix & Regression Gate** (`backend/engine/coverage_gate.py`): distinguishes `PASS`, `PARTIAL`, `WARN`, `FAIL`, and `UNVERIFIED` without guessing or fabricating coverage.

## Quick Start (Windows / Chrome Extension + Local Bridge)

1. Start the local bridge (`start-bridge.cmd` on Windows, or `python -m backend.server` from the repo root). Keep the terminal open; it binds exclusively to `127.0.0.1:48765`.
2. Open `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and select `AutomationAAE/extension`.
3. Click the AutomationAAE extension icon on any target page and enter the pairing token printed in the bridge console.
4. Configure **Base URL**, **Model ID**, and **API key**, then click **Save configuration**. Keys are stored locally in `.data/settings.sqlite3` and never written into reports. If your gateway requires `max_completion_tokens`, switch it under **Gateway limits & compatibility**.
5. Click **Scan current screen** for a local rule-based audit, or **Responsive scan (3 sizes)** to test Desktop/Tablet/Mobile.
6. Inspect the collected evidence, limitations, and masked screenshots. Check the consent box and click **Analyze with AI** only when you intend to send data to your configured gateway.
7. Click **Save report** to write `.json` and `.md` briefs into `AutomationAAE/reports/`. If disk write fails after a paid AI call, the report stays in pending recovery so you can retry saving without re-calling the AI.

## Auditing Extension UIs

Chrome restricts extensions from reading the DOM of other extension popups or side panels directly:

1. Open the target popup or side panel.
2. Right-click → **Inspect** to open Chrome DevTools.
3. Switch to the **AAE · UI/UX** DevTools tab.
4. Click **Scan current screen** — the panel reads the inspected surface directly without needing a second debugger.

For `chrome://`, Chrome Web Store, or non-inspectable surfaces, use **Import UI image** (redact sensitive data manually before consenting to AI upload).

## Autonomous CLI Usage (`run_audit.py`)

Install optional CLI dependencies (`backend/server.py` uses Python stdlib only):

```bash
pip install -r requirements.txt
playwright install chromium
```

Or run with `uv`:

```bash
# Local rule-based + Playwright audit (no AI calls)
uv run --no-project --with playwright==1.55.0 python run_audit.py https://example.com --no-ai

# Audit a local HTML file across 3 viewports
uv run --no-project --with playwright==1.55.0 python run_audit.py path/to/index.html --no-ai

# Screenshot-only visual audit
uv run --no-project --with playwright==1.55.0 --with pillow python run_audit.py path/to/screenshot.png --no-ai

# Compare a live URL against a reference design image
uv run --no-project --with playwright==1.55.0 --with pillow python run_audit.py https://example.com --reference-image path/to/desired.png --no-ai

# Output JSON or Markdown brief to stdout
python run_audit.py https://example.com --no-ai --brief
```

Environment variables:
- `AAE_CHROMIUM_PATH`: Optional path to a specific Chrome/Chromium executable (defaults to Playwright's installed Chromium).

### Spatial Alignment Contracts (Optional)

You can annotate elements in any web app to enforce geometric alignment checks:

```html
<div id="toolbar-title">Tools</div>
<button data-aae-align-with="#toolbar-title"
        data-aae-align="center-y"
        data-aae-tolerance="6">Add</button>
```

Supported rules: `center`, `center-x`, `center-y`, `left`, `right`, `top`, `bottom`. For node/diagram frameworks, annotate `data-aae-node` and `data-aae-port-role` (`anchor`, `button`, `handle`).

## Security & Data Boundaries

- Scans do **not** read values inside `input`, `textarea`, or `contenteditable` fields, nor cookies, `localStorage`/`sessionStorage`, scripts, or network payloads.
- Gateways must use **HTTPS** (plain HTTP is allowed only for `localhost` / `127.0.0.1`). Redirects, embedded URL credentials, queries/fragments, and link-local/multicast addresses are rejected.
- TLS verification is strictly enforced to protect API keys; expired or untrusted certificates are rejected.
- Reports may contain visible page text, URLs, and CSS selectors — review before sharing.
- `.data/settings.sqlite3` stores local runtime configuration unencrypted on disk; the bridge binds loopback only. Rotate the pairing token anytime via:

```bash
python -m backend.server --rotate-pairing
```

- **Evidence limits**: 3-viewport screenshot + DOM geometry checks without pixel-diff verification are marked `PARTIAL`, not full visual QA. Image-only audits remain `UNVERIFIED` for interaction, keyboard accessibility, and runtime behavior. Missing findings across mismatched coverage are marked `requires_retest`, never assumed `FIXED`.

## Verification

```bash
python -m py_compile run_audit.py backend/server.py backend/audit.py db/repositories.py
python -m backend.server --help
python run_audit.py --help
```

## License

[MIT](./LICENSE)
