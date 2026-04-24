const simApiBase = import.meta.env.VITE_SIM_API_URL || "";
const restApiBase = import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";
const analyticsApiBase = import.meta.env.VITE_ANALYTICS_API_BASE || "/analytics";

export const API_BASE_URL = restApiBase;
export const SIM_API_BASE_URL = simApiBase;
export const SIM_GRAPHQL_URL = `${simApiBase}/graphql`;
export const ANALYTICS_GRAPHQL_URL = `${analyticsApiBase}/graphql`;
// Deprecated alias: keep while external callers migrate.
export const ENVIO_GRAPHQL_URL = ANALYTICS_GRAPHQL_URL;
export const FAUCET_API_URL = `${window.location.origin}/api/faucet`;
