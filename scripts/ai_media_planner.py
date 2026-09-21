"""
Task 2 prototype — Bước 1-4 of the AI automation workflow.

1. Nhận vào — reuse Task 1's fact_daily / fact_weekly_cohort / mart_campaign_summary.
2. Xử lý    — rule-based signals only, no LLM (rolling CAC trend, z-score
              anomaly, CAC-vs-target, CTR trend, LTV:CAC trend).
3. Output   — Claude call (forced tool use) turns the pre-computed signal
              JSON into action / budget_delta_pct / confidence / rationale.
4. Suggest  — same call also returns supplementary ideas, flagged as
              needing Media team sign-off.

MOCK mode (default, no ANTHROPIC_API_KEY) runs a deterministic rule-based
stand-in for free. LIVE mode calls the real API, falling back to mock
per-campaign on error.
"""

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Force UTF-8 stdout so "—"/"Δ" render in cmd.exe/PowerShell (default cp1252).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output"

MODEL = "claude-haiku-4-5-20251001"

MEDIA_PLAN_TOOL = {
    "name": "emit_media_plan",
    "description": "Emit the recommended media plan action for one campaign, based strictly on the pre-computed signals given.",
    "input_schema": {
        "type": "object",
        "properties": {
            "campaign_id": {"type": "string"},
            "action": {"type": "string", "enum": ["Scale", "Maintain", "Optimize", "Pause"]},
            "budget_delta_pct": {
                "type": "number",
                "description": "Suggested % change to daily budget. Negative = decrease, positive = increase.",
            },
            "confidence": {"type": "string", "enum": ["Low", "Medium", "High"]},
            "rationale": {
                "type": "string",
                "description": "1-3 sentences citing the specific numeric signal values given (no invented numbers).",
            },
            "additional_suggestions": {
                "type": "array",
                "description": "Supplementary ideas for the Media team to validate — not final decisions.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["creative_fatigue", "audience_overlap", "ab_test", "other"]},
                        "detail": {"type": "string"},
                    },
                    "required": ["type", "detail"],
                },
            },
        },
        "required": ["campaign_id", "action", "budget_delta_pct", "confidence", "rationale", "additional_suggestions"],
    },
}

SYSTEM_PROMPT = (
    "Bạn là chuyên gia phân tích hiệu quả media cho Momo. Bạn CHỈ được dùng đúng "
    "các con số đã cho trong tín hiệu (KHÔNG bịa số mới, KHÔNG tự tính lại CAC/LTV). "
    "Nếu ltv_data_status khác 'Mature Available', TUYỆT ĐỐI KHÔNG đề xuất 'Scale' vì "
    "chưa đủ bằng chứng LTV — dù CAC có tốt cỡ nào. Trích dẫn số liệu cụ thể trong "
    "rationale. Ở additional_suggestions, chỉ nêu gợi ý bổ sung (creative fatigue nếu "
    "ctr_trend='worsening', audience overlap, đề xuất A/B test) — đây là gợi ý cần "
    "chuyên gia Media duyệt, không phải quyết định cuối cùng."
)


# ---------------- Bước 1: Nhận vào ----------------

def load_inputs():
    fact_daily = pd.read_csv(OUT_DIR / "fact_daily.csv", parse_dates=["date"])
    fact_weekly_cohort = pd.read_csv(OUT_DIR / "fact_weekly_cohort.csv", parse_dates=["cohort_week"])
    mart = pd.read_csv(OUT_DIR / "mart_campaign_summary.csv")
    return fact_daily, fact_weekly_cohort, mart


# ---------------- Bước 2: rule-based signals (no LLM) ----------------

def _trend(series: pd.Series, worsen_is_increase: bool, threshold_pct: float = 5.0):
    """Split a per-campaign time series in half and compare means, in real
    calendar order. Returns (direction, pct_change) or ("insufficient_data", None)."""
    series = series.dropna()
    if len(series) < 4:
        return "insufficient_data", None
    mid = len(series) // 2
    first_half, second_half = series.iloc[:mid].mean(), series.iloc[mid:].mean()
    if first_half == 0:
        return "insufficient_data", None
    pct_change = (second_half - first_half) / first_half * 100
    increased = pct_change > threshold_pct
    decreased = pct_change < -threshold_pct
    if worsen_is_increase:
        direction = "worsening" if increased else "improving" if decreased else "stable"
    else:
        direction = "improving" if increased else "worsening" if decreased else "stable"
    return direction, round(pct_change, 1)


