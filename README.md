# Momo Media — Data Analytics Engineer Assessment

Bài assessment cho vai trò **Data Analytics Engineer** hỗ trợ Momo Media, gồm 2 phần: (1) xây data mart + dashboard từ dữ liệu chiến dịch quảng cáo, và (2) prototype tự động hóa đề xuất media plan bằng AI.

- 🔗 **Live dashboard (Task 1)**: https://datastudio.google.com/reporting/3269670d-ed80-401d-9c98-f14f58837104

---

## 📊 Task 1 — Data Mart & Looker Studio Dashboard

Pipeline Python (pandas) transform 4 file CSV thô (`Momo Test Data/`) thành 4 bảng data mart sạch, đổ lên Google Sheets, rồi build dashboard trên Looker Studio.

![Task 1 — Data Architecture (Extract → Transform → Load → Visualize)](task1-data-architecture.png)

**Điểm khác biệt hóa chính**:

**1. Hai file dữ liệu không cùng "cấp độ chi tiết" (granularity mismatch)**
`momo_user_ltv.csv` mỗi dòng là 1 **cohort** — 1 nhóm user cùng cài app trong **1 tuần** cụ thể, được theo dõi chung với nhau (gọi là dữ liệu cấp **tuần**). Trong khi đó `momo_media_spend.csv`/`momo_user_acquisition.csv` mỗi dòng là **1 ngày** (dữ liệu cấp **ngày**). Nếu **nối thẳng cả 4 file lại với nhau** (cách làm phổ biến, hay gọi là "blend data" — tức ghép nhiều nguồn khác nhau lại mà không xử lý gì trước) thì 1 dòng "tuần" bên LTV sẽ khớp sai với 7 dòng "ngày" bên kia → dữ liệu bị nhân sai hoặc bị loại bỏ hẳn. Cách xử lý: cộng dồn spend/install của đúng 7 ngày liên tiếp — khớp đúng mốc tuần (`cohort_week`) bên LTV đang dùng — để đưa cả 2 bên về cùng "cấp độ tuần" trước khi nối, thay vì ghép lệch cấp độ.

**2. Chặn quyết định dựa trên số liệu LTV còn "non"**
Data chỉ có từ 01/06 đến 03/08/2024 (~63 ngày). Cột `D90_revenue_per_user` nghĩa là "doanh thu trung bình mỗi user tính đến ngày thứ 90 sau khi cài app" — nhưng vì data chỉ dài 63 ngày, **không cohort nào đã tồn tại đủ 90 ngày thật**, nên số ở cột này chỉ là ngoại suy, không phải số thật. Em gọi 1 cohort là "**chín**" khi nó đã tồn tại đủ lâu để mốc đó là số **thật** (vd cohort mới cài app 10 ngày thì D7 là số thật, còn D30/D90 chưa "chín"). Logic `COALESCE D90→D30→D7→D1` nghĩa là: luôn ưu tiên lấy mốc xa nhất **mà cohort đó đã thật sự đủ tuổi** — thử D90 trước, chưa đủ tuổi thì lùi về D30, rồi D7, cuối cùng mới D1 — không bao giờ lấy liều 1 mốc mà cohort chưa tới tuổi. Và quan trọng nhất: nếu 1 campaign chưa có bằng chứng LTV "chín" nào, hệ thống **không bao giờ** đề xuất *Scale* (tăng ngân sách) cho campaign đó dù chỉ số khác trông tốt — vì tăng tiền dựa trên số liệu còn non là rủi ro.

**3. Tính đúng "trung bình trượt 7 ngày" theo lịch thật, không theo số dòng dữ liệu**
Tưởng mỗi campaign có 1 dòng data/ngày, nhưng thực tế khoảng cách giữa các lần đo giãn dần — có lúc cách 1 ngày, có lúc cách tới 9 ngày. Nếu tính "trung bình 7 **dòng** gần nhất" (cách làm phổ biến), 7 dòng đó có lúc thực chất trải dài đến 30-40 ngày thực tế → CAC trung bình tính sai lệch nặng. Sửa: `rolling_7d_cac` tính theo đúng **7 ngày lịch thật** (1 tuần theo lịch), bất kể trong đó có bao nhiêu dòng data, để con số phản ánh đúng khoảng thời gian thực.

### Output
| File | Nội dung |
|---|---|
| `output/dim_campaign.csv` | Dimension — 14 campaign, platform, ngân sách, target CAC |
| `output/fact_daily.csv` | Fact theo ngày × campaign — spend, install, CAC, CTR, rolling 7d CAC |
| `output/fact_weekly_cohort.csv` | Fact theo tuần × campaign — nối đúng grain với LTV cohort |
| `output/mart_campaign_summary.csv` | 1 dòng/campaign — health_score, recommended_action, dùng cho toàn dashboard |

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
