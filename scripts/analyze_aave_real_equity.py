import argparse
import json
import math
import time
from pathlib import Path

import clickhouse_connect
import pandas as pd
import requests
from eth_utils import keccak


AAVE_V3_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
RPC_DEFAULT = "https://ethereum.publicnode.com"
ARTIFACT_DIR = Path("/home/ubuntu/RLD/scripts/artifacts")

TOPIC_SUPPLY = "0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61"
TOPIC_WITHDRAW = "0x3115d1449a7b732c986cba18244e897a450f61e1bb8d589cd2e69e6c8924f9f7"
TOPIC_BORROW = "0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0"
TOPIC_REPAY = "0xa534c8dbe71f871f9f3530e97a74601fea17b426cae02e1c5aee42c96c784051"
TOPIC_LIQUIDATION = "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Estimate gross collateral vs net equity across all Aave v3 Ethereum Core users."
    )
    parser.add_argument("--rpc-url", default=RPC_DEFAULT, help="Ethereum RPC endpoint.")
    parser.add_argument("--clickhouse-host", default="127.0.0.1", help="ClickHouse host.")
    parser.add_argument("--clickhouse-port", type=int, default=8123, help="ClickHouse port.")
    parser.add_argument("--batch-size", type=int, default=120, help="Batch size for JSON-RPC eth_call.")
    parser.add_argument("--max-retries", type=int, default=5, help="Max retries per batch request.")
    parser.add_argument("--sleep-seconds", type=float, default=0.05, help="Pause between RPC batches.")
    parser.add_argument(
        "--material-threshold-usd",
        type=float,
        default=100_000.0,
        help="Threshold for material account tags.",
    )
    return parser.parse_args()


def get_clickhouse_client(host: str, port: int):
    return clickhouse_connect.get_client(host=host, port=port)


def fetch_candidate_addresses(ch) -> list[str]:
    q = f"""
    WITH raw_users AS (
      SELECT lower(concat('0x', right(topic2, 40))) AS user
      FROM aave_events
      WHERE topic0 = '{TOPIC_SUPPLY}' AND topic2 IS NOT NULL
      UNION ALL
      SELECT lower(concat('0x', right(topic2, 40))) AS user
      FROM aave_events
      WHERE topic0 = '{TOPIC_WITHDRAW}' AND topic2 IS NOT NULL
      UNION ALL
      SELECT lower(concat('0x', right(topic2, 40))) AS user
      FROM aave_events
      WHERE topic0 = '{TOPIC_BORROW}' AND topic2 IS NOT NULL
      UNION ALL
      SELECT lower(concat('0x', right(topic2, 40))) AS user
      FROM aave_events
      WHERE topic0 = '{TOPIC_REPAY}' AND topic2 IS NOT NULL
      UNION ALL
      SELECT lower(concat('0x', right(topic3, 40))) AS user
      FROM aave_events
      WHERE topic0 = '{TOPIC_LIQUIDATION}' AND topic3 IS NOT NULL
    )
    SELECT DISTINCT user
    FROM raw_users
    WHERE length(user) = 42
      AND startsWith(user, '0x')
    ORDER BY user
    """
    rows = ch.query(q).result_rows
    return [row[0] for row in rows]


def fetch_protocol_market_snapshot(ch) -> tuple[str, pd.DataFrame]:
    ts = ch.command(
        """
        SELECT max(timestamp)
        FROM api_market_latest
        WHERE protocol = 'AAVE_MARKET'
        """
    )
    q = """
    WITH latest AS (
      SELECT
        symbol,
        argMax(supply_usd, inserted_at) AS supply_usd,
        argMax(borrow_usd, inserted_at) AS borrow_usd,
        argMax(utilization, inserted_at) AS utilization,
        argMax(borrow_apy, inserted_at) AS borrow_apy
      FROM api_market_latest
      WHERE protocol = 'AAVE_MARKET'
      GROUP BY symbol
    )
    SELECT symbol, supply_usd, borrow_usd, utilization, borrow_apy
    FROM latest
    ORDER BY supply_usd DESC
    """
    df = pd.DataFrame(ch.query(q).result_rows, columns=["symbol", "supply_usd", "borrow_usd", "utilization", "borrow_apy"])
    return str(ts), df


def _encode_get_user_account_data(address: str, selector: str) -> str:
    return "0x" + selector + address.lower().replace("0x", "").rjust(64, "0")


def _decode_get_user_account_data(result_hex: str) -> dict:
    raw = result_hex[2:]
    if len(raw) < 64 * 6:
        raise ValueError(f"Unexpected getUserAccountData response length: {len(raw)}")
    vals = [int(raw[i : i + 64], 16) for i in range(0, 64 * 6, 64)]
    return {
        "collateral_usd": vals[0] / 1e8,
        "total_debt_usd": vals[1] / 1e8,
        "available_borrows_usd": vals[2] / 1e8,
        "liquidation_threshold": vals[3] / 1e4,
        "ltv": vals[4] / 1e4,
        "hf": vals[5] / 1e18,
    }


