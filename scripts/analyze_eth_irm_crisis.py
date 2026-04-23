import argparse
import json
import math
import re
import time
from collections import defaultdict
from pathlib import Path

import clickhouse_connect
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import seaborn as sns
from eth_utils import keccak
from web3 import Web3


WETH_RESERVE = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
AAVE_V3_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
RPC_DEFAULT = "https://ethereum.publicnode.com"

TOPIC_BORROW = "0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0"
TOPIC_REPAY = "0xa534c8dbe71f871f9f3530e97a74601fea17b426cae02e1c5aee42c96c784051"
TOPIC_LIQUIDATION = "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"

ARTIFACT_DIR = Path("/home/ubuntu/RLD/scripts/artifacts")
SECONDS_PER_YEAR = 365.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ETH/WETH IRM crisis sensitivity using Envio event reconstruction + live Aave account data."
    )
    parser.add_argument("--rpc-url", default=RPC_DEFAULT, help="Ethereum RPC endpoint for getUserAccountData calls.")
    parser.add_argument("--clickhouse-host", default="127.0.0.1", help="ClickHouse host.")
    parser.add_argument("--clickhouse-port", type=int, default=8123, help="ClickHouse port.")
    parser.add_argument(
        "--material-threshold-usd",
        type=float,
        default=100_000.0,
        help="Debt threshold for material-account reporting.",
    )
    parser.add_argument(
        "--pre-change-window-start",
        default="2026-04-18 00:00:00",
        help="Window start for pre-change WETH APR extraction.",
    )
    parser.add_argument(
        "--pre-change-window-end",
        default="2026-04-20 00:00:00",
        help="Window end for pre-change WETH APR extraction.",
    )
    parser.add_argument(
        "--hypothetical-tight-apr",
        type=float,
        default=0.12,
        help="Hypothetical re-steepened WETH APR to test.",
    )
    parser.add_argument(
        "--hypothetical-crisis-apr",
        type=float,
        default=0.20,
        help="Hypothetical crisis WETH APR to test.",
    )
    return parser.parse_args()


def get_clickhouse_client(host: str, port: int):
    return clickhouse_connect.get_client(host=host, port=port)


def fmt_usd(value: float) -> str:
    return f"${value:,.0f}"


def scenario_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return re.sub(r"_+", "_", slug)


def fetch_onchain_weth_reserve_totals(rpc_url: str) -> dict:
    pool_abi = [
        {
            "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
            "name": "getReserveData",
            "outputs": [
                {
                    "components": [
                        {
                            "components": [{"internalType": "uint256", "name": "data", "type": "uint256"}],
                            "internalType": "struct DataTypes.ReserveConfigurationMap",
                            "name": "configuration",
                            "type": "tuple",
                        },
                        {"internalType": "uint128", "name": "liquidityIndex", "type": "uint128"},
                        {"internalType": "uint128", "name": "currentLiquidityRate", "type": "uint128"},
                        {"internalType": "uint128", "name": "variableBorrowIndex", "type": "uint128"},
                        {"internalType": "uint128", "name": "currentVariableBorrowRate", "type": "uint128"},
                        {"internalType": "uint128", "name": "currentStableBorrowRate", "type": "uint128"},
                        {"internalType": "uint40", "name": "lastUpdateTimestamp", "type": "uint40"},
                        {"internalType": "uint16", "name": "id", "type": "uint16"},
                        {"internalType": "address", "name": "aTokenAddress", "type": "address"},
                        {"internalType": "address", "name": "stableDebtTokenAddress", "type": "address"},
                        {"internalType": "address", "name": "variableDebtTokenAddress", "type": "address"},
                        {"internalType": "address", "name": "interestRateStrategyAddress", "type": "address"},
                        {"internalType": "uint128", "name": "accruedToTreasury", "type": "uint128"},
                        {"internalType": "uint128", "name": "unbacked", "type": "uint128"},
                        {"internalType": "uint128", "name": "isolationModeTotalDebt", "type": "uint128"},
                    ],
                    "internalType": "struct DataTypes.ReserveData",
                    "name": "",
                    "type": "tuple",
                }
            ],
            "stateMutability": "view",
            "type": "function",
        }
    ]
    erc20_abi = [
        {
            "constant": True,
            "inputs": [],
            "name": "totalSupply",
            "outputs": [{"name": "", "type": "uint256"}],
            "type": "function",
        },
        {
            "constant": True,
            "inputs": [{"name": "account", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}],
            "type": "function",
        },
    ]

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 45}))
    pool = w3.eth.contract(address=Web3.to_checksum_address(AAVE_V3_POOL), abi=pool_abi)
    reserve = pool.functions.getReserveData(Web3.to_checksum_address(WETH_RESERVE)).call()

    a_token = reserve[8]
    stable_debt_token = reserve[9]
    variable_debt_token = reserve[10]

    a_token_contract = w3.eth.contract(address=a_token, abi=erc20_abi)
    stable_debt_contract = w3.eth.contract(address=stable_debt_token, abi=erc20_abi)
    variable_debt_contract = w3.eth.contract(address=variable_debt_token, abi=erc20_abi)
    weth_contract = w3.eth.contract(address=Web3.to_checksum_address(WETH_RESERVE), abi=erc20_abi)

    total_supply_weth = a_token_contract.functions.totalSupply().call() / 1e18
    total_variable_debt_weth = variable_debt_contract.functions.totalSupply().call() / 1e18
    total_stable_debt_weth = stable_debt_contract.functions.totalSupply().call() / 1e18
    available_liquidity_weth = weth_contract.functions.balanceOf(a_token).call() / 1e18

    total_debt_weth = total_variable_debt_weth + total_stable_debt_weth
    utilization_onchain = total_debt_weth / total_supply_weth if total_supply_weth > 0 else 0.0

    return {
        "weth_total_supply_weth": float(total_supply_weth),
        "weth_total_debt_weth": float(total_debt_weth),
        "weth_available_liquidity_weth": float(available_liquidity_weth),
        "weth_utilization_onchain": float(utilization_onchain),
    }


