function liquidate(MarketId id, address user, uint256 debtToCover) external override nonReentrant {
    _applyFunding(id);
    
    MarketState storage state = marketStates[id];
    MarketConfig memory config = _getEffectiveConfig(id);
    
    // 1. Validations
    _validateLiquidationChecks(id, user, config);

    // 2. Debt Calculations & Updates
    // Returns principal to burn and normalization factor for later use
    (uint256 principalToCover, uint256 normFactor) = _updateLiquidationDebt(id, user, debtToCover, state, config);

    // 3. Seize Calculation via Oracle & Module
    uint256 seizeAmount = _calculateLiquidationSeize(id, user, debtToCover, normFactor, config);

    // 4. Execution & Settlement
    _settleLiquidation(id, user, seizeAmount, principalToCover);
}

function _validateLiquidationChecks(MarketId id, address user, MarketConfig memory config) internal view {
    if (_isSolvent(id, user, uint256(config.maintenanceMargin))) {
        revert UserSolvent(user);
    }
    if (config.brokerVerifier == address(0) || !IBrokerVerifier(config.brokerVerifier).isValidBroker(user)) {
        revert InvalidBroker(user);
    }
}

function _updateLiquidationDebt(
    MarketId id, 
    address user, 
    uint256 debtToCover, 
    MarketState storage state,
    MarketConfig memory config
) internal returns (uint256 principalToCover, uint256 normFactor) {
    normFactor = state.normalizationFactor;
    Position storage pos = positions[id][user];
    uint128 principal = pos.debtPrincipal;

    uint256 trueDebt = uint256(principal).mulWad(normFactor);
    
    if (debtToCover > trueDebt.mulWad(uint256(config.liquidationCloseFactor))) {
        revert CloseFactorExceeded();
    }

    principalToCover = debtToCover.divWad(normFactor);
    pos.debtPrincipal = principal - uint128(principalToCover);
}

function _calculateLiquidationSeize(
    MarketId id,
    address user,
    uint256 debtToCover,
    uint256 normFactor,
    MarketConfig memory config
) internal view returns (uint256 seizeAmount) {
     MarketAddresses storage addresses = marketAddresses[id];
     
     uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
        addresses.underlyingPool, 
        addresses.underlyingToken
    );
    
    uint256 spotPrice = addresses.spotOracle != address(0)
        ? ISpotOracle(addresses.spotOracle).getSpotPrice(addresses.collateralToken, addresses.underlyingToken)
        : 1e18;

    ILiquidationModule.PriceData memory priceData = ILiquidationModule.PriceData({
        indexPrice: indexPrice,
        spotPrice: spotPrice,
        normalizationFactor: normFactor
    });

    uint256 remainingTrueDebt = uint256(positions[id][user].debtPrincipal).mulWad(normFactor).mulWad(indexPrice);

    ( , seizeAmount) = ILiquidationModule(addresses.liquidationModule).calculateSeizeAmount(
        debtToCover, 
        IPrimeBroker(user).getNetAccountValue(),
        remainingTrueDebt,
        priceData, 
        config, 
        config.liquidationParams
    );
}

function _settleLiquidation(
    MarketId id,
    address user,
    uint256 seizeAmount,
    uint256 principalToCover
) internal {
    IPrimeBroker.SeizeOutput memory seizeOutput = IPrimeBroker(user).seize(seizeAmount, msg.sender);
    
    uint256 wRLPFromBroker = seizeOutput.wRLPExtracted > principalToCover 
        ? principalToCover 
        : seizeOutput.wRLPExtracted;
    
    address positionToken = marketAddresses[id].positionToken;
    
    if (wRLPFromBroker > 0) {
        PositionToken(positionToken).burn(address(this), wRLPFromBroker);
    }
    
    uint256 liquidatorOwes = principalToCover - wRLPFromBroker;
    if (liquidatorOwes > 0) {
        PositionToken(positionToken).burn(msg.sender, liquidatorOwes);
    }
    
    emit PositionModified(id, user, 0, -int256(principalToCover));
}
