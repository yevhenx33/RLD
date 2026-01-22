// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import {RLDCore} from "../src/core/RLDCore.sol";
import {RLDMarketFactory} from "../src/core/RLDMarketFactory.sol";
import {BrokerVerifier} from "../src/modules/verifier/BrokerVerifier.sol";
import {PrimeBrokerFactory} from "../src/core/PrimeBrokerFactory.sol";
import {PrimeBroker} from "../src/core/PrimeBroker.sol";
import {IRLDCore, MarketId} from "../src/interfaces/IRLDCore.sol";
import {IBrokerVerifier} from "../src/interfaces/IBrokerVerifier.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {MockERC20} from "solmate/src/test/utils/mocks/MockERC20.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolManager} from "v4-core/src/PoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {BeforeSwapDelta, BeforeSwapDeltaLibrary} from "v4-core/src/types/BeforeSwapDelta.sol";
import {ModifyLiquidityParams, SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {ISpotOracle} from "../src/interfaces/ISpotOracle.sol";
import {IRLDOracle} from "../src/interfaces/IRLDOracle.sol";
import {PositionToken} from "../src/tokens/PositionToken.sol";
import {IFundingModel} from "../src/interfaces/IFundingModel.sol";

import {UniswapV4SingletonOracle} from "../src/modules/oracles/UniswapV4SingletonOracle.sol";

import {BondMetadataRenderer} from "../src/utils/BondMetadataRenderer.sol";

// --- Mocks ---

contract MockOracle is ISpotOracle, IRLDOracle {
    uint256 public price = 1e18;
    function setPrice(uint256 _p) external { price = _p; }
    function getSpotPrice(address, address) external view returns (uint256) { return price; }
    function getIndexPrice(address, address) external view returns (uint256) { return 1e18; }
    function getMarkPrice(address, address) external view returns (uint256) { return price; }
}

contract MockFundingModel is IFundingModel {
    function calculateFunding(bytes32, address, uint256 oldNorm, uint48) external view returns (uint256, int256) {
        return (uint256(oldNorm), 0);
    }
}



contract MockHook is IHooks {
    function beforeInitialize(address, PoolKey calldata, uint160) external pure returns (bytes4) { return IHooks.beforeInitialize.selector; }
    function afterInitialize(address, PoolKey calldata, uint160, int24) external pure returns (bytes4) { return IHooks.afterInitialize.selector; }
    function beforeAddLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, bytes calldata) external pure returns (bytes4) { return IHooks.beforeAddLiquidity.selector; }
    function afterAddLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, BalanceDelta, BalanceDelta, bytes calldata) external pure returns (bytes4, BalanceDelta) { return (IHooks.afterAddLiquidity.selector, BalanceDelta.wrap(0)); }
    function beforeRemoveLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, bytes calldata) external pure returns (bytes4) { return IHooks.beforeRemoveLiquidity.selector; }
    function afterRemoveLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, BalanceDelta, BalanceDelta, bytes calldata) external pure returns (bytes4, BalanceDelta) { return (IHooks.afterRemoveLiquidity.selector, BalanceDelta.wrap(0)); }
    function beforeSwap(address, PoolKey calldata, SwapParams calldata, bytes calldata) external pure returns (bytes4, BeforeSwapDelta, uint24) { return (IHooks.beforeSwap.selector, BeforeSwapDelta.wrap(0), 0); }
    function afterSwap(address, PoolKey calldata, SwapParams calldata, BalanceDelta, bytes calldata) external pure returns (bytes4, int128) { return (IHooks.afterSwap.selector, 0); }
    function beforeDonate(address, PoolKey calldata, uint256, uint256, bytes calldata) external pure returns (bytes4) { return IHooks.beforeDonate.selector; }
    function afterDonate(address, PoolKey calldata, uint256, uint256, bytes calldata) external pure returns (bytes4) { return IHooks.afterDonate.selector; }
}

