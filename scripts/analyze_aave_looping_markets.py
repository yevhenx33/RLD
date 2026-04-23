import argparse
import json
from collections import defaultdict
from pathlib import Path
import sys

import clickhouse_connect
import numpy as np
import pandas as pd


ROOT = Path("/home/ubuntu/RLD")
ARTIFACT_DIR = ROOT / "scripts" / "artifacts"

DATA_PIPELINE_PATH = ROOT / "data-pipeline"
if str(DATA_PIPELINE_PATH) not in sys.path:
    sys.path.insert(0, str(DATA_PIPELINE_PATH))

from indexer.tokens import BTC_ASSETS, ETH_ASSETS, STABLES, TOKENS  # noqa: E402


TOPIC_SUPPLY = "0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61"
TOPIC_WITHDRAW = "0x3115d1449a7b732c986cba18244e897a450f61e1bb8d589cd2e69e6c8924f9f7"
TOPIC_BORROW = "0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0"
TOPIC_REPAY = "0xa534c8dbe71f871f9f3530e97a74601fea17b426cae02e1c5aee42c96c784051"
TOPIC_LIQUIDATION = "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"
TARGET_TOPICS = [TOPIC_SUPPLY, TOPIC_WITHDRAW, TOPIC_BORROW, TOPIC_REPAY, TOPIC_LIQUIDATION]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Identify Aave markets with loop-dominated activity and collateral health."
    )
    parser.add_argument("--clickhouse-host", default="127.0.0.1", help="ClickHouse host.")
    parser.add_argument("--clickhouse-port", type=int, default=8123, help="ClickHouse port.")
    parser.add_argument(
        "--equity-csv",
        default=None,
        help="Path to aave_real_equity_accounts_*.csv (defaults to latest in artifacts).",
    )
    parser.add_argument(
        "--event-batch-size",
        type=int,
        default=250_000,
        help="Rows per aave_events batch during decode.",
    )
    parser.add_argument(
        "--min-debt-usd",
        type=float,
        default=50_000_000.0,
        help="Minimum debt threshold for loop-dominated market ranking.",
    )
    parser.add_argument(
        "--min-supply-usd",
        type=float,
        default=50_000_000.0,
        help="Minimum supply threshold for collateral-health ranking.",
    )
    parser.add_argument(
        "--material-threshold-usd",
        type=float,
        default=100_000.0,
        help="Material user threshold in USD.",
    )
    return parser.parse_args()


def fmt_usd(value: float) -> str:
    return f"${value:,.0f}"


def safe_div(num: float, den: float) -> float:
    if den == 0:
        return 0.0
    return num / den


def get_clickhouse_client(host: str, port: int):
    return clickhouse_connect.get_client(host=host, port=port)


def load_latest_equity_csv(explicit_path: str | None) -> Path:
    if explicit_path:
        p = Path(explicit_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"equity CSV not found: {p}")
        return p

    candidates = sorted(ARTIFACT_DIR.glob("aave_real_equity_accounts_*.csv"))
    if not candidates:
        raise FileNotFoundError("No aave_real_equity_accounts_*.csv found in artifacts directory.")
    return candidates[-1]


def build_price_map(ch) -> dict[str, float]:
    q = """
    WITH latest AS (
      SELECT
        symbol,
        argMax(price_usd, inserted_at) AS price_usd
      FROM api_market_latest
      WHERE protocol = 'AAVE_MARKET'
      GROUP BY symbol
    )
    SELECT symbol, price_usd
    FROM latest
    """
    rows = ch.query(q).result_rows
    price_map = {symbol: float(price) for symbol, price in rows}

    weth_price = price_map.get("WETH", 0.0)
    wbtc_price = price_map.get("WBTC", price_map.get("cbBTC", 0.0))
    for sym in STABLES:
        price_map.setdefault(sym, 1.0)
    if weth_price > 0:
        for sym in ETH_ASSETS:
            price_map.setdefault(sym, weth_price)
    if wbtc_price > 0:
        for sym in BTC_ASSETS:
            price_map.setdefault(sym, wbtc_price)

    return price_map


