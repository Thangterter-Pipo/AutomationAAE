# AutomationAAE — AI UI/UX Reviewer

**🌐 Ngôn ngữ / Language:** [English](./README.md) · **Tiếng Việt**

Chrome Extension (MV3), local loopback bridge và CLI engine chạy trên Playwright giúp quét UI/UX website hoặc giao diện extension, tổng hợp báo cáo có bằng chứng DOM/hình học/thị giác và xuất brief Markdown/JSON cho coding agent đọc lại.

## Tính năng chính

- **Chrome Extension Review Studio (`extension/`)**:
  - Quét màn hình hiện tại và quét responsive 3 kích thước (`1440×900`, `768×1024`, `390×844`) với quyền debugger khi cần.
  - Tab DevTools (**AAE · UI/UX**) hỗ trợ kiểm tra popup, side panel hoặc giao diện của extension khác một cách an toàn.
  - Tự động làm mờ tạm thời các vùng `input`, `textarea`, `[contenteditable]`, `[data-private]`, `[data-sensitive]` khi chụp viewport.
  - **Đánh giá theo ảnh mục tiêu**: nhập ảnh chụp độc lập để phân tích thị giác, hoặc chọn ảnh thiết kế mẫu để đối chiếu pixel với giao diện thực tế.
  - **Reload extension an toàn**: reload chính AutomationAAE hoặc cấp quyền `management` tùy chọn để disable/re-enable extension khác.
  - Bắt buộc tick đồng ý (consent) trước khi gửi bằng chứng văn bản hoặc hình ảnh tới AI gateway.
- **Autonomous CLI Engine (`run_audit.py`)**:
  - Hỗ trợ URL live, file HTML cục bộ (phục vụ qua loopback HTTP cô lập và giới hạn loại file), hoặc ảnh PNG/JPEG/WebP độc lập.
  - **CSS Intelligence** (`backend/engine/css_intelligence.py`): trích xuất CSS custom properties (`--*`), typography scale, cascade overrides.
  - **Dynamic Interaction Suite** (`backend/engine/interaction.py`): kiểm tra thứ tự phím `Tab` và `focus-visible`, hiệu ứng hover, đóng/mở `<details>`, modal/canvas/scroll.
  - **Spatial Relations Engine** (`backend/engine/spatial_relations.py`): kiểm tra căn chỉnh hình học qua thuộc tính `data-aae-align-with` / `data-aae-port-role` và heuristic cho React Flow / diagram canvas.
  - **9-Dimension Acceptance Matrix & Regression Gate** (`backend/engine/coverage_gate.py`): phân biệt rõ `PASS`, `PARTIAL`, `WARN`, `FAIL` và `UNVERIFIED`, không suy đoán hay tự giả định đã sửa lỗi.

## Chạy lần đầu trên Windows (Chrome Extension + Local Bridge)

1. Mở `start-bridge.cmd` (hoặc chạy `python -m backend.server` từ thư mục gốc). Giữ cửa sổ này mở; dịch vụ chỉ lắng nghe `127.0.0.1:48765`.
2. Vào `chrome://extensions`, bật **Developer mode**, chọn **Load unpacked** và trỏ tới thư mục `AutomationAAE/extension`.
3. Bấm biểu tượng AutomationAAE trên trang cần kiểm tra, nhập mã ghép nối được in trong cửa sổ dịch vụ.
4. Nhập **Base URL**, **Model ID** và **API key**, bấm **Lưu cấu hình**. Key chỉ lưu trong `.data/settings.sqlite3` trên máy và không nằm trong báo cáo. Nếu gateway dùng schema mới, mở **Giới hạn & tương thích gateway** để chọn `max_completion_tokens`.
5. Bấm **Quét màn hình hiện tại** để chạy kiểm tra cục bộ, hoặc **Quét Responsive (3 sizes)** để kiểm tra Desktop/Tablet/Mobile.
6. Xem lại bằng chứng, giới hạn và ảnh chụp. Chỉ tick consent rồi bấm **Phân tích bằng AI** khi bạn thật sự muốn gửi dữ liệu tới gateway.
7. Bấm **Lưu báo cáo** để ghi `.json` và `.md` vào `AutomationAAE/reports/`. Nếu ổ đĩa lỗi sau khi AI đã trả kết quả, lịch sử giữ trạng thái **CHỜ LƯU · KHÔNG GỌI LẠI AI** để lưu lại mà không tốn thêm lượt gọi AI.

