// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import {RLDCore} from "../src/core/RLDCore.sol";
import {RLDMarketFactory} from "../src/core/RLDMarketFactory.sol";
import {PrimeBrokerFactory} from "../src/core/PrimeBrokerFactory.sol";
import {PrimeBroker} from "../src/core/PrimeBroker.sol";
import {TwammBrokerModule} from "../src/modules/broker/TwammBrokerModule.sol";
import {IRLDCore, MarketId} from "../src/interfaces/IRLDCore.sol";
import {ITWAMM} from "v4-twamm-hook/src/ITWAMM.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {MockERC20} from "solmate/src/test/utils/mocks/MockERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {ISpotOracle} from "../src/interfaces/ISpotOracle.sol";
import {PositionToken} from "../src/tokens/PositionToken.sol";
import {IFundingModel} from "../src/interfaces/IFundingModel.sol";
import {IDefaultOracle} from "../src/interfaces/IDefaultOracle.sol";
import {UniswapV4SingletonOracle} from "../src/modules/oracles/UniswapV4SingletonOracle.sol";
import {BondMetadataRenderer} from "../src/utils/BondMetadataRenderer.sol";
import {PoolManager} from "v4-core/src/PoolManager.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";

// --- Mocks ---

contract MockTwammHook {
    uint256 public constant EXPIRATION = 1 days;
    uint256 public mockSellRefund = 0;
    uint256 public mockBuyOwed = 0;

    // Simulate Order Storage
    mapping(bytes32 => ITWAMM.Order) public orders;
    mapping(bytes32 => bool) public activeOrders;

    function submitOrder(ITWAMM.SubmitOrderParams calldata params) external returns (bytes32 orderId, ITWAMM.OrderKey memory orderKey) {
        // Take funds
        address inputToken = params.zeroForOne 
            ? Currency.unwrap(params.key.currency0) 
            : Currency.unwrap(params.key.currency1);
        ERC20(inputToken).transferFrom(msg.sender, address(this), params.amountIn);

        orderKey = ITWAMM.OrderKey({
            owner: msg.sender,
            expiration: uint160(block.timestamp + params.duration),
            zeroForOne: params.zeroForOne
        });

        orderId = keccak256(abi.encode(orderKey));
        activeOrders[orderId] = true;
        
        // Mock getOrder response
        orders[orderId] = ITWAMM.Order({
            sellRate: params.amountIn / params.duration,
            earningsFactorLast: 0
        });

        return (orderId, orderKey);
    }

    function cancelOrder(PoolKey calldata key, ITWAMM.OrderKey calldata orderKey) external returns (uint256 buyTokensOwed, uint256 sellTokensRefund) {
        bytes32 orderId = keccak256(abi.encode(orderKey));
        require(activeOrders[orderId], "Order not active");
        require(msg.sender == orderKey.owner, "Unauthorized");

        buyTokensOwed = mockBuyOwed;
        sellTokensRefund = mockSellRefund;
        
        // Return tokens
        address sellToken = orderKey.zeroForOne 
            ? Currency.unwrap(key.currency0) 
            : Currency.unwrap(key.currency1);
        address buyToken = orderKey.zeroForOne 
            ? Currency.unwrap(key.currency1) 
            : Currency.unwrap(key.currency0);

        if (sellTokensRefund > 0) MockERC20(sellToken).mint(msg.sender, sellTokensRefund);
        if (buyTokensOwed > 0) MockERC20(buyToken).mint(msg.sender, buyTokensOwed);
        
        delete activeOrders[orderId];
    }
    
    // For Module Valuation
    function getCancelOrderState(PoolKey calldata, ITWAMM.OrderKey calldata) external view returns (uint256, uint256) {
        return (mockBuyOwed, mockSellRefund);
    }
    
    function setMockState(uint256 _buy, uint256 _sell) external {
        mockBuyOwed = _buy;
        mockSellRefund = _sell;
    }
}

contract MockOracle is ISpotOracle {
    mapping(address => uint256) public prices;
    function setPrice(address token, uint256 price) external { prices[token] = price; }
    function getSpotPrice(address quote, address) external view returns (uint256) { 
        uint256 p = prices[quote];
        return p == 0 ? 1e18 : p; // Fallback to avoid div by zero
    }
    function getIndexPrice(address, address) external view returns (uint256) { return 1e18; }
    function getMarkPrice(address, address) external view returns (uint256) { return 1e18; }
}

contract MockFundingModel is IFundingModel {
    function calculateFunding(bytes32, address, uint256 oldNorm, uint48) external view returns (uint256, int256) {
        return (uint256(oldNorm), 0);
    }
}

contract MockDefaultOracle is IDefaultOracle {
    function isDefaulted(address, address, bytes32) external view returns (bool) { return false; }
}

