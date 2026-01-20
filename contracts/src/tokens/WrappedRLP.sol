// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {Owned} from "solmate/src/auth/Owned.sol";
import {MarketId} from "../interfaces/IRLDCore.sol";
import {Initializable} from "@openzeppelin/contracts/proxy/utils/Initializable.sol";

contract WrappedRLP is ERC20, Owned, Initializable {
    MarketId public marketId;
    address public underlying;

    error MarketIdAlreadySet();
    error NotMarket();

    constructor() ERC20("Wrapped RLP Impl", "wRLP-IMPL", 18) Owned(msg.sender) {
        _disableInitializers();
    }

    function initialize(address _underlying) external initializer {
        owner = msg.sender;
        emit OwnershipTransferred(address(0), msg.sender);
        
        underlying = _underlying;
        
        // Auto-set Name and Symbol based on underlying?
        // Cloning copies bytecode, including hardcoded "Wrapped RLP Impl".
        // But Solmate ERC20 uses storage for name/symbol (assigned in constructor).
        // Since constructor didn't run effectively for storage, name/symbol are empty.
        // We MUST set them here.
        string memory underlyingSymbol = ERC20(_underlying).symbol();
        name = string(abi.encodePacked("Wrapped ", underlyingSymbol));
        symbol = string(abi.encodePacked("w", underlyingSymbol));
        // decimals is immutable (18) and shared by clones via bytecode.
    }

    function setMarketId(MarketId _id) external onlyOwner {
        if (MarketId.unwrap(marketId) != bytes32(0)) revert MarketIdAlreadySet();
        marketId = _id;
    }

    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    function burn(address from, uint256 amount) external onlyOwner {
        _burn(from, amount);
    }
}
