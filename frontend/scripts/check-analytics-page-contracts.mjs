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
assertIncludes(routes, "function AnalyticsShell", "Routes must define analytics-only shell");
assertIncludes(routes, "function SimulationRuntimeShell", "Routes must isolate simulation shell");
assertIncludes(routes, '<Route element={<AnalyticsShell />}>', "Analytics routes must use analytics shell");
assertIncludes(routes, '<Route element={<SimulationRuntimeShell />}>', "Simulation routes must use simulation shell");

const lendingDataPage = read("src/pages/app/LendingDataPage.jsx");
assertIncludes(lendingDataPage, "lendingDataPage(displayIn: $displayIn)", "LendingDataPage");
assertNotIncludes(lendingDataPage, "marketSnapshots(protocol:", "LendingDataPage");
assertNotIncludes(lendingDataPage, "protocolTvlHistory(", "LendingDataPage");
assertNotIncludes(lendingDataPage, "protocolApyHistory(", "LendingDataPage");

const protocolMarkets = read("src/components/charts/ProtocolMarkets.jsx");
assertIncludes(protocolMarkets, "protocolMarketsPage(protocol: $protocol)", "ProtocolMarkets");
assertNotIncludes(protocolMarkets, "protocolMarkets(protocol:", "ProtocolMarkets");

const lendingPoolPage = read("src/pages/app/LendingPoolPage.jsx");
assertIncludes(lendingPoolPage, "lendingPoolPage(", "LendingPoolPage");
assertNotIncludes(lendingPoolPage, "marketTimeseries(", "LendingPoolPage");
assertNotIncludes(lendingPoolPage, "marketFlowTimeseries(", "LendingPoolPage");
assertNotIncludes(lendingPoolPage, "protocolMarkets(protocol:", "LendingPoolPage");

console.log("Analytics page contract check passed.");
