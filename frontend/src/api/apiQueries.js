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
  query LendingDataHub($displayIn: String!) {
    lendingDataPage(displayIn: $displayIn) {
      freshness { ready status generatedAt }
      stats {
        totalSupplyUsd
        totalBorrowUsd
        averageSupplyApy
        averageBorrowApy
        marketCount
      }
      chartData {
        timestamp
        tvl
        averageSupplyApy
        averageBorrowApy
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
  query ProtocolMarketsByProtocol($protocol: String!) {
    protocolMarketsPage(protocol: $protocol) {
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
        isTrapped
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
        vaults { id address name }
        suppliedUsd
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
