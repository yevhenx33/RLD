const simApiBase = import.meta.env.VITE_SIM_API_URL || "";
const restApiBase = import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";

export const API_BASE_URL = restApiBase;
export const SIM_API_BASE_URL = simApiBase;
export const SIM_GRAPHQL_URL = `${simApiBase}/graphql`;
export const ENVIO_GRAPHQL_URL = "/envio-graphql";
export const FAUCET_API_URL = `${window.location.origin}/api/faucet`;