def fetch_latest_weth_market_state(ch, rpc_url: str) -> dict:
    q = """
    SELECT
      argMax(timestamp, inserted_at) AS timestamp,
      argMax(utilization, inserted_at) AS utilization,
      argMax(borrow_apy, inserted_at) AS borrow_apy,
      argMax(supply_apy, inserted_at) AS supply_apy,
      argMax(borrow_usd, inserted_at) AS borrow_usd,
      argMax(supply_usd, inserted_at) AS supply_usd,
      argMax(price_usd, inserted_at) AS price_usd
    FROM api_market_latest
    WHERE protocol='AAVE_MARKET' AND symbol='WETH'
    """
    row = ch.query(q).first_row
    if row is None:
        raise RuntimeError("No WETH row found in api_market_latest.")
    onchain = fetch_onchain_weth_reserve_totals(rpc_url=rpc_url)
    price_usd = float(row[6])
    supply_usd_onchain = onchain["weth_total_supply_weth"] * price_usd
    debt_usd_onchain = onchain["weth_total_debt_weth"] * price_usd
    available_usd_onchain = onchain["weth_available_liquidity_weth"] * price_usd

    return {
        "snapshot_ts": str(row[0]),
        "weth_utilization": onchain["weth_utilization_onchain"],
        "weth_utilization_api_model": float(row[1]),
        "weth_borrow_apy_observed": float(row[2]),
        "weth_supply_apy_observed": float(row[3]),
        "weth_borrow_usd": float(debt_usd_onchain),
        "weth_supply_usd": float(supply_usd_onchain),
        "weth_available_liquidity_usd": float(available_usd_onchain),
        "weth_price_usd": price_usd,
        "weth_total_supply_weth": onchain["weth_total_supply_weth"],
        "weth_total_debt_weth": onchain["weth_total_debt_weth"],
        "weth_available_liquidity_weth": onchain["weth_available_liquidity_weth"],
    }


def fetch_pre_change_observed_regime(ch, start_ts: str, end_ts: str) -> dict:
    q = f"""
    SELECT
      timestamp,
      argMax(utilization, inserted_at) AS utilization,
      argMax(borrow_apy, inserted_at) AS borrow_apy
    FROM aave_timeseries
    WHERE protocol='AAVE_MARKET'
      AND symbol='WETH'
      AND timestamp >= toDateTime('{start_ts}')
      AND timestamp < toDateTime('{end_ts}')
    GROUP BY timestamp
    ORDER BY borrow_apy DESC, timestamp DESC
    LIMIT 1
    """
    row = ch.query(q).first_row
    if row is None:
        raise RuntimeError("No WETH pre-change regime row found in provided window.")
    return {
        "timestamp": str(row[0]),
        "utilization": float(row[1]),
        "borrow_apy": float(row[2]),
    }