def fetch_live_account_data(
    addresses: list[str],
    rpc_url: str,
    batch_size: int,
    max_retries: int,
    sleep_seconds: float,
) -> tuple[dict[str, dict], dict]:
    selector = keccak(text="getUserAccountData(address)")[:4].hex()
    results: dict[str, dict] = {}

    request_failures = 0
    decode_failures = 0
    skipped_addresses = 0

    total_batches = math.ceil(len(addresses) / batch_size) if addresses else 0
    for batch_idx, start in enumerate(range(0, len(addresses), batch_size), start=1):
        chunk = addresses[start : start + batch_size]

        payload = []
        id_to_address = {}
        for i, address in enumerate(chunk, start=1):
            payload_id = start + i
            id_to_address[payload_id] = address
            payload.append(
                {
                    "jsonrpc": "2.0",
                    "id": payload_id,
                    "method": "eth_call",
                    "params": [
                        {"to": AAVE_V3_POOL, "data": _encode_get_user_account_data(address, selector)},
                        "latest",
                    ],
                }
            )

        response_items = None
        for attempt in range(max_retries):
            try:
                response = requests.post(rpc_url, json=payload, timeout=45)
                response.raise_for_status()
                response_items = response.json()
                break
            except Exception:
                request_failures += 1
                if attempt + 1 == max_retries:
                    response_items = []
                else:
                    time.sleep(0.5 * (attempt + 1))

        if isinstance(response_items, dict):
            response_items = [response_items]

        by_id = {}
        for item in response_items:
            if isinstance(item, dict) and "id" in item:
                by_id[item["id"]] = item

        for payload_id, address in id_to_address.items():
            item = by_id.get(payload_id)
            if not item:
                skipped_addresses += 1
                continue
            result_hex = item.get("result")
            if not result_hex:
                skipped_addresses += 1
                continue
            try:
                results[address] = _decode_get_user_account_data(result_hex)
            except Exception:
                decode_failures += 1

        if batch_idx % 25 == 0 or batch_idx == total_batches:
            print(
                f"[rpc] batches {batch_idx}/{total_batches} | fetched={len(results)} "
                f"| failures={request_failures} | decode_failures={decode_failures}"
            )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    meta = {
        "requested_addresses": len(addresses),
        "fetched_addresses": len(results),
        "request_failures": request_failures,
        "decode_failures": decode_failures,
        "skipped_addresses": skipped_addresses,
    }
    return results, meta


def safe_div(num: float, den: float) -> float:
    if den == 0:
        return float("inf")
    return num / den


def compute_summary(df: pd.DataFrame, material_threshold_usd: float) -> dict:
    active = df[(df["collateral_usd"] > 0) | (df["total_debt_usd"] > 0)].copy()
    debtors = active[active["total_debt_usd"] > 0].copy()
    suppliers = active[active["collateral_usd"] > 0].copy()
    material = active[
        (active["total_debt_usd"] >= material_threshold_usd) | (active["collateral_usd"] >= material_threshold_usd)
    ].copy()

    active["equity_usd"] = active["collateral_usd"] - active["total_debt_usd"]
    debtors["equity_usd"] = debtors["collateral_usd"] - debtors["total_debt_usd"]
    suppliers["equity_usd"] = suppliers["collateral_usd"] - suppliers["total_debt_usd"]
    material["equity_usd"] = material["collateral_usd"] - material["total_debt_usd"]

    total_collateral = float(active["collateral_usd"].sum())
    total_debt = float(active["total_debt_usd"].sum())
    total_equity = float(active["equity_usd"].sum())

    underwater = active[active["hf"] < 1.0]
    healthy = active[active["hf"] >= 1.0]

    top_debt = active.sort_values("total_debt_usd", ascending=False)

    concentration = {}
    for n in [1, 5, 10, 20, 50]:
        subset = top_debt.head(n)
        concentration[f"top{n}_debt_share"] = safe_div(float(subset["total_debt_usd"].sum()), total_debt)
        concentration[f"top{n}_collateral_share"] = safe_div(float(subset["collateral_usd"].sum()), total_collateral)
        concentration[f"top{n}_equity_share"] = safe_div(float(subset["equity_usd"].sum()), total_equity)

    hf_quantiles = {}
    if len(debtors) > 0:
        hf_quantiles = {
            "p01": float(debtors["hf"].quantile(0.01)),
            "p05": float(debtors["hf"].quantile(0.05)),
            "p10": float(debtors["hf"].quantile(0.10)),
            "p25": float(debtors["hf"].quantile(0.25)),
            "p50": float(debtors["hf"].quantile(0.50)),
            "p75": float(debtors["hf"].quantile(0.75)),
            "p90": float(debtors["hf"].quantile(0.90)),
            "p95": float(debtors["hf"].quantile(0.95)),
            "p99": float(debtors["hf"].quantile(0.99)),
        }

    largest = top_debt.head(1).to_dict("records")
    largest_payload = largest[0] if largest else None

    return {
        "counts": {
            "active_users": int(len(active)),
            "debtors": int(len(debtors)),
            "suppliers": int(len(suppliers)),
            "material_users": int(len(material)),
            "underwater_hf_lt_1": int(len(underwater)),
            "healthy_hf_ge_1": int(len(healthy)),
        },
        "totals_usd": {
            "collateral": total_collateral,
            "debt": total_debt,
            "equity": total_equity,
        },
        "ratios": {
            "debt_to_collateral": safe_div(total_debt, total_collateral),
            "collateral_to_equity": safe_div(total_collateral, total_equity),
            "debt_to_equity": safe_div(total_debt, total_equity),
            "equity_share_of_collateral": safe_div(total_equity, total_collateral),
        },
        "concentration": concentration,
        "debtor_hf_quantiles": hf_quantiles,
        "largest_debt_account": largest_payload,
    }


