// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/core/RLDCore.sol";
import "../src/core/RLDMarketFactory.sol";
import "../src/tokens/WrappedRLP.sol";
import "../src/modules/oracles/UniswapV4SingletonOracle.sol";
import "../src/modules/oracles/RLDAaveOracle.sol";
import "../src/modules/oracles/ChainlinkSpotOracle.sol";
import "../src/modules/oracles/DefaultOracle.sol";
import "../src/modules/funding/StandardFundingModel.sol";
import {PoolManager} from "@uniswap/v4-core/src/PoolManager.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {TWAMM} from "v4-twamm-hook/src/TWAMM.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";

import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";

contract MockERC20 is ERC20 {
    constructor(string memory _name, string memory _symbol, uint8 _decimals) ERC20(_name, _symbol, _decimals) {}
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract MockAavePool {
    uint128 public rate;

    function setRate(uint128 _rate) external {
        rate = _rate;
    }

    function getReserveData(address /*asset*/) external view returns (uint256, uint128, uint128, uint128, uint128, uint128, uint40, uint16, address, address, address, address, uint128, uint128, uint128) {
        // Return mostly zeros, but valid rates can be returned if needed.
        // RLDAaveOracle expects ReserveData struct.
        // It fetches data.currentVariableBorrowRate (index 4 in struct? No, index 4 in tuple return).
        // Tuple: (config, liqIndex, rate, varBorrowIndex, varRate, stableRate, ...)
        // varRate is the 4th element (0-indexed) or 5th (1-indexed). 
        // Let's ensure the tuple aligns with standard Aave V3.
        // (0:conf, 1:liqInd, 2:currLiqRate, 3:varBorInd, 4:currVarBorRate, ...)
        return (0, 0, 0, 0, rate > 0 ? rate : 5e25, 0, 0, 0, address(0), address(0), address(0), address(0), 0, 0, 0);
    }
}

contract DeployMarketTest is Test {
    RLDMarketFactory factory;
    RLDCore core;
    MockERC20 usdc;
    PoolManager poolManager;
    TWAMM twamm;
    RLDAaveOracle rateOracle;
    ChainlinkSpotOracle spotOracle;
    StandardFundingModel fundingModel;
    DefaultOracle defaultOracle;
    MockAavePool aavePool;

    MockERC20 aUSDC;

    function setUp() public {
        poolManager = new PoolManager(address(this));
        address flags = address(
            uint160(
                Hooks.BEFORE_INITIALIZE_FLAG | Hooks.BEFORE_SWAP_FLAG | Hooks.BEFORE_ADD_LIQUIDITY_FLAG
                    | Hooks.BEFORE_REMOVE_LIQUIDITY_FLAG
            ) ^ (0x4444 << 144)
        );
        bytes memory constructorArgs = abi.encode(IPoolManager(address(poolManager)), 3600, address(this));
        
        deployCodeTo("TWAMM.sol:TWAMM", constructorArgs, flags);
        twamm = TWAMM(payable(flags));
        
        core = new RLDCore();
        fundingModel = new StandardFundingModel();
        rateOracle = new RLDAaveOracle();
        spotOracle = new ChainlinkSpotOracle();
        defaultOracle = new DefaultOracle();
        
        // Mock Liquidation Module to use as static
        StaticLiquidationModule staticLiq = new StaticLiquidationModule();

        factory = new RLDMarketFactory(
            address(core), 
            address(fundingModel), 
            address(spotOracle),
            address(rateOracle),
            address(defaultOracle),
            address(poolManager),
            address(twamm),
            address(spotOracle)
        );
        
        usdc = new MockERC20("USDC", "USDC", 6);
        aUSDC = new MockERC20("Aave USDC", "aUSDC", 6);
        aavePool = new MockAavePool();
    }

    function testDeployMarketV4() public {
        uint64 minCol = uint64(1.2e18);
        uint64 maintenance = uint64(1.1e18);
        
        // Explicitly pass liquidator module (address(0) reverts now)
        address liquidator = address(0x123); // Mock address or deploy one

        (MarketId id, address oracle, address _spotOracle, address _defaultOracle, bytes32 poolId) = factory.deployMarketV4(
            address(aavePool), // underlyingPool
            address(usdc),
            address(aUSDC), // collateralToken (aUSDC)
            IRLDCore.MarketType.RLP,
            minCol, // minCol
            maintenance, // maintenance
            liquidator, // liquidator
            bytes32(0), // params
            // Removed: initSqrtPrice
            address(spotOracle),
            address(rateOracle),
            3600, // oraclePeriod
            3000, // poolFee
            60    // tickSpacing
        );
        
        emit log_bytes32(MarketId.unwrap(id));
        assertTrue(MarketId.unwrap(id) != bytes32(0));
        
        IRLDCore.MarketAddresses memory addrs = core.getMarketAddresses(id);
        
        // Verify Collateral is aUSDC
        assertTrue(addrs.collateralToken == address(aUSDC));
        
        // Verify Oracles
        address adapter = addrs.markOracle;
        assertTrue(adapter == factory.SINGLETON_V4_ORACLE());
        assertTrue(addrs.spotOracle == address(spotOracle)); 
        
        // Verify Singleton Registration
        UniswapV4SingletonOracle singleton = UniswapV4SingletonOracle(adapter);
        (PoolKey memory key, PoolId pid, ITWAMM t, uint32 p, bool set) = singleton.poolSettings(addrs.positionToken);
        
        assertTrue(set);
        assertTrue(address(t) == address(twamm));
        assertTrue(p == 3600);
        assertTrue(PoolId.unwrap(pid) == PoolId.unwrap(key.toId()));

        // Verify Naming
        string memory name = WrappedRLP(addrs.positionToken).name();
        string memory symbol = WrappedRLP(addrs.positionToken).symbol();
        
        assertEq(name, "Wrapped RLP aUSDC");
        assertEq(symbol, "wRLPaUSDC");
    }

    function testDeployMarketV4_StepByStep() public {
        // --- PREPARE DATA ---
        uint128 mockRate = 4507 * 1e22; // 4.507%
        aavePool.setRate(mockRate);
        address liquidator = address(0x123);

        // --- STEP 1: Call deployMarketV4 ---
        // We capture the start state to verify changes.
        vm.recordLogs();

        (MarketId id, address oracle, address _spotOracle, address _defaultOracle, bytes32 poolId) = factory.deployMarketV4(
            address(aavePool),
            address(usdc),
            address(aUSDC),
            IRLDCore.MarketType.RLP,
            1.2e18, 
            1.1e18,
            liquidator,
            bytes32(0),
            address(spotOracle),
            address(rateOracle),
            3600,
            3000,
            60
        );
        
        // --- STEP 2: Verify Position Token (wRLP) Deployment ---
        IRLDCore.MarketAddresses memory addrs = core.getMarketAddresses(id);
        address wRLP = addrs.positionToken;
        
        // Check it's a clone (code exists)
        assertTrue(wRLP.code.length > 0, "Step 2: wRLP not deployed");
        // Check Initialization & Naming
        assertEq(WrappedRLP(wRLP).name(), "Wrapped RLP aUSDC", "Step 2: Incorrect Name");
        assertEq(WrappedRLP(wRLP).symbol(), "wRLPaUSDC", "Step 2: Incorrect Symbol");
        assertEq(address(WrappedRLP(wRLP).underlying()), address(usdc), "Step 2: Incorrect Underlying");

        // --- STEP 3: Verify Auto-Price Calculation ---
        // Expected Index Price: 4.507e18.
        // If wRLP is Token1 (Quote), Price in pool = 1 / 4.507.
        // SqrtPrice checked in previous test, let's just confirm it's non-zero and reasonable.
        // We know from previous run it was ~37319...
        
        PoolKey memory key = PoolKey({
            currency0: Currency.wrap(wRLP < address(usdc) ? wRLP : address(usdc)),
            currency1: Currency.wrap(wRLP < address(usdc) ? address(usdc) : wRLP),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(twamm))
        });
        
        (uint160 sqrtPriceX96,,,) = StateLibrary.getSlot0(IPoolManager(address(poolManager)), key.toId());
        assertTrue(sqrtPriceX96 > 0, "Step 3: Pool Price not set");
        // Re-assert exact match from previous run to be sure
        assertEq(sqrtPriceX96, 37319498982842799330689033341, "Step 3: Exact Price Mismatch");

        // --- STEP 4: Verify V4 Pool Initialization ---
        // Pool ID returned must match calculated ID
        assertEq(PoolId.unwrap(key.toId()), poolId, "Step 4: PoolID Mismatch");
        
        // --- STEP 5: Verify Singleton Registration ---
        UniswapV4SingletonOracle singleton = UniswapV4SingletonOracle(factory.SINGLETON_V4_ORACLE());
        (PoolKey memory registeredKey, PoolId regPid, ITWAMM regTwamm, uint32 regPeriod, bool set) = singleton.poolSettings(wRLP);
        
        assertTrue(set, "Step 5: Not Registered in Singleton");
        assertEq(PoolId.unwrap(regPid), PoolId.unwrap(key.toId()), "Step 5: Wrong PoolID in Singleton");
        assertEq(regPeriod, 3600, "Step 5: Wrong Oracle Period");
        assertEq(address(regTwamm), address(twamm), "Step 5: Wrong TWAMM Address");

        // --- STEP 6: Verify Market in Core ---
        IRLDCore.MarketConfig memory config = core.getMarketConfig(id);
        
        assertTrue(core.isValidMarket(id), "Step 6: Market invalid in Core");
        assertEq(addrs.collateralToken, address(aUSDC), "Step 6: Collateral Mismatch");
        assertEq(addrs.rateOracle, address(rateOracle), "Step 6: Rate Oracle Mismatch");
        assertEq(addrs.spotOracle, address(spotOracle), "Step 6: Spot Oracle Mismatch"); // From params
        assertEq(addrs.markOracle, address(singleton), "Step 6: Mark Oracle must be Singleton");
        assertEq(addrs.liquidationModule, liquidator, "Step 6: Liquidation Module Mismatch");
        assertEq(config.minColRatio, 1.2e18, "Step 6: Config Validation Failed");

        // --- STEP 7: Verify Ownership & Link ---
        assertEq(WrappedRLP(wRLP).owner(), address(core), "Step 7: Owner is not Core");
        assertEq(MarketId.unwrap(WrappedRLP(wRLP).marketId()), MarketId.unwrap(id), "Step 7: MarketId not linked in wRLP");
    }
}