def _decode_amount(data_hex: str, start_word_idx: int) -> int:
    raw = (data_hex or "0x")[2:]
    start = start_word_idx * 64
    end = start + 64
    if len(raw) < end:
        return 0
    return int(raw[start:end], 16)


def _topic_address(topic_value: str | None) -> str | None:
    if not topic_value or len(topic_value) < 42:
        return None
    return "0x" + topic_value[-40:].lower()


def decode_event_user_and_deltas(
    topic0: str,
    topic1: str | None,
    topic2: str | None,
    topic3: str | None,
    data: str,
) -> list[tuple[str, str, str, float]]:
    """
    Returns list of deltas:
      (user_address, reserve_address, kind['supply'|'debt'], delta_tokens)
    """
    out = []
    if topic0 == TOPIC_SUPPLY:
        reserve = _topic_address(topic1)
        user = _topic_address(topic2)
        amount = _decode_amount(data, 1)
        if reserve and user and amount > 0:
            out.append((user, reserve, "supply", amount))
    elif topic0 == TOPIC_WITHDRAW:
        reserve = _topic_address(topic1)
        user = _topic_address(topic2)
        amount = _decode_amount(data, 0)
        if reserve and user and amount > 0:
            out.append((user, reserve, "supply", -amount))
    elif topic0 == TOPIC_BORROW:
        reserve = _topic_address(topic1)
        user = _topic_address(topic2)
        amount = _decode_amount(data, 1)
        if reserve and user and amount > 0:
            out.append((user, reserve, "debt", amount))
    elif topic0 == TOPIC_REPAY:
        reserve = _topic_address(topic1)
        user = _topic_address(topic2)
        amount = _decode_amount(data, 0)
        use_a_tokens = _decode_amount(data, 1)
        if reserve and user and amount > 0:
            out.append((user, reserve, "debt", -amount))
            # When repaid with aTokens, collateral side also decreases.
            if use_a_tokens == 1:
                out.append((user, reserve, "supply", -amount))
    elif topic0 == TOPIC_LIQUIDATION:
        collateral_reserve = _topic_address(topic1)
        debt_reserve = _topic_address(topic2)
        user = _topic_address(topic3)
        debt_covered = _decode_amount(data, 0)
        collateral_taken = _decode_amount(data, 1)
        if user and debt_reserve and debt_covered > 0:
            out.append((user, debt_reserve, "debt", -debt_covered))
        if user and collateral_reserve and collateral_taken > 0:
            out.append((user, collateral_reserve, "supply", -collateral_taken))
    return out


def reconstruct_user_symbol_positions(ch, batch_size: int) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float], dict]:
    supply = defaultdict(float)
    debt = defaultdict(float)
    unknown_reserve_rows = 0
    processed_rows = 0
    decoded_deltas = 0

    last_block = 0
    last_log = -1
    batch_idx = 0
    topics_csv = ",".join(f"'{t}'" for t in TARGET_TOPICS)

    while True:
        q = f"""
        SELECT block_number, log_index, topic0, topic1, topic2, topic3, data
        FROM aave_events
        WHERE topic0 IN ({topics_csv})
          AND (block_number > {last_block} OR (block_number = {last_block} AND log_index > {last_log}))
        ORDER BY block_number, log_index
        LIMIT {batch_size}
        """
        rows = ch.query(q).result_rows
        if not rows:
            break

        batch_idx += 1
        processed_rows += len(rows)

        for block_number, log_index, topic0, topic1, topic2, topic3, data in rows:
            deltas = decode_event_user_and_deltas(topic0, topic1, topic2, topic3, data)
            for user, reserve, kind, delta_raw in deltas:
                reserve_no0x = reserve[2:].lower()
                token_meta = TOKENS.get(reserve_no0x)
                if token_meta is None:
                    unknown_reserve_rows += 1
                    continue
                symbol, decimals = token_meta
                delta_tokens = float(delta_raw) / (10**decimals)
                key = (user, symbol)
                if kind == "supply":
                    supply[key] += delta_tokens
                else:
                    debt[key] += delta_tokens
                decoded_deltas += 1

            last_block = block_number
            last_log = log_index

        if batch_idx % 10 == 0:
            print(
                f"[events] batches={batch_idx} rows={processed_rows:,} "
                f"deltas={decoded_deltas:,} unknown_reserve_rows={unknown_reserve_rows:,}"
            )

    meta = {
        "processed_rows": processed_rows,
        "decoded_deltas": decoded_deltas,
        "unknown_reserve_rows": unknown_reserve_rows,
        "supply_keys": len(supply),
        "debt_keys": len(debt),
    }
    return supply, debt, meta


