// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import {RLDCore} from "../src/core/RLDCore.sol";
import {RLDMarketFactory} from "../src/core/RLDMarketFactory.sol";
import {BrokerVerifier} from "../src/modules/verifier/BrokerVerifier.sol";
import {PrimeBrokerFactory} from "../src/vaults/PrimeBrokerFactory.sol";
import {PrimeBroker} from "../src/vaults/PrimeBroker.sol";
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
import {ISpotOracle} from "../src/interfaces/ISpotOracle.sol";
import {IRLDOracle} from "../src/interfaces/IRLDOracle.sol";
import {WrappedRLP} from "../src/tokens/WrappedRLP.sol";
import {IFundingModel} from "../src/interfaces/IFundingModel.sol";
import {IDefaultOracle} from "../src/interfaces/IDefaultOracle.sol";
import {UniswapV4SingletonOracle} from "../src/modules/oracles/UniswapV4SingletonOracle.sol";



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

contract MockDefaultOracle is IDefaultOracle {
    function isDefaulted(address, address) external view returns (bool) { return false; }
}

contract MockHook is IHooks {
    function beforeInitialize(address, PoolKey calldata, uint160) external pure returns (bytes4) { return IHooks.beforeInitialize.selector; }
    function afterInitialize(address, PoolKey calldata, uint160, int24) external pure returns (bytes4) { return IHooks.afterInitialize.selector; }
    function beforeAddLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, bytes calldata) external pure returns (bytes4) { return IHooks.beforeAddLiquidity.selector; }
    function afterAddLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, BalanceDelta, BalanceDelta, bytes calldata) external pure returns (bytes4, BalanceDelta) { return (IHooks.afterAddLiquidity.selector, BalanceDelta.wrap(0)); }
    function beforeRemoveLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, bytes calldata) external pure returns (bytes4) { return IHooks.beforeRemoveLiquidity.selector; }
    function afterRemoveLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, BalanceDelta, BalanceDelta, bytes calldata) external pure returns (bytes4, BalanceDelta) { return (IHooks.afterRemoveLiquidity.selector, BalanceDelta.wrap(0)); }
    function beforeSwap(address, PoolKey calldata, IPoolManager.SwapParams calldata, bytes calldata) external pure returns (bytes4, BeforeSwapDelta, uint24) { return (IHooks.beforeSwap.selector, BeforeSwapDelta.wrap(0), 0); }
    function afterSwap(address, PoolKey calldata, IPoolManager.SwapParams calldata, BalanceDelta, bytes calldata) external pure returns (bytes4, int128) { return (IHooks.afterSwap.selector, 0); }
    function beforeDonate(address, PoolKey calldata, uint256, uint256, bytes calldata) external pure returns (bytes4) { return IHooks.beforeDonate.selector; }
    function afterDonate(address, PoolKey calldata, uint256, uint256, bytes calldata) external pure returns (bytes4) { return IHooks.afterDonate.selector; }
}