def fetch_non_weth_weighted_borrow_apy(ch) -> float:
    q = """
    WITH latest AS (
      SELECT
        symbol,
        argMax(borrow_apy, inserted_at) AS borrow_apy,
        argMax(borrow_usd, inserted_at) AS borrow_usd
      FROM api_market_latest
      WHERE protocol='AAVE_MARKET'
      GROUP BY symbol
    )
    SELECT sum(borrow_apy * borrow_usd) / nullIf(sum(borrow_usd), 0)
    FROM latest
    WHERE symbol != 'WETH' AND borrow_usd > 0
    """
    value = ch.command(q)
    if value is None:
        return 0.0
    return float(value)


def reconstruct_positive_weth_debt(ch) -> tuple[dict[str, float], dict]:
    weth_no0x = WETH_RESERVE[2:].lower()
    q = f"""
    SELECT topic0, topic1, topic2, topic3, data
    FROM aave_events
    WHERE (topic0 = '{TOPIC_BORROW}' AND lower(right(topic1, 40)) = '{weth_no0x}')
       OR (topic0 = '{TOPIC_REPAY}' AND lower(right(topic1, 40)) = '{weth_no0x}')
       OR (topic0 = '{TOPIC_LIQUIDATION}' AND lower(right(topic2, 40)) = '{weth_no0x}')
    """
    rows = ch.query(q).result_rows
    net_debt_tokens = defaultdict(float)

    decode_failures = 0
    for topic0, _topic1, topic2, topic3, data in rows:
        try:
            raw = (data or "0x")[2:]
            if topic0 == TOPIC_BORROW:
                if not topic2:
                    continue
                user = "0x" + topic2[-40:].lower()
                amount = int(raw[64:128], 16) / 1e18
                net_debt_tokens[user] += amount
            elif topic0 == TOPIC_REPAY:
                if not topic2:
                    continue
                user = "0x" + topic2[-40:].lower()
                amount = int(raw[0:64], 16) / 1e18
                net_debt_tokens[user] -= amount
            else:
                if not topic3:
                    continue
                user = "0x" + topic3[-40:].lower()
                amount = int(raw[0:64], 16) / 1e18
                net_debt_tokens[user] -= amount
        except Exception:
            decode_failures += 1

    positive = {addr: amt for addr, amt in net_debt_tokens.items() if amt > 0}
    meta = {
        "event_rows_processed": len(rows),
        "event_decode_failures": decode_failures,
        "users_seen": len(net_debt_tokens),
        "users_with_positive_weth_debt": len(positive),
        "total_positive_weth_debt_tokens": float(sum(positive.values())),
    }
    return positive, meta


def _encode_get_user_account_data(address: str, selector: str) -> str:
    return "0x" + selector + address.lower().replace("0x", "").rjust(64, "0")


def _decode_get_user_account_data(result_hex: str) -> dict:
    raw = result_hex[2:]
    if len(raw) < 64 * 6:
        raise ValueError(f"Invalid getUserAccountData response length: {len(raw)}")
    vals = [int(raw[i : i + 64], 16) for i in range(0, 64 * 6, 64)]
    return {
        "collateral_usd": vals[0] / 1e8,
        "total_debt_usd": vals[1] / 1e8,
        "available_borrows_usd": vals[2] / 1e8,
        "liquidation_threshold": vals[3] / 1e4,
        "ltv": vals[4] / 1e4,
        "hf": vals[5] / 1e18,
    }