def compute_signals(fact_daily: pd.DataFrame, fact_weekly_cohort: pd.DataFrame, mart: pd.DataFrame) -> list[dict]:
    signals = []
    for campaign_id, g in fact_daily.groupby("campaign_id"):
        g = g.sort_values("date")
        daily_cac = g["daily_cac"].dropna()

        # z-score of recent readings vs this campaign's own baseline
        # (recent window excluded from the baseline itself).
        if len(daily_cac) > 5:
            baseline, recent = daily_cac.iloc[:-3], daily_cac.iloc[-3:]
        else:
            baseline, recent = daily_cac, daily_cac
        baseline_std = baseline.std(ddof=0)
        z = float((recent.mean() - baseline.mean()) / baseline_std) if baseline_std else 0.0

        cac_trend, cac_pct_change = _trend(g["rolling_7d_cac"], worsen_is_increase=True)
        ctr_trend, ctr_pct_change = _trend(g["ctr"], worsen_is_increase=False, threshold_pct=10.0)

        cohort_g = fact_weekly_cohort[
            (fact_weekly_cohort.campaign_id == campaign_id) & (fact_weekly_cohort.matured_d7)
        ].sort_values("cohort_week")
        ltv_cac_series = cohort_g["ltv_cac_ratio"].dropna()
        if len(ltv_cac_series) >= 2:
            delta = ltv_cac_series.iloc[-1] - ltv_cac_series.iloc[0]
            ltv_cac_trend = "improving" if delta > 0.1 else "worsening" if delta < -0.1 else "stable"
        else:
            ltv_cac_trend = "insufficient_data"

        m = mart[mart.campaign_id == campaign_id].iloc[0]

        signals.append({
            "campaign_id": campaign_id,
            "campaign_name": m["campaign_name"],
            "platform": m["platform"],
            "blended_cac": round(float(m["blended_cac"])),
            "target_cac_vnd": int(m["target_cac_vnd"]),
            "cac_vs_target_pct": round(float(m["cac_vs_target_pct"]), 1),
            "cac_zscore_recent_vs_baseline": round(z, 2),
            "rolling_cac_trend": cac_trend,
            "rolling_cac_pct_change": cac_pct_change,
            "ctr_trend": ctr_trend,
            "ctr_pct_change": ctr_pct_change,
            "ltv_data_status": m["ltv_data_status"],
            "mature_ltv_cac": None if pd.isna(m["mature_ltv_cac"]) else round(float(m["mature_ltv_cac"]), 2),
            "ltv_cac_trend": ltv_cac_trend,
            "retention_d7": None if pd.isna(m["retention_d7"]) else round(float(m["retention_d7"]), 1),
            "health_score": round(float(m["health_score"]), 1),
        })
    return signals


# ---------------- Bước 3+4: LLM call (real or mock) ----------------

