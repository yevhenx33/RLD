import { API_GRAPHQL_URL } from "./endpoints";
import { postGraphQL } from "./graphqlClient";

export class ApiRequestError extends Error {
  constructor(message, { status = null, code = null, requestId = null, errors = null } = {}) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.errors = errors;
  }
}

export async function apiGraphQL(
  operationName,
  { query, variables = undefined, headers = undefined, signal = undefined },
) {
  if (!operationName || typeof operationName !== "string") {
    throw new ApiRequestError("API GraphQL calls require an operationName", {
      code: "OPERATION_NAME_REQUIRED",
    });
  }

  try {
    return await postGraphQL(API_GRAPHQL_URL, {
      query,
      variables,
      operationName,
      headers,
      signal,
    });
  } catch (error) {
    const first = error?.errors?.[0] || {};
    throw new ApiRequestError(first.message || error.message || "API request failed", {
      status: error?.status ?? null,
      code: first.extensions?.code || null,
      requestId: error?.requestId || null,
      errors: error?.errors || null,
    });
  }
}

export function apiSWRFetcher([operationName, query, variables]) {
  return apiGraphQL(operationName, { query, variables });
}