def build_user_symbol_frame(
    supply_map: dict[tuple[str, str], float],
    debt_map: dict[tuple[str, str], float],
    price_map: dict[str, float],
) -> tuple[pd.DataFrame, dict]:
    keys = set(supply_map.keys()) | set(debt_map.keys())
    rows = []
    missing_price_symbols = set()
    for user, symbol in keys:
        supply_tokens = max(float(supply_map.get((user, symbol), 0.0)), 0.0)
        debt_tokens = max(float(debt_map.get((user, symbol), 0.0)), 0.0)
        if supply_tokens == 0 and debt_tokens == 0:
            continue
        price = price_map.get(symbol)
        if price is None:
            missing_price_symbols.add(symbol)
            price = 0.0
        rows.append(
            {
                "address": user.lower(),
                "symbol": symbol,
                "supply_tokens": supply_tokens,
                "debt_tokens": debt_tokens,
                "price_usd": float(price),
                "supply_usd": supply_tokens * float(price),
                "debt_usd": debt_tokens * float(price),
            }
        )

    df = pd.DataFrame(rows)
    meta = {
        "rows": int(len(df)),
        "missing_price_symbols": sorted(missing_price_symbols),
    }
    return df, meta


def attach_user_profiles(user_symbol_df: pd.DataFrame, equity_csv: Path) -> pd.DataFrame:
    profiles = pd.read_csv(equity_csv)
    profiles["address"] = profiles["address"].str.lower()
    cols = [
        "address",
        "hf",
        "collateral_usd",
        "total_debt_usd",
        "equity_usd",
        "collateral_to_equity",
        "debt_to_collateral",
    ]
    profiles = profiles[cols].copy()

    merged = user_symbol_df.merge(profiles, on="address", how="left")
    merged["hf"] = merged["hf"].fillna(np.inf)
    merged["collateral_usd"] = merged["collateral_usd"].fillna(0.0)
    merged["total_debt_usd"] = merged["total_debt_usd"].fillna(0.0)
    merged["equity_usd"] = merged["equity_usd"].fillna(0.0)
    merged["collateral_to_equity"] = merged["collateral_to_equity"].fillna(0.0)
    merged["debt_to_collateral"] = merged["debt_to_collateral"].fillna(0.0)

    equity_share = np.where(
        merged["collateral_usd"] > 0,
        np.clip(merged["equity_usd"] / merged["collateral_usd"], 0.0, 1.0),
        0.0,
    )
    merged["equity_share_of_collateral"] = equity_share
    merged["equity_backed_supply_usd"] = merged["supply_usd"] * merged["equity_share_of_collateral"]

    merged["is_high_loop_user"] = (
        (merged["collateral_to_equity"] >= 4.0)
        & (merged["debt_to_collateral"] >= 0.60)
        & (merged["total_debt_usd"] > 0)
        & (merged["hf"] >= 1.0)
    )
    merged["is_extreme_loop_user"] = (
        (merged["collateral_to_equity"] >= 8.0)
        & (merged["debt_to_collateral"] >= 0.75)
        & (merged["total_debt_usd"] > 0)
        & (merged["hf"] >= 1.0)
    )
    merged["is_low_hf_user"] = merged["hf"] < 1.10
    merged["is_material_user"] = (merged["collateral_usd"] >= 100_000.0) | (merged["total_debt_usd"] >= 100_000.0)
    return merged


