"""Wallet trade-count distribution.

Histogram of trades-per-wallet, with explicit counts at >=10, >=30, >=100, >=500.
Run from the project root: python vizz/trade_dist.py
"""

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

DATA_ROOT = "./data"

# ------------------------------------------------------------------
# Build trades-per-wallet via DuckDB over the wallet_index parquet glob
# ------------------------------------------------------------------
con = duckdb.connect()
df = con.execute(f"""
    SELECT wallet, COUNT(*) AS trade_count
    FROM read_parquet(
        '{DATA_ROOT}/wallet_index/**/*.parquet',
        hive_partitioning = false
    )
    GROUP BY wallet
""").df()

counts = df["trade_count"].values

thresholds = [10, 30, 100, 500]
threshold_counts = {t: int((counts >= t).sum()) for t in thresholds}

total_wallets = len(counts)
print(f"Total wallets: {total_wallets:,}")
for t, n in threshold_counts.items():
    print(f"  >= {t:>4} trades: {n:>8,}  ({100*n/total_wallets:.1f}%)")

# ------------------------------------------------------------------
# Plot
# ------------------------------------------------------------------
fig, axes = plt.subplots(1, 2, figsize=(14, 5))
fig.suptitle("Wallet Trade-Count Distribution", fontsize=14, fontweight="bold")

# Left: log-scale histogram of full distribution
ax = axes[0]
bins = np.logspace(0, np.log10(counts.max() + 1), 60)
ax.hist(counts, bins=bins, color="#4C72B0", edgecolor="none", alpha=0.85)
ax.set_xscale("log")
ax.set_yscale("log")
ax.set_xlabel("Trades per wallet (log scale)")
ax.set_ylabel("Number of wallets (log scale)")
ax.set_title("Full distribution (log–log)")
ax.grid(True, which="both", linestyle="--", alpha=0.4)

# Right: threshold bar chart
ax2 = axes[1]
labels = [f"≥{t}" for t in thresholds]
values = [threshold_counts[t] for t in thresholds]
bars = ax2.bar(labels, values, color=["#4C72B0", "#DD8452", "#55A868", "#C44E52"])
ax2.set_xlabel("Minimum trades per wallet")
ax2.set_ylabel("Number of wallets")
ax2.set_title("Wallets meeting trade-count thresholds")
ax2.grid(True, axis="y", linestyle="--", alpha=0.4)

for bar, val in zip(bars, values):
    ax2.text(
        bar.get_x() + bar.get_width() / 2,
        bar.get_height() + max(values) * 0.01,
        f"{val:,}",
        ha="center", va="bottom", fontsize=10, fontweight="bold",
    )

plt.tight_layout()
out = "vizz/trade_dist.png"
plt.savefig(out, dpi=150, bbox_inches="tight")
print(f"Saved: {out}")
