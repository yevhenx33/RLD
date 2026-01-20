// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {RLDCore} from "../src/core/RLDCore.sol";
import {IRLDCore, MarketId} from "../src/interfaces/IRLDCore.sol";
import {IRLDOracle} from "../src/interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../src/interfaces/ISpotOracle.sol";
import {IDefaultOracle} from "../src/interfaces/IDefaultOracle.sol";
import {IFundingModel} from "../src/interfaces/IFundingModel.sol";
import {IERC20} from "../src/interfaces/IERC20.sol";
import {StaticLiquidationModule} from "../src/modules/liquidation/StaticLiquidationModule.sol";

// --- MOCKS ---

contract MockERC20 is IERC20 {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    uint8 public decimals = 18;

    function totalSupply() external view returns (uint256) {
        return 0; // Mock doesn't track total supply for now
    }
    
    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        if (allowance[from][msg.sender] != type(uint256).max) {
             allowance[from][msg.sender] -= amount;
        }
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function symbol() external pure returns (string memory) {
        return "MOCK";
    }

    function name() external pure returns (string memory) {
        return "Mock Token";
    }
}

contract MockOracle is IRLDOracle, ISpotOracle, IDefaultOracle {
    uint256 public price;
    bool public defaulted;

    function setPrice(uint256 _price) external {
        price = _price;
    }

    function setDefault(bool _defaulted) external {
        defaulted = _defaulted;
    }

    // IRLDOracle
    function getIndexPrice(address, address) external view returns (uint256) {
        return price;
    }

    // ISpotOracle
    function getSpotPrice(address, address) external view returns (uint256) {
        return price;
    }

    // IDefaultOracle
    function isDefaulted(address, address) external view returns (bool) {
        return defaulted;
    }
}

contract MockFunding is IFundingModel {
    function calculateFunding(
        bytes32 /*marketId*/,
        address /*core*/,
        uint256 lastNormFactor,
        uint48 /*lastTimestamp*/
    ) external pure returns (uint256, int256) {
        return (uint256(lastNormFactor), 0);
    }
}

// --- TEST SUITE ---

contract RLDCoreTest is Test {
    RLDCore core;
    MockERC20 collateral;
    MockERC20 underlying;
    MockERC20 positionToken;
    MockOracle oracle;
    MockFunding funding;

    MarketId marketId;

    StaticLiquidationModule staticLiq;

    function setUp() public {
        core = new RLDCore();
        collateral = new MockERC20();
        underlying = new MockERC20();
        positionToken = new MockERC20();
        oracle = new MockOracle();
        funding = new MockFunding();
        staticLiq = new StaticLiquidationModule();

        oracle.setPrice(1e18); // 1:1 price initially

        // Create Market
        IRLDCore.MarketAddresses memory addresses = IRLDCore.MarketAddresses({
            collateralToken: address(collateral),
            underlyingToken: address(underlying),
            underlyingPool: address(0x1), 
            rateOracle: address(oracle),
            spotOracle: address(oracle),
            markOracle: address(oracle),
            fundingModel: address(funding),
            feeHook: address(0),
            hook: address(0),
            defaultOracle: address(oracle),
            liquidationModule: address(staticLiq),
            positionToken: address(positionToken)
        });

        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            marketType: IRLDCore.MarketType.RLP,

            minColRatio: 1.5e18,
            maintenanceMargin: 1.1e18,
            liquidationParams: bytes32(uint256(1.05e18))
        });

        marketId = core.createMarket(addresses, config);
    }

    // Helper for locking
    function lockAcquired(bytes calldata data) external returns (bytes memory) {
        (uint8 action, int256 deltaAdj, int256 deltaDebt, MarketId id) = abi.decode(data, (uint8, int256, int256, MarketId));
        if (action == 1) {
            core.modifyPosition(id, deltaAdj, deltaDebt);
        }
        return "";
    }

    function test_CreateMarket() public view {
        assertTrue(core.isValidMarket(marketId));
        IRLDCore.MarketState memory state = core.getMarketState(marketId);
        assertEq(state.normalizationFactor, 1e18);
        assertEq(state.isSettled, false);
    }

    function test_ModifyPosition_Deposit() public {
        collateral.mint(address(this), 100e18);
        collateral.approve(address(core), 100e18);

        bytes memory data = abi.encode(1, int256(100e18), int256(0), marketId);
        core.lock(data);

        IRLDCore.Position memory pos = core.getPosition(marketId, address(this));
        assertEq(pos.collateral, 100e18);
        assertEq(collateral.balanceOf(address(core)), 100e18);
    }

    function test_ModifyPosition_MintDebt() public {
        collateral.mint(address(this), 200e18); // 200 Collateral
        collateral.approve(address(core), 200e18);

        // Deposit 200, Mint 100 Debt (Ratio 2.0 > 1.1)
        bytes memory data = abi.encode(1, int256(200e18), int256(100e18), marketId);
        core.lock(data);

        IRLDCore.Position memory pos = core.getPosition(marketId, address(this));
        assertEq(pos.debtPrincipal, 100e18);
    }

    function test_Revert_Insolvent() public {
        collateral.mint(address(this), 100e18); 
        collateral.approve(address(core), 100e18);

        // Deposit 100, Mint 100 Debt. 
        // Ratio Requirement 1.1x. 
        // 100 Collateral * 1.0 Price = 100 Value
        // 100 Debt * 1.0 Price * 1.1 Ratio = 110 Required
        // Should Fail.
        bytes memory data = abi.encode(1, int256(100e18), int256(100e18), marketId);
        
        vm.expectRevert("Insolvent");
        core.lock(data);
    }

    function test_SettleMarket() public {
        // 1. Not Defaulted
        vm.expectRevert();
        core.settleMarket(marketId);

        // 2. Default
        oracle.setDefault(true);
        core.settleMarket(marketId);

        IRLDCore.MarketState memory state = core.getMarketState(marketId);
        assertTrue(state.isSettled);

        // 3. Confirm operations locked
        collateral.mint(address(this), 10e18);
        collateral.approve(address(core), 10e18);
        bytes memory data = abi.encode(1, int256(10e18), int256(0), marketId);
        
        vm.expectRevert("Market Settled");
        core.lock(data);
    }


    
    // Updated lockAcquired to handle marketId from data
    // Removed lockAcquiredNew
}
