const DEFAULT_READINESS = {
  ready: false,
  status: "missing",
  reasons: ["manifest_unavailable"],
  indexerLagBlocks: null,
  maxIndexerLagBlocks: null,
};

const ZERO_ADDRESS = "0x0000000000000000000000000000000000000000";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function cleanString(value) {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeBoolean(value, defaultValue = false) {
  if (typeof value === "boolean") return value;
  if (value == null || value === "") return defaultValue;
  if (typeof value === "number") return value !== 0;
  const text = String(value).trim().toLowerCase();
  if (["1", "true", "yes", "y", "on"].includes(text)) return true;
  if (["0", "false", "no", "n", "off"].includes(text)) return false;
  return defaultValue;
}

function normalizeNumber(value, defaultValue = 0) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : defaultValue;
}

function normalizeReadiness(readiness) {
  const source = asObject(readiness);
  const reasons = Array.isArray(source.reasons)
    ? source.reasons.map(String).filter(Boolean)
    : [];
  return {
    ready: source.ready === true && reasons.length === 0,
    status: cleanString(source.status) || (source.ready === true ? "ready" : "degraded"),
    reasons,
    indexerLagBlocks:
      source.indexerLagBlocks == null ? null : Number(source.indexerLagBlocks),
    maxIndexerLagBlocks:
      source.maxIndexerLagBlocks == null ? null : Number(source.maxIndexerLagBlocks),
  };
}

function normalizeToken(token) {
  const source = asObject(token);
  return {
    name: cleanString(source.name || source.symbol),
    symbol: cleanString(source.symbol || source.name),
    address: cleanString(source.address),
  };
}

function normalizePoolKey(key, fallback = {}) {
  const source = asObject(key);
  return {
    currency0: cleanString(source.currency0 || fallback.currency0),
    currency1: cleanString(source.currency1 || fallback.currency1),
    fee: normalizeNumber(source.fee ?? fallback.fee, 0),
    tickSpacing: normalizeNumber(source.tickSpacing ?? fallback.tickSpacing, 0),
    hooks: cleanString(source.hooks || fallback.hooks || ZERO_ADDRESS),
  };
}

function normalizeContracts(source, fallback = {}) {
  const contracts = asObject(source);
  return {
    brokerFactory: cleanString(contracts.brokerFactory || contracts.broker_factory || fallback.brokerFactory),
    brokerRouter: cleanString(contracts.brokerRouter || contracts.broker_router || fallback.brokerRouter),
    brokerExecutor: cleanString(contracts.brokerExecutor || contracts.broker_executor || fallback.brokerExecutor),
    depositAdapter: cleanString(contracts.depositAdapter || contracts.deposit_adapter || fallback.depositAdapter),
    bondFactory: cleanString(contracts.bondFactory || contracts.bond_factory || fallback.bondFactory),
    cdsCoverageFactory: cleanString(
      contracts.cdsCoverageFactory || contracts.cds_coverage_factory || fallback.cdsCoverageFactory,
    ),
    fundingModel: cleanString(contracts.fundingModel || contracts.funding_model || fallback.fundingModel),
    settlementModule: cleanString(
      contracts.settlementModule || contracts.settlement_module || fallback.settlementModule,
    ),
  };
}

