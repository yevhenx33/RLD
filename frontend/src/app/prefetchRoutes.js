const routePrefetchers = {
  "/bonds": () => import("../components/bonds/BondsDirectory"),
  "/markets/perps": () => import("../components/trading/PerpsDirectory"),
  "/markets/cds": () => import("../components/cds/CdsDirectory"),
  "/markets/pools": () => import("../components/pools/PoolsDirectory"),
  "/portfolio": () => import("../components/portfolio/Portfolio"),
  "/data": () => import("../pages/app/LendingDataPage"),
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
