import fs from "fs";
import path from "path";

const root = process.cwd();

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), "utf8");
}

function assertIncludes(content, needle, label) {
  if (!content.includes(needle)) {
    throw new Error(`${label}: missing ${needle}`);
  }
}

function assertNotIncludes(content, needle, label) {
  if (content.includes(needle)) {
    throw new Error(`${label}: must not include ${needle}`);
  }
}

const providers = read("src/app/providers.jsx");
assertNotIncludes(
  providers,
  "SimulationProvider",
  "AppProviders must not globally mount simulation polling",
);

const routes = read("src/app/routes.jsx");
assertIncludes(routes, "function ApiShell", "Routes must define API shell");
assertIncludes(routes, "function SimulationRuntimeShell", "Routes must isolate simulation shell");
assertIncludes(routes, '<Route element={<ApiShell />}>', "API routes must use API shell");
assertIncludes(routes, '<Route element={<SimulationRuntimeShell />}>', "Simulation routes must use simulation shell");
assertIncludes(routes, "ProtocolOverviewPage", "Protocol overview routes must share one page shell");
assertNotIncludes(routes, "AaveProtocolPage", "Protocol overview routes must not use protocol-specific page shells");
assertNotIncludes(routes, "FluidProtocolPage", "Protocol overview routes must not use protocol-specific page shells");
for (const route of ["/data/aave", "/data/spark", "/data/morpho", "/data/fluid", "/data/euler", "/data/compound-v3"]) {
  assertIncludes(
    routes,
    `path="${route}" element={renderLazy(ProtocolOverviewPage)}`,
    "Protocol overview routes",
  );
}


const lendingDataPage = read("src/pages/app/LendingDataPage.jsx");
const apiQueries = read("src/api/apiQueries.js");
assertIncludes(apiQueries, "lendingDataPage(displayIn: $displayIn, flowWindowDays: $flowWindowDays)", "ApiQueries");
assertNotIncludes(lendingDataPage, "marketSnapshots(protocol:", "LendingDataPage");
assertNotIncludes(lendingDataPage, "protocolTvlHistory(", "LendingDataPage");
assertNotIncludes(lendingDataPage, "protocolApyHistory(", "LendingDataPage");
assertNotIncludes(lendingDataPage, "Math.random", "LendingDataPage");
assertIncludes(apiQueries, "totalUsers", "ApiQueries");
assertIncludes(lendingDataPage, "stats.totalUsers", "LendingDataPage");
assertIncludes(apiQueries, "alluvialFlows", "ApiQueries");
assertIncludes(lendingDataPage, "AlluvialFlowChart", "LendingDataPage");
assertIncludes(lendingDataPage, "LENDING FLOWS", "LendingDataPage");
assertIncludes(lendingDataPage, "NET INFLOWS", "LendingDataPage");
assertIncludes(lendingDataPage, "NET OUTFLOWS", "LendingDataPage");
assertIncludes(apiQueries, "aaveTvl", "ApiQueries");
assertIncludes(apiQueries, "eulerTvl", "ApiQueries");
assertIncludes(apiQueries, "fluidTvl", "ApiQueries");
assertIncludes(apiQueries, "morphoTvl", "ApiQueries");
assertIncludes(lendingDataPage, 'key: "tvl"', "LendingDataPage");
assertIncludes(lendingDataPage, "areas={[tvlArea]}", "LendingDataPage");
assertIncludes(lendingDataPage, "LENDING_PROTOCOL_GROUPS", "LendingDataPage");
assertNotIncludes(lendingDataPage, "EULER <span", "LendingDataPage");

const protocolMarkets = read("src/components/charts/ProtocolMarkets.jsx");
assertIncludes(apiQueries, "protocolMarketsPage(protocol: $protocol", "ApiQueries");
assertNotIncludes(protocolMarkets, "protocolMarkets(protocol:", "ProtocolMarkets");

const protocolOverviewPage = read("src/pages/app/protocols/ProtocolOverviewPage.jsx");
assertIncludes(apiQueries, "protocolOverviewPage(", "ApiQueries");
assertIncludes(protocolOverviewPage, "PROTOCOL_OVERVIEW_PAGE_QUERY", "ProtocolOverviewPage");
assertIncludes(protocolOverviewPage, "apiProtocolOverviewPage", "ProtocolOverviewPage");
assertNotIncludes(protocolOverviewPage, "apiProtocolMarkets", "ProtocolOverviewPage");
assertNotIncludes(protocolOverviewPage, "apiFluidProductSnapshots", "ProtocolOverviewPage");
assertNotIncludes(protocolOverviewPage, "apiMetaMorphoVaults", "ProtocolOverviewPage");
const protocolOverviewSWRCount = (protocolOverviewPage.match(/useSWR\(/g) || []).length;
if (protocolOverviewSWRCount !== 1) {
  throw new Error(`ProtocolOverviewPage must issue exactly one SWR-backed GraphQL request; found ${protocolOverviewSWRCount}`);
}

const marketPage = read("src/pages/app/markets/AaveMarketPage.jsx");
const eulerMarketPage = read("src/pages/app/markets/EulerMarketPage.jsx");
assertIncludes(apiQueries, "marketPage(", "ApiQueries");
assertNotIncludes(marketPage, "marketTimeseries(", "MarketPage");
assertNotIncludes(marketPage, "marketFlowTimeseries(", "MarketPage");
assertNotIncludes(marketPage, "protocolMarkets(protocol:", "MarketPage");
assertIncludes(routes, 'path="/data/euler/:marketId"', "Routes");
assertIncludes(eulerMarketPage, 'const protocolSlug = "euler"', "EulerMarketPage");
assertNotIncludes(eulerMarketPage, "Vault Breakdown", "EulerMarketPage");

const endpointConfig = read("src/api/endpoints.js");
assertNotIncludes(endpointConfig, "ENVIO", "Endpoint config");
const queryKeys = read("src/api/queryKeys.js");
assertNotIncludes(queryKeys, "envio.", "Query keys");

console.log("API page contract check passed.");