function normalizeMarket(market, key) {
  const source = asObject(market);
  const collateral = normalizeToken(source.collateral);
  const positionToken = normalizeToken(source.positionToken || source.position_token);
  const type = cleanString(source.type || key);
  const marketId = cleanString(source.marketId || source.market_id);
  const poolId = cleanString(source.poolId || source.pool_id);
  const fallbackContracts = {
    brokerFactory: source.brokerFactory || source.broker_factory,
    brokerRouter: source.brokerRouter || source.broker_router,
    brokerExecutor: source.brokerExecutor || source.broker_executor,
    depositAdapter: source.depositAdapter || source.deposit_adapter,
    bondFactory: source.bondFactory || source.bond_factory,
    cdsCoverageFactory: source.cdsCoverageFactory || source.cds_coverage_factory,
    fundingModel: source.fundingModel || source.funding_model,
    settlementModule: source.settlementModule || source.settlement_module,
  };
  const contracts = normalizeContracts(source.contracts, fallbackContracts);
  const poolSource = asObject(source.pool);
  const poolFee = normalizeNumber(source.poolFee ?? source.pool_fee ?? poolSource.fee, 0);
  const tickSpacing = normalizeNumber(
    source.tickSpacing ?? source.tick_spacing ?? poolSource.tickSpacing,
    0,
  );
  const token0 = cleanString(poolSource.token0 || source.token0);
  const token1 = cleanString(poolSource.token1 || source.token1);
  const zeroForOneLong = normalizeBoolean(
    source.zeroForOneLong ?? source.zero_for_one_long ?? poolSource.zeroForOneLong,
    false,
  );
  const poolKey = normalizePoolKey(poolSource.key, {
    currency0: token0,
    currency1: token1,
    fee: poolFee,
    tickSpacing,
    hooks: source.twammHook || source.twamm_hook || ZERO_ADDRESS,
  });
  const pool = {
    ...poolSource,
    id: cleanString(poolSource.id || poolSource.poolId || poolId),
    poolId: cleanString(poolSource.poolId || poolSource.id || poolId),
    token0: token0 || poolKey.currency0,
    token1: token1 || poolKey.currency1,
    fee: poolFee,
    tickSpacing,
    key: poolKey,
    zeroForOneLong,
  };
  const executionSource = asObject(source.execution);
  const execution = {
    ...executionSource,
    marketId: cleanString(executionSource.marketId || marketId),
    poolId: cleanString(executionSource.poolId || poolId),
    brokerFactory: cleanString(executionSource.brokerFactory || contracts.brokerFactory),
    brokerRouter: cleanString(executionSource.brokerRouter || contracts.brokerRouter),
    brokerExecutor: cleanString(executionSource.brokerExecutor || contracts.brokerExecutor),
    depositAdapter: cleanString(executionSource.depositAdapter || contracts.depositAdapter),
    collateralToken: cleanString(executionSource.collateralToken || collateral.address),
    collateralSymbol: cleanString(executionSource.collateralSymbol || collateral.symbol),
    positionToken: cleanString(executionSource.positionToken || positionToken.address),
    positionSymbol: cleanString(executionSource.positionSymbol || positionToken.symbol),
    poolKey: normalizePoolKey(executionSource.poolKey, poolKey),
    buyPositionZeroForOne: normalizeBoolean(
      executionSource.buyPositionZeroForOne,
      zeroForOneLong,
    ),
    sellPositionZeroForOne: normalizeBoolean(
      executionSource.sellPositionZeroForOne,
      !zeroForOneLong,
    ),
  };
  const twammSource = asObject(source.twamm);
  const twapEngine = cleanString(source.twapEngine || source.twap_engine || twammSource.engine);
  const twamm = {
    ...twammSource,
    enabled: normalizeBoolean(twammSource.enabled, Boolean(twapEngine)),
    engine: cleanString(twammSource.engine || twapEngine),
    lens: cleanString(twammSource.lens || source.twapEngineLens || source.twap_engine_lens),
    marketId: cleanString(twammSource.marketId || twammSource.poolId || poolId || marketId),
    poolId: cleanString(twammSource.poolId || twammSource.marketId || poolId),
    hook: cleanString(twammSource.hook || source.twammHook || source.twamm_hook || ZERO_ADDRESS),
    zeroForOneLong: normalizeBoolean(twammSource.zeroForOneLong, zeroForOneLong),
    buyPositionZeroForOne: normalizeBoolean(
      twammSource.buyPositionZeroForOne,
      zeroForOneLong,
    ),
    sellPositionZeroForOne: normalizeBoolean(
      twammSource.sellPositionZeroForOne,
      !zeroForOneLong,
    ),
    sellCollateralZeroForOne: normalizeBoolean(
      twammSource.sellCollateralZeroForOne,
      zeroForOneLong,
    ),
    sellPositionTokenZeroForOne: normalizeBoolean(
      twammSource.sellPositionTokenZeroForOne,
      !zeroForOneLong,
    ),
  };
  const featureFlags = {
    perps: source.featureFlags?.perps === true || type === "perp",
    bonds: source.featureFlags?.bonds === true || Boolean(contracts.bondFactory),
    cdsCoverage:
      source.featureFlags?.cdsCoverage === true || Boolean(contracts.cdsCoverageFactory),
    twamm: source.featureFlags?.twamm === true || twamm.enabled,
    liquidity: source.featureFlags?.liquidity === true || Boolean(source.v4PositionManager),
  };

  return {
    ...source,
    type,
    marketId,
    market_id: marketId,
    poolId,
    pool_id: poolId,
    zeroForOneLong,
    zero_for_one_long: zeroForOneLong,
    collateral,
    positionToken,
    position_token: positionToken,
    contracts,
    pool,
    execution,
    twamm,
    brokerFactory: contracts.brokerFactory,
    broker_factory: contracts.brokerFactory,
    brokerRouter: contracts.brokerRouter,
    brokerExecutor: contracts.brokerExecutor,
    depositAdapter: contracts.depositAdapter,
    bondFactory: contracts.bondFactory,
    cdsCoverageFactory: contracts.cdsCoverageFactory,
    ghostRouter: cleanString(source.ghostRouter || source.ghost_router),
    twammHook: twamm.hook || ZERO_ADDRESS,
    twamm_hook: twamm.hook || ZERO_ADDRESS,
    twapEngine: twamm.engine,
    twapEngineLens: twamm.lens,
    poolManager: cleanString(source.poolManager || source.pool_manager),
    v4Quoter: cleanString(source.v4Quoter || source.v4_quoter),
    v4PositionManager: cleanString(source.v4PositionManager || source.v4_position_manager),
    v4StateView: cleanString(source.v4StateView || source.v4_state_view),
    poolFee: poolFee,
    pool_fee: poolFee,
    tickSpacing: tickSpacing,
    tick_spacing: tickSpacing,
    fundingModel: contracts.fundingModel,
    settlementModule: contracts.settlementModule,
    featureFlags,
    riskParams: asObject(source.riskParams || source.risk_params),
  };
}

