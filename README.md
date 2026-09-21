# lending-campaign-agent

AI Agent phân tích hàng ngày các chiến dịch Marketing của sản phẩm Lending, chạy trên GreenNode AgentBase. Agent đọc dữ liệu, tự tính chỉ số, phát hiện điểm bất thường, nhờ LLM diễn giải bằng ngôn ngữ kinh doanh và dựng Dashboard cho CEO/Marketing/Sale.

**Nguyên tắc cốt lõi**
- **Python tính toán, LLM chỉ diễn giải.** Mọi con số đều do Python tính; LLM không được tự bịa số.
- **Agent chỉ phân tích và đề xuất.** Con người quyết định và thực hiện hành động.
- **Hai sản phẩm phân tích độc lập:** First Loan (khách mới) và Re-loan (khách cũ) là hai tab riêng, mỗi tab một lần gọi LLM riêng.

## Agent làm gì mỗi ngày

```
Google Sheet / file Excel  →  tính chỉ số  →  so với 7 ngày gần nhất  →  LLM diễn giải  →  Dashboard (+ email)
```

- Hành trình theo dõi: Tiếp cận (lead) → Đăng ký → Duyệt đơn → Giải ngân → Tất toán. First Loan có thêm Cài app → Xác thực SĐT trước khi đăng ký.
- Chỉ số chính: số đơn và doanh số giải ngân, tỷ lệ duyệt, ticket size bình quân, CPL, CAC, đóng góp của từng kênh vào phần tăng/giảm so với trung bình 7 ngày.
- Job tự chạy **lúc 8h sáng giờ Việt Nam** (đổi bằng `DAILY_CUTOFF_HOUR`). Có thể chạy tay bằng `POST /api/refresh`.

## Cấu trúc dự án

```
main.py                     Cổng vào (FastAPI): /health, /, /api/refresh
app/
  data_source.py            Đọc dữ liệu (Google Sheet hoặc Excel), tổng hợp thành bảng ngày x kênh
  metrics.py                Tính chỉ số, mức bình thường, điểm bất thường, đóng góp theo kênh
  llm_insight.py            Prompt và gọi LLM (chỉ diễn giải số đã tính sẵn)
  dashboard.py              Chuẩn bị dữ liệu và dựng Dashboard
  templates/dashboard.html  Giao diện Dashboard (Chart.js)
  pipeline.py               Điều phối toàn bộ luồng chạy hàng ngày
  scheduler.py              Lịch chạy tự động
  notify.py                 Gửi email tóm tắt (tuỳ chọn)
scripts/                    Công cụ tạo/chỉnh dữ liệu mô phỏng (xem bên dưới)
data/
  full_schema_mock.xlsx     Dữ liệu mô phỏng mẫu (8 tab)
  SCHEMA.md                 Mô tả chi tiết cấu trúc dữ liệu
Dockerfile, requirements.txt, .env.example
```

## Chạy trên máy cá nhân

Yêu cầu: Python 3.12.

```bash
python -m venv venv
```

