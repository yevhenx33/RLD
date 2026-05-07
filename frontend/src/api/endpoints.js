const env = import.meta.env || {};
const browserOrigin =
  typeof window !== "undefined" && window.location?.origin
    ? window.location.origin
    : "";

function trimTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

function endpoint(value, fallback) {
  return trimTrailingSlash(value || fallback);
}

function joinEndpoint(base, path) {
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  return base ? `${base}${cleanPath}` : cleanPath;
}

const simApiBase = endpoint(env.VITE_SIM_API_URL, "");
const restApiBase = endpoint(env.VITE_API_BASE_URL, "/api");
const publicApiBase = endpoint(env.VITE_PUBLIC_API_BASE, "/analytics");
const defaultOrigin = browserOrigin || "";

export const API_BASE_URL = restApiBase;
export const SIM_API_BASE_URL = simApiBase;
export const SIM_GRAPHQL_URL = joinEndpoint(simApiBase, "/graphql");
export const RUNTIME_MANIFEST_URL = joinEndpoint(simApiBase, "/api/runtime-manifest");
export const API_GRAPHQL_URL = joinEndpoint(publicApiBase, "/graphql");
export const API_STATUS_URL = joinEndpoint(publicApiBase, "/public-readyz");
export const RPC_URL = endpoint(
  env.VITE_RPC_URL,
  defaultOrigin ? `${defaultOrigin}/rpc` : "/rpc",
);
export const FAUCET_API_URL = endpoint(
  env.VITE_FAUCET_API_URL,
  defaultOrigin ? `${defaultOrigin}/api/faucet` : "/api/faucet",
);