export function normalizeRuntimeManifest(raw) {
  if (!raw) return null;
  const source = asObject(raw);
  if (Number(source.schemaVersion) !== 1) {
    throw new Error(`Unsupported runtime manifest schema: ${source.schemaVersion ?? "missing"}`);
  }
  const readiness = normalizeReadiness(source.readiness);
  const markets = Object.fromEntries(
    Object.entries(asObject(source.markets)).map(([key, value]) => [
      key,
      normalizeMarket(value, key),
    ]),
  );

  return {
    schemaVersion: 1,
    deploymentId: cleanString(source.deploymentId),
    chainId: Number(source.chainId || 0),
    rpcUrl: cleanString(source.rpcUrl),
    faucetUrl: cleanString(source.faucetUrl),
    indexerBlock: Number(source.indexerBlock || 0),
    chainBlock: source.chainBlock == null ? null : Number(source.chainBlock),
    readiness,
    globalContracts: asObject(source.globalContracts),
    contracts: asObject(source.contracts),
    markets,
  };
}

export function getRuntimeReadiness(manifest) {
  return manifest?.readiness || DEFAULT_READINESS;
}

export function getRuntimeMarket(manifest, marketKey = "perp") {
  if (!manifest?.markets) return null;
  const key = marketKey || "perp";
  if (manifest.markets[key]) return manifest.markets[key];
  const normalizedKey = String(key).toLowerCase();
  return (
    Object.values(manifest.markets).find((market) => {
      return (
        market.type?.toLowerCase() === normalizedKey ||
        market.marketId?.toLowerCase() === normalizedKey ||
        market.poolId?.toLowerCase() === normalizedKey
      );
    }) || null
  );
}

