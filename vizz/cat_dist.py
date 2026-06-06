"""Category distribution.

Bar chart of market count per category from the rule-based tagging in markets.parquet.
Run from the project root: python vizz/cat_dist.py
"""

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DATA_ROOT = "./data"

con = duckdb.connect()
df = con.execute(f"""
    SELECT
        COALESCE(NULLIF(TRIM(category), ''), '(untagged)') AS category,
        COUNT(*) AS market_count,
        SUM(total_volume_usd) AS total_volume_usd
    FROM read_parquet('{DATA_ROOT}/markets/markets.parquet')
    GROUP BY 1
    ORDER BY market_count DESC
""").df()

total = df["market_count"].sum()
df["share_pct"] = df["market_count"] / total * 100

print(f"{'Category':<30} {'Markets':>8} {'Share%':>8} {'Volume USD':>16}")
print("-" * 66)
for _, row in df.iterrows():
    print(
        f"{row['category']:<30} {int(row['market_count']):>8,} "
        f"{row['share_pct']:>7.1f}% "
        f"{row['total_volume_usd']:>16,.0f}"
    )

# ------------------------------------------------------------------
# Plot
# ------------------------------------------------------------------
fig, axes = plt.subplots(1, 2, figsize=(16, 6))
fig.suptitle("Category Distribution", fontsize=14, fontweight="bold")

colors = plt.cm.tab20.colors

# Left: market count bar
ax = axes[0]
bars = ax.barh(df["category"], df["market_count"], color=colors[:len(df)])
ax.set_xlabel("Number of markets")
ax.set_title("Market count per category")
ax.invert_yaxis()
ax.grid(True, axis="x", linestyle="--", alpha=0.4)
for bar, val in zip(bars, df["market_count"]):
    ax.text(bar.get_width() + total * 0.003, bar.get_y() + bar.get_height() / 2,
            f"{int(val):,}", va="center", fontsize=8)

# Right: volume share pie
ax2 = axes[1]
# Show top-N in pie, collapse rest to "Other"
TOP_N = 10
top = df.head(TOP_N).copy()
other_vol = df.iloc[TOP_N:]["total_volume_usd"].sum()
if other_vol > 0:
    import pandas as pd
    top = pd.concat([
        top,
        pd.DataFrame([{"category": "Other", "total_volume_usd": other_vol}])
    ], ignore_index=True)
wedges, texts, autotexts = ax2.pie(
    top["total_volume_usd"],
    labels=top["category"],
    autopct="%1.1f%%",
    colors=colors[:len(top)],
    startangle=140,
    pctdistance=0.82,
)
for t in autotexts:
    t.set_fontsize(8)
for t in texts:
    t.set_fontsize(8)
ax2.set_title(f"Volume share (top {TOP_N} categories)")

plt.tight_layout()
out = "vizz/cat_dist.png"
plt.savefig(out, dpi=150, bbox_inches="tight")
print(f"\nSaved: {out}")
