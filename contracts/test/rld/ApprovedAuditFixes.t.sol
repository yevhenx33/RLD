// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";

import {PrimeBroker} from "../../src/rld/broker/PrimeBroker.sol";
import {StandardFundingModel} from "../../src/rld/modules/funding/StandardFundingModel.sol";
import {DutchLiquidationModule} from "../../src/rld/modules/liquidation/DutchLiquidationModule.sol";
import {UniswapV4BrokerModule} from "../../src/rld/modules/broker/UniswapV4BrokerModule.sol";
import {AaveAdapter} from "../../src/rld/modules/adapters/AaveAdapter.sol";
import {BrokerRouter} from "../../src/periphery/BrokerRouter.sol";
import {BondFactory} from "../../src/periphery/BondFactory.sol";
import {PeripheryGhostLib} from "../../src/periphery/lib/PeripheryGhostLib.sol";
import {WrappedAToken} from "../../src/shared/wrappers/WrappedAToken.sol";
import {IRLDCore, MarketId} from "../../src/shared/interfaces/IRLDCore.sol";
import {IPrimeBroker} from "../../src/shared/interfaces/IPrimeBroker.sol";
import {ILiquidationModule} from "../../src/shared/interfaces/ILiquidationModule.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {Position} from "v4-core/src/libraries/Position.sol";

import "../dex/mocks/MockERC20.sol";

contract MockAuditFactory {
    address public owner;

    constructor(address owner_) {
        owner = owner_;
    }

    function ownerOf(uint256) external view returns (address) {
        return owner;
    }
}

contract MockAuditCore {
    IRLDCore.MarketAddresses public addresses;
    IRLDCore.MarketConfig public config;
    bool public solvent = true;

    function setAddresses(IRLDCore.MarketAddresses memory addresses_) external {
        addresses = addresses_;
    }

    function setConfig(IRLDCore.MarketConfig memory config_) external {
        config = config_;
    }

    function setSolvent(bool solvent_) external {
        solvent = solvent_;
    }

    function getMarketAddresses(MarketId) external view returns (IRLDCore.MarketAddresses memory) {
        return addresses;
    }

    function getMarketConfig(MarketId) external view returns (IRLDCore.MarketConfig memory) {
        return config;
    }

    function isSolvent(MarketId, address) external view returns (bool) {
        return solvent;
    }
}

contract MockAuditTwapEngine {
    address public orderOwner;
    uint256 public sellRate = 1;
    uint256 public claimAmount = 1;

    function setOrderOwner(address owner_) external {
        orderOwner = owner_;
    }

    function streamOrders(bytes32, bytes32)
        external
        view
        returns (
            address owner,
            uint256 orderSellRate,
            uint256 earningsFactorLast,
            uint256 startEpoch,
            uint256 expiration,
            bool zeroForOne
        )
    {
        return (orderOwner, sellRate, 0, 0, block.timestamp - 1, true);
    }

    function claimTokens(bytes32, bytes32) external view returns (uint256) {
        return claimAmount;
    }
}

contract MockAuditOracle {
    uint256 public price = 1e18;

    function setPrice(uint256 price_) external {
        price = price_;
    }

    function getSpotPrice(address, address) external view returns (uint256) {
        return price;
    }

    function getIndexPrice(address, address) external view returns (uint256) {
        return price;
    }
}

contract BrokerRouterHarness is BrokerRouter {
    constructor()
        BrokerRouter(
            address(0x1000),
            address(0x2000),
            MarketConfig({
                brokerFactory: address(0x3000),
                marketId: MarketId.wrap(bytes32("MARKET")),
                collateralToken: address(0x4000),
                positionToken: address(0x5000),
                underlyingToken: address(0x6000),
                depositAdapter: address(0x7000)
            })
        )
    {}

    function exposedValidate(PoolKey calldata poolKey, address collateral, address position) external pure {
        _validatePoolKey(poolKey, collateral, position);
    }
}

contract BondFactoryHarness is BondFactory {
    constructor()
        BondFactory(
            address(0x1000), address(0x2000), address(0x3000), address(0x4000), address(0x5000)
        )
    {}

    function exposedValidate(PoolKey calldata poolKey, address tokenA, address tokenB) external pure {
        PeripheryGhostLib.validatePoolKey(poolKey, tokenA, tokenB);
    }
}

contract UniswapV4BrokerModuleHarness is UniswapV4BrokerModule {
    function exposedPositionId(address positionManager, int24 tickLower, int24 tickUpper, uint256 tokenId)
        external
        pure
        returns (bytes32)
    {
        return _positionId(positionManager, tickLower, tickUpper, tokenId);
    }
}

