// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {RLDCore} from "../../src/rld/core/RLDCore.sol";
import {IRLDCore, MarketId} from "../../src/shared/interfaces/IRLDCore.sol";
import {IRLDOracle} from "../../src/shared/interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../../src/shared/interfaces/ISpotOracle.sol";

import {IFundingModel} from "../../src/shared/interfaces/IFundingModel.sol";
import {IERC20} from "../../src/shared/interfaces/IERC20.sol";
import {StaticLiquidationModule} from "../../src/rld/modules/liquidation/StaticLiquidationModule.sol";

// --- MOCKS ---

contract MockERC20 is IERC20 {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    uint8 public decimals = 18;

    function totalSupply() external pure returns (uint256) {
        return 1000000e18;
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

contract MockOracle is IRLDOracle, ISpotOracle {
    uint256 public price;
    bool public defaulted;

    function setPrice(uint256 _price) external {
        price = _price;
    }



    // IRLDOracle
    function getIndexPrice(address, address) external view returns (uint256) {
        return price;
    }

    // ISpotOracle
    function getSpotPrice(address, address) external view returns (uint256) {
        return price;
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

contract MockBrokerVerifier {
    function isValidBroker(address) external pure returns (bool) {
        return true;
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
    MockBrokerVerifier verifier;

    MarketId marketId;

    StaticLiquidationModule staticLiq;
    
    // Broker Mock State
    uint256 public mockNav;

    function setUp() public {
        collateral = new MockERC20();
        underlying = new MockERC20();
        positionToken = new MockERC20();
        oracle = new MockOracle();
        funding = new MockFunding();
        staticLiq = new StaticLiquidationModule();
        verifier = new MockBrokerVerifier();

        oracle.setPrice(1e18); // 1:1 price initially

        // Create Market
        core = new RLDCore();
        // Since test manually calls createMarket, we set this test contract as the factory
        core.setFactory(address(this));

        IRLDCore.MarketAddresses memory addresses = IRLDCore.MarketAddresses({
            collateralToken: address(collateral),
            underlyingToken: address(underlying),
            underlyingPool: address(0x1), 
            rateOracle: address(oracle),
            spotOracle: address(oracle),
            markOracle: address(oracle),
            fundingModel: address(funding),
            curator: address(0),

            liquidationModule: address(staticLiq),
            positionToken: address(positionToken)
        });

        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({


            minColRatio: 1.5e18,
            maintenanceMargin: 1.1e18,
            liquidationCloseFactor: 0.5e18,
            fundingPeriod: 30 days,
            liquidationParams: bytes32(uint256(1.05e18)),

            brokerVerifier: address(verifier)
        });

        marketId = core.createMarket(addresses, config);
        
        // Default safe NAV
        mockNav = 10000e18; 
    }
    
    // --- IPrimeBroker Implementation ---
    function getNetAccountValue() external view returns (uint256) {
        return mockNav;
    }
    
    function seize(uint256 value, address recipient) external {
        // Mock seize
    }

    function setMockNav(uint256 _nav) public {
        mockNav = _nav;
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
    }

    function test_ModifyPosition_Deposit() public {
        // Note: Collateral is now managed by PrimeBroker, not tracked in Core
        // This test verifies the lock mechanism works, but collateral tracking is delegated
        // Core only tracks debtPrincipal now
        
        bytes memory data = abi.encode(1, int256(0), int256(0), marketId);
        core.lock(data);

        IRLDCore.Position memory pos = core.getPosition(marketId, address(this));
        assertEq(pos.debtPrincipal, 0);
    }

    function test_ModifyPosition_MintDebt() public {
        collateral.mint(address(this), 200e18); // 200 Collateral
        collateral.approve(address(core), 200e18);

        // Deposit 200, Mint 100 Debt (Ratio 2.0 > 1.1)
        // With Broker Mode, RLD checks `getNetAccountValue()` against Debt Value.
        // Debt = 100. Price = 1.0. Debt Value = 100.
        // NAV = 10000 (Default). 10000 >= 100 * 1.1 (110). OK.
        
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
        // 100 Debt * 1.0 Price * 1.1 Ratio = 110 Required Value
        
        // Set NAV to be LOW to simulate insolvency.
        // We need failing NAV. e.g. 109.
        setMockNav(109e18);

        bytes memory data = abi.encode(1, int256(100e18), int256(100e18), marketId);
        
        vm.expectRevert(abi.encodeWithSelector(IRLDCore.Insolvent.selector, address(this)));
        core.lock(data);
    }

    function test_Revert_Unauthorized() public {
        // Unauthorized Access
        // Expect NotLocked because modifyPosition calls onlyLock modifier first
        vm.expectRevert(IRLDCore.NotLocked.selector); 
        core.modifyPosition(marketId, 10e18, 0);
    }


}
