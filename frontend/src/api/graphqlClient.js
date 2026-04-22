export class GraphQLRequestError extends Error {
  constructor(message, { status = null, errors = null, response = null } = {}) {
    super(message);
    this.name = "GraphQLRequestError";
    this.status = status;
    this.errors = errors;
    this.response = response;
  }
}

export async function postGraphQL(
  url,
  { query, variables = undefined, headers = undefined, signal = undefined },
) {
  const payload = { query };
  if (variables !== undefined) {
    payload.variables = variables;
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

  let json = null;
  try {
    json = await res.json();
  } catch {
    throw new GraphQLRequestError(`Invalid JSON response from ${url}`, {
      status: res.status,
    });
  }

  if (!res.ok) {
    throw new GraphQLRequestError(
      `GraphQL HTTP ${res.status}`,
      { status: res.status, errors: json?.errors || null, response: json },
    );
  }

  if (json?.errors?.length) {
    throw new GraphQLRequestError(
      json.errors[0]?.message || "GraphQL query failed",
      { status: res.status, errors: json.errors, response: json },
    );
  }

  return json?.data ?? null;
}

export async function swrGraphQLFetcher([url, query, variables]) {
  return postGraphQL(url, { query, variables });
}
