// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import "../../lib/solady/test/utils/mocks/MockERC20.sol";

/// @title V4 NFT Transfer Attack Test
/// @notice Verifies that transferring V4 LP NFT out doesn't allow over-leverage
contract V4NFTTransferAttackTest is Test {
    MockBrokerWithNFT broker;
    MockERC721 positionManager;
    MockERC20 USDC;
    
    address user = address(0x1);
    address attacker = address(0x666);
    
    function setUp() public {
        USDC = new MockERC20("USDC", "USDC", 6);
        positionManager = new MockERC721();
        broker = new MockBrokerWithNFT(address(positionManager), address(USDC));
        
        // Setup: Broker has NFT #1 (worth 10,000 USDC)
        positionManager.mint(address(broker), 1);
        broker.setActiveV4Position(1);
    }
    
    function test_NAVDropsWhenNFTTransferredOut() public {
        // Initial NAV with NFT
        uint256 navBefore = broker.getNetAccountValue();
        assertEq(navBefore, 10_000e6); // NFT worth 10k
        
        // Transfer NFT out
        vm.prank(address(broker));
        positionManager.transferFrom(address(broker), attacker, 1);
        
        // NAV should drop to 0 (NFT no longer counted)
        uint256 navAfter = broker.getNetAccountValue();
        assertEq(navAfter, 0);
    }
    
    function test_PreventOverLeverageAfterNFTTransfer() public {
        // Scenario: User tries to keep debt after transferring NFT
        
        // Step 1: NAV = 10,000 (from NFT)
        uint256 nav = broker.getNetAccountValue();
        assertEq(nav, 10_000e6);
        
        // Step 2: Simulate borrowing (we'll just set debt manually)
        uint256 debt = 6_000e6; // $6,000 debt
        broker.setDebt(debt);
        
        // Step 3: Check solvency BEFORE transfer
        bool solventBefore = broker.checkSolvency(nav, debt);
        assertTrue(solventBefore); // 10,000 >= 6,000 × 1.5 ✅
        
        // Step 4: Transfer NFT out
        vm.prank(address(broker));
        positionManager.transferFrom(address(broker), attacker, 1);
        
        // Step 5: Check solvency AFTER transfer
        uint256 navAfter = broker.getNetAccountValue();
        bool solventAfter = broker.checkSolvency(navAfter, debt);
        
        assertEq(navAfter, 0); // NFT no longer counted
        assertFalse(solventAfter); // 0 < 6,000 × 1.5 ❌ INSOLVENT
    }
    
    function test_NFTStillCountedIfOwned() public {
        // Verify NFT is counted when broker owns it
        uint256 nav = broker.getNetAccountValue();
        assertEq(nav, 10_000e6);
        
        // Verify ownership
        assertEq(positionManager.ownerOf(1), address(broker));
    }
}

/* ============================================ */
/*              MOCK CONTRACTS                 */
/* ============================================ */

contract MockBrokerWithNFT {
    address public POSM;
    address public collateralToken;
    uint256 public activeTokenId;
    uint256 public debt;
    
    constructor(address _posm, address _collateral) {
        POSM = _posm;
        collateralToken = _collateral;
    }
    
    function setActiveV4Position(uint256 tokenId) external {
        require(IERC721(POSM).ownerOf(tokenId) == address(this), "Not owner");
        activeTokenId = tokenId;
    }
    
    function setDebt(uint256 _debt) external {
        debt = _debt;
    }
    
    function getNetAccountValue() external view returns (uint256 totalValue) {
        // Cash balance
        totalValue += IERC20(collateralToken).balanceOf(address(this));
        
        // V4 LP position (with ownership check)
        if (activeTokenId != 0) {
            // SECURITY FIX: Only count if still owned
            if (IERC721(POSM).ownerOf(activeTokenId) == address(this)) {
                totalValue += 10_000e6; // Mock: NFT worth 10k
            }
        }
    }
    
    function checkSolvency(uint256 totalAssets, uint256 debtValue) external pure returns (bool) {
        if (debtValue == 0) return true;
        if (totalAssets < debtValue) return false;
        
        uint256 netWorth = totalAssets - debtValue;
        uint256 minRatio = 1.5e18;
        uint256 marginReq = minRatio - 1e18; // 0.5e18
        
        return netWorth >= (debtValue * marginReq) / 1e18;
    }
}

contract MockERC721 {
    mapping(uint256 => address) public ownerOf;
    
    function mint(address to, uint256 tokenId) external {
        ownerOf[tokenId] = to;
    }
    
    function transferFrom(address from, address to, uint256 tokenId) external {
        require(ownerOf[tokenId] == from, "Not owner");
        ownerOf[tokenId] = to;
    }
}

interface IERC721 {
    function ownerOf(uint256 tokenId) external view returns (address);
    function transferFrom(address from, address to, uint256 tokenId) external;
}

interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
}