contract MockAuditAavePool {
    MockERC20 public immutable asset;
    MockERC20 public immutable aToken;
    MockERC20 public immutable vDebtToken;

    constructor(MockERC20 asset_, MockERC20 aToken_, MockERC20 vDebtToken_) {
        asset = asset_;
        aToken = aToken_;
        vDebtToken = vDebtToken_;
    }

    function supply(address, uint256 amount, address onBehalfOf, uint16) external {
        asset.transferFrom(msg.sender, address(this), amount);
        aToken.mint(onBehalfOf, amount);
    }

    function withdraw(address, uint256 amount, address to) external returns (uint256) {
        aToken.transferFrom(msg.sender, address(this), amount);
        asset.transfer(to, amount);
        return amount;
    }

    function borrow(address, uint256 amount, uint256, uint16, address onBehalfOf) external {
        vDebtToken.mint(onBehalfOf, amount);
        asset.mint(msg.sender, amount);
    }

    function repay(address, uint256 amount, uint256, address) external returns (uint256) {
        uint256 repaid = amount / 2;
        asset.transferFrom(msg.sender, address(this), repaid);
        return repaid;
    }

    function getReserveData(address)
        external
        view
        returns (uint256, uint128, uint128, uint128, uint128, uint128, uint40, uint16, address, address, address)
    {
        return (0, 0, 0, 0, 0, 0, 0, 0, address(aToken), address(0), address(vDebtToken));
    }
}

