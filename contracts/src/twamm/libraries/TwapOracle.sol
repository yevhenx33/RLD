// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @title TwapOracle
/// @notice simplified Oracle library for tracking tick cumulatives (TWAP)
library TwapOracle {
    struct Observation {
        uint32 blockTimestamp;
        int56 tickCumulative;
        bool initialized;
    }

    struct State {
        uint16 index;
        uint16 cardinality;
        uint16 cardinalityNext;
    }

    /// @notice Initialize the oracle array by writing the first slot
    function initialize(
        mapping(uint256 => Observation) storage self, 
        State storage state,
        uint32 time
    ) internal {
        self[0] = Observation({
            blockTimestamp: time,
            tickCumulative: 0,
            initialized: true
        });
        state.index = 0;
        state.cardinality = 1;
        state.cardinalityNext = 1;
    }

    /// @notice Writes an oracle observation to the array
    function write(
        mapping(uint256 => Observation) storage self,
        State storage state,
        uint32 blockTimestamp,
        int24 tick
    ) internal returns (uint16 indexUpdated, uint16 cardinalityUpdated) {
        uint16 index = state.index;
        uint16 cardinality = state.cardinality;
        uint16 cardinalityNext = state.cardinalityNext;

        Observation memory last = self[index];

        // early return if we've already written an observation this block
        if (last.blockTimestamp == blockTimestamp) return (index, cardinality);

        // if the conditions are right, we can bump the cardinality
        if (cardinalityNext > cardinality && index == (cardinality - 1)) {
            cardinalityUpdated = cardinalityNext;
            state.cardinality = cardinalityNext;
        } else {
            cardinalityUpdated = cardinality;
        }

        indexUpdated = (index + 1) % cardinalityUpdated;
        state.index = indexUpdated;

        self[indexUpdated] = transform(last, blockTimestamp, tick);
    }

    /// @notice Transforms a previous observation into a new observation
    function transform(
        Observation memory last,
        uint32 blockTimestamp,
        int24 tick
    ) private pure returns (Observation memory) {
        uint32 delta = blockTimestamp - last.blockTimestamp;
        return Observation({
            blockTimestamp: blockTimestamp,
            tickCumulative: last.tickCumulative + int56(tick) * int56(uint56(delta)),
            initialized: true
        });
    }

    /// @notice Increases the cardinality of the oracle array
    function grow(
        mapping(uint256 => Observation) storage self,
        State storage state,
        uint16 next
    ) internal returns (uint16) {
        uint16 current = state.cardinalityNext;
        if (next <= current) return current;
        
        // no-op for actual storage as mappings don't need initialization like arrays
        state.cardinalityNext = next;
        return next;
    }

    /// @notice Fetches observations for given secondsAgos
    function observe(
        mapping(uint256 => Observation) storage self,
        State storage state,
        uint32 time,
        uint32[] memory secondsAgos,
        int24 tick
    ) internal view returns (int56[] memory tickCumulatives) {
        uint16 cardinality = state.cardinality;
        require(cardinality > 0, "I");

        tickCumulatives = new int56[](secondsAgos.length);
        for (uint256 i = 0; i < secondsAgos.length; i++) {
            tickCumulatives[i] = observeSingle(
                self,
                time,
                secondsAgos[i],
                tick,
                state.index,
                cardinality
            );
        }
    }

    function observeSingle(
        mapping(uint256 => Observation) storage self,
        uint32 time,
        uint32 secondsAgo,
        int24 tick,
        uint16 index,
        uint16 cardinality
    ) internal view returns (int56 tickCumulative) {
        if (secondsAgo == 0) {
            Observation memory last = self[index];
            if (last.blockTimestamp != time) last = transform(last, time, tick);
            return last.tickCumulative;
        }

        uint32 target = time - secondsAgo;

        (Observation memory beforeOrAt, Observation memory atOrAfter) =
            getSurroundingObservations(self, time, target, tick, index, cardinality);

        if (target == beforeOrAt.blockTimestamp) {
            return beforeOrAt.tickCumulative;
        } else if (target == atOrAfter.blockTimestamp) {
            return atOrAfter.tickCumulative;
        } else {
            uint32 observationTimeDelta = atOrAfter.blockTimestamp - beforeOrAt.blockTimestamp;
            uint32 targetDelta = target - beforeOrAt.blockTimestamp;
            return beforeOrAt.tickCumulative +
                ((atOrAfter.tickCumulative - beforeOrAt.tickCumulative) / int56(uint56(observationTimeDelta))) *
                int56(uint56(targetDelta));
        }
    }

    function getSurroundingObservations(
        mapping(uint256 => Observation) storage self,
        uint32 time,
        uint32 target,
        int24 tick,
        uint16 index,
        uint16 cardinality
    ) private view returns (Observation memory beforeOrAt, Observation memory atOrAfter) {
        beforeOrAt = self[index];

        if (lte(time, beforeOrAt.blockTimestamp, target)) {
            if (beforeOrAt.blockTimestamp == target) {
                return (beforeOrAt, atOrAfter);
            } else {
                return (beforeOrAt, transform(beforeOrAt, target, tick));
            }
        }

        beforeOrAt = self[(index + 1) % cardinality];
        if (!beforeOrAt.initialized) beforeOrAt = self[0];

        require(lte(time, beforeOrAt.blockTimestamp, target), "OLD");

        return binarySearch(self, time, target, index, cardinality);
    }

    function binarySearch(
        mapping(uint256 => Observation) storage self,
        uint32 time,
        uint32 target,
        uint16 index,
        uint16 cardinality
    ) private view returns (Observation memory beforeOrAt, Observation memory atOrAfter) {
        uint256 l = (index + 1) % cardinality; 
        uint256 r = l + cardinality - 1; 
        uint256 i;
        while (true) {
            i = (l + r) / 2;

            beforeOrAt = self[i % cardinality];

            if (!beforeOrAt.initialized) {
                l = i + 1;
                continue;
            }

            atOrAfter = self[(i + 1) % cardinality];

            bool targetAtOrAfter = lte(time, beforeOrAt.blockTimestamp, target);

            if (targetAtOrAfter && lte(time, target, atOrAfter.blockTimestamp)) break;

            if (!targetAtOrAfter) r = i - 1;
            else l = i + 1;
        }
    }

    function lte(uint32 time, uint32 a, uint32 b) private pure returns (bool) {
        if (a <= time && b <= time) return a <= b;
        uint256 aAdjusted = a > time ? a : a + 2**32;
        uint256 bAdjusted = b > time ? b : b + 2**32;
        return aAdjusted <= bAdjusted;
    }
}
