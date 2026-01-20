// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/core/RLDCore.sol";
import "../src/core/RLDMarketFactory.sol";
import "../src/tokens/WrappedRLP.sol";
import "../src/modules/oracles/UniswapV4OracleAdapter.sol";
import "../src/modules/oracles/RLDAaveOracle.sol";
import "../src/modules/oracles/ChainlinkSpotOracle.sol";
import "../src/modules/oracles/DefaultOracle.sol";
import "../src/modules/funding/StandardFundingModel.sol";
import {PoolManager} from "@uniswap/v4-core/src/PoolManager.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {TWAMM} from "v4-twamm-hook/src/TWAMM.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";

contract MockERC20 is ERC20 {
    constructor(string memory _name, string memory _symbol, uint8 _decimals) ERC20(_name, _symbol, _decimals) {}
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract DeployMarketTest is Test {
    RLDMarketFactory factory;
    RLDCore core;
    MockERC20 usdc;
    PoolManager poolManager;
    TWAMM twamm;

    function setUp() public {
poolManager = new PoolManager(address(this));
        address flags = address(
            uint160(
                Hooks.BEFORE_INITIALIZE_FLAG | Hooks.BEFORE_SWAP_FLAG | Hooks.BEFORE_ADD_LIQUIDITY_FLAG
                    | Hooks.BEFORE_REMOVE_LIQUIDITY_FLAG
            ) ^ (0x4444 << 144)
        );
        bytes memory constructorArgs = abi.encode(IPoolManager(address(poolManager)), 3600, address(this));
        
        vm.etch(flags, vm.getDeployedCode("TWAMM.sol:TWAMM"));
        // Initialize with constructor args (needs low-level call or deployCodeTo)
        // deployCodeTo is cleaner but requires the file path.
        // Let's use deployCodeTo if available in standard forge-std, otherwise etch + manual init is tricky.
        // Better: Use specific Etch approach from TWAMM.t.sol
        
        // Actually, just use deployCodeTo:
        deployCodeTo("TWAMM.sol:TWAMM", constructorArgs, flags);
        twamm = TWAMM(payable(flags));
        
        core = new RLDCore();
        StandardFundingModel fundingModel = new StandardFundingModel();
        RLDAaveOracle rateOracle = new RLDAaveOracle();
        ChainlinkSpotOracle spotOracle = new ChainlinkSpotOracle();
        DefaultOracle defaultOracle = new DefaultOracle();
        
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
    }

    function testDeployMarketV4() public {
        uint64 minCol = uint64(1.2e18);
        uint64 maintenance = uint64(1.1e18);
        
        (MarketId id, address oracle, address spotOracle, address defaultOracle, bytes32 poolId) = factory.deployMarketV4(
            address(0), // underlyingPool (mock)
            address(usdc),
            address(usdc), // collateralToken (using USDC as mock collateral)
            IRLDCore.MarketType.RLP,
            minCol, // minCol
            maintenance, // maintenance
            address(0), // liquidator
            bytes32(0), // params
            79228162514264337593543950336, // initSqrtPrice (1:1)
            3600 // oraclePeriod
        );
        
        emit log_bytes32(MarketId.unwrap(id));
        assertTrue(MarketId.unwrap(id) != bytes32(0));
        
        IRLDCore.MarketAddresses memory addrs = core.getMarketAddresses(id);
        
        // 1. Verify Collateral is USDC (passed explicitly)
        assertTrue(addrs.collateralToken == address(usdc));
        
        // 2. Verify Oracles
        address adapter = addrs.markOracle;
        assertTrue(adapter != address(0));
        
        assertTrue(addrs.spotOracle != address(0));
        // Spot Oracle (Chainlink) != Mark Oracle (V4 Adapter)
        assertTrue(addrs.spotOracle != adapter); 
        
        // 3. Verify V4 Adapter
        UniswapV4OracleAdapter oracleAdapter = UniswapV4OracleAdapter(adapter);
        assertTrue(address(oracleAdapter.manager()) == address(poolManager));


    }
}
