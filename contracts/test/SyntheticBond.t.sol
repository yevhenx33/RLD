// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import "../src/core/RLDCore.sol";
import "../src/core/RLDMarketFactory.sol";
import "../src/tokens/WrappedRLP.sol";
import "../src/vaults/SyntheticBond.sol";
import "./RLDCore.t.sol"; // Mocks

// Mock Uniswap V4 PoolManager
contract MockPoolManager is IPoolManager {
    
    function unlock(bytes calldata data) external override returns (bytes memory) {
        return ISaleCallback(msg.sender).unlockCallback(data);
    }

    function swap(
        PoolKey memory key,
        IPoolManager.SwapParams memory params,
        bytes calldata hookData
    ) external override returns (BalanceDelta delta) {
        // Mock Swap: wRLP (Input) -> USDC (Output)
        int128 amount0 = 0;
        int128 amount1 = 0;
        
        if (params.zeroForOne) {
            // Input: wRLP (amountSpecified < 0)
            amount0 = int128(params.amountSpecified); 
            amount1 = int128(-params.amountSpecified); 
        } else {
             amount1 = int128(params.amountSpecified);
             amount0 = int128(-params.amountSpecified);
        }
        
        // Invert packing order to match observed accessor behavior
        // Put amount0 in Upper (High) and amount1 in Lower (Low)
        int256 packed = (int256(amount0) << 128) | int256(uint256(uint128(amount1)));
        delta = BalanceDelta.wrap(packed);
        
        return delta;
    }
    
    // Updated settle() signature
    function settle() external payable override returns (uint256 paid) {
        return 0; // Assume success
    }

    function take(Currency currency, address to, uint256 amount) external override {
        address token = Currency.unwrap(currency);
        (bool success, ) = token.call(abi.encodeWithSignature("mint(address,uint256)", to, amount));
        if (!success) {
            IERC20(token).transfer(to, amount); 
        }
    }

    // Stubs for IPoolManager
    function initialize(PoolKey memory key, uint160 sqrtPriceX96) external override returns (int24 tick) { return 0; }
    
    // modifyLiquidity signature check: 
    // function modifyLiquidity(PoolKey memory key, ModifyLiquidityParams memory params, bytes calldata hookData) external returns (BalanceDelta callerDelta, BalanceDelta feesAccrued);
    function modifyLiquidity(PoolKey memory key, IPoolManager.ModifyLiquidityParams memory params, bytes calldata hookData) external override returns (BalanceDelta, BalanceDelta) { 
        return (BalanceDelta.wrap(0), BalanceDelta.wrap(0)); 
    }
    
    function donate(PoolKey memory key, uint256 amount0, uint256 amount1, bytes calldata hookData) external override returns (BalanceDelta) { return BalanceDelta.wrap(0); }
    function sync(Currency currency) external override {}
    
    // Protocol Fees
    function protocolFeesAccrued(Currency currency) external view override returns (uint256) { return 0; }
    function collectProtocolFees(address recipient, Currency currency, uint256 amount) external override returns (uint256 amountCollected) { return 0; }
    function updateDynamicLPFee(PoolKey memory key, uint24 newDynamicLPFee) external override {}
    function extsload(bytes32 slot) external view override returns (bytes32 value) { return bytes32(0); }
    function extsload(bytes32[] calldata slots) external view override returns (bytes32[] memory values) { return new bytes32[](0); }
    function extsload(bytes32 startSlot, uint256 nSlots) external view override returns (bytes32[] memory values) { return new bytes32[](0); }
    function exttload(bytes32 slot) external view override returns (bytes32 value) { return bytes32(0); }
    function exttload(bytes32[] calldata slots) external view override returns (bytes32[] memory values) { return new bytes32[](0); }
    function setProtocolFee(PoolKey memory key, uint24 newProtocolFee) external override {}
    function setProtocolFeeController(address controller) external override {}
    function protocolFeeController() external view override returns (address) { return address(0); }
    
    // ERC6909 Stubs
    function balanceOf(address owner, uint256 id) external view override returns (uint256 amount) { return 0; }
    function allowance(address owner, address spender, uint256 id) external view override returns (uint256 amount) { return 0; }
    function approve(address spender, uint256 id, uint256 amount) external override returns (bool) { return true; }
    function transfer(address receiver, uint256 id, uint256 amount) external override returns (bool) { return true; }
    function transferFrom(address sender, address receiver, uint256 id, uint256 amount) external override returns (bool) { return true; }
    function mint(address to, uint256 id, uint256 amount) external override {}
    function burn(address from, uint256 id, uint256 amount) external override {}
    function isOperator(address owner, address spender) external view override returns (bool approved) { return true; }
    function setOperator(address operator, bool approved) external override returns (bool) { return true; }
    
    // Other Stubs
    function settleFor(address recipient) external payable override returns (uint256 paid) { return 0; }
    function clear(Currency currency, uint256 amount) external override {}
}