def call_claude_real(signal: dict) -> dict:
    import anthropic  # imported lazily so MOCK mode never requires the package

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[MEDIA_PLAN_TOOL],
        tool_choice={"type": "tool", "name": "emit_media_plan"},
        messages=[{"role": "user", "content": json.dumps(signal, ensure_ascii=False)}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Claude response had no tool_use block")


def call_claude_mock(signal: dict) -> dict:
    """Deterministic stand-in for the LLM call — applies the same governance
    rule as SYSTEM_PROMPT so mock and live output are comparable."""
    ltv_ok = signal["ltv_data_status"] == "Mature Available"
    over_target = signal["cac_vs_target_pct"]

    if not ltv_ok:
        action = "Pause" if over_target > 50 else "Optimize"
        budget_delta = -20 if action == "Pause" else -5
        confidence = "Low"
        rationale = (
            f"CAC hiện tại {signal['blended_cac']:,}đ ({over_target:+.1f}% so target {signal['target_cac_vnd']:,}đ), "
            f"nhưng ltv_data_status='{signal['ltv_data_status']}' nên chưa đủ bằng chứng LTV để Scale. "
            f"Đề xuất {action}, theo dõi thêm cho tới khi có dữ liệu LTV chín."
        )
    else:
        hs = signal["health_score"]
        if hs >= 75 and signal["cac_zscore_recent_vs_baseline"] <= 0.5:
            action, budget_delta, confidence = "Scale", 15, "High"
        elif hs >= 55:
            action, budget_delta, confidence = "Maintain", 0, "Medium"
        elif hs >= 35:
            action, budget_delta, confidence = "Optimize", -10, "Medium"
        else:
            action, budget_delta, confidence = "Pause", -30, "High"
        rationale = (
            f"Health score {hs}, CAC {over_target:+.1f}% so target, mature LTV:CAC={signal['mature_ltv_cac']}, "
            f"rolling CAC đang {signal['rolling_cac_trend']} ({signal['rolling_cac_pct_change']}% qua giai đoạn quan sát). "
            f"Đề xuất {action}."
        )

    suggestions = []
    if signal["ctr_trend"] == "worsening":
        suggestions.append({
            "type": "creative_fatigue",
            "detail": f"CTR giảm {signal['ctr_pct_change']}% qua giai đoạn quan sát — dấu hiệu creative fatigue, đề xuất refresh creative.",
        })
    if abs(signal["cac_zscore_recent_vs_baseline"]) >= 2:
        suggestions.append({
            "type": "other",
            "detail": (
                f"CAC gần đây lệch {signal['cac_zscore_recent_vs_baseline']:.1f} độ lệch chuẩn so với baseline riêng "
                f"của campaign — đáng điều tra nguyên nhân (audience overlap, thay đổi bid, mùa vụ...)."
            ),
        })

    return {
        "campaign_id": signal["campaign_id"],
        "action": action,
        "budget_delta_pct": budget_delta,
        "confidence": confidence,
        "rationale": rationale,
        "additional_suggestions": suggestions,
    }


def get_media_plan(signal: dict, use_mock: bool) -> dict:
    if use_mock:
        plan = call_claude_mock(signal)
        plan["_source"] = "MOCK"
        return plan
    try:
        plan = call_claude_real(signal)
        plan["_source"] = "LLM"
        return plan
    except Exception as e:  # noqa: BLE001 - one bad call shouldn't kill the batch
        print(f"  [!] Live API call failed for {signal['campaign_id']} ({e}); falling back to mock.", file=sys.stderr)
        plan = call_claude_mock(signal)
        plan["_source"] = "MOCK (fallback)"
        return plan


def main():
    fact_daily, fact_weekly_cohort, mart = load_inputs()
    signals = compute_signals(fact_daily, fact_weekly_cohort, mart)

    use_mock = not bool(os.environ.get("ANTHROPIC_API_KEY"))
    mode_label = "MOCK — no ANTHROPIC_API_KEY found (free, deterministic stand-in)" if use_mock else "LIVE — calling Claude API"
    print(f"=== AI Media Planner — {mode_label} ===\n")

    plans = [get_media_plan(s, use_mock) for s in signals]

    summary = pd.DataFrame(plans)[["campaign_id", "action", "budget_delta_pct", "confidence", "_source"]]
    print(summary.to_string(index=False))
    print()

    for p in plans:
        print(f"--- {p['campaign_id']} — {p['action']} ({p['confidence']} confidence, Δbudget {p['budget_delta_pct']:+.0f}%) ---")
        print(f"Rationale: {p['rationale']}")
        for s in p.get("additional_suggestions", []):
            print(f"  [Gợi ý — cần Media duyệt] ({s['type']}): {s['detail']}")
        print()

    out_path = OUT_DIR / "ai_media_plan.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(plans, f, ensure_ascii=False, indent=2)
    print(f"Đã lưu media plan đầy đủ vào {out_path}")


if __name__ == "__main__":
    main()