def aggregate_market_metrics(df: pd.DataFrame) -> pd.DataFrame:
    markets = []
    for symbol, grp in df.groupby("symbol", sort=False):
        total_supply = float(grp["supply_usd"].sum())
        total_debt = float(grp["debt_usd"].sum())
        total_activity = total_supply + total_debt

        suppliers = grp[grp["supply_usd"] > 0]
        borrowers = grp[grp["debt_usd"] > 0]

        debt_high_loop = float(borrowers.loc[borrowers["is_high_loop_user"], "debt_usd"].sum())
        debt_extreme_loop = float(borrowers.loc[borrowers["is_extreme_loop_user"], "debt_usd"].sum())
        debt_low_hf = float(borrowers.loc[borrowers["is_low_hf_user"], "debt_usd"].sum())

        supply_high_loop = float(suppliers.loc[suppliers["is_high_loop_user"], "supply_usd"].sum())
        supply_extreme_loop = float(suppliers.loc[suppliers["is_extreme_loop_user"], "supply_usd"].sum())
        supply_low_hf = float(suppliers.loc[suppliers["is_low_hf_user"], "supply_usd"].sum())

        equity_backed_supply = float(suppliers["equity_backed_supply_usd"].sum())

        debt_high_loop_share = safe_div(debt_high_loop, total_debt)
        debt_extreme_loop_share = safe_div(debt_extreme_loop, total_debt)
        debt_low_hf_share = safe_div(debt_low_hf, total_debt)
        supply_high_loop_share = safe_div(supply_high_loop, total_supply)
        supply_extreme_loop_share = safe_div(supply_extreme_loop, total_supply)
        supply_low_hf_share = safe_div(supply_low_hf, total_supply)

        equity_backing_ratio = safe_div(equity_backed_supply, total_supply)
        synthetic_or_looped_supply_ratio = max(0.0, min(1.0, 1.0 - equity_backing_ratio))

        looping_activity_score = safe_div(
            (debt_high_loop_share * total_debt) + (supply_high_loop_share * total_supply),
            total_activity,
        )

        healthy_collateral_score = (
            equity_backing_ratio
            * max(0.0, 1.0 - supply_low_hf_share)
            * max(0.0, 1.0 - supply_extreme_loop_share)
        )

        markets.append(
            {
                "symbol": symbol,
                "total_supply_usd": total_supply,
                "total_debt_usd": total_debt,
                "total_activity_usd": total_activity,
                "supplier_users": int(suppliers["address"].nunique()),
                "borrower_users": int(borrowers["address"].nunique()),
                "active_users": int(grp["address"].nunique()),
                "debt_from_high_loopers_usd": debt_high_loop,
                "debt_from_extreme_loopers_usd": debt_extreme_loop,
                "debt_from_low_hf_users_usd": debt_low_hf,
                "supply_from_high_loopers_usd": supply_high_loop,
                "supply_from_extreme_loopers_usd": supply_extreme_loop,
                "supply_from_low_hf_users_usd": supply_low_hf,
                "equity_backed_supply_usd": equity_backed_supply,
                "debt_high_loop_share": debt_high_loop_share,
                "debt_extreme_loop_share": debt_extreme_loop_share,
                "debt_low_hf_share": debt_low_hf_share,
                "supply_high_loop_share": supply_high_loop_share,
                "supply_extreme_loop_share": supply_extreme_loop_share,
                "supply_low_hf_share": supply_low_hf_share,
                "equity_backing_ratio": equity_backing_ratio,
                "synthetic_or_looped_supply_ratio": synthetic_or_looped_supply_ratio,
                "looping_activity_score": looping_activity_score,
                "healthy_collateral_score": healthy_collateral_score,
            }
        )

    out = pd.DataFrame(markets)
    out = out.sort_values("total_activity_usd", ascending=False).reset_index(drop=True)
    return out


