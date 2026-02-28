// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import {PoolLiquidityManager} from "../src/rld/broker/PoolLiquidityManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {
    IPositionManager
} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";

/// @dev Simple mock ERC20 for testing
contract MockERC20 {
    string public name;
    string public symbol;
    uint8 public constant decimals = 6;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 amount);
    event Approval(
        address indexed owner,
        address indexed spender,
        uint256 amount
    );

    constructor(string memory _name, string memory _symbol) {
        name = _name;
        symbol = _symbol;
    }

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        totalSupply += amount;
        emit Transfer(address(0), to, amount);
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "Insufficient");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }

    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool) {
        if (allowance[from][msg.sender] != type(uint256).max) {
            allowance[from][msg.sender] -= amount;
        }
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
        return true;
    }
}

interface IERC721 {
    function ownerOf(uint256) external view returns (address);
    function balanceOf(address) external view returns (uint256);
}

interface IPermit2 {
    function approve(address, address, uint160, uint48) external;
}

/// @title PoolLiquidityManager — Comprehensive Tests
/// @dev Forks mainnet for canonical V4 infrastructure (POSM, Permit2, PoolManager)
///      Deploys fresh mock tokens + initializes a V4 pool — fully independent of deployer
contract PoolLiquidityManagerTest is Test {
    /* ═══════════════════════════════════════════════════════════════ */
    /*                    CANONICAL MAINNET ADDRESSES                  */
    /* ═══════════════════════════════════════════════════════════════ */

    address constant POSM = 0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e;
    address constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;
    address constant POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;

    PoolLiquidityManager public manager;
    MockERC20 public tokenA;
    MockERC20 public tokenB;
    address public token0; // sorted lower
    address public token1; // sorted higher
    address public hookAddr; // address(0) = no hook

    /* ═══════════════════════════════════════════════════════════════ */
    /*                           SETUP                                */
    /* ═══════════════════════════════════════════════════════════════ */

    function setUp() public {
        // Fork mainnet for canonical V4 contracts
        vm.createSelectFork("http://127.0.0.1:8545");

        // Deploy fresh mock tokens
        tokenA = new MockERC20("TokenA", "TKA");
        tokenB = new MockERC20("TokenB", "TKB");

        // Sort for V4 (currency0 < currency1)
        if (address(tokenA) < address(tokenB)) {
            token0 = address(tokenA);
            token1 = address(tokenB);
        } else {
            token0 = address(tokenB);
            token1 = address(tokenA);
        }

        // No hook for simple testing
        hookAddr = address(0);

        // Initialize a V4 pool (no hook, fee=500, tickSpacing=10)
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(token0),
            currency1: Currency.wrap(token1),
            fee: 500,
            tickSpacing: 10,
            hooks: IHooks(address(0))
        });

        // Initialize at 1:1 price (sqrtPriceX96 for tick 0)
        uint160 sqrtPriceX96 = 79228162514264337593543950336; // tick 0
        IPoolManager(POOL_MANAGER).initialize(poolKey, sqrtPriceX96);

        // Deploy PoolLiquidityManager with our tokens (using tickSpacing=10 match)
        manager = new PoolLiquidityManagerForTest(POSM, token0, token1);

        // Fund the manager
        MockERC20(token0).mint(address(manager), 100_000e6);
        MockERC20(token1).mint(address(manager), 100_000e6);
    }

    /* ═══════════════════════════════════════════════════════════════ */
    /*                    BASIC STATE TESTS                           */
    /* ═══════════════════════════════════════════════════════════════ */

    function test_initialState() public view {
        assertEq(manager.POSM(), POSM);
        assertEq(manager.positionToken(), token0);
        assertEq(manager.collateralToken(), token1);
        assertEq(manager.activeTokenId(), 0);
        assertEq(manager.owner(), address(this));
    }

    function test_tokenBalances() public view {
        assertEq(MockERC20(token0).balanceOf(address(manager)), 100_000e6);
        assertEq(MockERC20(token1).balanceOf(address(manager)), 100_000e6);
    }

    /* ═══════════════════════════════════════════════════════════════ */
    /*                    ADD LIQUIDITY TESTS                         */
    /* ═══════════════════════════════════════════════════════════════ */

    function test_addPoolLiquidity_basic() public {
        int24 tickLower = -100; // aligned to 10
        int24 tickUpper = 100;

        uint256 tokenId = manager.addPoolLiquidity(
            hookAddr,
            tickLower,
            tickUpper,
            1_000_000,
            50_000e6,
            50_000e6
        );

        assertGt(tokenId, 0, "TokenId should be nonzero");
        assertEq(
            IERC721(POSM).ownerOf(tokenId),
            address(manager),
            "Manager should own NFT"
        );
        assertEq(
            manager.activeTokenId(),
            tokenId,
            "Should auto-track first position"
        );
    }

    function test_addPoolLiquidity_multiplePositions() public {
        uint256 tokenId1 = manager.addPoolLiquidity(
            hookAddr,
            -100,
            100,
            500_000,
            50_000e6,
            50_000e6
        );
        assertEq(manager.activeTokenId(), tokenId1);

        uint256 tokenId2 = manager.addPoolLiquidity(
            hookAddr,
            -200,
            200,
            500_000,
            50_000e6,
            50_000e6
        );
        assertNotEq(tokenId1, tokenId2, "Should be different positions");
        assertEq(
            manager.activeTokenId(),
            tokenId1,
            "First position should still be tracked"
        );

        assertEq(IERC721(POSM).ownerOf(tokenId1), address(manager));
        assertEq(IERC721(POSM).ownerOf(tokenId2), address(manager));
    }

    function test_addPoolLiquidity_revertsZeroLiquidity() public {
        vm.expectRevert("Zero liquidity");
        manager.addPoolLiquidity(hookAddr, -100, 100, 0, 50_000e6, 50_000e6);
    }

    function test_addPoolLiquidity_revertsNotOwner() public {
        vm.prank(address(0xdead));
        vm.expectRevert("Not owner");
        manager.addPoolLiquidity(
            hookAddr,
            -100,
            100,
            1_000_000,
            50_000e6,
            50_000e6
        );
    }

    /* ═══════════════════════════════════════════════════════════════ */
    /*                   REMOVE LIQUIDITY TESTS                       */
    /* ═══════════════════════════════════════════════════════════════ */

    function test_removePoolLiquidity_partial() public {
        uint256 tokenId = manager.addPoolLiquidity(
            hookAddr,
            -100,
            100,
            1_000_000,
            50_000e6,
            50_000e6
        );

        uint256 bal0Before = MockERC20(token0).balanceOf(address(manager));
        uint256 bal1Before = MockERC20(token1).balanceOf(address(manager));

        manager.removePoolLiquidity(tokenId, 500_000);

        assertEq(
            IERC721(POSM).ownerOf(tokenId),
            address(manager),
            "Should still own NFT"
        );
        assertEq(manager.activeTokenId(), tokenId, "Should still be tracked");

        uint256 bal0After = MockERC20(token0).balanceOf(address(manager));
        uint256 bal1After = MockERC20(token1).balanceOf(address(manager));
        assertTrue(
            bal0After > bal0Before || bal1After > bal1Before,
            "Should receive tokens back"
        );
    }

    function test_removePoolLiquidity_full() public {
        uint256 tokenId = manager.addPoolLiquidity(
            hookAddr,
            -100,
            100,
            1_000_000,
            50_000e6,
            50_000e6
        );
        assertEq(manager.activeTokenId(), tokenId);

        manager.removePoolLiquidity(tokenId, 1_000_000);
        assertEq(
            manager.activeTokenId(),
            0,
            "Tracking should be cleared after full removal"
        );
    }

    function test_removePoolLiquidity_fullExcess() public {
        uint256 tokenId = manager.addPoolLiquidity(
            hookAddr,
            -100,
            100,
            1_000_000,
            50_000e6,
            50_000e6
        );

        manager.removePoolLiquidity(tokenId, type(uint128).max);
        assertEq(
            manager.activeTokenId(),
            0,
            "Should clear tracking on full removal"
        );
    }

    function test_removePoolLiquidity_nonTrackedPosition() public {
        uint256 tokenId1 = manager.addPoolLiquidity(
            hookAddr,
            -100,
            100,
            500_000,
            50_000e6,
            50_000e6
        );
        uint256 tokenId2 = manager.addPoolLiquidity(
            hookAddr,
            -200,
            200,
            500_000,
            50_000e6,
            50_000e6
        );

        assertEq(manager.activeTokenId(), tokenId1);

        manager.removePoolLiquidity(tokenId2, type(uint128).max);
        assertEq(
            manager.activeTokenId(),
            tokenId1,
            "Tracked position unaffected"
        );
    }

    function test_removePoolLiquidity_revertsNotOwner() public {
        uint256 tokenId = manager.addPoolLiquidity(
            hookAddr,
            -100,
            100,
            1_000_000,
            50_000e6,
            50_000e6
        );

        vm.prank(address(0xdead));
        vm.expectRevert("Not owner");
        manager.removePoolLiquidity(tokenId, 500_000);
    }

    function test_removePoolLiquidity_revertsZeroLiquidity() public {
        uint256 tokenId = manager.addPoolLiquidity(
            hookAddr,
            -100,
            100,
            1_000_000,
            50_000e6,
            50_000e6
        );

        vm.expectRevert("Zero liquidity");
        manager.removePoolLiquidity(tokenId, 0);
    }

    function test_removePoolLiquidity_revertsNotPositionOwner() public {
        vm.expectRevert(); // ownerOf will revert for non-existent tokenId
        manager.removePoolLiquidity(999_999, 500_000);
    }

    /* ═══════════════════════════════════════════════════════════════ */
    /*                  SET ACTIVE POSITION TESTS                     */
    /* ═══════════════════════════════════════════════════════════════ */

    function test_setActiveV4Position_switch() public {
        uint256 tokenId1 = manager.addPoolLiquidity(
            hookAddr,
            -100,
            100,
            500_000,
            50_000e6,
            50_000e6
        );
        uint256 tokenId2 = manager.addPoolLiquidity(
            hookAddr,
            -200,
            200,
            500_000,
            50_000e6,
            50_000e6
        );

        assertEq(manager.activeTokenId(), tokenId1);

        manager.setActiveV4Position(tokenId2);
        assertEq(manager.activeTokenId(), tokenId2);

        manager.setActiveV4Position(tokenId1);
        assertEq(manager.activeTokenId(), tokenId1);
    }

    function test_setActiveV4Position_clear() public {
        uint256 tokenId = manager.addPoolLiquidity(
            hookAddr,
            -100,
            100,
            1_000_000,
            50_000e6,
            50_000e6
        );
        assertEq(manager.activeTokenId(), tokenId);

        manager.setActiveV4Position(0);
        assertEq(manager.activeTokenId(), 0);
    }

    function test_setActiveV4Position_revertsNotOwner() public {
        vm.prank(address(0xdead));
        vm.expectRevert("Not owner");
        manager.setActiveV4Position(0);
    }

    function test_setActiveV4Position_revertsInvalidToken() public {
        vm.expectRevert();
        manager.setActiveV4Position(999_999);
    }

    /* ═══════════════════════════════════════════════════════════════ */
    /*                   FULL LIFECYCLE TEST                          */
    /* ═══════════════════════════════════════════════════════════════ */

    function test_fullLifecycle() public {
        uint256 initialBal0 = MockERC20(token0).balanceOf(address(manager));
        uint256 initialBal1 = MockERC20(token1).balanceOf(address(manager));

        // 1. Add first position (auto-tracked)
        uint256 id1 = manager.addPoolLiquidity(
            hookAddr,
            -50,
            50,
            500_000,
            50_000e6,
            50_000e6
        );
        assertEq(manager.activeTokenId(), id1);

        // 2. Add second position (not auto-tracked)
        uint256 id2 = manager.addPoolLiquidity(
            hookAddr,
            -150,
            150,
            500_000,
            50_000e6,
            50_000e6
        );
        assertEq(manager.activeTokenId(), id1);

        // 3. Switch tracking to position 2
        manager.setActiveV4Position(id2);
        assertEq(manager.activeTokenId(), id2);

        // 4. Partially remove from position 1 (non-tracked)
        manager.removePoolLiquidity(id1, 250_000);
        assertEq(manager.activeTokenId(), id2);

        // 5. Fully remove position 2 (tracked) → tracking cleared
        manager.removePoolLiquidity(id2, type(uint128).max);
        assertEq(manager.activeTokenId(), 0);

        // 6. Switch tracking to remaining position 1
        manager.setActiveV4Position(id1);
        assertEq(manager.activeTokenId(), id1);

        // 7. Fully remove position 1
        manager.removePoolLiquidity(id1, type(uint128).max);
        assertEq(manager.activeTokenId(), 0);

        // 8. Should have roughly recovered tokens (minus rounding)
        uint256 finalBal0 = MockERC20(token0).balanceOf(address(manager));
        uint256 finalBal1 = MockERC20(token1).balanceOf(address(manager));

        assertGt(
            finalBal0,
            (initialBal0 * 90) / 100,
            "Token0 should be mostly recovered"
        );
        assertGt(
            finalBal1,
            (initialBal1 * 90) / 100,
            "Token1 should be mostly recovered"
        );
    }

    /* ═══════════════════════════════════════════════════════════════ */
    /*                      FUZZ TESTS                                */
    /* ═══════════════════════════════════════════════════════════════ */

    /// @dev Fuzz tick ranges — must be aligned to spacing=10, within valid range
    function testFuzz_addLiquidity_ticks(
        int24 tickLower,
        int24 tickUpper
    ) public {
        // Align to spacing 10
        tickLower = int24(bound(int256(tickLower), -887270, 887260));
        tickUpper = int24(bound(int256(tickUpper), -887270, 887270));
        tickLower = (tickLower / 10) * 10;
        tickUpper = (tickUpper / 10) * 10;

        if (tickLower >= tickUpper) return;
        if (tickUpper - tickLower < 20) return;

        try
            manager.addPoolLiquidity(
                hookAddr,
                tickLower,
                tickUpper,
                100_000,
                50_000e6,
                50_000e6
            )
        returns (uint256 tokenId) {
            assertGt(tokenId, 0, "Should mint valid tokenId");
            assertEq(IERC721(POSM).ownerOf(tokenId), address(manager));
        } catch {
            // Some tick ranges may fail (e.g., not enough tokens for wide range)
        }
    }

    /// @dev Fuzz liquidity amounts
    function testFuzz_addRemove_liquidity(
        uint128 addLiquidity,
        uint128 removeLiquidity
    ) public {
        addLiquidity = uint128(bound(uint256(addLiquidity), 1000, 10_000_000));
        removeLiquidity = uint128(
            bound(uint256(removeLiquidity), 1, type(uint128).max)
        );

        uint256 tokenId = manager.addPoolLiquidity(
            hookAddr,
            -100,
            100,
            addLiquidity,
            50_000e6,
            50_000e6
        );
        assertGt(tokenId, 0);

        manager.removePoolLiquidity(tokenId, removeLiquidity);

        if (removeLiquidity >= addLiquidity) {
            assertEq(
                manager.activeTokenId(),
                0,
                "Should clear on full removal"
            );
        } else {
            assertEq(manager.activeTokenId(), tokenId, "Should keep tracking");
            assertEq(IERC721(POSM).ownerOf(tokenId), address(manager));
        }
    }

    /// @dev Fuzz multiple positions and track switching
    function testFuzz_multiplePositions_tracking(uint8 numPositions) public {
        numPositions = uint8(bound(uint256(numPositions), 1, 5));

        uint256[] memory tokenIds = new uint256[](numPositions);

        for (uint8 i = 0; i < numPositions; i++) {
            int24 tickLower = int24(int8(i)) * -50 - 50;
            int24 tickUpper = int24(int8(i)) * 50 + 50;
            tickLower = (tickLower / 10) * 10;
            tickUpper = (tickUpper / 10) * 10;
            if (tickLower >= tickUpper) tickUpper = tickLower + 20;

            tokenIds[i] = manager.addPoolLiquidity(
                hookAddr,
                tickLower,
                tickUpper,
                100_000,
                20_000e6,
                20_000e6
            );
        }

        assertEq(manager.activeTokenId(), tokenIds[0]);

        for (uint8 i = 0; i < numPositions; i++) {
            manager.setActiveV4Position(tokenIds[i]);
            assertEq(manager.activeTokenId(), tokenIds[i]);
        }

        for (uint8 i = 0; i < numPositions; i++) {
            bool isTracked = manager.activeTokenId() == tokenIds[i];
            manager.removePoolLiquidity(tokenIds[i], type(uint128).max);
            if (isTracked) {
                assertEq(
                    manager.activeTokenId(),
                    0,
                    "Should clear tracked position"
                );
            }
        }
    }

    /* ═══════════════════════════════════════════════════════════════ */
    /*                   POOL KEY CONSTRUCTION                        */
    /* ═══════════════════════════════════════════════════════════════ */

    function test_poolKey_tokenSorting() public {
        uint256 tokenId = manager.addPoolLiquidity(
            hookAddr,
            -100,
            100,
            1_000_000,
            50_000e6,
            50_000e6
        );

        (PoolKey memory pk, ) = IPositionManager(POSM).getPoolAndPositionInfo(
            tokenId
        );

        assertTrue(
            Currency.unwrap(pk.currency0) < Currency.unwrap(pk.currency1),
            "Currencies should be sorted"
        );

        address c0 = Currency.unwrap(pk.currency0);
        address c1 = Currency.unwrap(pk.currency1);
        assertTrue(
            (c0 == token0 && c1 == token1) || (c0 == token1 && c1 == token0),
            "Should use our tokens"
        );
    }
}

/// @dev Test variant of PoolLiquidityManager with tickSpacing=10 and no hook
///      Overrides _getPoolKey to match our test pool configuration
contract PoolLiquidityManagerForTest is PoolLiquidityManager {
    constructor(
        address _posm,
        address _positionToken,
        address _collateralToken
    ) PoolLiquidityManager(_posm, _positionToken, _collateralToken) {}

    /// @dev Override to match our test pool (no hook, tickSpacing=10)
    function _getPoolKey(
        address /* twammHook */
    ) internal view override returns (PoolKey memory) {
        (address c0, address c1) = positionToken < collateralToken
            ? (positionToken, collateralToken)
            : (collateralToken, positionToken);

        return
            PoolKey({
                currency0: Currency.wrap(c0),
                currency1: Currency.wrap(c1),
                fee: 500,
                tickSpacing: 10, // matches our test pool
                hooks: IHooks(address(0)) // no hook
            });
    }
}