## Kiểm tra UI/UX của chính extension

Chrome không cho extension tùy ý đọc DOM của popup/side panel hoặc extension khác. Cách an toàn là:

1. Mở popup/side panel cần đánh giá.
2. Chuột phải → **Inspect** để mở DevTools.
3. Mở tab **AAE · UI/UX** do AutomationAAE thêm vào DevTools.
4. Bấm **Quét màn hình hiện tại**. DevTools panel đọc DOM của bề mặt đang inspect, không cần gắn debugger thứ hai.

Nếu bề mặt là `chrome://`, Chrome Web Store hoặc trang không inspect được, dùng **Nhập ảnh giao diện** (hãy che thông tin nhạy cảm trước khi đồng ý gửi AI).

## Dùng qua CLI (`run_audit.py`)

Cài thêm thư viện tùy chọn cho CLI (`backend/server.py` chỉ dùng Python stdlib):

```bash
pip install -r requirements.txt
playwright install chromium
```

Hoặc chạy trực tiếp bằng `uv`:

```bash
# Quét cục bộ bằng Playwright + rule engine (không gọi AI)
uv run --no-project --with playwright==1.55.0 python run_audit.py https://example.com --no-ai

# Quét file HTML local qua 3 viewport
uv run --no-project --with playwright==1.55.0 python run_audit.py path/to/index.html --no-ai

# Đánh giá chỉ từ ảnh chụp màn hình
uv run --no-project --with playwright==1.55.0 --with pillow python run_audit.py path/to/screenshot.png --no-ai

# Đối chiếu URL thực tế với ảnh thiết kế mục tiêu
uv run --no-project --with playwright==1.55.0 --with pillow python run_audit.py https://example.com --reference-image path/to/desired.png --no-ai

# Xuất JSON hoặc brief Markdown ra stdout
python run_audit.py https://example.com --no-ai --brief
```

Biến môi trường hỗ trợ:
- `AAE_CHROMIUM_PATH`: Đường dẫn tùy chọn tới file chạy Chrome/Chromium (mặc định dùng Chromium của Playwright).

### Khai báo ràng buộc căn chỉnh hình học (Tùy chọn)

Có thể gắn thuộc tính vào HTML để scanner kiểm tra lệch tọa độ thực tế:

```html
<div id="toolbar-title">Tools</div>
<button data-aae-align-with="#toolbar-title"
        data-aae-align="center-y"
        data-aae-tolerance="6">Add</button>
```

Hỗ trợ các quy tắc: `center`, `center-x`, `center-y`, `left`, `right`, `top`, `bottom`. Với framework sơ đồ/node, dùng `data-aae-node` và `data-aae-port-role` (`anchor`, `button`, `handle`).

## Dữ liệu, bảo mật và giới hạn

- Quét **không** đọc giá trị bên trong `input`/`textarea`/`contenteditable`, cookies, `localStorage`/`sessionStorage`, script hay payload mạng.
- Gateway bắt buộc dùng **HTTPS** (HTTP chỉ cho phép với `localhost` / `127.0.0.1`). Không hỗ trợ redirect, URL chứa credentials/query/fragment hoặc địa chỉ link-local/multicast.
- Kiểm tra chứng chỉ TLS được bật chặt chẽ để bảo vệ API key; chứng chỉ hết hạn hoặc không tin cậy sẽ bị từ chối.
- Báo cáo có thể chứa URL, chữ hiển thị và CSS selector trên trang đã quét — hãy kiểm tra trước khi chia sẻ.
- `.data/settings.sqlite3` lưu cấu hình runtime cục bộ chưa mã hóa trên đĩa; dịch vụ chỉ lắng nghe loopback. Xoay mã ghép nối khi cần:

```bash
python -m backend.server --rotate-pairing
```

- **Giới hạn bằng chứng**: Quét 3 viewport bằng screenshot + DOM geometry chưa có pixel-diff tự động được xếp `PARTIAL`. Kiểm tra chỉ từ ảnh giữ trạng thái `UNVERIFIED` cho tương tác, bàn phím và runtime. Các finding biến mất khi phạm vi quét không tương đương được đánh dấu `requires_retest`, không tự coi là `FIXED`.

## Kiểm tra nhanh mã nguồn

```bash
python -m py_compile run_audit.py backend/server.py backend/audit.py db/repositories.py
python -m backend.server --help
python run_audit.py --help
```

## Giấy phép

[MIT](./LICENSE)
