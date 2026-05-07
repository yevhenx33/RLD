#!/usr/bin/env python3
"""Audit event-sourced Aave account reconstruction against RPC reads.

RPC is intentionally used only by this offline audit script, never by the
GraphQL serving path.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import uuid

from eth_abi import decode as abi_decode
from eth_utils import keccak

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analytics.aave_accounts import (  # noqa: E402
    AAVE_CHAIN_ID,
    AAVE_DEPLOYMENT_ID,
    RAY,
    _encode_address_arg,
    _rpc_call,
    _selector,
    clickhouse_client_from_env,
    deterministic_audit_users,
    ensure_aave_account_tables,
)
from analytics.aave_constants import AAVE_V3_POOL  # noqa: E402


SCALED_BALANCE_OF = _selector("scaledBalanceOf(address)")
GET_USER_ACCOUNT_DATA = _selector("getUserAccountData(address)")
GET_USER_CONFIGURATION = _selector("getUserConfiguration(address)")
GET_USER_EMODE = _selector("getUserEMode(address)")
GET_RESERVE_NORMALIZED_INCOME = _selector("getReserveNormalizedIncome(address)")
GET_RESERVE_NORMALIZED_VARIABLE_DEBT = _selector("getReserveNormalizedVariableDebt(address)")


def _call_uint(rpc_url: str, contract: str, selector: str, user_or_reserve: str, block_tag: str) -> int:
    raw = _rpc_call(rpc_url, contract, selector + _encode_address_arg(user_or_reserve), block_tag)
    return int(abi_decode(["uint256"], bytes.fromhex(raw[2:]))[0])


def _reconstructed_positions(ch, users: list[str]):
    if not users:
        return []
    escaped = ", ".join("'" + user.replace("'", "''") + "'" for user in users)
    return ch.query(
        f"""
        SELECT p.user, p.reserve, tokens.a_token, tokens.variable_debt_token,
               sumIf(p.scaled_delta_raw, p.token_type = 'ATOKEN') AS scaled_supply_raw,
               sumIf(p.scaled_delta_raw, p.token_type = 'VARIABLE_DEBT') AS scaled_debt_raw
        FROM aave_account_events AS p
        LEFT JOIN aave_reserve_tokens FINAL AS tokens
          ON tokens.deployment_id = p.deployment_id AND tokens.reserve = p.reserve
        WHERE p.deployment_id = '{AAVE_DEPLOYMENT_ID}'
          AND p.user IN ({escaped})
          AND p.reserve != ''
        GROUP BY p.user, p.reserve, tokens.a_token, tokens.variable_debt_token
        HAVING scaled_supply_raw != 0 OR scaled_debt_raw != 0
        """
    ).result_rows


def _diff(run_id: str, block_number: int, user: str, reserve: str, field: str, reconstructed, rpc, reason: str = ""):
    reconstructed_f = float(reconstructed)
    rpc_f = float(rpc)
    abs_diff = abs(reconstructed_f - rpc_f)
    rel_diff = abs_diff / max(abs(rpc_f), 1.0)
    passed = abs_diff <= 2.0
    return [
        run_id,
        AAVE_DEPLOYMENT_ID,
        AAVE_CHAIN_ID,
        int(block_number),
        user,
        reserve,
        field,
        str(reconstructed),
        str(rpc),
        abs_diff,
        rel_diff,
        1 if passed else 0,
        reason if passed else reason or "scaled_balance_mismatch",
    ]


def run_audit(args) -> int:
    rpc_url = args.rpc_url or os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL")
    if not rpc_url:
        raise RuntimeError("MAINNET_RPC_URL or --rpc-url is required")

    ch = clickhouse_client_from_env()
    ensure_aave_account_tables(ch)
    run_id = args.run_id or str(uuid.uuid4())
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    block_tag = hex(args.block_number) if args.block_number else "latest"
    block_number = int(args.block_number or 0)
    users = deterministic_audit_users(ch, limit=args.users)
    rows = _reconstructed_positions(ch, users)

    diffs = []
    sampled_positions = 0
    for user, reserve, a_token, variable_debt_token, scaled_supply, scaled_debt in rows:
        sampled_positions += 1
        if a_token and int(scaled_supply or 0) != 0:
            rpc_scaled = _call_uint(rpc_url, str(a_token), SCALED_BALANCE_OF, str(user), block_tag)
            diffs.append(_diff(run_id, block_number, str(user), str(reserve), "scaledSupply", scaled_supply, rpc_scaled))
        if variable_debt_token and int(scaled_debt or 0) != 0:
            rpc_scaled = _call_uint(rpc_url, str(variable_debt_token), SCALED_BALANCE_OF, str(user), block_tag)
            diffs.append(_diff(run_id, block_number, str(user), str(reserve), "scaledVariableDebt", scaled_debt, rpc_scaled))

        if len(diffs) >= args.max_diffs:
            break

    failed = sum(1 for row in diffs if not row[11])
    if diffs:
        ch.insert(
            "aave_reconstruction_audit_diffs",
            diffs,
            column_names=[
                "run_id",
                "deployment_id",
                "chain_id",
                "block_number",
                "user",
                "reserve",
                "field",
                "reconstructed",
                "rpc",
                "abs_diff",
                "rel_diff",
                "passed",
                "reason",
            ],
        )
    finished = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    ch.insert(
        "aave_reconstruction_audit_runs",
        [[
            run_id,
            AAVE_DEPLOYMENT_ID,
            AAVE_CHAIN_ID,
            block_number,
            len(users),
            sampled_positions,
            failed,
            "PASS" if failed == 0 else "FAIL",
            started,
            finished,
            f"{{\"blockTag\":\"{block_tag}\"}}",
        ]],
        column_names=[
            "run_id",
            "deployment_id",
            "chain_id",
            "block_number",
            "sampled_users",
            "sampled_positions",
            "failed_diffs",
            "status",
            "started_at",
            "finished_at",
            "details",
        ],
    )
    print(f"run_id={run_id} users={len(users)} positions={sampled_positions} failed={failed}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Aave account reconstruction against RPC")
    parser.add_argument("--rpc-url", default=None)
    parser.add_argument("--block-number", type=int, default=None)
    parser.add_argument("--users", type=int, default=600)
    parser.add_argument("--max-diffs", type=int, default=5000)
    parser.add_argument("--run-id", default=None)
    return run_audit(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
