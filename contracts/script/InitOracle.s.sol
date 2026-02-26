// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";

interface IJTM {
    function increaseCardinality(bytes32 poolId, uint16 next) external returns (uint16);
    function oracleStates(bytes32 poolId)
        external
        view
        returns (uint16 index, uint16 cardinality, uint16 cardinalityNext);
}

interface IPoolManager {
    function getSlot0(bytes32 poolId) external view returns (uint160, int24, uint24, uint24);
}

/// @notice Initialize the TWAMM oracle that was never set up, then prime it with observations
contract PrimeOracle is Script {
    address constant TWAMM = 0x2d1B11cE8ea5204839458789873da6b0ce182Ac0;
    address constant POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    address constant BROKER_ROUTER = 0xb334795bf50e4943d076Dfb38D8C1A50F9F5a101;
    address constant BROKER = 0xA138073E73ca0Cb58A505F6cecdAb37Be50f4D2E;

    function run() external {
        // Compute poolId = keccak256(abi.encode(currency0, currency1, fee, tickSpacing, hooks))
        bytes32 poolId = keccak256(
            abi.encode(
                address(0x6c8d360090263de9fFdBc17Bccc6969D8a7F2379),
                address(0x905Ad472d7eeB94ed1Fc29D8ff4B53FD4D5a5Eb4),
                uint24(500),
                int24(5),
                address(TWAMM)
            )
        );
        console.log("PoolId:");
        console.logBytes32(poolId);

        uint16 idx;
        uint16 card;
        uint16 cardNext;
        (idx, card, cardNext) = IJTM(TWAMM).oracleStates(poolId);
        console.log("Oracle state - index:", idx, "cardinality:", card);
        console.log("cardinalityNext:", cardNext);

        (, int24 tick,,) = IPoolManager(POOL_MANAGER).getSlot0(poolId);
        console.log("Current tick:", tick);

        uint256 deployerKey = 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80;
        vm.startBroadcast(deployerKey);

        if (card == 0) {
            console.log("Oracle uninitialized! Finding storage slots...");

            // Find the oracleStates mapping slot by scanning
            uint256 oracleStatesSlot = type(uint256).max;
            for (uint256 s = 0; s <= 30; s++) {
                bytes32 storageSlot = keccak256(abi.encode(poolId, s));
                bytes32 value = vm.load(TWAMM, storageSlot);
                if (value != bytes32(0)) {
                    uint16 storedCardNext = uint16(uint256(value) >> 32);
                    if (storedCardNext == cardNext) {
                        oracleStatesSlot = s;
                        console.log("Found oracleStates at base slot:", s);
                        break;
                    }
                }
            }
            require(oracleStatesSlot != type(uint256).max, "Could not find oracleStates slot");

            // The observations mapping is likely at oracleStatesSlot - 1
            // (observations is declared before oracleStates in TWAMM.sol)
            // Let's try a few candidates
            uint256 observationsSlot = oracleStatesSlot - 1;
            console.log("Trying observations at base slot:", observationsSlot);

            // Write observation[0]:
            // Observation { uint32 blockTimestamp, int56 tickCumulative, bool initialized }
            // Storage packing: blockTimestamp at byte offset 0 (4 bytes),
            //                  tickCumulative at byte offset 4 (7 bytes),
            //                  initialized at byte offset 11 (1 byte)
            //
            // For nested mapping: observations[poolId][0]
            // Base = keccak256(abi.encode(poolId, observationsSlot))
            // observations[poolId][0] = keccak256(abi.encode(0, base))
            bytes32 obsBase = keccak256(abi.encode(poolId, observationsSlot));
            bytes32 obsSlot0 = keccak256(abi.encode(uint256(0), obsBase));

            // Use a timestamp 2 hours ago to give room for TWAP window
            uint32 initTimestamp = uint32(block.timestamp) - 7200;
            uint256 obsValue = uint256(initTimestamp) | (uint256(1) << 88); // initialized=true, tickCumulative=0
            vm.store(TWAMM, obsSlot0, bytes32(obsValue));
            console.log("Wrote observation[0] at timestamp:", initTimestamp);

            // Set oracleStates: index=0, cardinality=1, cardinalityNext=30
            bytes32 stateSlot = keccak256(abi.encode(poolId, oracleStatesSlot));
            uint256 newState = uint256(0) | (uint256(1) << 16) | (uint256(cardNext) << 32);
            vm.store(TWAMM, stateSlot, bytes32(newState));
            console.log("Set oracle state: index=0, cardinality=1, cardinalityNext:", cardNext);

            // Verify
            (idx, card, cardNext) = IJTM(TWAMM).oracleStates(poolId);
            console.log("Verified - index:", idx, "cardinality:", card);
            console.log("cardinalityNext:", cardNext);
        }

        vm.stopBroadcast();

        console.log("");
        console.log("=== Oracle initialized. Now run swaps at time intervals to fill the buffer. ===");
        console.log("=== Use the shell script below to prime observations across 1+ hour: ===");
    }
}
