export function runtimeMarketKeyForPath(pathname, address = null) {
  const normalizedAddress = address ? String(address).toLowerCase() : null;
  if (pathname.startsWith("/markets/cds")) return normalizedAddress || "cds";
  if (pathname.startsWith("/markets/perps")) return normalizedAddress || "perp";
  if (pathname.startsWith("/markets/pools/")) return normalizedAddress || "perp";
  return "perp";
}
