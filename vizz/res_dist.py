"""Resolution outcome balance.

Counts YES vs NO resolutions across the dataset.
Severe imbalance affects the extremization baseline.
Run from the project root: python vizz/res_dist.py
"""

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

DATA_ROOT = "./data"

con = duckdb.connect()

# resolution_outcome: 1 = YES, 0 = NO; NULL = unresolved
df = con.execute(f"""
    SELECT
        resolution_outcome,
        COUNT(*) AS market_count,
        SUM(total_volume_usd) AS total_volume_usd,
        AVG(total_volume_usd) AS avg_volume_usd
    FROM read_parquet('{DATA_ROOT}/markets/markets.parquet')
    GROUP BY resolution_outcome
    ORDER BY resolution_outcome
""").df()

label_map = {0: "NO (0)", 1: "YES (1)"}
df["label"] = df["resolution_outcome"].map(label_map).fillna("Unresolved / NULL")

total = df["market_count"].sum()
print(f"Resolution outcome balance (total markets: {total:,})")
print(f"\n{'Outcome':<18} {'Markets':>8} {'Share%':>8} {'Total Vol USD':>16} {'Avg Vol USD':>14}")
print("-" * 70)
for _, row in df.iterrows():
    print(
        f"{row['label']:<18} {int(row['market_count']):>8,} "
        f"{row['market_count']/total*100:>7.1f}% "
        f"{row['total_volume_usd']:>16,.0f} "
        f"{row['avg_volume_usd']:>14,.0f}"
    )

# Imbalance ratio (YES / NO)
yes_count = int(df.loc[df["resolution_outcome"] == 1, "market_count"].sum())
no_count  = int(df.loc[df["resolution_outcome"] == 0, "market_count"].sum())
if no_count > 0:
    ratio = yes_count / no_count
    print(f"\nYES/NO ratio: {ratio:.3f}  ({'balanced' if 0.8 <= ratio <= 1.25 else 'IMBALANCED — review extremization baseline'})")

# ------------------------------------------------------------------
# Per-category resolution balance
# ------------------------------------------------------------------
cat_df = con.execute(f"""
    SELECT
        COALESCE(NULLIF(TRIM(category), ''), '(untagged)') AS category,
        SUM(CASE WHEN resolution_outcome = 1 THEN 1 ELSE 0 END) AS yes_count,
        SUM(CASE WHEN resolution_outcome = 0 THEN 1 ELSE 0 END) AS no_count,
        COUNT(*) AS total
    FROM read_parquet('{DATA_ROOT}/markets/markets.parquet')
    GROUP BY 1
    ORDER BY total DESC
""").df()
cat_df["yes_pct"] = cat_df["yes_count"] / cat_df["total"].clip(lower=1) * 100

print(f"\n{'Category':<30} {'YES':>6} {'NO':>6} {'YES%':>7}")
print("-" * 55)
for _, row in cat_df.iterrows():
    print(f"{row['category']:<30} {int(row['yes_count']):>6,} {int(row['no_count']):>6,} {row['yes_pct']:>6.1f}%")

# ------------------------------------------------------------------
# Plot
# ------------------------------------------------------------------
fig, axes = plt.subplots(1, 2, figsize=(13, 5))
fig.suptitle("Resolution Outcome Balance", fontsize=14, fontweight="bold")

colors = {"YES (1)": "#55A868", "NO (0)": "#C44E52", "Unresolved / NULL": "#8172B2"}
bar_colors = [colors.get(l, "#4C72B0") for l in df["label"]]

# Left: overall count
ax = axes[0]
bars = ax.bar(df["label"], df["market_count"], color=bar_colors)
ax.set_ylabel("Number of markets")
ax.set_title("Overall resolution outcome counts")
ax.grid(True, axis="y", linestyle="--", alpha=0.4)
for bar, val in zip(bars, df["market_count"]):
    ax.text(bar.get_x() + bar.get_width() / 2,
            bar.get_height() + total * 0.003,
            f"{int(val):,}\n({val/total*100:.1f}%)",
            ha="center", va="bottom", fontsize=9, fontweight="bold")

# Right: per-category YES% stacked bar
ax2 = axes[1]
top_cats = cat_df.head(12)
x = np.arange(len(top_cats))
w = 0.6
b_no  = ax2.bar(x, top_cats["no_count"],  w, label="NO",  color="#C44E52", alpha=0.85)
b_yes = ax2.bar(x, top_cats["yes_count"], w, bottom=top_cats["no_count"],
                label="YES", color="#55A868", alpha=0.85)
ax2.set_xticks(x)
ax2.set_xticklabels(top_cats["category"], rotation=45, ha="right", fontsize=8)
ax2.set_ylabel("Number of markets")
ax2.set_title("YES vs NO by category (top 12)")
ax2.legend()
ax2.grid(True, axis="y", linestyle="--", alpha=0.4)

plt.tight_layout()
out = "vizz/res_dist.png"
plt.savefig(out, dpi=150, bbox_inches="tight")
print(f"\nSaved: {out}")
