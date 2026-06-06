"""Per-market volume reconciliation.

Compares sum(size_usd) in trades vs. Gamma's reported total_volume_usd.
Flags any market where the discrepancy exceeds 1%.
Output written to vizz/out.txt.
Run from the project root: python vizz/permarket.py
"""

import duckdb

DATA_ROOT = "./data"
OUT_FILE = "vizz/out.txt"
THRESHOLD_PCT = 1.0  # flag if |discrepancy| > 1%

con = duckdb.connect()

# Sum size_usd per market from all trade partitions
trade_vol = con.execute(f"""
    SELECT condition_id, SUM(size_usd) AS trade_vol_usd
    FROM read_parquet(
        '{DATA_ROOT}/trades/**/*.parquet',
        hive_partitioning = false
    )
    GROUP BY condition_id
""").df()

# Reported volume from markets table
market_vol = con.execute(f"""
    SELECT condition_id, total_volume_usd AS reported_vol_usd, question
    FROM read_parquet('{DATA_ROOT}/markets/markets.parquet')
""").df()

# Join
merged = market_vol.merge(trade_vol, on="condition_id", how="left")
merged["trade_vol_usd"] = merged["trade_vol_usd"].fillna(0.0)
merged["reported_vol_usd"] = merged["reported_vol_usd"].fillna(0.0)

# Discrepancy: (trade - reported) / max(reported, 1) * 100
merged["discrepancy_pct"] = (
    (merged["trade_vol_usd"] - merged["reported_vol_usd"])
    / merged["reported_vol_usd"].clip(lower=1.0)
    * 100
)
merged["abs_disc_pct"] = merged["discrepancy_pct"].abs()

flagged = merged[merged["abs_disc_pct"] > THRESHOLD_PCT].sort_values(
    "abs_disc_pct", ascending=False
)

lines = []
lines.append("=" * 80)
lines.append("PER-MARKET VOLUME RECONCILIATION")
lines.append(f"Threshold: discrepancy > {THRESHOLD_PCT}%")
lines.append("=" * 80)
lines.append(
    f"\nTotal markets:  {len(merged):,}"
)
lines.append(f"Flagged (>{THRESHOLD_PCT}%): {len(flagged):,}\n")

if flagged.empty:
    lines.append("No markets exceed the discrepancy threshold. All volumes reconcile.")
else:
    lines.append(
        f"{'condition_id':<46} {'reported_usd':>14} {'trade_usd':>14} {'disc_%':>8}  question"
    )
    lines.append("-" * 120)
    for _, row in flagged.iterrows():
        q = row["question"][:60] if isinstance(row["question"], str) else ""
        lines.append(
            f"{row['condition_id']:<46} "
            f"{row['reported_vol_usd']:>14,.2f} "
            f"{row['trade_vol_usd']:>14,.2f} "
            f"{row['discrepancy_pct']:>+8.2f}%  {q}"
        )

lines.append("\n--- SUMMARY STATS ---")
lines.append(f"Mean  |discrepancy|: {merged['abs_disc_pct'].mean():.3f}%")
lines.append(f"Median|discrepancy|: {merged['abs_disc_pct'].median():.3f}%")
lines.append(f"Max   |discrepancy|: {merged['abs_disc_pct'].max():.3f}%")

output = "\n".join(lines)
print(output)

with open(OUT_FILE, "w") as fh:
    fh.write(output + "\n")

print(f"\nSaved: {OUT_FILE}")
