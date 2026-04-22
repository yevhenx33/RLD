const routePrefetchers = {
  "/bonds": () => import("../components/bonds/BondsDirectory"),
  "/markets/perps": () => import("../components/trading/PerpsDirectory"),
  "/markets/cds": () => import("../components/cds/CdsDirectory"),
  "/markets/pools": () => import("../components/pools/PoolsDirectory"),
  "/portfolio": () => import("../components/portfolio/Portfolio"),
  "/strategies": () => import("../components/common/Vaults"),
  "/explore": () => import("../features/explore/pages/MarketsPage"),
  "/brokers": () => import("../components/brokers/BrokersDashboard"),
};

const prefetched = new Set();

export function prefetchRoute(path) {
  const loader = routePrefetchers[path];
  if (!loader || prefetched.has(path)) {
    return;
  }
  prefetched.add(path);
  loader().catch(() => {
    prefetched.delete(path);
  });
}
