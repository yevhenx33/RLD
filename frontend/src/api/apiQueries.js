export const API_STATUS_QUERY = `
  query ApiStatus {
    apiStatus {
      ready
      status
      version
      generatedAt
      protocols {
        protocol
        ready
        status
        freshness {
          collectorLag
          processingLag
          status
          issues { code severity message }
        }
        coverage {
          total
          indexed
          priced
          unpriced
          unsupported
          partial
          status
        }
      }
    }
  }
`;

export const LENDING_DATA_QUERY = `
  query LendingDataHub($displayIn: String!, $flowWindowDays: Int!) {
    lendingDataPage(displayIn: $displayIn, flowWindowDays: $flowWindowDays) {
      freshness { ready status generatedAt }
      stats {
        totalSupplyUsd
        totalBorrowUsd
        pooledSupplyUsd
        isolatedSupplyUsd
        averageSupplyApy
        averageBorrowApy
        marketCount
        totalUsers
      }
      chartData {
        timestamp
        tvl
        aaveTvl
        sparkTvl
        eulerTvl
        fluidTvl
        morphoTvl
        compoundV3Tvl
        averageSupplyApy
        averageBorrowApy
        sofrRate
      }
      alluvialFlows {
        protocol
        action
        asset
        valueUsd
      }
      markets {
        entityId
        symbol
        protocol
        supplyUsd
        borrowUsd
        supplyApy
        borrowApy
        utilization
        netWorth
        collateralSymbol
      }
    }
  }
`;

export const PROTOCOL_MARKETS_QUERY = `
  query ProtocolMarketsByProtocol($protocol: String!, $maxBorrowApy: Float) {
    protocolMarketsPage(protocol: $protocol, maxBorrowApy: $maxBorrowApy) {
      freshness { ready status generatedAt }
      stats {
        totalSupplyUsd
        totalBorrowUsd
        averageUtilization
        averageSupplyApy
        averageBorrowApy
        marketCount
      }
      rows {
        entityId
        symbol
        protocol
        supplyUsd
        borrowUsd
        supplyApy
        borrowApy
        utilization
        collateralSymbol
        lltv
        lltvMin
        lltvMax
        isTrapped
      }
    }
  }
`;

export const COMPOUND_V3_PROTOCOL_PAGE_QUERY = `
  query CompoundV3ProtocolPage($flowWindowDays: Int!, $timeseriesLimit: Int!, $assetSymbols: [String!]) {
    compoundV3ProtocolPage(flowWindowDays: $flowWindowDays, timeseriesLimit: $timeseriesLimit, assetSymbols: $assetSymbols) {
      freshness { ready status generatedAt }
      stats {
        totalSupplyUsd
        totalBorrowUsd
        averageUtilization
        averageSupplyApy
        averageBorrowApy
        marketCount
      }
      rows {
        entityId
        symbol
        protocol
        supplyUsd
        borrowUsd
        supplyApy
        borrowApy
        utilization
        collateralSymbol
        lltv
        lltvMin
        lltvMax
        isTrapped
      }
      apyHistory {
        timestamp
        averageSupplyApy
        averageBorrowApy
        supplyUsd
        borrowUsd
        sofrRate
      }
      assetApyHistory {
        timestamp
        symbol
        supplyApy
        borrowApy
        sofrRate
      }
      alluvialFlows {
        protocol
        action
        asset
        valueUsd
      }
    }
  }
`;

export const METAMORPHO_VAULTS_QUERY = `
  query MetaMorphoVaults($limit: Int!) {
    metamorphoVaults(limit: $limit) {
      vaultAddress
      name
      assetSymbol
      assetAddress
      curatorAddress
      curator
      totalAssets
      totalSupply
      sharePriceUsd
      sharePriceAssets
      tvlUsd
      apy
      exposure {
        symbol
        valueUsd
      }
      isCanonicalTvl
      lastSnapshotTimestamp
    }
  }
`;

export const METAMORPHO_VAULT_PAGE_QUERY = `
  query MetaMorphoVaultPage($vaultAddress: String!, $timeseriesLimit: Int!, $flowLimit: Int!, $allocationLimit: Int!) {
    metamorphoVaultPage(
      vaultAddress: $vaultAddress
      timeseriesLimit: $timeseriesLimit
      flowLimit: $flowLimit
      allocationLimit: $allocationLimit
    ) {
      freshness { ready status generatedAt }
      vault {
        vaultAddress
        name
        assetSymbol
        assetAddress
        curatorAddress
        curator
        totalAssets
        totalSupply
        sharePriceUsd
        tvlUsd
        apy
        exposure {
          symbol
          valueUsd
        }
        isCanonicalTvl
        lastSnapshotTimestamp
      }
      history {
        timestamp
        totalDepositsUsd
        allocatedUsd
        liquidityUsd
        utilization
        sharePriceUsd
        netApy
      }
      flowChart {
        timestamp
        assetSymbol
        depositUsd
        withdrawUsd
        netFlowUsd
      }
      flowLinks {
        action
        asset
        valueUsd
      }
      exposures {
        marketId
        marketLabel
        loanSymbol
        collateralSymbol
        suppliedUsd
        allocationShare
        liquidityUsd
        supplyApy
        borrowApy
        utilization
        lltv
      }
    }
  }
`;