```bash
venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

Tạo file `.env` từ mẫu rồi điền giá trị thật (tối thiểu `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`):

```bash
copy .env.example .env
```

```bash
python main.py
```

Mở http://localhost:8080. Lần đầu chưa có Dashboard lưu sẵn nên agent tự chạy toàn bộ luồng, mất khoảng 2 phút (chủ yếu là thời gian gọi LLM). Kiểm tra nhanh: `GET /health` trả về `{"status": "healthy"}`.

| Đường dẫn | Việc |
|---|---|
| `GET /health` | Kiểm tra agent còn sống |
| `GET /` | Xem Dashboard mới nhất |
| `POST /api/refresh` | Chạy lại toàn bộ luồng (đọc dữ liệu → LLM → Dashboard → email) |

## Biến môi trường

Sao chép `.env.example` thành `.env`. **Không bao giờ đưa `.env` lên Git.** Biến tuỳ chọn không dùng thì bỏ hẳn dòng, đừng để giá trị trống.

| Biến | Bắt buộc | Ý nghĩa |
|---|---|---|
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | Có (để có insight) | LLM tương thích OpenAI. Thiếu thì Dashboard vẫn chạy nhưng không có phần diễn giải |
| `GOOGLE_SHEET_ID` | Không | ID hoặc link Google Sheet. Để trống thì đọc file Excel cục bộ |
| `DATA_SOURCE_PATH` | Không | File Excel cục bộ (mặc định `data/full_schema_mock.xlsx`) |
| `DAILY_CUTOFF_HOUR` | Không | Giờ chạy tự động, giờ Việt Nam (mặc định 8) |
| `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`, `EMAIL_RECIPIENTS` | Không | Gửi email tóm tắt qua Gmail (cần đủ cả 3 biến) |
| `DASHBOARD_BASE_URL` | Không | Địa chỉ Dashboard dùng trong email |
| `GREENNODE_*` | Chỉ khi chạy local | Trên AgentBase Runtime nền tảng tự cấp, **không** đặt khi deploy |

## Nguồn dữ liệu: Google Sheets

Mỗi lần chạy, agent tải bản `.xlsx` của cả Google Sheet rồi tính lại Dashboard.

1. Tải `data/full_schema_mock.xlsx` lên Google Drive, mở bằng Google Sheets và chọn Tệp → **Lưu dưới dạng Google Trang tính**.
2. Giữ nguyên tên 8 tab và tên cột (xem `data/SCHEMA.md`).
3. Chia sẻ → Quyền truy cập chung → **Bất kỳ ai có đường liên kết** → **Người xem**.
4. Đặt `GOOGLE_SHEET_ID` trong `.env` (và trong biến môi trường của Runtime khi deploy).

Nếu Google không tải được (Sheet chưa chia sẻ, ID sai, mất mạng), agent báo lỗi rõ ràng và **không** âm thầm dùng dữ liệu cũ. Cách này chỉ dùng với dữ liệu mô phỏng vì ai có link đều xem được; dữ liệu khách hàng thật cần kho dữ liệu nội bộ được ngân hàng phê duyệt.

## Đóng gói và deploy lên AgentBase Runtime

```bash
docker build --platform linux/amd64 -t <registry>/<ten-anh>:<tag> .
```

Các điểm cần nhớ khi tạo Runtime:
- Container lắng nghe cổng **8080**, kiểm tra sức khoẻ ở `/health`.
- **Chỉ chạy 1 bản** (min = max = 1 replica) và không cho ngủ: lịch chạy nằm trong tiến trình, không có khóa phân tán nên chạy 2 bản sẽ chạy trùng job và gửi trùng email.
- Chế độ mạng **PUBLIC**: agent cần ra internet để tải Google Sheet và gọi LLM.
- Truyền biến môi trường bằng file env khi tạo Runtime (không có `.env` trong ảnh Docker). Đừng đưa các biến `GREENNODE_*` vào file này.
- Ổ đĩa container không bền: sau mỗi lần deploy hoặc khởi động lại, Dashboard lưu sẵn mất. Nên gọi `POST /api/refresh` một lần sau khi deploy.
- Hiện `/api/refresh` và Dashboard chưa có đăng nhập riêng trong code; cần bảo vệ ở tầng endpoint của nền tảng.

Chi tiết các bước xem Console AgentBase: https://aiplatform.console.vngcloud.vn/agent-runtime?tab=runtime

## Lưu ý bảo mật

- Không commit `.env`, `.greennode.json`, `iam-credentials.json` hay bất kỳ khóa nào. Các file này đã nằm trong `.gitignore`.
- Không đưa dữ liệu khách hàng thật lên GitHub hay Google Sheet công khai.
- Nếu lỡ commit một khóa, phải tạo khóa mới và thu hồi khóa cũ; xóa file khỏi lịch sử là chưa đủ.
