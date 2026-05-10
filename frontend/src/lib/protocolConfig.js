const PROTOCOLS = {
  aave: {
    slug: "aave",
    apiProtocol: "AAVE_MARKET",
    displayName: "Aave",
    supportsMarketPage: true,
    stripRoutePrefix: true,
  },
  spark: {
    slug: "spark",
    apiProtocol: "SPARK_MARKET",
    displayName: "Spark",
    supportsMarketPage: true,
    stripRoutePrefix: true,
  },
  morpho: {
    slug: "morpho",
    apiProtocol: "MORPHO_MARKET",
    displayName: "Morpho",
    supportsMarketPage: true,
    stripRoutePrefix: false,
  },
  fluid: {
    slug: "fluid",
    apiProtocol: "FLUID_MARKET",
    displayName: "Fluid",
    supportsMarketPage: true,
    stripRoutePrefix: false,
  },
  pendle: {
    slug: "pendle",
    apiProtocol: "PENDLE_ETHEREUM_PT_YT_PRICES",
    displayName: "Pendle",
    supportsMarketPage: true,
    stripRoutePrefix: true,
  },
  euler: {
    slug: "euler",
    apiProtocol: "EULER_MARKET",
    displayName: "Euler",
    supportsMarketPage: true,
    stripRoutePrefix: true,
  },
};

const BY_API_PROTOCOL = Object.values(PROTOCOLS).reduce((acc, config) => {
  acc[config.apiProtocol] = config;
  return acc;
}, {});

export function protocolConfigForSlug(slug) {
  return PROTOCOLS[String(slug || "").toLowerCase()] || null;
}

export function protocolConfigForApiProtocol(protocol) {
  return BY_API_PROTOCOL[String(protocol || "").toUpperCase()] || null;
}

export function apiProtocolForSlug(slug, fallback = "AAVE_MARKET") {
  return protocolConfigForSlug(slug)?.apiProtocol || fallback;
}

export function protocolSlugForApiProtocol(protocol, fallback = "aave") {
  return protocolConfigForApiProtocol(protocol)?.slug || fallback;
}

export function normalizeMarketIdForApi(slug, marketId) {
  const config = protocolConfigForSlug(slug);
  const raw = String(marketId || "").trim().toLowerCase();
  if (!raw) return null;
  if (config?.stripRoutePrefix && !raw.startsWith("0x")) {
    return `0x${raw}`;
  }
  return raw;
}

export function marketRouteFor(protocol, marketId) {
  const config = protocolConfigForApiProtocol(protocol) || protocolConfigForSlug(protocol);
  const slug = config?.slug || "aave";
  const raw = String(marketId || "").trim().toLowerCase();
  const routeId = config?.stripRoutePrefix ? raw.replace(/^0x/, "") : raw;
  return `/data/${slug}/${routeId}`;
}

export const API_PROTOCOLS = Object.freeze(PROTOCOLS);
