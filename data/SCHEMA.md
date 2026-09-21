# Cấu trúc dữ liệu đầu vào

Agent đọc **một file Excel hoặc một Google Sheet gồm 8 tab**. Tên tab và tên cột phải đúng như dưới đây (thứ tự cột không quan trọng). Dữ liệu trong repo (`data/full_schema_mock.xlsx`) là **dữ liệu mô phỏng**, không phải dữ liệu khách hàng thật.

Mọi cột ngày giờ dùng dạng `YYYY-MM-DD HH:MM:SS` và phải là kiểu ngày giờ của Excel/Sheets, không phải chữ.

## Hành trình khách hàng và các bảng

```
Tương tác quảng cáo → Cài app → Nhập SĐT → Xác thực SĐT → Đăng ký vay → Duyệt → Giải ngân → Tất toán
   fact_lead        fact_app_install (chỉ First Loan)   fact_loan      fact_reject   fact_loan
```

- **First Loan** (khách mới): đi qua đủ các bước, kể cả cài app và xác thực SĐT.
- **Re-loan** (khách cũ, kênh Zalo Ads): đã có app và tài khoản nên **không** có dòng nào trong `fact_app_install`.
- Thứ tự thời gian của một khách First Loan luôn là: lead → cài app → nhập SĐT → xác thực SĐT → đăng ký vay → (duyệt) → giải ngân.
- Đơn được duyệt gần như chắc chắn được giải ngân (trong dữ liệu mô phỏng: tối đa 2 giờ sau đăng ký, cùng ngày), nên Dashboard không theo dõi tỷ lệ Duyệt → Giải ngân.

## 8 tab

| Tab | Mỗi dòng là | Cột |
|---|---|---|
| `fact_lead` | 1 lead: mã phát sinh khi khách tương tác với quảng cáo, **trước** khi cài app. Một khách có thể có nhiều lead | `lead_id`, `Customer_id`, `partner_code`, `channel`, `sub_channel`, `create_at`, `product_id`, `campaign_id`, `campaign_name`, `utm_source`, `utm_medium` |
| `fact_app_install` | 1 lượt cài app từ 1 lead (First Loan) | `install_id`, `install_time`, `channel`, `phone_captured_time`, `phone_verified_time`, `lead_id` |
| `fact_loan` | 1 hồ sơ vay | `application_id`, `Customer_id`, `product_id`, `product_name`, `create_at`, `disbursement_date`, `settlement_date`, `partner_code`, `channel`, `sub_channel`, `tenure`, `no_paid`, `last_duedate`, `last_dayslate`, `max_dayslate`, `nominal_interest_rate`, `loan_amount`, `loan_number_rank`, `loan_balance`, `reloan_cadence_days` |
| `fact_reject` | 1 hồ sơ bị từ chối | `loan_application_id`, `reason_level_1`, `reason_level_2` |
| `dim_customer` | 1 khách hàng | `customer_id`, `Age`, `Customer_open_date`, `occupation`, `active_status`, `has_app`, `income` |
| `loan_application_pnl` | Thu nhập và chi phí của 1 hồ sơ vay | `loan_application_id`, `interest_income`, `overdue_interest`, `early_paid_off_fee`, `loan_processing_cost`, `funding_cost`, `lead_cost`, `credit_loss`, `operation_cost`, `marketing_cost`, `Processing_Fee`, `Partner_Fee`, `Collection_Cost` |
| `fact_digital_footprint` | Dấu vết thiết bị khi điền hồ sơ | `loan_application_id`, `device_os`, `device_price_segment`, `form_filling_time`, `ip_address`, `geo_location_match` |
| `fact_lead_cost` | Chi phí lead của 1 chiến dịch trong 1 ngày | `cost_date`, `channel`, `campaign_id`, `campaign_name`, `lead_cost` |

## Các liên kết giữa bảng

- `fact_app_install.lead_id` → `fact_lead.lead_id`. Một lead có tối đa một lượt cài app.
- `fact_loan` **không có** `lead_id`, vì một khách có thể tương tác quảng cáo nhiều lần trước khi vay. Khoản vay nối với lead qua `Customer_id`: khi khách xác thực SĐT và tạo khoản vay, `Customer_id` được bổ sung vào lead của khách đó trong `fact_lead`. Lead chưa chuyển đổi để trống `Customer_id`.
- `fact_reject`, `loan_application_pnl`, `fact_digital_footprint` nối với `fact_loan` qua `loan_application_id` = `application_id`.
- `fact_loan.Customer_id` → `dim_customer.customer_id`.

## Cách Dashboard tính các chỉ số chính

- **Kênh** là mức chi tiết nhỏ nhất xuyên suốt hành trình (`channel`); sau bước đăng ký dữ liệu không có `campaign_name`.
- **CPL** = tổng `fact_lead_cost.lead_cost` của ngày/kênh chia số lead cùng ngày/kênh.
- **CAC** = tổng (`lead_cost` + `marketing_cost` trong `loan_application_pnl`) của các hồ sơ **đã giải ngân**, chia số hồ sơ đã giải ngân.
- **Ticket size bình quân** = tổng `loan_amount` giải ngân chia số khoản giải ngân.
- **Mức bình thường** để so sánh = trung bình 7 ngày gần nhất (không tính hôm nay) của chính kênh đó.
- Số lượt cài app và xác thực SĐT tính theo **ngày cài**, nên có thể đến từ lead của những ngày trước.

## Định dạng cũ (`funnel_daily`)

Agent còn đọc được file chỉ có 1 tab `funnel_daily`, mỗi dòng là số liệu tổng hợp 1 kênh trong 1 ngày, với các cột bắt buộc: `date`, `campaign_name`, `channel`, `ad_spend`, `reach_count`, `registered_count`, `approved_count`, `rejected_count`, `top_reject_reason`, `avg_processing_time_hours`, `disbursed_count`, `disbursed_amount`, `settled_count`, `early_settled_count`, `outstanding_balance`, `interest_income` (cột `product` là tuỳ chọn). Mỗi kênh cần ít nhất 4 ngày liên tục (lý tưởng từ 8 ngày trở lên) để tính được mức bình thường.