def fetch_live_user_account_data(
    addresses: list[str],
    rpc_url: str,
    batch_size: int = 120,
    max_retries: int = 4,
    pause_seconds: float = 0.15,
) -> tuple[dict[str, dict], dict]:
    selector = keccak(text="getUserAccountData(address)")[:4].hex()
    out = {}
    request_failures = 0
    decode_failures = 0
    skipped = 0

    for start in range(0, len(addresses), batch_size):
        chunk = addresses[start : start + batch_size]
        payload = []
        id_to_addr = {}
        for i, addr in enumerate(chunk, start=1):
            payload_id = start + i
            id_to_addr[payload_id] = addr
            payload.append(
                {
                    "jsonrpc": "2.0",
                    "id": payload_id,
                    "method": "eth_call",
                    "params": [
                        {
                            "to": AAVE_V3_POOL,
                            "data": _encode_get_user_account_data(addr, selector),
                        },
                        "latest",
                    ],
                }
            )

        responses = None
        for attempt in range(max_retries):
            try:
                resp = requests.post(rpc_url, json=payload, timeout=45)
                resp.raise_for_status()
                responses = resp.json()
                break
            except Exception:
                request_failures += 1
                if attempt + 1 == max_retries:
                    responses = []
                else:
                    time.sleep(0.5 * (attempt + 1))

        if isinstance(responses, dict):
            responses = [responses]

        response_by_id = {}
        for item in responses:
            if isinstance(item, dict) and "id" in item:
                response_by_id[item["id"]] = item

        for payload_id, addr in id_to_addr.items():
            item = response_by_id.get(payload_id)
            if not item:
                skipped += 1
                continue
            result_hex = item.get("result")
            if not result_hex:
                skipped += 1
                continue
            try:
                out[addr] = _decode_get_user_account_data(result_hex)
            except Exception:
                decode_failures += 1

        if pause_seconds > 0:
            time.sleep(pause_seconds)

    meta = {
        "requested_addresses": len(addresses),
        "fetched_addresses": len(out),
        "request_failures": request_failures,
        "decode_failures": decode_failures,
        "skipped_addresses": skipped,
    }
    return out, meta


