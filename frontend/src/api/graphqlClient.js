export class GraphQLRequestError extends Error {
  constructor(message, { status = null, errors = null, response = null, requestId = null } = {}) {
    super(message);
    this.name = "GraphQLRequestError";
    this.status = status;
    this.errors = errors;
    this.response = response;
    this.requestId = requestId;
  }
}

export async function postGraphQL(
  url,
  { query, variables = undefined, operationName = undefined, headers = undefined, signal = undefined },
) {
  const payload = { query };
  if (variables !== undefined) {
    payload.variables = variables;
  }
  if (operationName !== undefined) {
    payload.operationName = operationName;
  }

  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(headers || {}),
    },
    body: JSON.stringify(payload),
    signal,
  });
  const requestId = res.headers.get("x-request-id");

  let json = null;
  try {
    json = await res.json();
  } catch {
    throw new GraphQLRequestError(`Invalid JSON response from ${url}`, {
      status: res.status,
      requestId,
    });
  }

  if (!res.ok) {
    throw new GraphQLRequestError(
      `GraphQL HTTP ${res.status}`,
      { status: res.status, errors: json?.errors || null, response: json, requestId },
    );
  }

  if (json?.errors?.length) {
    throw new GraphQLRequestError(
      json.errors[0]?.message || "GraphQL query failed",
      { status: res.status, errors: json.errors, response: json, requestId },
    );
  }

  return json?.data ?? null;
}

export async function swrGraphQLFetcher([url, query, variables]) {
  return postGraphQL(url, { query, variables });
}