export const MARKET_PAGE_QUERY = `
  query MarketPage($protocol: String!, $marketId: String!, $timeseriesLimit: Int!, $flowLimit: Int!, $allocationLimit: Int!) {
    marketPage(
      protocol: $protocol
      marketId: $marketId
      timeseriesLimit: $timeseriesLimit
      flowLimit: $flowLimit
      allocationLimit: $allocationLimit
    ) {
      freshness { ready status generatedAt }
      market {
        entityId
        symbol
        protocol
        supplyUsd
        borrowUsd
        supplyApy
        borrowApy
        utilization
        collateralSymbol
        lltv
        lltvMin
        lltvMax
        collateralUsd
        oracle
        loanPriceUsd
        collateralPriceUsd
        loanToken
        collateralToken
        oracleSupport
      }
      rateChart {
        timestamp
        supplyApy
        borrowApy
        utilization
        supplyUsd
        borrowUsd
      }
      flowChart {
        timestamp
        supplyInflowUsd
        supplyOutflowUsd
        borrowInflowUsd
        borrowOutflowUsd
        netSupplyFlowUsd
        netBorrowFlowUsd
        cumulativeSupplyNetInflowUsd
        cumulativeBorrowNetInflowUsd
      }
      allocationColumnar {
        timestamps
        vaults { id address name curator }
        suppliedUsd
      }
      vaultBreakdown {
        vault
        vaultId
        collateral
        debt
        ltv
        supplyApy
        borrowApy
        supplyUsd
        borrowUsd
      }
      collateralBreakdown {
        asset
        symbol
        priceFeed
        borrowCollateralFactor
        liquidateCollateralFactor
        liquidationFactor
        supplyCap
        supplyCapTokens
        totalCollateral
        totalCollateralTokens
        collateralUsd
        borrowEnabled
      }
    }
  }
`;

export const FLUID_VAULT_PAGE_QUERY = `
  query FluidVaultPage($vaultId: String!, $timeseriesLimit: Int!, $flowLimit: Int!) {
    fluidVaultPage(
      vaultId: $vaultId
      timeseriesLimit: $timeseriesLimit
      flowLimit: $flowLimit
    ) {
      freshness { ready status generatedAt }
      market {
        entityId
        symbol
        protocol
        supplyUsd
        borrowUsd
        supplyApy
        borrowApy
        utilization
        collateralSymbol
        loanAsset
        collateralPriceUsd
        oracleSupport
        lltvMin
        lltvMax
      }
      rateChart {
        timestamp
        supplyApy
        borrowApy
        utilization
        supplyUsd
        borrowUsd
      }
      flowChart {
        timestamp
        supplyInflowUsd
        supplyOutflowUsd
        borrowInflowUsd
        borrowOutflowUsd
        netSupplyFlowUsd
        netBorrowFlowUsd
      }
    }
  }
`;


export const PENDLE_MARKET_QUERY = `
  query PendleMarket($search: String!) {
    pendleMarketPage(search: $search) {
      marketAddress
      freshness { ready status generatedAt }
      assets {
        assetAddress
        assetType
        symbol
        marketAddress
        expiry
        active
        matured
      }
      latestPrices {
        assetAddress
        assetType
        symbol
        priceUsd
        timestamp
      }
    }
  }
`;

export const HISTORICAL_RATES_QUERY = `
  query HistoricalRates($resolution: String!, $limit: Int!) {
    historicalRates(symbols: ["USDC"], resolution: $resolution, limit: $limit) {
      timestamp
      symbol
      apy
      price
    }
  }
`;

export const SUSDE_QUERY = `
  query SusdeLatest {
    latestRates { susde }
  }
`;

export const PROTOCOL_APY_HISTORY_QUERY = `
  query ProtocolApyHistory($protocol: String!, $resolution: String!, $limit: Int!, $maxBorrowApy: Float) {
    protocolApyHistory(protocol: $protocol, resolution: $resolution, limit: $limit, maxBorrowApy: $maxBorrowApy) {
      timestamp
      averageSupplyApy
      averageBorrowApy
      supplyUsd
      borrowUsd
      sofrRate
    }
  }
`;

export const PROTOCOL_ASSET_APY_HISTORY_QUERY = `
  query ProtocolAssetApyHistory($protocol: String!, $symbols: [String!]!, $resolution: String!, $limit: Int!, $maxBorrowApy: Float) {
    protocolAssetApyHistory(protocol: $protocol, symbols: $symbols, resolution: $resolution, limit: $limit, maxBorrowApy: $maxBorrowApy) {
      timestamp
      symbol
      supplyApy
      borrowApy
      sofrRate
    }
  }
`;

export const MORPHO_CURATOR_FLOWS_QUERY = `
  query MorphoCuratorFlows($flowWindowDays: Int!, $topN: Int!, $maxBorrowApy: Float) {
    morphoCuratorFlows(flowWindowDays: $flowWindowDays, topN: $topN, maxBorrowApy: $maxBorrowApy) {
      action
      asset
      curator
      curatorAddress
      valueUsd
    }
  }
`;

export const EULER_CHANNEL_FLOWS_QUERY = `
  query EulerChannelFlows($flowWindowDays: Int!, $topN: Int!, $maxBorrowApy: Float) {
    eulerChannelFlows(flowWindowDays: $flowWindowDays, topN: $topN, maxBorrowApy: $maxBorrowApy) {
      action
      asset
      curator
      curatorAddress
      valueUsd
    }
  }
`;

export const MORPHO_CURATOR_ALLOCATION_HISTORY_QUERY = `
  query MorphoCuratorAllocationHistory($resolution: String!, $limit: Int!, $topN: Int!, $maxBorrowApy: Float) {
    morphoCuratorAllocationHistory(resolution: $resolution, limit: $limit, topN: $topN, maxBorrowApy: $maxBorrowApy) {
      timestamp
      curator
      curatorAddress
      suppliedUsd
    }
  }
`;
