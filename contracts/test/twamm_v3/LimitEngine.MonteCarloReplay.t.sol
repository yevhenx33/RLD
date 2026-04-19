// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test, stdJson} from "forge-std/Test.sol";

contract LimitEngineMonteCarloReplayTest is Test {
    using stdJson for string;

    uint256 private constant BUY_BASE = 0;
    uint256 private constant SELL_BASE = 1;
    uint256 private constant MAKER_SUBMIT = 0;
    uint256 private constant TAKER_FLOW = 1;
    uint256 private constant PRICE_SCALE = 1_000_000;
    uint256 private constant EF_SCALE = 1e18;

    struct MakerState {
        uint8 side;
        uint256 tick;
        uint256 pendingInput;
        uint256 activeShares;
        uint256 earningsFactorLast;
        bool exists;
    }

    struct PoolState {
        uint256 remainingInput;
        uint256 totalShares;
        uint256 earningsFactor;
        uint256 distributedOutput;
    }

    function test_replayFixtureMatchesGhostReference() external view {
        string memory raw = vm.readFile("test/twamm_v3/fixtures/limit_engine_replay_case_1.json");

        uint256 makerCount = raw.readUint(".meta.maker_count");
        uint256[] memory kinds = raw.readUintArray(".events.kind");
        uint256[] memory sides = raw.readUintArray(".events.side");
        uint256[] memory amounts = raw.readUintArray(".events.amount");
        int256[] memory makerIds = raw.readIntArray(".events.maker_id");
        uint256[] memory ticks = raw.readUintArray(".events.tick");
        uint256[] memory spotPrices = raw.readUintArray(".events.spot_price");

        uint256 eventCount = kinds.length;
        assertEq(sides.length, eventCount, "sides length mismatch");
        assertEq(amounts.length, eventCount, "amounts length mismatch");
        assertEq(makerIds.length, eventCount, "makerIds length mismatch");
        assertEq(ticks.length, eventCount, "ticks length mismatch");
        assertEq(spotPrices.length, eventCount, "spotPrices length mismatch");

        MakerState[] memory makers = new MakerState[](makerCount);
        PoolState memory buyPool;
        PoolState memory sellPool;
        int256 dustQuote = 0;
        int256 dustBase = 0;

        for (uint256 i = 0; i < eventCount; ++i) {
            uint256 kind = kinds[i];
            uint256 side = sides[i];
            uint256 amount = amounts[i];
            uint256 spot = spotPrices[i];

            if (kind == MAKER_SUBMIT) {
                int256 rawMakerId = makerIds[i];
                assertGe(rawMakerId, 0, "maker id must be >= 0");
                uint256 makerId = uint256(rawMakerId);
                assertLt(makerId, makerCount, "maker id out of range");
                makers[makerId] = MakerState({
                    side: uint8(side),
                    tick: ticks[i],
                    pendingInput: amount,
                    activeShares: 0,
                    earningsFactorLast: 0,
                    exists: true
                });
                continue;
            }

            assertEq(kind, TAKER_FLOW, "unknown event kind");
            if (side == BUY_BASE) {
                _activateEligible(makers, BUY_BASE, spot, buyPool);
                uint256 desiredOutQuote = (amount * spot) / PRICE_SCALE;
                uint256 filledOutQuote = _min(desiredOutQuote, buyPool.remainingInput);
                uint256 inputConsumedBase = _mulDivCeil(filledOutQuote, PRICE_SCALE, spot);

                buyPool.remainingInput -= filledOutQuote;
                if (buyPool.totalShares > 0 && inputConsumedBase > 0) {
                    uint256 deltaEf = (inputConsumedBase * EF_SCALE) / buyPool.totalShares;
                    buyPool.earningsFactor += deltaEf;
                    uint256 distributed = (deltaEf * buyPool.totalShares) / EF_SCALE;
                    buyPool.distributedOutput += distributed;
                    dustBase += int256(inputConsumedBase - distributed);
                }
            } else {
                assertEq(side, SELL_BASE, "unknown side");
                _activateEligible(makers, SELL_BASE, spot, sellPool);
                uint256 desiredOutBase = (amount * PRICE_SCALE) / spot;
                uint256 filledOutBase = _min(desiredOutBase, sellPool.remainingInput);
                uint256 inputConsumedQuote = _mulDivCeil(filledOutBase, spot, PRICE_SCALE);

                sellPool.remainingInput -= filledOutBase;
                if (sellPool.totalShares > 0 && inputConsumedQuote > 0) {
                    uint256 deltaEf = (inputConsumedQuote * EF_SCALE) / sellPool.totalShares;
                    sellPool.earningsFactor += deltaEf;
                    uint256 distributed = (deltaEf * sellPool.totalShares) / EF_SCALE;
                    sellPool.distributedOutput += distributed;
                    dustQuote += int256(inputConsumedQuote - distributed);
                }
            }
        }

        uint256[] memory quoteBalances = new uint256[](makerCount);
        uint256[] memory baseBalances = new uint256[](makerCount);

        // Pending principal.
        for (uint256 i = 0; i < makerCount; ++i) {
            if (!makers[i].exists) continue;
            if (makers[i].side == BUY_BASE) {
                quoteBalances[i] += makers[i].pendingInput;
            } else {
                baseBalances[i] += makers[i].pendingInput;
            }
        }

        // BUY_BASE active pool settlement.
        if (buyPool.totalShares > 0) {
            uint256 runningWeight = 0;
            uint256 runningAlloc = 0;
            uint256 claimsTotal = 0;
            for (uint256 i = 0; i < makerCount; ++i) {
                MakerState memory maker = makers[i];
                if (!maker.exists || maker.side != BUY_BASE || maker.activeShares == 0) continue;
                runningWeight += maker.activeShares;
                uint256 expectedAlloc = (buyPool.remainingInput * runningWeight) / buyPool.totalShares;
                uint256 alloc = expectedAlloc - runningAlloc;
                runningAlloc = expectedAlloc;
                quoteBalances[i] += alloc;

                uint256 claim = (maker.activeShares * (buyPool.earningsFactor - maker.earningsFactorLast)) / EF_SCALE;
                baseBalances[i] += claim;
                claimsTotal += claim;
            }
            dustBase += int256(buyPool.distributedOutput) - int256(claimsTotal);
        }

        // SELL_BASE active pool settlement.
        if (sellPool.totalShares > 0) {
            uint256 runningWeight = 0;
            uint256 runningAlloc = 0;
            uint256 claimsTotal = 0;
            for (uint256 i = 0; i < makerCount; ++i) {
                MakerState memory maker = makers[i];
                if (!maker.exists || maker.side != SELL_BASE || maker.activeShares == 0) continue;
                runningWeight += maker.activeShares;
                uint256 expectedAlloc = (sellPool.remainingInput * runningWeight) / sellPool.totalShares;
                uint256 alloc = expectedAlloc - runningAlloc;
                runningAlloc = expectedAlloc;
                baseBalances[i] += alloc;

                uint256 claim = (maker.activeShares * (sellPool.earningsFactor - maker.earningsFactorLast)) / EF_SCALE;
                quoteBalances[i] += claim;
                claimsTotal += claim;
            }
            dustQuote += int256(sellPool.distributedOutput) - int256(claimsTotal);
        }

        uint256 finalSpot = raw.readUint(".expected.final_spot_price");
        uint256[] memory terminalValues = new uint256[](makerCount);
        for (uint256 i = 0; i < makerCount; ++i) {
            terminalValues[i] = quoteBalances[i] + ((baseBalances[i] * finalSpot) / PRICE_SCALE);
        }

        uint256[] memory expectedQuote = raw.readUintArray(".expected.quote_balances");
        uint256[] memory expectedBase = raw.readUintArray(".expected.base_balances");
        uint256[] memory expectedTerminal = raw.readUintArray(".expected.terminal_value_quote");
        uint256 expectedDustQuote = raw.readUint(".expected.dust_quote");
        uint256 expectedDustBase = raw.readUint(".expected.dust_base");

        assertEq(expectedQuote.length, makerCount, "expected quote length mismatch");
        assertEq(expectedBase.length, makerCount, "expected base length mismatch");
        assertEq(expectedTerminal.length, makerCount, "expected terminal length mismatch");

        for (uint256 i = 0; i < makerCount; ++i) {
            assertEq(quoteBalances[i], expectedQuote[i], "quote mismatch");
            assertEq(baseBalances[i], expectedBase[i], "base mismatch");
            assertEq(terminalValues[i], expectedTerminal[i], "terminal value mismatch");
        }

        assertGe(dustQuote, 0, "dust quote underflow");
        assertGe(dustBase, 0, "dust base underflow");
        assertEq(uint256(dustQuote), expectedDustQuote, "dust quote mismatch");
        assertEq(uint256(dustBase), expectedDustBase, "dust base mismatch");
    }

    function _activateEligible(
        MakerState[] memory makers,
        uint256 side,
        uint256 spot,
        PoolState memory pool
    ) internal pure {
        uint256 makerCount = makers.length;
        for (uint256 i = 0; i < makerCount; ++i) {
            MakerState memory maker = makers[i];
            if (!maker.exists || maker.side != side || maker.pendingInput == 0) continue;
            if (!_isExecutable(side, maker.tick, spot)) continue;
            uint256 shares = maker.pendingInput;
            maker.pendingInput = 0;
            maker.activeShares += shares;
            maker.earningsFactorLast = pool.earningsFactor;
            pool.totalShares += shares;
            pool.remainingInput += shares;
            makers[i] = maker;
        }
    }

    function _isExecutable(uint256 side, uint256 tick, uint256 spot) internal pure returns (bool) {
        if (side == BUY_BASE) return spot <= tick;
        return spot >= tick;
    }

    function _min(uint256 a, uint256 b) internal pure returns (uint256) {
        return a < b ? a : b;
    }

    function _mulDivCeil(uint256 a, uint256 b, uint256 d) internal pure returns (uint256) {
        if (a == 0 || b == 0) return 0;
        return ((a * b) + d - 1) / d;
    }
}