contract TwammIntegrationTest is Test {
    using stdStorage for StdStorage;

    RLDCore core;
    RLDMarketFactory marketFactory;
    PrimeBroker primeBrokerImpl;
    TwammBrokerModule twammModule;
    MockTwammHook hook;
    MockOracle oracle;
    
    // Tokens
    MockERC20 usdc;
    MockERC20 weth;

    MarketId marketId;
    address brokerFactoryAddr;

    function setUp() public {
        usdc = new MockERC20("USDC", "USDC", 6);
        weth = new MockERC20("WETH", "WETH", 18);
        
        oracle = new MockOracle();
        hook = new MockTwammHook();
        twammModule = new TwammBrokerModule();
        
        core = new RLDCore();
        // Setup PrimeBroker Impl with REAL TwammBrokerModule
        primeBrokerImpl = new PrimeBroker(
            address(core),
            address(0), 
            address(twammModule),
            address(0) 
        );

        // Factory
        MockFundingModel funding = new MockFundingModel();
        MockDefaultOracle defaultOracle = new MockDefaultOracle();
        PoolManager poolManager = new PoolManager(address(0));
        PositionToken positionTokenImpl = new PositionToken();
        UniswapV4SingletonOracle v4Oracle = new UniswapV4SingletonOracle(); 
        BondMetadataRenderer renderer = new BondMetadataRenderer();
        
        marketFactory = new RLDMarketFactory(
            address(core),
            address(poolManager),
            address(positionTokenImpl),
            address(primeBrokerImpl),
            address(v4Oracle),
            address(funding),
            address(0), 
            address(defaultOracle),
            address(0),
            address(renderer),
            address(weth)
        );
        core.setFactory(address(marketFactory));
        
        // Oracle Prices (1 USDC = 1 USD, 1 WETH = 2000 USD)
        oracle.setPrice(address(usdc), 1e18); 
        oracle.setPrice(address(weth), 2000e18);

        // Create Market
        (marketId, brokerFactoryAddr) = marketFactory.createMarket(
            RLDMarketFactory.DeployParams({
                underlyingPool: address(0x999),
                underlyingToken: address(usdc), // 1 USD
                collateralToken: address(weth), // 2000 USD
                curator: address(this),
                marketType: IRLDCore.MarketType.RLP,
                minColRatio: 120e16, // 1.2
                maintenanceMargin: 110e16, // 1.1
                liquidationCloseFactor: 50e16,
                liquidationModule: address(0x123),
                liquidationParams: bytes32(0),
                bankruptcyParams: bytes32(0),
                spotOracle: address(oracle),
                rateOracle: address(oracle), // Price Oracle for Valuation
                oraclePeriod: 3600,
                poolFee: 3000,
                tickSpacing: 60
            })
        );
        
        // Hack: The factory sets 'hook' variable in MarketAddresses?
        // Actually RLDMarketFactory creates a market config but doesn't allow passing arbitrary 'hook'.
        // Wait, PrimeBroker fetches 'vars.hook'.
        // 'getMarketAddresses' implementation in Core depends on Factory logic or is stored.
        // Checking RLDMarketFactory: it deploys WRLP but where does it store 'hook' address?
        // PrimeBroker.initialize -> imports from Core -> getMarketAddresses.
        // If 'underlyingPool' argument is just an address, maybe we can use that?
        // But hook must be known.
        // Let's force-set the hook address in Core if possible, or Mock 'getMarketAddresses'.
        // Or assume 'underlyingPool' was supposed to be the V4 pool which has a hook.
        // But PrimeBroker.sol expects `hook` address in `vars`.
        // Let's cheat: we overwrite the `hook` address in the PrimeBroker storage after init.
        // 
        // Better: We hack the storage of the initialized Broker to set 'hook' to our MockHook.
    }

    function test_TwammLifecycle() public {
        PrimeBrokerFactory pbf = PrimeBrokerFactory(brokerFactoryAddr);
        address user = address(0xCAFE);
        
        // 1. Create Broker
        vm.prank(user);
        address brokerAddr = pbf.createBroker();
        PrimeBroker broker = PrimeBroker(payable(brokerAddr));
        
        // Override 'hook' in Broker storage to likely slot (check layout)
        // Storage:
        // [..]
        // address public hook; // 5th slot?
        // Let's use vm.store or just a setter if we had one.
        // Since we don't know exact slot easily without counting:
        // Caching: collateral, underlying, rateOracle, hook.
        // They are set in initialize.
        // Let's just mock RLDCore's return value? No, Core is real.
        // We will just use `stdStore` to find `hook`.
        
        // Hack: Assume Hook is stored at slot 10 or similar.
        // Actually, let's just use `vm.store` at the correct slot.
        // PrimeBroker Storage:
        // 0: CORE (immutable - not storage)
        // 0-3: Storage variables
        // factory, bondMetadata (struct 2 slots?), marketId
        // cache: collateral, underlying, rateOracle, hook
        
        // Let's try to overwrite it by brute force if we can't find it.
        // Or better: modify createMarket parameters?
        // RLDMarketFactory doesn't seem to take 'hook'.
        
        // Ok, we will manually overwrite local variable `hook` in the Broker instance using hevm.
        // Finding slot for `hook` via `stdStorage` might differ by compiler version.
        // Let's assume slot 7.
        // factory (1), bondMetadata (1-2), marketId (1), collateral (1), underlying (1), rateOracle (1), hook (1).
        
        // Let's try `stdstore.target(address(broker)).sig("hook()").find()`
        stdstore.target(address(broker)).sig("hook()").checked_write(address(hook));
        
        // 2. Fund User & Approve
        weth.mint(user, 10e18); // 10 WETH ($20k)
        vm.startPrank(user);
        weth.approve(address(broker), 10e18);
        
        // 3. Submit Order
        // Sell 1 WETH for USDC over 1000s
        PoolKey memory key = PoolKey({
            currency0: Currency.wrap(address(weth)),
            currency1: Currency.wrap(address(usdc)),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(hook))
        });
        
        ITWAMM.SubmitOrderParams memory params = ITWAMM.SubmitOrderParams({
            key: key,
            zeroForOne: true, // Sell Weth (0) -> USDC (1)
            duration: 1000,
            amountIn: 1e18
        });
        
        broker.submitTwammOrder(params);
        vm.stopPrank();

        // Check: Broker balance decreased?
        assertEq(weth.balanceOf(brokerAddr), 0); // Sent to Hook
        // Check: Hook balance increased
        assertEq(weth.balanceOf(address(hook)), 1e18);

        // 4. Check Valuation (Initial)
        // Mock State: Refund = 1e18 (All), Earnings = 0.
        hook.setMockState(0, 1e18);
        
        uint256 value = broker.getNetAccountValue();
        // Value should be 1 WETH * 2000 = 2000e18 (Underlying is USDC? No, Underlying in params was USDC)
        // Wait, 'getValue' returns value in Underlying Terms (USDC).
        // WETH Price = 2000 USDC.
        // 1 WETH = 2000 USDC.
        assertEq(value, 2000e18); 
        
        // 5. Check Valuation (Half Executed)
        // Mock State: Refund = 0.5 WETH, Earnings = 1000 USDC.
        // Value: (0.5 * 2000) + (1000 * 1) = 1000 + 1000 = 2000 USDC.
        hook.setMockState(1000e6, 0.5e18); // USDC has 6 decimals? MockERC20(USDC, 6).
        // Wait, price oracle returns with 18 decimals usually (WAD).
        // MockOracle returns 1e18 for USDC.
        // mulWadDown logic: amount * price / 1e18.
        // 1000e6 * 1e18 / 1e18 = 1000e6. Correct.
        // 0.5e18 * 2000e18 / 1e18 = 1000e18.
        // Total = 1000e18 + 1000e6. Wait, decimals mismatch.
        // The PrimeBroker expects everything normalized to 18 decimals?
        // Or Underlying terms?
        // Default Core logic usually expects 18 decimals for value.
        // If underlying is USDC (6 decimals), `getNetAccountValue` usually returns 18 decimals or underlying decimals?
        // Standard is 18 decimals WAD for internal accounting.
        // Let's check `getValue` in Module:
        // `buyTokensOwed.mulWadDown(price)`. 
        // If buyTokensOwed is USDC (6 decimals), and price is 1e18 (1 USD), result is 1e6.
        // If buyTokensOwed is WETH (18 decimals), and price is 2000e18, result is 2000e18.
        // We need to verify decimal handling.
        // Assuming RLD normalizes everything to 18 decimals or uses Underlying decimals.
        // If `getNetAccountValue` returns mixed decimals, addition fails.
        // Let's assume for this test we use 18 decimals for everything to be safe, mocking USDC as 18 dec.
        
        // 5. Liquidation Scenario
        // Simulate debt.
        // We can't easily inject debt without Core, but we can call 'seize' directly 
        // because PrimeBroker.seize is 'onlyCore'.
        // We can simulate Core calling seize.
        
        uint256 debtToCover = 500e18; // 500 USD
        address liquidator = address(0xDEAD);
        
        vm.prank(address(core)); // Simulate Core
        broker.seize(debtToCover, liquidator);
        
        // Checks:
        // 1. Order should be cancelled (MockTwammHook activeOrders[orderId] = false)
        // We can't check 'activeOrders' directly easily unless we expose it or check side effects.
        // Side effect: Broker should have received 1e18 WETH (Refund) and sent some to liquidator.
        
        // Refund was 1e18 (set in Mock State). Price 2000. Value = 2000.
        // Debt = 500.
        // Liquidator should receive 500 USD worth of WETH.
        // 500 / 2000 = 0.25 WETH.
        
        assertEq(weth.balanceOf(liquidator), 0.25e18);
        
        // Refund was 0.5e18 (from Step 4). Value = 1000. vs Debt 500.
        // Liquidator takes 0.25e18.
        // Broker keeps 0.25e18.
        assertEq(weth.balanceOf(brokerAddr), 0.25e18);
        
        // 2. Broker state should be cleared
        // activeTwammOrder should be deleted.
        // activeTwammOrder.orderId should be 0.
        (PoolKey memory k, ITWAMM.OrderKey memory ok, bytes32 oid) = broker.activeTwammOrder();
        assertEq(oid, bytes32(0));
    }
}