contract AtomicDeploymentTest is Test {
    RLDCore core;
    RLDMarketFactory marketFactory;
    PrimeBroker primeBrokerImpl;
    
    PoolManager poolManager;
    WrappedRLP wRLPImpl;
    UniswapV4SingletonOracle v4Oracle;
    
    MockOracle oracle;
    MockFundingModel funding;
    MockDefaultOracle defaultOracle;
    MockHook twamm; 

    address underlyingToken;
    address collateralToken;
    address underlyingPool = address(0x999); 

    function setUp() public {
        poolManager = new PoolManager(address(0));
        oracle = new MockOracle();
        funding = new MockFundingModel();
        defaultOracle = new MockDefaultOracle();
        twamm = new MockHook();
        
        core = new RLDCore();
        wRLPImpl = new WrappedRLP();
        
        primeBrokerImpl = new PrimeBroker(
            address(core),
            address(0), 
            address(twamm),
            address(0) // POSM
        );
        
        v4Oracle = new UniswapV4SingletonOracle(); // No args
        
        marketFactory = new RLDMarketFactory(
            address(core),
            address(poolManager),
            address(wRLPImpl),
            address(primeBrokerImpl),
            address(v4Oracle),
            address(funding),
            address(0), 
            address(defaultOracle),
            address(twamm)
        );

        underlyingToken = address(new MockERC20("USDC", "USDC", 6));
        collateralToken = address(new MockERC20("aUSDC", "aUSDC", 6));
    }

    function test_AtomicDeployment_Succeeds() public {
        bytes32 liqParams = bytes32(0);
        
        vm.prank(address(marketFactory)); // Just to be safe, though not needed
        
        (MarketId marketId, address brokerFactory) = marketFactory.createMarket(
            RLDMarketFactory.DeployParams({
                underlyingPool: underlyingPool,
                underlyingToken: underlyingToken,
                collateralToken: collateralToken,
                curator: address(this),
                marketType: IRLDCore.MarketType.RLP,
                minColRatio: 120e16,
                maintenanceMargin: 110e16,
                liquidationCloseFactor: 50e16,
                liquidationModule: address(0x123),
                liquidationParams: liqParams,
                spotOracle: address(oracle),
                rateOracle: address(oracle),
                oraclePeriod: 3600,
                poolFee: 3000,
                tickSpacing: 60
            })
        );
        
        // 1. Verify Market Created
        assertTrue(MarketId.unwrap(marketId) != bytes32(0), "Market ID zero");
        IRLDCore.MarketAddresses memory addrs = core.getMarketAddresses(marketId);
        assertEq(addrs.collateralToken, collateralToken, "Col Token mismatch");
        
        // 2. Verify Broker Factory Deployed
        assertTrue(brokerFactory != address(0), "Broker Factory zero");
        
        // 3. Verify Broker Verify Integration
        IRLDCore.MarketConfig memory config = core.getMarketConfig(marketId);
        address verifier = config.brokerVerifier;
        assertTrue(verifier != address(0), "Verifier zero");
        
        // 4. Verify Verifier points to Factory
        BrokerVerifier bv = BrokerVerifier(verifier);
        assertEq(address(bv.FACTORY()), brokerFactory, "Verifier Factory mismatch");
        
        // 5. Verify Factory points to Correct MarketId
        PrimeBrokerFactory pbf = PrimeBrokerFactory(brokerFactory);
        assertEq(MarketId.unwrap(pbf.MARKET_ID()), MarketId.unwrap(marketId), "Factory MarketID mismatch");
    }

    function test_CreateBroker_And_Interact() public {
         (MarketId marketId, address brokerFactoryAddr) = marketFactory.createMarket(
            RLDMarketFactory.DeployParams({
                underlyingPool: underlyingPool,
                underlyingToken: underlyingToken,
                collateralToken: collateralToken,
                curator: address(this),
                marketType: IRLDCore.MarketType.RLP,
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
        
        // Create Broker
        address user = address(0xCAFE);
        vm.prank(user);
        address brokerAddr = pbf.createBroker();
        
        assertTrue(brokerAddr != address(0));
        
        PrimeBroker broker = PrimeBroker(payable(brokerAddr));
        
        // Check Owner & MarketId
        assertEq(broker.owner(), user);
        assertEq(MarketId.unwrap(broker.marketId()), MarketId.unwrap(marketId));
        
        // Verify Solvency Check (Thin Broker dynamic data fetch)
        // We will call `isSolvent` on Core. 
        // Core will revert if it tries to access empty addresses for Collateral etc.
        // Since we deployed properly, Core has addresses.
        // `isSolvent` calls `broker.getNetAccountValue()` -> accesses Core -> accesses addresses
        
        // Mock balance for broker to avoid 0 interaction
        MockERC20(collateralToken).mint(brokerAddr, 1000e6);
        
        // Core calls verifier -> verifier calls factory -> returns true
        // Then Core calls broker.getNetAccountValue()
        
        bool solvent = core.isSolvent(marketId, brokerAddr);
        assertTrue(solvent, "Broker should be solvent");
    }
}