contract GenericExecutionTest is Test {
    using stdStorage for StdStorage;

    RLDCore core;
    RLDMarketFactory marketFactory;
    PrimeBroker primeBrokerImpl;
    
    PoolManager poolManager;
    PositionToken positionTokenImpl;
    UniswapV4SingletonOracle v4Oracle;
    
    MockOracle oracle;
    MockFundingModel funding;

    MockHook twamm; 
    BondMetadataRenderer renderer;

    address underlyingToken;
    address collateralToken;
    address underlyingPool = address(0x999); 
    address otherToken;

    function setUp() public {
        poolManager = new PoolManager(address(0));
        oracle = new MockOracle();
        funding = new MockFundingModel();

        twamm = new MockHook();
        renderer = new BondMetadataRenderer();
        
        core = new RLDCore();
        positionTokenImpl = new PositionToken();
        
        primeBrokerImpl = new PrimeBroker(
            address(core),
            address(0), 
            address(twamm),
            address(0) 
        );
        
        v4Oracle = new UniswapV4SingletonOracle(); 
        
        marketFactory = new RLDMarketFactory(
            address(core),
            address(poolManager),
            address(positionTokenImpl),
            address(primeBrokerImpl),
            address(v4Oracle),
            address(funding),
            address(0), 
            address(renderer)
        );
        
        core.setFactory(address(marketFactory));

        underlyingToken = address(new MockERC20("USDC", "USDC", 6));
        collateralToken = address(new MockERC20("aUSDC", "aUSDC", 18));
        otherToken = address(new MockERC20("WBTC", "WBTC", 8));
    }

    function test_GenericExecution_Safe() public {
        // 1. Create Market
        (MarketId marketId, address brokerFactoryAddr) = marketFactory.createMarket(
            RLDMarketFactory.DeployParams({
                underlyingPool: underlyingPool,
                underlyingToken: underlyingToken,
                collateralToken: collateralToken,
                curator: address(this),

                minColRatio: 120e16,
                maintenanceMargin: 110e16,
                liquidationCloseFactor: 50e16,
                liquidationModule: address(0x123),
                liquidationParams: bytes32(0),

                spotOracle: address(oracle),
                rateOracle: address(oracle),
                oraclePeriod: 3600,
                poolFee: 3000,
                tickSpacing: 60
            })
        );
        PrimeBrokerFactory pbf = PrimeBrokerFactory(brokerFactoryAddr);
        
        // 2. Create Broker
        address user = address(0xCAFE);
        vm.prank(user);
        address brokerAddr = pbf.createBroker();
        PrimeBroker broker = PrimeBroker(payable(brokerAddr));

        // 3. Fund Broker with Collateral and Other Token
        MockERC20(collateralToken).mint(brokerAddr, 1000e18);
        MockERC20(otherToken).mint(brokerAddr, 10e8);

        // 4. Inject Debt into Core (Simulate User Borrowing)
        // Debt = 500 USD (Principal)
        // Slot calculation for: mapping(MarketId => mapping(address => Position)) public positions;
        // Check RLDCore storage layout:
        // 0: marketAddresses (From RLDStorage)
        // 1: marketConfigs
        // 2: marketStates
        // 3: positions
        // 4: factory (From RLDCore)
        
        bytes32 slot1 = keccak256(abi.encode(MarketId.unwrap(marketId), uint256(3)));
        bytes32 finalSlot = keccak256(abi.encode(brokerAddr, slot1));
        
        // Write DebtPrincipal (High 128 bits)
        vm.store(address(core), finalSlot, bytes32(uint256(500e18) << 128));

        // 5. Execute Safe Action: Transfer Other Token
        vm.prank(user);
        broker.execute(
            otherToken, 
            abi.encodeWithSelector(ERC20.transfer.selector, user, 5e8)
        );
        
        // Verify transfer happened
        assertEq(MockERC20(otherToken).balanceOf(user), 5e8);
        assertEq(MockERC20(otherToken).balanceOf(brokerAddr), 5e8);
    }

    function test_GenericExecution_Unsafe() public {
         // 1. Create Market & Broker
        (MarketId marketId, address brokerFactoryAddr) = marketFactory.createMarket(
            RLDMarketFactory.DeployParams({
                underlyingPool: underlyingPool,
                underlyingToken: underlyingToken,
                collateralToken: collateralToken,
                curator: address(this),

                minColRatio: 120e16,
                maintenanceMargin: 110e16,
                liquidationCloseFactor: 50e16,
                liquidationModule: address(0x123),
                liquidationParams: bytes32(0),

                spotOracle: address(oracle),
                rateOracle: address(oracle),
                oraclePeriod: 3600,
                poolFee: 3000,
                tickSpacing: 60
            })
        );
        PrimeBrokerFactory pbf = PrimeBrokerFactory(brokerFactoryAddr);
        address user = address(0xCAFE);
        vm.prank(user);
        address brokerAddr = pbf.createBroker();
        PrimeBroker broker = PrimeBroker(payable(brokerAddr));

        // 2. Fund Broker with Collateral
        MockERC20(collateralToken).mint(brokerAddr, 1000e18); // 1000 USD Value

        // 3. Inject Debt: 800 USD.
        // Slot calculation (Slot 3)
        bytes32 slot1 = keccak256(abi.encode(MarketId.unwrap(marketId), uint256(3)));
        bytes32 finalSlot = keccak256(abi.encode(brokerAddr, slot1));
        
        vm.store(address(core), finalSlot, bytes32(uint256(800e18) << 128));

        // 4. Execute Unsafe Action: Transfer 500 Collateral Away
        // Remaining Collateral = 500.
        // Limit for 500 Collateral @ 1.1 Margin = 500/1.1 = ~454.
        // Debt = 800. Insolvent.
        
        vm.prank(user);
        vm.expectRevert("Action causes Insolvency");
        broker.execute(
            collateralToken,
            abi.encodeWithSelector(ERC20.transfer.selector, user, 500e18)
        );
    }
}