def fmt_usd(value: float) -> str:
    return f"${value:,.0f}"


def build_markdown(
    snapshot_ts: str,
    summary: dict,
    protocol_markets: pd.DataFrame,
    out_path: Path,
    material_threshold_usd: float,
) -> None:
    counts = summary["counts"]
    totals = summary["totals_usd"]
    ratios = summary["ratios"]
    concentration = summary["concentration"]
    largest = summary["largest_debt_account"]

    lines = []
    lines.append("# [Data] Aave Core: Gross TVL vs Net Equity")
    lines.append("")
    lines.append(
        "This computes user-level net equity (`totalCollateralBase - totalDebtBase`) across all active Aave v3 Ethereum Core users."
    )
    lines.append("")
    lines.append("## Snapshot")
    lines.append("")
    lines.append(f"- Snapshot time: **{snapshot_ts} UTC**")
    lines.append(f"- Active users (collateral>0 or debt>0): **{counts['active_users']:,}**")
    lines.append(f"- Debtors: **{counts['debtors']:,}**")
    lines.append(f"- Suppliers: **{counts['suppliers']:,}**")
    lines.append(
        f"- Material users (`collateral >= ${material_threshold_usd:,.0f}` or debt >= ${material_threshold_usd:,.0f}): "
        f"**{counts['material_users']:,}**"
    )
    lines.append(f"- HF < 1 users: **{counts['underwater_hf_lt_1']:,}**")
    lines.append("")
    lines.append("## Gross vs Net")
    lines.append("")
    lines.append(f"- Gross collateral: **{fmt_usd(totals['collateral'])}**")
    lines.append(f"- Gross debt: **{fmt_usd(totals['debt'])}**")
    lines.append(f"- Net equity (collateral - debt): **{fmt_usd(totals['equity'])}**")
    lines.append("")
    lines.append(f"- Debt / collateral: **{ratios['debt_to_collateral']*100:.2f}%**")
    lines.append(f"- Equity share of collateral: **{ratios['equity_share_of_collateral']*100:.2f}%**")
    lines.append(f"- Collateral / equity (loop multiple proxy): **{ratios['collateral_to_equity']:.2f}x**")
    lines.append(f"- Debt / equity: **{ratios['debt_to_equity']:.2f}x**")
    lines.append("")
    lines.append("## Concentration")
    lines.append("")
    lines.append(f"- Top 1 debt share: **{concentration['top1_debt_share']*100:.2f}%**")
    lines.append(f"- Top 5 debt share: **{concentration['top5_debt_share']*100:.2f}%**")
    lines.append(f"- Top 10 debt share: **{concentration['top10_debt_share']*100:.2f}%**")
    lines.append(f"- Top 20 debt share: **{concentration['top20_debt_share']*100:.2f}%**")
    lines.append("")
    if largest:
        equity = float(largest["collateral_usd"]) - float(largest["total_debt_usd"])
        lines.append("## Largest Debt Account")
        lines.append("")
        lines.append(f"- Address: `{largest['address']}`")
        lines.append(f"- Collateral: **{fmt_usd(float(largest['collateral_usd']))}**")
        lines.append(f"- Debt: **{fmt_usd(float(largest['total_debt_usd']))}**")
        lines.append(f"- Net equity: **{fmt_usd(equity)}**")
        lines.append(f"- HF: **{float(largest['hf']):.4f}**")
        lines.append("")
    lines.append("## Protocol Market Totals (api_market_latest)")
    lines.append("")
    lines.append(
        f"- Sum supply across symbols: **{fmt_usd(float(protocol_markets['supply_usd'].sum()))}**"
    )
    lines.append(
        f"- Sum borrow across symbols: **{fmt_usd(float(protocol_markets['borrow_usd'].sum()))}**"
    )
    lines.append(
        f"- Net supply-borrow: **{fmt_usd(float((protocol_markets['supply_usd'] - protocol_markets['borrow_usd']).sum()))}**"
    )
    lines.append("")
    lines.append("| Symbol | Supply | Borrow | Utilization |")
    lines.append("|---|---:|---:|---:|")
    for row in protocol_markets.head(15).itertuples(index=False):
        lines.append(
            f"| {row.symbol} | {fmt_usd(float(row.supply_usd))} | {fmt_usd(float(row.borrow_usd))} | {float(row.utilization)*100:.2f}% |"
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    ch = get_clickhouse_client(args.clickhouse_host, args.clickhouse_port)
    snapshot_ts, protocol_markets = fetch_protocol_market_snapshot(ch)

    print("[1/4] Fetching candidate addresses from Aave events...")
    candidates = fetch_candidate_addresses(ch)
    print(f"      candidate addresses: {len(candidates):,}")

    print("[2/4] Fetching live getUserAccountData for candidates...")
    live_data, rpc_meta = fetch_live_account_data(
        addresses=candidates,
        rpc_url=args.rpc_url,
        batch_size=args.batch_size,
        max_retries=args.max_retries,
        sleep_seconds=args.sleep_seconds,
    )
    print(f"      fetched addresses: {len(live_data):,}")

    print("[3/4] Building user equity table...")
    rows = []
    for address, metrics in live_data.items():
        collateral = float(metrics["collateral_usd"])
        debt = float(metrics["total_debt_usd"])
        equity = collateral - debt
        rows.append(
            {
                "address": address,
                "hf": float(metrics["hf"]),
                "collateral_usd": collateral,
                "total_debt_usd": debt,
                "equity_usd": equity,
                "available_borrows_usd": float(metrics["available_borrows_usd"]),
                "liquidation_threshold": float(metrics["liquidation_threshold"]),
                "ltv": float(metrics["ltv"]),
                "is_active": bool((collateral > 0) or (debt > 0)),
                "is_material_100k": bool((collateral >= args.material_threshold_usd) or (debt >= args.material_threshold_usd)),
                "debt_to_collateral": safe_div(debt, collateral) if collateral > 0 else float("inf"),
                "collateral_to_equity": safe_div(collateral, equity) if equity > 0 else float("inf"),
                "debt_to_equity": safe_div(debt, equity) if equity > 0 else float("inf"),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("No user account data returned from RPC.")
    df = df.sort_values("total_debt_usd", ascending=False).reset_index(drop=True)
    df.insert(0, "rank_by_debt", range(1, len(df) + 1))

    summary = compute_summary(df, args.material_threshold_usd)

    date = snapshot_ts.split(" ")[0]
    out_csv = ARTIFACT_DIR / f"aave_real_equity_accounts_{date}.csv"
    out_json = ARTIFACT_DIR / f"aave_real_equity_summary_{date}.json"
    out_md = ARTIFACT_DIR / f"aave_real_equity_report_{date}.md"

    df.to_csv(out_csv, index=False)

    payload = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "snapshot_ts": snapshot_ts,
        "source": {
            "clickhouse_table": "aave_events",
            "rpc_method": "getUserAccountData(address)",
            "pool": AAVE_V3_POOL,
            "rpc_endpoint": args.rpc_url,
            "candidate_construction": "distinct users from Supply/Withdraw/Borrow/Repay/LiquidationCall events",
        },
        "meta": {
            "candidate_addresses": len(candidates),
            "rpc_fetch": rpc_meta,
            "material_threshold_usd": args.material_threshold_usd,
        },
        "summary": summary,
        "protocol_market_totals": {
            "sum_supply_usd": float(protocol_markets["supply_usd"].sum()),
            "sum_borrow_usd": float(protocol_markets["borrow_usd"].sum()),
            "sum_net_supply_minus_borrow_usd": float((protocol_markets["supply_usd"] - protocol_markets["borrow_usd"]).sum()),
        },
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    build_markdown(
        snapshot_ts=snapshot_ts,
        summary=summary,
        protocol_markets=protocol_markets,
        out_path=out_md,
        material_threshold_usd=args.material_threshold_usd,
    )

    print("[4/4] Done.")
    print(f"Wrote account table: {out_csv}")
    print(f"Wrote summary JSON: {out_json}")
    print(f"Wrote report MD: {out_md}")


if __name__ == "__main__":
    main()
