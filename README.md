# Momo Media — Data Analytics Engineer Assessment

Bài assessment cho vai trò **Data Analytics Engineer** hỗ trợ Momo Media, gồm 2 phần: (1) xây data mart + dashboard từ dữ liệu chiến dịch quảng cáo, và (2) prototype tự động hóa đề xuất media plan bằng AI.

- 🔗 **Live dashboard (Task 1)**: https://datastudio.google.com/reporting/3269670d-ed80-401d-9c98-f14f58837104

---

## 📊 Task 1 — Data Mart & Looker Studio Dashboard

Pipeline Python (pandas) transform 4 file CSV thô (`Momo Test Data/`) thành 4 bảng data mart sạch, đổ lên Google Sheets, rồi build dashboard trên Looker Studio.

![Task 1 — Data Architecture (Extract → Transform → Load → Visualize)](task1-data-architecture.png)

**Điểm khác biệt hóa chính**:
- **Cấp độ dữ liệu không khớp nhau**: LTV tính theo **tuần** (cohort), spend/install tính theo **ngày** → phải cộng dồn spend/install thành đúng từng tuần trước khi nối 2 bảng, nếu nối thẳng luôn (blend) sẽ bị sai lệch hoặc mất data LTV.
- **Không dùng số LTV còn "non"**: data chỉ dài 63 ngày nên chưa cohort nào đủ 90 ngày tuổi thật (D90 chỉ là số ngoại suy) → chỉ lấy mốc D90/D30/D7 mà cohort đã thật sự đủ tuổi, và không bao giờ đề xuất tăng ngân sách (Scale) nếu thiếu bằng chứng LTV "chín" (đủ tuổi để số liệu là thật).
- **Tính CAC trung bình theo đúng lịch**: khoảng cách giữa các lần đo dữ liệu không đều (1–9 ngày/lần) → tính rolling 7 ngày theo **lịch thật**, không theo số dòng dữ liệu, để tránh sai lệch nặng.

### Output
| File | Nội dung |
|---|---|
| `output/dim_campaign.csv` | Dimension — 14 campaign, platform, ngân sách, target CAC |
| `output/fact_daily.csv` | Fact theo ngày × campaign — spend, install, CAC, CTR, rolling 7d CAC |
| `output/fact_weekly_cohort.csv` | Fact theo tuần × campaign — nối đúng grain với LTV cohort |
| `output/mart_campaign_summary.csv` | 1 dòng/campaign — health_score, recommended_action, dùng cho toàn dashboard |

### Công thức các Metric trên Dashboard

| Card / Chart | Công thức | Ý nghĩa |
|---|---|---|
| Total Spend | `SUM(total_spend)` | Tổng ngân sách đã chi của toàn bộ campaign |
| Blended CAC | `SUM(total_spend) / SUM(total_installs)` | Chi phí trung bình để có 1 lượt cài app — tính gộp trên tổng chi phí và tổng install, không phải trung bình cộng CAC của từng campaign (cộng dồn kiểu đó sẽ sai số) |
| Avg Mature LTV:CAC | `AVG(mature_ltv_cac)`, chỉ lấy campaign có `ltv_data_status = Mature Available` | Trung bình tỷ lệ giá trị thu về / chi phí, chỉ tính trên campaign đã có số LTV thật (đã "chín") |
| # Scale / # Pause | `COUNT_DISTINCT(campaign_id)`, filter `recommended_action = Scale` / `Pause` | Số campaign đang được đề xuất tăng ngân sách / nên dừng lại |
| Spend – Prior 7 Days | `SUM(daily_spend_vnd)`, filter ngày 21/07–27/07/2024 | Tổng chi tiêu 7 ngày trước đó (kỳ để so sánh) |
| Spend – Last 7 Days | `SUM(daily_spend_vnd)`, filter ngày 28/07–03/08/2024 | Tổng chi tiêu 7 ngày gần nhất (kỳ hiện tại) |
| WoW % | `(Last 7 Days − Prior 7 Days) / Prior 7 Days × 100` | % thay đổi spend tuần này so với tuần trước. Lưu ý: hiện đang tính tay và gõ cố định vào text box, chưa phải calculated field tự chạy lại khi data đổi |
| Platform CAC (line chart) | `SUM(daily_spend_vnd) / SUM(installs)`, breakdown theo `platform` | CAC theo từng nền tảng, biến động theo thời gian |
| Campaign Ranking (bar + table) | `health_score`, sắp xếp giảm dần | Điểm tổng hợp sức khỏe campaign: 40% hiệu quả CAC + 35% LTV:CAC + 25% retention D7 |
| Budget Allocation (pie chart) | `SUM(total_spend)`, dimension `platform` | Ngân sách đã chi được phân bổ ra sao giữa các kênh |

Tất cả metric trên dùng nguồn `mart_campaign_summary` hoặc `fact_daily` (đều là output của `build_data_mart.py`) — không có metric nào tính trực tiếp từ 4 file CSV gốc.

---

## 🤖 Task 2 — AI-Powered Media Planning Automation

Prototype đọc trực tiếp output của Task 1 (`fact_daily`, `mart_campaign_summary`), tính tín hiệu hiệu suất bằng rule-based logic, rồi gọi Claude API (structured output) để sinh đề xuất media plan.

![Task 2 — AI Automation Workflow (rule-based signals → AI đề xuất → người duyệt → mutate/dừng)](task2-ai-workflow.svg)

*Vùng viền xanh lá (Rule-based + AI) = đã code, chạy thật trên data thật. Các vùng còn lại (nguồn dữ liệu, người duyệt, kết quả sau duyệt) = mới là đề xuất kiến trúc, cố ý chưa code trong phạm vi thời gian assessment — cần hạ tầng/credentials thật (Slack/Email, Ads API) và cơ chế duyệt đúng cách trước khi tự động chỉnh ngân sách thật. Nếu bị từ chối (NO), hệ thống dừng hẳn và ghi log — không tự động lặp lại vì data đầu vào chưa đổi.*

### Chạy thử
```bash
pip install pandas numpy anthropic

# Mock mode (mặc định, không cần API key) — deterministic rule-based stand-in cho phần LLM
python scripts/ai_media_planner.py

# Live mode — gọi Claude API thật
export ANTHROPIC_API_KEY=xxx
python scripts/ai_media_planner.py
```
Output: `output/ai_media_plan.json`.

---

## 🗂️ Cấu trúc thư mục

```
.
├── Momo Test Data/            # 4 file CSV gốc đề bài cung cấp
│   ├── momo_campaign_meta.csv
│   ├── momo_media_spend.csv
│   ├── momo_user_acquisition.csv
│   └── momo_user_ltv.csv
├── scripts/
│   ├── build_data_mart.py     # Task 1 — ETL: 4 CSV thô → 4 data mart
│   └── ai_media_planner.py    # Task 2 — rule-based signals + Claude API
├── output/                    # Data mart + media plan output (generated)
└── README.md
```

## ⚙️ Cách chạy toàn bộ pipeline

```bash
pip install pandas numpy anthropic

# Task 1 — build data mart
python scripts/build_data_mart.py
# → output/dim_campaign.csv, fact_daily.csv, fact_weekly_cohort.csv, mart_campaign_summary.csv
# (upload thủ công 4 file này lên Google Sheets làm data source cho Looker Studio)

# Task 2 — AI media plan
python scripts/ai_media_planner.py
# → output/ai_media_plan.json
```