export function runtimeMarketToMarketInfo(manifest, marketKey = "perp") {
  const market = getRuntimeMarket(manifest, marketKey);
  if (!market) return null;
  const readiness = getRuntimeReadiness(manifest);
  const runtimeReady = readiness.ready === true;
  return {
    marketId: market.marketId,
    market_id: market.marketId,
    poolId: market.poolId,
    pool_id: market.poolId,
    zeroForOneLong: market.zeroForOneLong,
    zero_for_one_long: market.zeroForOneLong,
    collateral: market.collateral,
    position_token: market.positionToken,
    positionToken: market.positionToken,
    contracts: market.contracts,
    pool: market.pool,
    execution: market.execution,
    twamm: market.twamm,
    broker_factory: market.brokerFactory,
    brokerFactory: market.brokerFactory,
    infrastructure: {
      broker_router: market.execution.brokerRouter,
      brokerRouter: market.execution.brokerRouter,
      broker_executor: market.execution.brokerExecutor,
      brokerExecutor: market.execution.brokerExecutor,
      deposit_adapter: market.execution.depositAdapter,
      depositAdapter: market.execution.depositAdapter,
      bond_factory: market.contracts.bondFactory,
      bondFactory: market.contracts.bondFactory,
      cds_coverage_factory: market.contracts.cdsCoverageFactory,
      cdsCoverageFactory: market.contracts.cdsCoverageFactory,
      ghost_router: market.ghostRouter,
      ghostRouter: market.ghostRouter,
      twamm_hook: market.twamm.hook || ZERO_ADDRESS,
      twammHook: market.twamm.hook || ZERO_ADDRESS,
      twap_engine: market.twamm.engine,
      twapEngine: market.twamm.engine,
      twap_engine_lens: market.twamm.lens,
      twapEngineLens: market.twamm.lens,
      twamm_market_id: market.twamm.marketId,
      twammMarketId: market.twamm.marketId,
      zero_for_one_long: market.zeroForOneLong,
      zeroForOneLong: market.zeroForOneLong,
      buy_position_zero_for_one: market.twamm.buyPositionZeroForOne,
      buyPositionZeroForOne: market.twamm.buyPositionZeroForOne,
      sell_position_zero_for_one: market.twamm.sellPositionZeroForOne,
      sellPositionZeroForOne: market.twamm.sellPositionZeroForOne,
      pool_id: market.poolId,
      poolId: market.poolId,
      pool_key: market.pool.key,
      poolKey: market.pool.key,
      pool_fee: market.pool.fee,
      poolFee: market.pool.fee,
      tick_spacing: market.pool.tickSpacing,
      tickSpacing: market.pool.tickSpacing,
      pool_manager: market.poolManager,
      poolManager: market.poolManager,
      v4_quoter: market.v4Quoter,
      v4Quoter: market.v4Quoter,
      v4_position_manager: market.v4PositionManager,
      v4PositionManager: market.v4PositionManager,
      v4_state_view: market.v4StateView,
      v4StateView: market.v4StateView,
      funding_model: market.fundingModel,
      fundingModel: market.fundingModel,
      settlement_module: market.settlementModule,
      settlementModule: market.settlementModule,
      runtime_ready: runtimeReady,
      runtimeReady,
      runtimeReadiness: readiness,
      chain_id: manifest.chainId,
      chainId: manifest.chainId,
      chain_block: manifest.chainBlock,
      chainBlock: manifest.chainBlock,
      indexer_block: manifest.indexerBlock,
      indexerBlock: manifest.indexerBlock,
    },
    feature_flags: market.featureFlags,
    featureFlags: market.featureFlags,
    risk_params: market.riskParams,
    riskParams: market.riskParams,
    external_contracts: null,
  };
}

export function runtimeExecutionBlockReason(manifest, marketKey = "perp") {
  const readiness = getRuntimeReadiness(manifest);
  if (!readiness.ready) {
    return readiness.reasons.length
      ? `Runtime not ready: ${readiness.reasons.join(", ")}`
      : "Runtime manifest is not ready";
  }
  if (!getRuntimeMarket(manifest, marketKey)) {
    return `Market ${marketKey || "perp"} is missing from runtime manifest`;
  }
  return null;
}