contract ApprovedAuditFixesTest is Test {
    using Clones for address;

    address constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;
    MarketId constant MARKET_ID = MarketId.wrap(bytes32(uint256(1)));

    function test_claimExpiredTwammOrderRevertsIfClaimLeavesBrokerInsolvent() external {
        (PrimeBroker broker, MockAuditCore core, MockAuditTwapEngine twap) = _deployBrokerHarness();
        bytes32 orderId = bytes32(uint256(123));

        twap.setOrderOwner(address(broker));
        broker.setActiveTwammOrder(
            address(twap), IPrimeBroker.TwammOrderInfo({marketId: bytes32("TWAP"), orderId: orderId})
        );

        core.setSolvent(false);
        vm.expectRevert(PrimeBroker.Insolvent.selector);
        broker.claimExpiredTwammOrder();
    }

    function test_claimExpiredTwammOrderWithIdRevertsIfClaimLeavesBrokerInsolvent() external {
        (PrimeBroker broker, MockAuditCore core, MockAuditTwapEngine twap) = _deployBrokerHarness();
        bytes32 orderId = bytes32(uint256(456));

        twap.setOrderOwner(address(broker));
        core.setSolvent(false);

        vm.expectRevert(PrimeBroker.Insolvent.selector);
        broker.claimExpiredTwammOrderWithId(address(twap), bytes32("TWAP"), orderId);
    }

    function test_standardFundingModelRevertsBeforeNormalizationFactorCollapsesToZero() external {
        StandardFundingModel model = new StandardFundingModel();
        MockAuditCore core = new MockAuditCore();
        MockAuditOracle markOracle = new MockAuditOracle();
        MockAuditOracle indexOracle = new MockAuditOracle();
        markOracle.setPrice(1_000e18);
        indexOracle.setPrice(1e18);

        IRLDCore.MarketAddresses memory addresses = _marketAddresses(address(0), address(0), address(0));
        addresses.markOracle = address(markOracle);
        addresses.rateOracle = address(indexOracle);
        core.setAddresses(addresses);

        IRLDCore.MarketConfig memory config;
        config.fundingPeriod = 1 days;
        core.setConfig(config);

        vm.warp(365 days + 1);
        vm.expectRevert(StandardFundingModel.InvalidExponentialResult.selector);
        model.calculateFunding(MarketId.unwrap(MARKET_ID), address(core), 1e18, 1);
    }

    function test_brokerRouterRejectsHookedPoolKeys() external {
        BrokerRouterHarness router = new BrokerRouterHarness();
        (address tokenA, address tokenB) = _orderedTokens();
        PoolKey memory key = _poolKey(tokenA, tokenB, address(0xBEEF));

        vm.expectRevert(BrokerRouter.UnexpectedHook.selector);
        router.exposedValidate(key, tokenA, tokenB);
    }

    function test_bondFactoryRejectsHookedPoolKeys() external {
        BondFactoryHarness factory = new BondFactoryHarness();
        (address tokenA, address tokenB) = _orderedTokens();
        PoolKey memory key = _poolKey(tokenA, tokenB, address(0xBEEF));

        vm.expectRevert(PeripheryGhostLib.UnexpectedHook.selector);
        factory.exposedValidate(key, tokenA, tokenB);
    }

    function test_dutchLiquidationBonusUsesNetWorthMarginHealth() external {
        DutchLiquidationModule module = new DutchLiquidationModule();
        IRLDCore.MarketConfig memory config;
        config.maintenanceMargin = uint64(1.1e18);

        ILiquidationModule.PriceData memory priceData =
            ILiquidationModule.PriceData({indexPrice: 1e18, spotPrice: 1e18, normalizationFactor: 1e18});
        bytes32 params = bytes32(uint256(100) | (uint256(500) << 16) | (uint256(200) << 32));

        (, uint256 seizeAmount) = module.calculateSeizeAmount(
            100e18,
            5e18, // net worth is half the 10e18 maintenance margin requirement
            100e18,
            priceData,
            config,
            params
        );

        assertEq(seizeAmount, 105e18, "bonus should cap at max discount from net-worth health");
    }

    function test_v4BrokerModuleUsesPositionManagerOwnerAndTokenIdSaltForPositionKey() external {
        UniswapV4BrokerModuleHarness module = new UniswapV4BrokerModuleHarness();
        address positionManager = address(0xCAFE);
        int24 tickLower = -120;
        int24 tickUpper = 120;
        uint256 tokenId = 42;

        bytes32 actual = module.exposedPositionId(positionManager, tickLower, tickUpper, tokenId);
        bytes32 expected = Position.calculatePositionKey(positionManager, tickLower, tickUpper, bytes32(tokenId));

        assertEq(actual, expected);
    }

    function test_wrappedATokenLocksInitialLiquidityAndStillMintsAfterDonation() external {
        MockERC20 aToken = new MockERC20("Aave USDC", "aUSDC", 6);
        WrappedAToken wrapper = new WrappedAToken(address(aToken), "Wrapped aUSDC", "waUSDC");

        address first = address(0xA11CE);
        address victim = address(0xB0B);
        aToken.mint(first, 100e6);
        aToken.mint(victim, 1e6);
        aToken.mint(address(this), 1e6);

        vm.prank(first);
        aToken.approve(address(wrapper), 100e6);
        vm.prank(first);
        uint256 firstShares = wrapper.wrap(100e6);
        assertEq(firstShares, 100e6 - wrapper.minimumLiquidity());
        assertEq(wrapper.balanceOf(address(0)), wrapper.minimumLiquidity());

        aToken.transfer(address(wrapper), 1e6);

        vm.prank(victim);
        aToken.approve(address(wrapper), 1e6);
        vm.prank(victim);
        uint256 victimShares = wrapper.wrap(1e6);
        assertGt(victimShares, 0, "victim deposit should not round to zero after donation");
    }

    function test_aaveAdapterUsesStandardCallCustodySemantics() external {
        MockERC20 asset = new MockERC20("USDC", "USDC", 6);
        MockERC20 aToken = new MockERC20("Aave USDC", "aUSDC", 6);
        MockERC20 vDebt = new MockERC20("Variable Debt USDC", "vdUSDC", 6);
        MockAuditAavePool pool = new MockAuditAavePool(asset, aToken, vDebt);
        AaveAdapter adapter = new AaveAdapter(address(pool));
        address user = address(0xA11CE);

        asset.mint(user, 300e6);
        vm.startPrank(user);
        asset.approve(address(adapter), type(uint256).max);

        (address collateralToken, uint256 collateralAmount) = adapter.supply(address(asset), 100e6);
        assertEq(collateralToken, address(aToken));
        assertEq(collateralAmount, 100e6);
        assertEq(aToken.balanceOf(user), 100e6);

        aToken.approve(address(adapter), 40e6);
        uint256 withdrawn = adapter.withdraw(address(asset), 40e6);
        assertEq(withdrawn, 40e6);

        uint256 beforeBorrow = asset.balanceOf(user);
        adapter.borrow(address(asset), 25e6);
        assertEq(asset.balanceOf(user), beforeBorrow + 25e6);
        assertEq(vDebt.balanceOf(user), 25e6);

        uint256 beforeRepay = asset.balanceOf(user);
        adapter.repay(address(asset), 20e6);
        assertEq(asset.balanceOf(user), beforeRepay - 10e6, "mock repays half and adapter refunds remainder");
        vm.stopPrank();
    }

    function _deployBrokerHarness()
        internal
        returns (PrimeBroker broker, MockAuditCore core, MockAuditTwapEngine twap)
    {
        vm.etch(PERMIT2, hex"00");
        MockERC20 collateral = new MockERC20("Collateral", "COL", 18);
        MockERC20 position = new MockERC20("Position", "POS", 18);
        core = new MockAuditCore();
        MockAuditFactory factory = new MockAuditFactory(address(this));
        twap = new MockAuditTwapEngine();

        core.setAddresses(_marketAddresses(address(collateral), address(position), address(0)));

        PrimeBroker brokerImpl = new PrimeBroker(address(0), address(0), address(0));
        broker = PrimeBroker(payable(address(brokerImpl).clone()));
        address[] memory operators = new address[](0);
        broker.initialize(MARKET_ID, address(factory), address(core), operators);
    }

    function _marketAddresses(address collateral, address position, address settlementModule)
        internal
        pure
        returns (IRLDCore.MarketAddresses memory addresses)
    {
        addresses = IRLDCore.MarketAddresses({
            collateralToken: collateral,
            underlyingToken: collateral,
            underlyingPool: address(0x1001),
            rateOracle: address(0x1002),
            spotOracle: address(0),
            markOracle: address(0x1003),
            fundingModel: address(0x1004),
            curator: address(0x1005),
            liquidationModule: address(0x1006),
            positionToken: position,
            settlementModule: settlementModule
        });
    }

    function _orderedTokens() internal pure returns (address tokenA, address tokenB) {
        tokenA = address(0x100);
        tokenB = address(0x200);
    }

    function _poolKey(address tokenA, address tokenB, address hooks) internal pure returns (PoolKey memory) {
        return PoolKey({
            currency0: Currency.wrap(tokenA),
            currency1: Currency.wrap(tokenB),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(hooks)
        });
    }
}

