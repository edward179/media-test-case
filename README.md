# Momo Media — Data Analytics Engineer Assessment

Bài assessment cho vai trò **Data Analytics Engineer** hỗ trợ Momo Media, gồm 2 phần: (1) xây data mart + dashboard từ dữ liệu chiến dịch quảng cáo, và (2) prototype tự động hóa đề xuất media plan bằng AI.

- 🔗 **Live dashboard (Task 1)**: https://datastudio.google.com/reporting/3269670d-ed80-401d-9c98-f14f58837104

---

## 📊 Task 1 — Data Mart & Looker Studio Dashboard

Pipeline Python (pandas) transform 4 file CSV thô (`Momo Test Data/`) thành 4 bảng data mart sạch, đổ lên Google Sheets, rồi build dashboard trên Looker Studio.

```mermaid
flowchart LR
    subgraph EX["1. EXTRACT"]
        A1["momo_campaign_meta.csv<br/>(14 campaigns)"]
        A2["momo_media_spend.csv<br/>(261 rows, daily)"]
        A3["momo_user_acquisition.csv<br/>(261 rows, daily)"]
        A4["momo_user_ltv.csv<br/>(99 rows, weekly cohort)"]
    end

    subgraph ET["2. TRANSFORM — Python/pandas (build_data_mart.py)"]
        B1["Join spend + acquisition<br/>theo date + campaign_id"]
        B2["Cộng dồn spend & install theo tuần<br/>(nhóm 7 ngày đúng mốc cohort_week của LTV)"]
        B3["Tính CAC, CTR, rolling 7-day CAC,<br/>cohort maturity, LTV:CAC"]
        B4["Composite health_score →<br/>Scale / Maintain / Optimize / Pause"]
    end

    subgraph LD["3. LOAD"]
        C1[("Google Sheets<br/>4 tabs")]
    end

    subgraph VZ["4. VISUALIZE — Looker Studio"]
        D1["Executive<br/>Summary"]
        D2["Platform<br/>Performance"]
        D3["Campaign<br/>Ranking"]
        D4["Budget<br/>Allocation"]
    end

    A1 --> B1
    A2 --> B1
    A3 --> B1
    A4 --> B2
    B1 --> B2
    B2 --> B3
    B3 --> B4
    B4 --> C1
    C1 --> D1
    C1 --> D2
    C1 --> D3
    C1 --> D4
```

**Điểm khác biệt hóa chính**:
- **Granularity mismatch**: `momo_user_ltv.csv` ở grain **tuần** (cohort), còn spend/acquisition ở grain **ngày** — xử lý bằng cách gộp daily data thành bucket 7-ngày đúng mốc `cohort_week` trước khi join, thay vì blend thẳng 4 CSV (dễ join sai/mất data LTV).
- **Cohort immaturity guardrail**: data chỉ trải dài ~63 ngày nên không cohort nào đủ tuổi có D90 thật. `best_ltv_window` chỉ lấy window đã thực sự "chín" (COALESCE D90→D30→D7→D1), và `recommended_action` không bao giờ đề xuất *Scale* nếu thiếu bằng chứng LTV chín — tránh quyết định dựa trên số liệu rỗng/giả.
- **Time-based rolling window**: dữ liệu "daily" thực chất lấy mẫu thưa và giãn dần (1→9 ngày/lần đo), nên `rolling_7d_cac` dùng rolling theo **7 ngày lịch thực** thay vì 7 dòng dữ liệu.

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

```mermaid
flowchart LR
    subgraph SRC["Nguồn dữ liệu thật — đề xuất, chưa nối"]
        S0["Meta / Google / TikTok<br/>Ads API"]
    end

    subgraph RULE["RULE-BASED — Python/pandas, KHÔNG dùng LLM"]
        B1["Bước 1 — Nhận vào<br/>fact_daily.csv +<br/>mart_campaign_summary.csv"]
        B2["Bước 2 — Xử lý tín hiệu<br/>rolling CAC trend · z-score anomaly<br/>CAC vs target · LTV:CAC trend<br/>CTR decay"]
    end

    subgraph LLM["LLM — Claude API, 1 lần gọi, structured output"]
        B34["Bước 3: action / budget_delta_% /<br/>confidence / rationale<br/>Bước 4: additional_suggestions<br/>(cần Media duyệt)"]
    end

    subgraph GOV["GOVERNANCE — đề xuất kiến trúc, CHƯA code"]
        B6a["Bước 6 — Human approval gate<br/>(vd: Slack approval message)"]
        B6b["Audit log<br/>ghi ai duyệt/từ chối"]
    end

    S0 -. "Bước 5 (đề xuất):<br/>Cloud Function + Scheduler + dbt" .-> B1
    B1 --> B2
    B2 --> B34
    B34 -. "media plan JSON" .-> B6a
    B6a -. "luôn ghi lại" .-> B6b
    B6a -. "nếu duyệt → mutate budget" .-> S0
```

*Khung viền liền (RULE-BASED, LLM) = đã code, chạy thật trên data thật. Khung nét đứt (SRC, GOV) = đề xuất kiến trúc, cố ý chưa code trong phạm vi thời gian assessment — cần hạ tầng/credentials thật (Slack, Ads API) và governance đúng cách trước khi mutate ngân sách thật.*

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
