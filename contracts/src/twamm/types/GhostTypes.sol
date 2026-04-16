// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

// GhostTypes — Shared data structures for the Ghost Matching Engine
// All structs and constants used across Ghost Engine modules.
// Designed for extensibility: new order types can extend without modifying core.

// Constants

uint256 constant RATE_SCALER = 1e18;
uint256 constant MAX_EPOCHS_PER_TX = 24;

// ─── Ghost Pool (Core State) ──────────────────────────────────────────────────

struct GhostPoolState {
    uint256 streamGhostT0;
    uint256 limitGhostT0;
    uint256 streamGhostT1;
    uint256 limitGhostT1;
    uint256 totalActiveLimitT0;
    uint256 totalActiveLimitT1;
    uint256 lastUpdateTime;
    uint256 epochInterval;
    uint160 twap;
}

// ─── Stream Module ────────────────────────────────────────────────────────────

struct StreamPool {
    uint256 sellRateCurrent;
    uint256 earningsFactorCurrent;
    // Mappings are stored inline via the module contract
}

struct StreamOrder {
    address owner;
    uint256 sellRate;
    uint256 earningsFactorLast;
    uint256 startEpoch;
    uint256 expiration;
    bool zeroForOne;
}

// ─── Limit Module ─────────────────────────────────────────────────────────────

struct LimitBucket {
    uint256 totalDeposit;
    uint256 totalOriginal;
    uint256 accumulator;
    uint256 t1Surplus;
    bool activated;
}

struct LimitOrder {
    address owner;
    uint256 original;
    uint256 remaining;
    uint256 startAccumulator;
}

// ─── Fill Result ──────────────────────────────────────────────────────────────

struct FillResult {
    uint256 fillOut;
    uint256 fillIn;
    bool filled;
}

// ─── Netting Result ───────────────────────────────────────────────────────────

struct NettingResult {
    uint256 matchedT0;
    uint256 matchedT1;
    uint256 streamShareT0;
    uint256 limitShareT0;
    uint256 streamShareT1;
    uint256 limitShareT1;
    bool netted;
}
