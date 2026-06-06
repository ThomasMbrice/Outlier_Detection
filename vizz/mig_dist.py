"""Migration-split market distribution.

Shows how many markets have migration_split=true and compares trade counts
across the V1/V2 contract boundary for those markets.
Run from the project root: python vizz/mig_dist.py
"""

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DATA_ROOT = "./data"
# V1→V2 cutover timestamp from config.yaml
V1_V2_CUTOVER_TS = 1_745_841_600  # 2026-04-28 12:00 UTC

con = duckdb.connect()

# Market-level split flag counts
markets_df = con.execute(f"""
    SELECT
        migration_split,
        COUNT(*) AS market_count,
        SUM(total_volume_usd) AS total_volume_usd
    FROM read_parquet('{DATA_ROOT}/markets/markets.parquet')
    GROUP BY migration_split
    ORDER BY migration_split
""").df()

split_market_ids = con.execute(f"""
    SELECT condition_id
    FROM read_parquet('{DATA_ROOT}/markets/markets.parquet')
    WHERE migration_split = true
""").df()["condition_id"].tolist()

print(f"Markets by migration_split flag:")
for _, row in markets_df.iterrows():
    label = "split=True " if row["migration_split"] else "split=False"
    print(
        f"  {label}: {int(row['market_count']):>6,} markets, "
        f"${row['total_volume_usd']:>16,.0f} reported volume"
    )
print()

# For migration-split markets only: count trades before/after cutover
if split_market_ids:
    ids_sql = ", ".join(f"'{cid}'" for cid in split_market_ids[:5000])  # DuckDB IN limit safety
    split_trades = con.execute(f"""
        SELECT
            condition_id,
            SUM(CASE WHEN block_ts < {V1_V2_CUTOVER_TS} THEN 1 ELSE 0 END) AS v1_trades,
            SUM(CASE WHEN block_ts >= {V1_V2_CUTOVER_TS} THEN 1 ELSE 0 END) AS v2_trades,
            COUNT(*) AS total_trades
        FROM read_parquet(
            '{DATA_ROOT}/trades/**/*.parquet',
            hive_partitioning = false
        )
        WHERE condition_id IN ({ids_sql})
        GROUP BY condition_id
    """).df()

    print(f"Trade distribution for {len(split_trades):,} migration-split markets:")
    print(f"  Total trades:      {split_trades['total_trades'].sum():>10,}")
    print(f"  V1-era trades:     {split_trades['v1_trades'].sum():>10,}")
    print(f"  V2-era trades:     {split_trades['v2_trades'].sum():>10,}")
    print()
    markets_with_both = ((split_trades["v1_trades"] > 0) & (split_trades["v2_trades"] > 0)).sum()
    print(f"  Markets with trades on BOTH sides of cutover: {markets_with_both:,}")
    markets_v1_only = ((split_trades["v1_trades"] > 0) & (split_trades["v2_trades"] == 0)).sum()
    markets_v2_only = ((split_trades["v1_trades"] == 0) & (split_trades["v2_trades"] > 0)).sum()
    markets_no_trades = ((split_trades["v1_trades"] == 0) & (split_trades["v2_trades"] == 0)).sum()
    print(f"  V1-only:  {markets_v1_only:,}   V2-only: {markets_v2_only:,}   no trades: {markets_no_trades:,}")
else:
    print("No migration-split markets found.")
    split_trades = None

# ------------------------------------------------------------------
# Plot
# ------------------------------------------------------------------
fig, axes = plt.subplots(1, 2, figsize=(13, 5))
fig.suptitle("Migration-Split Market Analysis", fontsize=14, fontweight="bold")

# Left: split vs non-split market count
ax = axes[0]
split_false = int(markets_df.loc[markets_df["migration_split"] == False, "market_count"].sum()) if not markets_df.empty else 0
split_true  = int(markets_df.loc[markets_df["migration_split"] == True,  "market_count"].sum()) if not markets_df.empty else 0
bars = ax.bar(["split=False", "split=True"], [split_false, split_true],
              color=["#4C72B0", "#DD8452"])
ax.set_ylabel("Number of markets")
ax.set_title("Markets by migration_split flag")
ax.grid(True, axis="y", linestyle="--", alpha=0.4)
for bar, val in zip(bars, [split_false, split_true]):
    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(split_false, split_true) * 0.01,
            f"{val:,}", ha="center", va="bottom", fontweight="bold")

# Right: V1 vs V2 trade breakdown for split markets
ax2 = axes[1]
if split_trades is not None and not split_trades.empty:
    v1_total = int(split_trades["v1_trades"].sum())
    v2_total = int(split_trades["v2_trades"].sum())
    bars2 = ax2.bar(["V1-era trades\n(pre-cutover)", "V2-era trades\n(post-cutover)"],
                    [v1_total, v2_total], color=["#55A868", "#C44E52"])
    ax2.set_ylabel("Number of trades")
    ax2.set_title("Trades in split markets by contract era")
    ax2.grid(True, axis="y", linestyle="--", alpha=0.4)
    for bar, val in zip(bars2, [v1_total, v2_total]):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(v1_total, v2_total) * 0.01,
                 f"{val:,}", ha="center", va="bottom", fontweight="bold")
else:
    ax2.text(0.5, 0.5, "No migration-split\nmarkets found",
             ha="center", va="center", transform=ax2.transAxes, fontsize=12)
    ax2.set_title("Trades in split markets by contract era")

plt.tight_layout()
out = "vizz/mig_dist.png"
plt.savefig(out, dpi=150, bbox_inches="tight")
print(f"\nSaved: {out}")