interface ISaleCallback {
    function unlockCallback(bytes calldata data) external returns (bytes memory);
}

contract MockTWAMM {
    function observe(PoolId, uint32[] calldata secondsAgos) external pure returns (int56[] memory tickCumulatives) {
        tickCumulatives = new int56[](secondsAgos.length);
        // Retun 0 for all cumulatives (implies 0 delta -> tick 0 -> price 1.0)
        return tickCumulatives;
    }
}

contract MockAdapter is ILendingAdapter {
    address public asset;
    address public collateral;
    
    constructor(address _asset, address _collateral) {
        asset = _asset;
        collateral = _collateral;
    }
    
    function supply(address token, uint256 amount) external override returns (address, uint256) {
        IERC20(asset).transferFrom(msg.sender, address(this), amount);
        IERC20(collateral).transfer(msg.sender, amount);
        return (collateral, amount);
    }
    
    function withdraw(address token, uint256 amount) external override returns (uint256) {
        IERC20(collateral).transferFrom(msg.sender, address(this), amount);
        IERC20(asset).transfer(msg.sender, amount);
        return amount;
    }
    
    function borrow(address _asset, uint256 amount) external override {}
    function repay(address _asset, uint256 amount) external override {}
    function getDebt(address _asset, address user) external view override returns (uint256) { return 0; }
    
    function getLiquidity(address) external view returns (uint256) { return 0; }
    function getSupplyRate(address) external view returns (uint256) { return 0; }
}

contract SyntheticBondTest is Test {
    RLDCore core;
    RLDMarketFactory factory;
    MockPoolManager pm;
    MockTWAMM twamm;
    
    MockERC20 usdc;
    MockERC20 aUSDC; 
    
    MockOracle oracle;
    MockFunding funding;
    
    address alice = address(0x1);
    address bob = address(0x2);

    function setUp() public {
        usdc = new MockERC20();
        aUSDC = new MockERC20();
        oracle = new MockOracle();
        oracle.setPrice(1e18);
        funding = new MockFunding();
        
        core = new RLDCore();
        pm = new MockPoolManager();
        twamm = new MockTWAMM();
        
        factory = new RLDMarketFactory(
            address(core), 
            address(funding), 
            address(oracle), 
            address(oracle),
            address(0), // markOracle removed
            address(pm), 
            address(twamm)
        );
    }
    
    function test_BondLifecycle() public {
        vm.startPrank(alice);
        (MarketId id, , , , ) = factory.deployMarketV4(
            address(0x123), 
            address(usdc), 
            address(aUSDC), 
            IRLDCore.MarketType.RLP,
            1.2e18, 
            1.1e18,
            address(0x999), // Explicit liquidator
            bytes32(0),
            // Removed: initSqrtPrice
            address(oracle), // spotOracle
            address(oracle), // rateOracle
            3600,
            3000,
            60
        );
        vm.stopPrank();
        
        IRLDCore.MarketAddresses memory addrs = core.getMarketAddresses(id);
        address wRLP = addrs.positionToken;
        
        // PoolKey Setup
        Currency c0 = Currency.wrap(wRLP);
        Currency c1 = Currency.wrap(address(usdc));
        if (c0 > c1) (c0, c1) = (c1, c0);
        
        PoolKey memory key = PoolKey({
            currency0: c0,
            currency1: c1,
            fee: 3000, 
            tickSpacing: 60,
            hooks: IHooks(address(0))
        });
        
        MockAdapter adapterContract = new MockAdapter(address(usdc), address(aUSDC));
        aUSDC.mint(address(adapterContract), 100000e18);
        
        SyntheticBond vault = new SyntheticBond(
            address(core),
            id,
            address(adapterContract),
            address(usdc),
            address(aUSDC),
            wRLP,
            address(pm),
            key
        );
        
        usdc.mint(bob, 1000e18);
        usdc.mint(address(pm), 1000e18); // Liquidity for swap
        
        vm.startPrank(bob);
        usdc.approve(address(vault), 1000e18);
        vault.deposit(1000e18, bob);
        vm.stopPrank();
        
        IRLDCore.Position memory pos = core.getPosition(id, address(vault));
        // Verify
        assertEq(pos.debtPrincipal, 10e18, "Debt mismatch"); 
        assertEq(pos.collateral, 1010e18, "Collateral mismatch"); 
        assertEq(IERC20(wRLP).balanceOf(address(vault)), 0, "Vault still holds wRLP");
        assertEq(usdc.balanceOf(address(vault)), 0, "Vault still holds USDC");
        assertEq(vault.totalShares(), 1000e18);
    }
}
