"""
Task 1 data mart builder. Reads 4 raw CSVs (Momo Test Data/) -> 4 clean
outputs (output/) for Sheets -> Looker Studio.

Don't "fix" away:
- spend/acq are daily-ish, LTV is weekly cohort -> pre-aggregate into the
  same weekly buckets before joining.
- Sampling gaps widen over time (1-9 days) -> rolling_7d_cac uses a
  time-based window, not row count.
- Data spans only ~63 days -> no cohort is ever D90-mature; best LTV
  window falls back D90->D30->D7->D1 by real cohort age.
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "Momo Test Data"
OUT_DIR = ROOT / "output"
OUT_DIR.mkdir(exist_ok=True)


def load_raw():
    meta = pd.read_csv(RAW_DIR / "momo_campaign_meta.csv", parse_dates=["start_date", "end_date"])
    spend = pd.read_csv(RAW_DIR / "momo_media_spend.csv", parse_dates=["date"])
    acq = pd.read_csv(RAW_DIR / "momo_user_acquisition.csv", parse_dates=["date"])
    ltv = pd.read_csv(RAW_DIR / "momo_user_ltv.csv", parse_dates=["cohort_week"])
    return meta, spend, acq, ltv


def week_bucket(dates: pd.Series, anchor: pd.Timestamp) -> pd.Series:
    """Floor each date to the 7-day bucket that starts on `anchor` (a Saturday
    in this dataset), matching the global cohort_week grid used in user_ltv."""
    days_since_anchor = (dates - anchor).dt.days
    bucket_offset = (days_since_anchor // 7) * 7
    return anchor + pd.to_timedelta(bucket_offset, unit="D")


def build_dim_campaign(meta: pd.DataFrame, reference_date: pd.Timestamp) -> pd.DataFrame:
    dim = meta.copy()
    dim["campaign_age_days"] = (reference_date - dim["start_date"]).dt.days
    dim["is_still_active"] = (dim["status"] == "Active") & (dim["end_date"] >= reference_date)
    return dim


def build_fact_daily(spend: pd.DataFrame, acq: pd.DataFrame) -> pd.DataFrame:
    spend_cols = ["date", "campaign_id", "campaign_name", "platform", "daily_spend_vnd", "clicks", "impressions", "spend_category"]
    acq_cols = ["date", "campaign_id", "installs", "install_rate_percent", "attributed_revenue_vnd",
                "attributed_installs_D1_active", "attributed_installs_D7_active"]

    fact = pd.merge(spend[spend_cols], acq[acq_cols], on=["date", "campaign_id"], how="inner", validate="one_to_one")
    fact = fact.sort_values(["campaign_id", "date"]).reset_index(drop=True)

    fact["daily_cac"] = np.where(fact["installs"] > 0, fact["daily_spend_vnd"] / fact["installs"], np.nan)
    fact["ctr"] = np.where(fact["impressions"] > 0, fact["clicks"] / fact["impressions"], np.nan)

    fact["days_since_prev_row"] = fact.groupby("campaign_id")["date"].diff().dt.days

    # Time-based 7-day rolling CAC per campaign (robust to widening gaps above).
    def rolling_cac(g: pd.DataFrame) -> pd.Series:
        # Must keep the ORIGINAL row index, or groupby.apply misaligns and
        # silently produces all-NaN on pandas 3.x.
        orig_index = g.index
        g = g.set_index("date")
        roll_spend = g["daily_spend_vnd"].rolling("7D").sum()
        roll_installs = g["installs"].rolling("7D").sum()
        result = roll_spend / roll_installs
        result.index = orig_index
        return result

    fact["rolling_7d_cac"] = fact.groupby("campaign_id", group_keys=False).apply(rolling_cac, include_groups=False)

    return fact


def build_fact_weekly_cohort(fact_daily: pd.DataFrame, ltv: pd.DataFrame, reference_date: pd.Timestamp) -> pd.DataFrame:
    anchor = ltv["cohort_week"].min()
    fd = fact_daily.copy()
    fd["cohort_week"] = week_bucket(fd["date"], anchor)

    weekly_spend = (
        fd.groupby(["campaign_id", "cohort_week"])
        .agg(weekly_spend_vnd=("daily_spend_vnd", "sum"), weekly_installs=("installs", "sum"))
        .reset_index()
    )

    cohort = pd.merge(ltv, weekly_spend, on=["campaign_id", "cohort_week"], how="left")

    cohort["weekly_cac"] = np.where(
        cohort["weekly_installs"] > 0, cohort["weekly_spend_vnd"] / cohort["weekly_installs"], np.nan
    )

    days_old = (reference_date - cohort["cohort_week"]).dt.days
    cohort["cohort_age_days"] = days_old
    cohort["matured_d90"] = days_old >= 90
    cohort["matured_d30"] = days_old >= 30
    cohort["matured_d7"] = days_old >= 7
    cohort["matured_d1"] = days_old >= 1

    def pick_best(row):
        if row["matured_d90"]:
            return row["D90_revenue_per_user"], "D90"
        if row["matured_d30"]:
            return row["D30_revenue_per_user"], "D30"
        if row["matured_d7"]:
            return row["D7_revenue_per_user"], "D7"
        if row["matured_d1"]:
            return row["D1_revenue_per_user"], "D1"
        return np.nan, "None"

    best = cohort.apply(pick_best, axis=1, result_type="expand")
    cohort["best_ltv_per_user"] = best[0]
    cohort["best_ltv_window"] = best[1]

    cohort["cohort_maturity"] = np.select(
        [cohort["matured_d90"], cohort["matured_d30"], cohort["matured_d7"], cohort["matured_d1"]],
        ["Mature (D90)", "Mature (D30)", "Mature (D7)", "Mature (D1)"],
        default="Immature",
    )

    cohort["ltv_cac_ratio"] = np.where(
        cohort["weekly_cac"] > 0, cohort["best_ltv_per_user"] / cohort["weekly_cac"], np.nan
    )

    return cohort


def build_mart_campaign_summary(dim: pd.DataFrame, fact_daily: pd.DataFrame, cohort: pd.DataFrame) -> pd.DataFrame:
    agg = (
        fact_daily.groupby("campaign_id")
        .agg(
            total_spend=("daily_spend_vnd", "sum"),
            total_installs=("installs", "sum"),
            total_clicks=("clicks", "sum"),
            total_impressions=("impressions", "sum"),
            total_attributed_revenue=("attributed_revenue_vnd", "sum"),
        )
        .reset_index()
    )
    agg["blended_cac"] = agg["total_spend"] / agg["total_installs"]
    agg["overall_ctr"] = agg["total_clicks"] / agg["total_impressions"]

    # Only D7+ mature cohorts count toward the campaign LTV rollup.
    mature = cohort[cohort["matured_d7"]].copy()
    mature["weighted_ltv"] = mature["best_ltv_per_user"] * mature["cohort_size"]
    ltv_roll = (
        mature.groupby("campaign_id")
        .agg(
            weighted_ltv_sum=("weighted_ltv", "sum"),
            cohort_weight_sum=("cohort_size", "sum"),
            weighted_retention_d7_sum=("retention_D7_pct", lambda s: (s * mature.loc[s.index, "cohort_size"]).sum()),
            mature_cohort_count=("cohort_week", "count"),
        )
        .reset_index()
    )
    ltv_roll["mature_ltv_per_user"] = ltv_roll["weighted_ltv_sum"] / ltv_roll["cohort_weight_sum"]
    ltv_roll["retention_d7"] = ltv_roll["weighted_retention_d7_sum"] / ltv_roll["cohort_weight_sum"]

    mart = dim[["campaign_id", "campaign_name", "platform", "status", "target_cac_vnd",
                "campaign_age_days", "is_still_active"]].copy()
    mart = mart.merge(agg, on="campaign_id", how="left")
    mart = mart.merge(ltv_roll[["campaign_id", "mature_ltv_per_user", "retention_d7", "mature_cohort_count"]],
                       on="campaign_id", how="left")

    # Distinguish "never tracked in user_ltv.csv" from "tracked but not
    # mature yet" — conflating them would inflate untracked campaigns' score.
    campaigns_with_any_ltv_row = set(cohort["campaign_id"].unique())
    mart["has_ltv_tracking"] = mart["campaign_id"].isin(campaigns_with_any_ltv_row)
    mart["has_mature_ltv_data"] = mart["mature_cohort_count"].fillna(0) > 0
    mart["ltv_data_status"] = np.select(
        [~mart["has_ltv_tracking"], ~mart["has_mature_ltv_data"]],
        ["No LTV Tracking", "Immature Only"],
        default="Mature Available",
    )

    mart["cac_vs_target_pct"] = (mart["blended_cac"] - mart["target_cac_vnd"]) / mart["target_cac_vnd"] * 100
    mart["mature_ltv_cac"] = mart["mature_ltv_per_user"] / mart["blended_cac"]

    def cac_efficiency_score(row):
        # 100 = right at target, decays as blended_cac overshoots target; capped [0,100]
        ratio = row["target_cac_vnd"] / row["blended_cac"] if row["blended_cac"] > 0 else 0
        return float(np.clip(ratio * 100, 0, 130))

    def ltv_cac_score(row):
        if pd.isna(row["mature_ltv_cac"]):
            return np.nan
        # 3:1 LTV:CAC treated as "excellent" ceiling -> 100
        return float(np.clip(row["mature_ltv_cac"] / 3 * 100, 0, 130))

    def retention_score(row):
        if pd.isna(row["retention_d7"]):
            return np.nan
        # 50% D7 retention treated as "excellent" ceiling -> 100
        return float(np.clip(row["retention_d7"] / 50 * 100, 0, 130))

    mart["cac_efficiency_score"] = mart.apply(cac_efficiency_score, axis=1)
    mart["ltv_cac_score"] = mart.apply(ltv_cac_score, axis=1)
    mart["retention_score"] = mart.apply(retention_score, axis=1)

    # Weighted composite, re-weighted across available components. Tracks
    # score_components_used so a CAC-only score (missing LTV) isn't treated
    # the same as a fully-vetted one.
    weights = {"cac_efficiency_score": 0.40, "ltv_cac_score": 0.35, "retention_score": 0.25}

    def composite(row):
        total_w, total, n = 0.0, 0.0, 0
        for col, w in weights.items():
            v = row[col]
            if pd.notna(v):
                total += v * w
                total_w += w
                n += 1
        return pd.Series({
            "health_score": round(total / total_w, 1) if total_w > 0 else np.nan,
            "score_components_used": n,
        })

    mart = pd.concat([mart, mart.apply(composite, axis=1)], axis=1)

    def recommend(row):
        # Guardrail: never Scale without mature LTV data.
        if row["ltv_data_status"] != "Mature Available":
            if pd.notna(row["cac_vs_target_pct"]) and row["cac_vs_target_pct"] > 50:
                return "Pause"
            return "Optimize"
        if pd.isna(row["health_score"]):
            return "Optimize"
        if row["health_score"] >= 75:
            return "Scale"
        if row["health_score"] >= 55:
            return "Maintain"
        if row["health_score"] >= 35:
            return "Optimize"
        return "Pause"

    mart["recommended_action"] = mart.apply(recommend, axis=1)

    return mart.sort_values("health_score", ascending=False)


def main():
    meta, spend, acq, ltv = load_raw()
    reference_date = max(spend["date"].max(), acq["date"].max(), ltv["cohort_week"].max())
    print(f"Reference date (max date across all raw files): {reference_date.date()}")

    dim_campaign = build_dim_campaign(meta, reference_date)
    fact_daily = build_fact_daily(spend, acq)
    fact_weekly_cohort = build_fact_weekly_cohort(fact_daily, ltv, reference_date)
    mart_campaign_summary = build_mart_campaign_summary(dim_campaign, fact_daily, fact_weekly_cohort)

    # Round floats before writing (shorter decimals survive non-US-locale
    # Sheets imports better). CTR needs 4dp — it's a small ratio and 2dp
    # collapses it to ~3 distinct values.
    HIGH_PRECISION_COLS = {"ctr", "overall_ctr"}
    for df in (dim_campaign, fact_daily, fact_weekly_cohort, mart_campaign_summary):
        float_cols = df.select_dtypes(include="float").columns
        normal_cols = [c for c in float_cols if c not in HIGH_PRECISION_COLS]
        high_prec_cols = [c for c in float_cols if c in HIGH_PRECISION_COLS]
        df[normal_cols] = df[normal_cols].round(2)
        df[high_prec_cols] = df[high_prec_cols].round(4)

    dim_campaign.to_csv(OUT_DIR / "dim_campaign.csv", index=False)
    fact_daily.to_csv(OUT_DIR / "fact_daily.csv", index=False)
    fact_weekly_cohort.to_csv(OUT_DIR / "fact_weekly_cohort.csv", index=False)
    mart_campaign_summary.to_csv(OUT_DIR / "mart_campaign_summary.csv", index=False)

    print(f"dim_campaign: {len(dim_campaign)} rows")
    print(f"fact_daily: {len(fact_daily)} rows")
    print(f"fact_weekly_cohort: {len(fact_weekly_cohort)} rows "
          f"({fact_weekly_cohort['cohort_maturity'].eq('Immature').sum()} immature, "
          f"{(fact_weekly_cohort['best_ltv_window']=='D90').sum()} using D90 as best window)")
    print(f"mart_campaign_summary: {len(mart_campaign_summary)} rows")
    print()
    print(mart_campaign_summary[["campaign_id", "platform", "blended_cac", "cac_vs_target_pct",
                                  "mature_ltv_cac", "ltv_data_status", "health_score", "recommended_action"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