def build_base_snapshot(
    live_data: dict[str, dict],
    positive_weth_debt: dict[str, float],
    weth_price_usd: float,
    material_threshold_usd: float,
) -> pd.DataFrame:
    rows = []
    for addr, debt_tokens in positive_weth_debt.items():
        metrics = live_data.get(addr)
        if not metrics:
            continue
        total_debt = float(metrics["total_debt_usd"])
        if total_debt <= 0:
            continue

        weth_debt_usd = float(debt_tokens * weth_price_usd)
        share_raw = weth_debt_usd / total_debt if total_debt > 0 else 0.0
        share_for_model = float(min(1.0, max(0.0, share_raw)))

        rows.append(
            {
                "address": addr,
                "hf": float(metrics["hf"]),
                "is_hf_below_1": bool(metrics["hf"] < 1.0),
                "total_debt_usd": total_debt,
                "collateral_usd": float(metrics["collateral_usd"]),
                "available_borrows_usd": float(metrics["available_borrows_usd"]),
                "liquidation_threshold": float(metrics["liquidation_threshold"]),
                "ltv": float(metrics["ltv"]),
                "weth_debt_events_tokens": float(debt_tokens),
                "weth_debt_events_usd": weth_debt_usd,
                "weth_debt_share_of_total_debt": share_raw,
                "weth_share_for_model": share_for_model,
                "is_material_debt_ge_100k": bool(total_debt >= material_threshold_usd),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("Base snapshot is empty after merging live account data with WETH debt users.")
    df = df.sort_values("hf", ascending=True).reset_index(drop=True)
    df.insert(0, "rank_by_lowest_hf", np.arange(1, len(df) + 1))
    return df


def hf_after_days(hf0: float, tracked_share: float, tracked_apr: float, other_apr: float, days: float) -> float:
    t = days / SECONDS_PER_YEAR
    tracked_growth = math.exp(math.log(1 + tracked_apr) * t)
    other_growth = math.exp(math.log(1 + other_apr) * t)
    growth = tracked_share * tracked_growth + (1 - tracked_share) * other_growth
    return hf0 / growth


def days_to_liquidation(
    hf0: float,
    tracked_share: float,
    tracked_apr: float,
    other_apr: float,
    max_days: float = 3650,
) -> float:
    if hf0 <= 1.0:
        return 0.0

    def f(days: float) -> float:
        return hf_after_days(hf0, tracked_share, tracked_apr, other_apr, days) - 1.0

    if f(max_days) > 0:
        return float("inf")

    lo, hi = 0.0, max_days
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return hi


def summarize_scenario(
    df: pd.DataFrame,
    ttl_col: str,
    material_threshold_usd: float,
) -> dict:
    alive = df[(df["hf"] >= 1.0) & (df["total_debt_usd"] > 0)].copy()
    material = alive[alive["total_debt_usd"] >= material_threshold_usd].copy()

    def metrics(sub: pd.DataFrame, horizon_days: float) -> tuple[int, float]:
        in_window = sub[(sub[ttl_col] <= horizon_days) & np.isfinite(sub[ttl_col])]
        return int(len(in_window)), float(in_window["total_debt_usd"].sum())

    all_7d = metrics(alive, 7)
    all_30d = metrics(alive, 30)
    material_7d = metrics(material, 7)
    material_30d = metrics(material, 30)

    finite_ttl = material[np.isfinite(material[ttl_col])][ttl_col]
    if len(finite_ttl) == 0:
        ttl_percentiles = {}
    else:
        ttl_percentiles = {
            "p05": float(np.percentile(finite_ttl, 5)),
            "p25": float(np.percentile(finite_ttl, 25)),
            "p50": float(np.percentile(finite_ttl, 50)),
            "p75": float(np.percentile(finite_ttl, 75)),
            "p95": float(np.percentile(finite_ttl, 95)),
        }

    top = (
        material.sort_values(ttl_col, ascending=True)
        .head(10)[["address", "hf", "total_debt_usd", "weth_share_for_model", ttl_col]]
        .rename(columns={ttl_col: "days_to_liq"})
        .to_dict("records")
    )

    return {
        "all_users_alive_hf_ge_1": {
            "count": int(len(alive)),
            "users_liq_7d": all_7d[0],
            "debt_liq_7d_usd": all_7d[1],
            "users_liq_30d": all_30d[0],
            "debt_liq_30d_usd": all_30d[1],
        },
        "material_users_debt_ge_threshold": {
            "count": int(len(material)),
            "threshold_usd": material_threshold_usd,
            "users_liq_7d": material_7d[0],
            "debt_liq_7d_usd": material_7d[1],
            "users_liq_30d": material_30d[0],
            "debt_liq_30d_usd": material_30d[1],
            "time_to_liq_percentiles_days": ttl_percentiles,
        },
        "top10_fastest_material": top,
    }


def run_scenarios(
    base_df: pd.DataFrame,
    scenario_aprs: dict[str, float],
    other_apr: float,
    material_threshold_usd: float,
) -> tuple[pd.DataFrame, dict, dict]:
    projection = base_df.copy()
    scenario_to_ttl_col = {}
    summary = {}

    for scenario, apr in scenario_aprs.items():
        slug = scenario_slug(scenario)
        ttl_col = f"days_to_liq_{slug}"
        hf7_col = f"hf_7d_{slug}"
        hf30_col = f"hf_30d_{slug}"

        projection[ttl_col] = projection.apply(
            lambda r: days_to_liquidation(
                hf0=float(r["hf"]),
                tracked_share=float(r["weth_share_for_model"]),
                tracked_apr=apr,
                other_apr=other_apr,
            ),
            axis=1,
        )
        projection[hf7_col] = projection.apply(
            lambda r: hf_after_days(
                hf0=float(r["hf"]),
                tracked_share=float(r["weth_share_for_model"]),
                tracked_apr=apr,
                other_apr=other_apr,
                days=7,
            ),
            axis=1,
        )
        projection[hf30_col] = projection.apply(
            lambda r: hf_after_days(
                hf0=float(r["hf"]),
                tracked_share=float(r["weth_share_for_model"]),
                tracked_apr=apr,
                other_apr=other_apr,
                days=30,
            ),
            axis=1,
        )

        scenario_to_ttl_col[scenario] = ttl_col
        summary[scenario] = summarize_scenario(projection, ttl_col, material_threshold_usd)

    return projection, summary, scenario_to_ttl_col


def write_charts(
    summary: dict,
    scenario_order: list[str],
    projection: pd.DataFrame,
    scenario_to_ttl_col: dict[str, str],
    material_threshold_usd: float,
    out_bar: Path,
    out_cumulative: Path,
) -> None:
    sns.set_theme(style="darkgrid")

    labels = scenario_order
    risk_7d = []
    risk_30d = []
    for scenario in labels:
        payload = summary[scenario]["material_users_debt_ge_threshold"]
        risk_7d.append(payload["debt_liq_7d_usd"] / 1e6)
        risk_30d.append(payload["debt_liq_30d_usd"] / 1e6)

    x = np.arange(len(labels))
    width = 0.38
    fig, ax = plt.subplots(figsize=(13, 7))
    ax.bar(x - width / 2, risk_7d, width, label="7-day debt-at-risk ($M)", color="#7F1D1D")
    ax.bar(x + width / 2, risk_30d, width, label="30-day debt-at-risk ($M)", color="#EA580C")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=10, ha="right")
    ax.set_ylabel("Debt-at-risk (USD millions)")
    ax.set_title("WETH IRM Scenario Sensitivity (Material Users)", fontsize=14, fontweight="bold")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(out_bar, dpi=240)
    plt.close(fig)

    material = projection[(projection["total_debt_usd"] >= material_threshold_usd) & (projection["hf"] >= 1.0)].copy()
    days = np.arange(0, 91)
    fig, ax = plt.subplots(figsize=(13, 7))
    for scenario in labels:
        ttl_col = scenario_to_ttl_col[scenario]
        ttl = material[ttl_col].values
        debt = material["total_debt_usd"].values
        cumulative = [float(debt[(ttl <= d)].sum()) / 1e6 for d in days]
        ax.plot(days, cumulative, linewidth=2.5, label=scenario)
    ax.axvline(7, color="#7F1D1D", linestyle="--", linewidth=1.5, alpha=0.8)
    ax.axvline(30, color="#B91C1C", linestyle="--", linewidth=1.5, alpha=0.8)
    ax.set_title("Cumulative Material Debt-at-Risk (WETH Debt Universe)", fontsize=14, fontweight="bold")
    ax.set_xlabel("Days from snapshot")
    ax.set_ylabel("Cumulative debt-at-risk (USD millions)")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(out_cumulative, dpi=240)
    plt.close(fig)


def write_report(
    out_md: Path,
    state: dict,
    population: dict,
    scenario_aprs: dict[str, float],
    scenario_summary: dict,
    scenario_order: list[str],
    projection_df: pd.DataFrame,
    scenario_to_ttl_col: dict[str, str],
    material_threshold_usd: float,
    bar_chart_path: Path,
    cumulative_chart_path: Path,
    pre_change_regime: dict,
) -> None:
    top_largest = projection_df.sort_values("total_debt_usd", ascending=False).head(10)
    largest = top_largest.iloc[0]

    lines = []
    lines.append("# [Data] WETH IRM Crisis Sensitivity (Aave v3 Ethereum Core)")
    lines.append("")
    lines.append(
        "This analysis reconstructs the live WETH-debt borrower set from Aave events and "
        "joins it with current `getUserAccountData` risk state to test IRM-rate scenarios."
    )
    lines.append("")
    lines.append("## Why this matters")
    lines.append("")
    lines.append(
        "- Changing IRM primarily changes **debt accrual speed** and liquidation clocks; it does not directly unblock withdrawals if the blocker is a freeze/queue mechanism."
    )
    lines.append(
        "- This quantifies how much the Apr-20 WETH IRM flattening likely reduced liquidation pressure on loop-heavy accounts."
    )
    lines.append("")
    lines.append("## Snapshot")
    lines.append("")
    lines.append(f"- Snapshot time: **{state['snapshot_ts']} UTC**")
    lines.append(f"- WETH utilization: **{state['weth_utilization']*100:.2f}%**")
    lines.append(f"- WETH observed borrow APR: **{state['weth_borrow_apy_observed']*100:.2f}%**")
    lines.append(f"- WETH observed supply APR: **{state['weth_supply_apy_observed']*100:.2f}%**")
    lines.append(f"- WETH borrow / supply: **{fmt_usd(state['weth_borrow_usd'])} / {fmt_usd(state['weth_supply_usd'])}**")
    lines.append(
        f"- On-chain available liquidity: **{state['weth_available_liquidity_weth']:.4f} WETH** "
        f"(**{fmt_usd(state['weth_available_liquidity_usd'])}**)"
    )
    lines.append(f"- WETH price used for event debt conversion: **${state['weth_price_usd']:,.2f}**")
    lines.append(f"- Non-WETH debt-weighted baseline APR: **{state['non_weth_weighted_borrow_apy']*100:.2f}%**")
    lines.append(f"- Users with positive reconstructed WETH debt: **{population['users_with_positive_weth_debt']:,}**")
    lines.append(
        f"- Material users (`debt >= ${material_threshold_usd:,.0f}` and HF>=1): "
        f"**{population['material_users_hf_ge_1']:,}**"
    )
    lines.append("")
    lines.append("## Scenario APRs")
    lines.append("")
    lines.append("| Scenario | APR |")
    lines.append("|---|---:|")
    for scenario in scenario_order:
        lines.append(f"| {scenario} | {scenario_aprs[scenario]*100:.2f}% |")
    lines.append("")
    lines.append(
        f"Pre-change anchor APR was taken from **{pre_change_regime['timestamp']} UTC** "
        f"at **{pre_change_regime['borrow_apy']*100:.2f}%**."
    )
    lines.append("")
    lines.append("## Material Debt-at-Risk")
    lines.append("")
    lines.append("| Scenario | 7d users | 7d debt-at-risk | 30d users | 30d debt-at-risk |")
    lines.append("|---|---:|---:|---:|---:|")
    for scenario in scenario_order:
        m = scenario_summary[scenario]["material_users_debt_ge_threshold"]
        lines.append(
            f"| {scenario} | {m['users_liq_7d']} | {fmt_usd(m['debt_liq_7d_usd'])} | "
            f"{m['users_liq_30d']} | {fmt_usd(m['debt_liq_30d_usd'])} |"
        )
    lines.append("")
    lines.append("## Charts")
    lines.append("")
    lines.append("![Material WETH debt-at-risk bar chart](<UPLOAD_WETH_CHART_1_URL>)")
    lines.append(f"_Local artifact: `{bar_chart_path}`_")
    lines.append("")
    lines.append("![Cumulative WETH debt-at-risk](<UPLOAD_WETH_CHART_2_URL>)")
    lines.append(f"_Local artifact: `{cumulative_chart_path}`_")
    lines.append("")
    lines.append("## Largest WETH-Debt Concentration")
    lines.append("")
    lines.append(f"- Address: `{largest['address']}`")
    lines.append(f"- Total debt: **{fmt_usd(float(largest['total_debt_usd']))}**")
    lines.append(
        f"- Reconstructed WETH debt: **{largest['weth_debt_events_tokens']:,.2f} WETH** "
        f"(**{fmt_usd(float(largest['weth_debt_events_usd']))}**)"
    )
    lines.append(f"- Current HF: **{float(largest['hf']):.4f}**")
    lines.append("")
    lines.append("| Scenario | Days to liquidation | HF @ 7d | HF @ 30d |")
    lines.append("|---|---:|---:|---:|")
    for scenario in scenario_order:
        slug = scenario_slug(scenario)
        ttl = float(largest[f"days_to_liq_{slug}"])
        hf7 = float(largest[f"hf_7d_{slug}"])
        hf30 = float(largest[f"hf_30d_{slug}"])
        ttl_display = "inf" if math.isinf(ttl) else f"{ttl:.1f}"
        lines.append(f"| {scenario} | {ttl_display} | {hf7:.4f} | {hf30:.4f} |")
    lines.append("")
    lines.append("## Top 10 WETH-Debt Accounts (by total debt)")
    lines.append("")
    lines.append("| Rank | Address | Total debt | WETH debt (events) | HF |")
    lines.append("|---:|---|---:|---:|---:|")
    for idx, row in enumerate(top_largest.itertuples(index=False), start=1):
        lines.append(
            f"| {idx} | `{row.address}` | {fmt_usd(float(row.total_debt_usd))} | "
            f"{fmt_usd(float(row.weth_debt_events_usd))} | {float(row.hf):.4f} |"
        )
    lines.append("")
    lines.append("## Read-through for the crisis question")
    lines.append("")
    post_name = scenario_order[0]
    pre_name = scenario_order[1]
    post_30d = scenario_summary[post_name]["material_users_debt_ge_threshold"]["debt_liq_30d_usd"]
    pre_30d = scenario_summary[pre_name]["material_users_debt_ge_threshold"]["debt_liq_30d_usd"]
    delta = pre_30d - post_30d
    lines.append(
        f"- Flattening from `{pre_name}` to `{post_name}` reduces modeled 30-day material debt-at-risk by **{fmt_usd(delta)}**."
    )
    lines.append(
        "- This supports the \"save loopers\" objective (slower debt growth, longer liquidation clocks)."
    )
    lines.append(
        "- It does **not** by itself unfreeze collateral withdrawals; that requires liquidity-path/freeze-policy resolution."
    )
    lines.append("")

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    ch = get_clickhouse_client(args.clickhouse_host, args.clickhouse_port)

    state = fetch_latest_weth_market_state(ch, rpc_url=args.rpc_url)
    pre_change = fetch_pre_change_observed_regime(
        ch,
        start_ts=args.pre_change_window_start,
        end_ts=args.pre_change_window_end,
    )
    state["pre_change_peak_borrow_apy"] = pre_change["borrow_apy"]
    state["pre_change_peak_utilization"] = pre_change["utilization"]
    state["non_weth_weighted_borrow_apy"] = fetch_non_weth_weighted_borrow_apy(ch)

    positive_weth_debt, debt_meta = reconstruct_positive_weth_debt(ch)
    live_data, rpc_meta = fetch_live_user_account_data(
        sorted(positive_weth_debt.keys()),
        rpc_url=args.rpc_url,
    )

    base_df = build_base_snapshot(
        live_data=live_data,
        positive_weth_debt=positive_weth_debt,
        weth_price_usd=state["weth_price_usd"],
        material_threshold_usd=args.material_threshold_usd,
    )

    scenario_aprs = {
        "Post-change flat (observed)": state["weth_borrow_apy_observed"],
        "Pre-change regime (observed peak)": pre_change["borrow_apy"],
        f"Hypothetical re-steepen ({args.hypothetical_tight_apr*100:.0f}%)": args.hypothetical_tight_apr,
        f"Hypothetical crisis ({args.hypothetical_crisis_apr*100:.0f}%)": args.hypothetical_crisis_apr,
    }
    scenario_order = list(scenario_aprs.keys())

    projection_df, scenario_summary, scenario_to_ttl_col = run_scenarios(
        base_df=base_df,
        scenario_aprs=scenario_aprs,
        other_apr=state["non_weth_weighted_borrow_apy"],
        material_threshold_usd=args.material_threshold_usd,
    )

    material_alive = projection_df[
        (projection_df["hf"] >= 1.0) & (projection_df["total_debt_usd"] >= args.material_threshold_usd)
    ]
    population = {
        "users_with_positive_weth_debt": int(len(projection_df)),
        "material_users_hf_ge_1": int(len(material_alive)),
        "total_debt_usd_all": float(projection_df["total_debt_usd"].sum()),
        "total_debt_usd_material_hf_ge_1": float(material_alive["total_debt_usd"].sum()),
    }

    snapshot_date = str(state["snapshot_ts"]).split(" ")[0]
    base_csv = ARTIFACT_DIR / f"weth_hf_sorted_envio_reconstruction_{snapshot_date}.csv"
    projection_csv = ARTIFACT_DIR / f"weth_irm_crisis_sensitivity_projection_{snapshot_date}.csv"
    summary_json = ARTIFACT_DIR / f"weth_irm_crisis_sensitivity_summary_{snapshot_date}.json"
    report_md = ARTIFACT_DIR / f"weth_irm_crisis_report_{snapshot_date}.md"
    bar_chart = ARTIFACT_DIR / f"chart_weth_material_debt_risk_7d_30d_{snapshot_date}.png"
    cumulative_chart = ARTIFACT_DIR / f"chart_weth_cumulative_material_debt_90d_{snapshot_date}.png"

    base_df.to_csv(base_csv, index=False)
    projection_df.to_csv(projection_csv, index=False)
    write_charts(
        summary=scenario_summary,
        scenario_order=scenario_order,
        projection=projection_df,
        scenario_to_ttl_col=scenario_to_ttl_col,
        material_threshold_usd=args.material_threshold_usd,
        out_bar=bar_chart,
        out_cumulative=cumulative_chart,
    )

    payload = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "snapshot_ts": state["snapshot_ts"],
        "source": {
            "clickhouse_table": "aave_events",
            "construction": "WETH Borrow - Repay - LiquidationCall (debt leg)",
            "rpc_method": "getUserAccountData(address)",
            "pool": AAVE_V3_POOL,
            "rpc_endpoint": args.rpc_url,
        },
        "state": state,
        "population": population,
        "meta": {
            "debt_reconstruction": debt_meta,
            "rpc_fetch": rpc_meta,
            "material_threshold_usd": args.material_threshold_usd,
            "pre_change_window_start": args.pre_change_window_start,
            "pre_change_window_end": args.pre_change_window_end,
        },
        "scenario_aprs": scenario_aprs,
        "scenario_ttl_columns": scenario_to_ttl_col,
        "scenarios": scenario_summary,
    }
    summary_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    write_report(
        out_md=report_md,
        state=state,
        population=population,
        scenario_aprs=scenario_aprs,
        scenario_summary=scenario_summary,
        scenario_order=scenario_order,
        projection_df=projection_df,
        scenario_to_ttl_col=scenario_to_ttl_col,
        material_threshold_usd=args.material_threshold_usd,
        bar_chart_path=bar_chart,
        cumulative_chart_path=cumulative_chart,
        pre_change_regime=pre_change,
    )

    print(f"Wrote base snapshot: {base_csv}")
    print(f"Wrote projection: {projection_csv}")
    print(f"Wrote summary: {summary_json}")
    print(f"Wrote report: {report_md}")
    print(f"Wrote chart: {bar_chart}")
    print(f"Wrote chart: {cumulative_chart}")


if __name__ == "__main__":
    main()
