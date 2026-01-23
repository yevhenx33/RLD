// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import {RLDCore} from "../src/rld/core/RLDCore.sol";
import {RLDMarketFactory} from "../src/rld/core/RLDMarketFactory.sol";
import {BrokerVerifier} from "../src/rld/modules/verifier/BrokerVerifier.sol";
import {PrimeBrokerFactory} from "../src/rld/core/PrimeBrokerFactory.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";
import {IRLDCore, MarketId} from "../src/shared/interfaces/IRLDCore.sol";
import {IBrokerVerifier} from "../src/shared/interfaces/IBrokerVerifier.sol";
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
import {ISpotOracle} from "../src/shared/interfaces/ISpotOracle.sol";
import {IRLDOracle} from "../src/shared/interfaces/IRLDOracle.sol";
import {PositionToken} from "../src/rld/tokens/PositionToken.sol";
import {IFundingModel} from "../src/shared/interfaces/IFundingModel.sol";

import {UniswapV4SingletonOracle} from "../src/rld/modules/oracles/UniswapV4SingletonOracle.sol";
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
    
    function setPriceBounds(PoolKey calldata, uint160, uint160) external {}
}

contract AtomicDeploymentTest is Test {
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

    function setUp() public virtual {
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
            address(0) // POSM
        );
        
        v4Oracle = new UniswapV4SingletonOracle(); // No args
        
        marketFactory = new RLDMarketFactory(
            address(core),
            address(poolManager),
            address(positionTokenImpl),
            address(primeBrokerImpl),
            address(v4Oracle),
            address(funding),
            address(0), // No hook for atomic test
            address(renderer),
            30 days
        );
        
        // --- Register Factory ---
        core.setFactory(address(marketFactory));

        // Create Mock Tokens
        underlyingToken = address(new MockERC20("USDC", "USDC", 6));
        collateralToken = address(new MockERC20("aUSDC", "aUSDC", 6));
    }

    function getGlobalDeployParams(
        address _pool,
        address _underlying,
        address _collateral,
        address _curator,
        address _spotOracle,
        address _rateOracle,
        address _liqModule
    ) internal pure returns (RLDMarketFactory.DeployParams memory) {
        return RLDMarketFactory.DeployParams({
            underlyingPool: _pool,
            underlyingToken: _underlying,
            collateralToken: _collateral,
            curator: _curator,
            positionTokenName: "Wrapped RLP Position: aUSDC",
            positionTokenSymbol: "wRLPaUSDC",
            minColRatio: 120e16,
            maintenanceMargin: 110e16,
            liquidationCloseFactor: 50e16,
            liquidationModule: _liqModule,
            liquidationParams: bytes32(0),
            spotOracle: _spotOracle,
            rateOracle: _rateOracle,
            oraclePeriod: 3600,
            poolFee: 3000,
            tickSpacing: 60
        });
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

                positionTokenName: "Wrapped RLP Position: aUSDC",
                positionTokenSymbol: "wRLPaUSDC",

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

                minColRatio: 120e16,
                maintenanceMargin: 110e16,
                liquidationCloseFactor: 50e16,
                liquidationModule: address(0x123),
                positionTokenName: "Wrapped RLP Position: aUSDC",
                positionTokenSymbol: "wRLPaUSDC",
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
        // assertEq(broker.owner(), user); // Replaced by NFT check
        assertEq(pbf.ownerOf(uint256(uint160(brokerAddr))), user);
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
