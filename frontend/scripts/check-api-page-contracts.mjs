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

const lendingDataPage = read("src/pages/app/LendingDataPage.jsx");
const apiQueries = read("src/api/apiQueries.js");
assertIncludes(apiQueries, "lendingDataPage(displayIn: $displayIn)", "ApiQueries");
assertNotIncludes(lendingDataPage, "marketSnapshots(protocol:", "LendingDataPage");
assertNotIncludes(lendingDataPage, "protocolTvlHistory(", "LendingDataPage");
assertNotIncludes(lendingDataPage, "protocolApyHistory(", "LendingDataPage");
assertNotIncludes(lendingDataPage, "Math.random", "LendingDataPage");

const protocolMarkets = read("src/components/charts/ProtocolMarkets.jsx");
assertIncludes(apiQueries, "protocolMarketsPage(protocol: $protocol)", "ApiQueries");
assertNotIncludes(protocolMarkets, "protocolMarkets(protocol:", "ProtocolMarkets");

const marketPage = read("src/pages/app/markets/AaveMarketPage.jsx");
assertIncludes(apiQueries, "marketPage(", "ApiQueries");
assertNotIncludes(marketPage, "marketTimeseries(", "MarketPage");
assertNotIncludes(marketPage, "marketFlowTimeseries(", "MarketPage");
assertNotIncludes(marketPage, "protocolMarkets(protocol:", "MarketPage");

const endpointConfig = read("src/api/endpoints.js");
assertNotIncludes(endpointConfig, "ENVIO", "Endpoint config");
const queryKeys = read("src/api/queryKeys.js");
assertNotIncludes(queryKeys, "envio.", "Query keys");

console.log("API page contract check passed.");
