// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Strings} from "@openzeppelin/contracts/utils/Strings.sol";
import {IPrimeBroker} from "../shared/interfaces/IPrimeBroker.sol";
import {PrimeBroker} from "../rld/broker/PrimeBroker.sol";

/// @title BondMetadataRenderer
/// @notice Generates dynamic on-chain SVG metadata for RLD Smart Bond NFTs.
contract BondMetadataRenderer {
    using Strings for uint256;
    using Strings for address;

    function render(uint256 tokenId, address brokerAddr) external view returns (string memory) {
        // TokenID is the broker address
        PrimeBroker broker = PrimeBroker(payable(brokerAddr));
        
        // Fetch Metadata
        IPrimeBroker.BondMetadata memory meta = broker.getBondMetadata();
        
        // Determine Visuals
        string memory bondTypeStr = meta.bondType == IPrimeBroker.BondType.YIELD ? "FIXED YIELD" : "FIXED RATE";
        string memory color = meta.bondType == IPrimeBroker.BondType.YIELD ? "#10B981" : "#EF4444"; // Green for Yield, Red for Debt
        
        // Format Values
        string memory principalStr = string.concat(meta.principal.toString(), " WEI"); // Simplified for now
        string memory rateStr = string.concat(meta.rate.toString(), " bps");
        string memory maturityStr = meta.maturityDate.toString();

        // Generate SVG
        string memory svg = string.concat(
            '<svg xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMinYMin meet" viewBox="0 0 350 350">',
            '<style>.base { fill: white; font-family: sans-serif; font-size: 14px; }</style>',
            '<rect width="100%" height="100%" fill="black" />',
            '<text x="50%" y="40%" class="base" text-anchor="middle" font-weight="bold" font-size="20">', bondTypeStr, '</text>',
            '<text x="50%" y="50%" class="base" text-anchor="middle">', principalStr, '</text>',
            '<text x="50%" y="60%" class="base" text-anchor="middle">', rateStr, '</text>',
            '<text x="50%" y="70%" class="base" text-anchor="middle">Mat: ', maturityStr, '</text>',
            '<rect x="0" y="0" width="100%" height="10" fill="', color, '" />',
            '</svg>'
        );

        // Encode to Base64
        string memory json = string.concat(
            '{"name": "RLD Bond #', tokenId.toString(), '",',
            '"description": "RLD Smart Broker Position",',
            '"image": "data:image/svg+xml;base64,', encode(bytes(svg)), '",',
            '"attributes": [',
            '{"trait_type": "Type", "value": "', bondTypeStr, '"},',
            '{"trait_type": "Rate", "value": ', meta.rate.toString(), '},',
            '{"trait_type": "Maturity", "value": ', maturityStr, '}',
            ']}'
        );

        return string.concat('data:application/json;base64,', encode(bytes(json)));
    }

    // Internal Base64 Encoding (Credit: Solady / OpenZeppelin)
    string internal constant TABLE_ENCODE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

    function encode(bytes memory data) internal pure returns (string memory result) {
        if (data.length == 0) return "";
        string memory table = TABLE_ENCODE;
        uint256 encodedLen = 4 * ((data.length + 2) / 3);
        result = new string(encodedLen + 32);

        assembly {
            mstore(result, encodedLen)
            let tablePtr := add(table, 1)
            let dataPtr := data
            let endPtr := add(dataPtr, mload(data))
            let resultPtr := add(result, 32)

            for {} lt(dataPtr, endPtr) {} {
                dataPtr := add(dataPtr, 3)
                let input := mload(dataPtr)
                let out := mload(add(tablePtr, and(shr(18, input), 0x3F)))
                out := shl(8, out)
                out := add(out, and(mload(add(tablePtr, and(shr(12, input), 0x3F))), 0xFF))
                out := shl(8, out)
                out := add(out, and(mload(add(tablePtr, and(shr(6, input), 0x3F))), 0xFF))
                out := shl(8, out)
                out := add(out, and(mload(add(tablePtr, and(input, 0x3F))), 0xFF))
                out := shl(224, out)
                mstore(resultPtr, out)
                resultPtr := add(resultPtr, 4)
            }

            switch mod(mload(data), 3)
            case 1 { mstore(sub(resultPtr, 2), shl(240, 0x3d3d)) }
            case 2 { mstore(sub(resultPtr, 1), shl(248, 0x3d)) }
        }
    }
}