def write_markdown_report(
    market_df: pd.DataFrame,
    out_path: Path,
    min_debt_usd: float,
    min_supply_usd: float,
    material_threshold_usd: float,
) -> None:
    debt_rank = market_df[market_df["total_debt_usd"] >= min_debt_usd].copy()
    debt_rank = debt_rank.sort_values(["debt_high_loop_share", "total_debt_usd"], ascending=[False, False]).head(15)

    collateral_rank = market_df[market_df["total_supply_usd"] >= min_supply_usd].copy()
    collateral_rank = collateral_rank.sort_values(
        ["healthy_collateral_score", "equity_backing_ratio"], ascending=[False, False]
    ).head(15)

    weak_collateral = market_df[market_df["total_supply_usd"] >= min_supply_usd].copy()
    weak_collateral = weak_collateral.sort_values(
        ["synthetic_or_looped_supply_ratio", "supply_extreme_loop_share"], ascending=[False, False]
    ).head(15)

    lines = []
    lines.append("# [Data] Aave Markets: Loop-Dominated Activity and Collateral Health")
    lines.append("")
    lines.append(
        "This report combines event-reconstructed user-level per-market exposures with live account-level equity to locate "
        "markets where activity appears most recursive."
    )
    lines.append("")
    lines.append("## Definitions")
    lines.append("")
    lines.append(
        "- **High-loop user:** `collateral_to_equity >= 4x`, `debt_to_collateral >= 60%`, `HF >= 1`."
    )
    lines.append(
        "- **Extreme-loop user:** `collateral_to_equity >= 8x`, `debt_to_collateral >= 75%`, `HF >= 1`."
    )
    lines.append(
        "- **Equity backing ratio (supply side):** supply weighted by each supplier's `equity/collateral` (clipped to [0,1])."
    )
    lines.append(
        "- **Healthy collateral score:** `equity_backing_ratio * (1-supply_low_hf_share) * (1-supply_extreme_loop_share)`."
    )
    lines.append("")
    lines.append(
        f"Material user threshold used upstream in account profiles: **${material_threshold_usd:,.0f}**."
    )
    lines.append("")

    lines.append("## Markets Most Loop-Dominated on Debt Side")
    lines.append("")
    lines.append(
        f"Filter: debt >= **{fmt_usd(min_debt_usd)}**; ranked by `debt_high_loop_share`."
    )
    lines.append("")
    lines.append("| Symbol | Debt | High-loop debt share | Extreme-loop debt share | Debt low-HF share | Borrowers |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for row in debt_rank.itertuples(index=False):
        lines.append(
            f"| {row.symbol} | {fmt_usd(float(row.total_debt_usd))} | "
            f"{float(row.debt_high_loop_share)*100:.1f}% | {float(row.debt_extreme_loop_share)*100:.1f}% | "
            f"{float(row.debt_low_hf_share)*100:.1f}% | {int(row.borrower_users):,} |"
        )
    lines.append("")

    lines.append("## Healthiest Collateral Markets")
    lines.append("")
    lines.append(
        f"Filter: supply >= **{fmt_usd(min_supply_usd)}**; ranked by `healthy_collateral_score`."
    )
    lines.append("")
    lines.append("| Symbol | Supply | Equity backing ratio | Supply high-loop share | Supply extreme-loop share | Supply low-HF share |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for row in collateral_rank.itertuples(index=False):
        lines.append(
            f"| {row.symbol} | {fmt_usd(float(row.total_supply_usd))} | {float(row.equity_backing_ratio)*100:.1f}% | "
            f"{float(row.supply_high_loop_share)*100:.1f}% | {float(row.supply_extreme_loop_share)*100:.1f}% | "
            f"{float(row.supply_low_hf_share)*100:.1f}% |"
        )
    lines.append("")

    lines.append("## Most Synthetic / Recursively-Backed Collateral Markets")
    lines.append("")
    lines.append(
        f"Filter: supply >= **{fmt_usd(min_supply_usd)}**; ranked by `synthetic_or_looped_supply_ratio`."
    )
    lines.append("")
    lines.append("| Symbol | Supply | Synthetic/looped supply ratio | Equity backing ratio | Supply extreme-loop share |")
    lines.append("|---|---:|---:|---:|---:|")
    for row in weak_collateral.itertuples(index=False):
        lines.append(
            f"| {row.symbol} | {fmt_usd(float(row.total_supply_usd))} | "
            f"{float(row.synthetic_or_looped_supply_ratio)*100:.1f}% | "
            f"{float(row.equity_backing_ratio)*100:.1f}% | {float(row.supply_extreme_loop_share)*100:.1f}% |"
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    ch = get_clickhouse_client(args.clickhouse_host, args.clickhouse_port)
    equity_csv = load_latest_equity_csv(args.equity_csv)
    print(f"[setup] Using equity profiles: {equity_csv}")

    price_map = build_price_map(ch)
    print(f"[setup] Loaded prices for {len(price_map)} symbols")

    print("[1/4] Reconstructing user per-market supply/debt from events...")
    supply_map, debt_map, event_meta = reconstruct_user_symbol_positions(ch, args.event_batch_size)
    print(
        f"[1/4] done rows={event_meta['processed_rows']:,} deltas={event_meta['decoded_deltas']:,} "
        f"unknown_reserve_rows={event_meta['unknown_reserve_rows']:,}"
    )

    print("[2/4] Building user-symbol exposure frame...")
    user_symbol_df, exposure_meta = build_user_symbol_frame(supply_map, debt_map, price_map)
    print(f"[2/4] rows={exposure_meta['rows']:,}, missing_price_symbols={len(exposure_meta['missing_price_symbols'])}")

    print("[3/4] Attaching live user equity profiles...")
    merged = attach_user_profiles(user_symbol_df, equity_csv)
    market_df = aggregate_market_metrics(merged)
    print(f"[3/4] aggregated markets={len(market_df):,}")

    snapshot_tag = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    out_csv = ARTIFACT_DIR / f"aave_market_looping_summary_{snapshot_tag}.csv"
    out_json = ARTIFACT_DIR / f"aave_market_looping_summary_{snapshot_tag}.json"
    out_md = ARTIFACT_DIR / f"aave_market_looping_report_{snapshot_tag}.md"

    market_df.to_csv(out_csv, index=False)

    debt_rank = (
        market_df[market_df["total_debt_usd"] >= args.min_debt_usd]
        .sort_values(["debt_high_loop_share", "total_debt_usd"], ascending=[False, False])
        .head(20)
        .to_dict("records")
    )
    collateral_rank = (
        market_df[market_df["total_supply_usd"] >= args.min_supply_usd]
        .sort_values(["healthy_collateral_score", "equity_backing_ratio"], ascending=[False, False])
        .head(20)
        .to_dict("records")
    )
    weak_rank = (
        market_df[market_df["total_supply_usd"] >= args.min_supply_usd]
        .sort_values(["synthetic_or_looped_supply_ratio", "supply_extreme_loop_share"], ascending=[False, False])
        .head(20)
        .to_dict("records")
    )

    payload = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "inputs": {
            "equity_csv": str(equity_csv),
            "material_threshold_usd": args.material_threshold_usd,
            "min_debt_usd": args.min_debt_usd,
            "min_supply_usd": args.min_supply_usd,
            "loop_thresholds": {
                "high_loop": {"collateral_to_equity_gte": 4.0, "debt_to_collateral_gte": 0.60, "hf_gte": 1.0},
                "extreme_loop": {"collateral_to_equity_gte": 8.0, "debt_to_collateral_gte": 0.75, "hf_gte": 1.0},
            },
        },
        "meta": {
            "event_reconstruction": event_meta,
            "exposure_frame": exposure_meta,
            "market_rows": int(len(market_df)),
        },
        "top_loop_dominated_debt_markets": debt_rank,
        "top_healthiest_collateral_markets": collateral_rank,
        "top_most_synthetic_collateral_markets": weak_rank,
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    write_markdown_report(
        market_df=market_df,
        out_path=out_md,
        min_debt_usd=args.min_debt_usd,
        min_supply_usd=args.min_supply_usd,
        material_threshold_usd=args.material_threshold_usd,
    )

    print("[4/4] Done.")
    print(f"Wrote market summary CSV: {out_csv}")
    print(f"Wrote market summary JSON: {out_json}")
    print(f"Wrote market report MD: {out_md}")


if __name__ == "__main__":
    main()
