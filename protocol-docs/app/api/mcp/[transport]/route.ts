import { source, getLLMText } from '@/lib/source';
import { NextRequest, NextResponse } from 'next/server';

// --------------------------------------------------------------------------
// Lightweight MCP-compatible JSON-RPC handler.
// Implements the subset needed for tool discovery and invocation:
//   - initialize       (handshake)
//   - tools/list        (enumerate available tools)
//   - tools/call        (invoke a tool)
// --------------------------------------------------------------------------

const SERVER_INFO = {
  name: 'rld-docs',
  version: '1.0.0',
};

const TOOLS = [
  {
    name: 'list_pages',
    description: 'List all available RLD documentation pages with titles, descriptions, and URLs',
    inputSchema: { type: 'object' as const, properties: {} },
  },
  {
    name: 'get_page',
    description: 'Get the full Markdown content of a page by slug (e.g. "traders/core-concepts")',
    inputSchema: {
      type: 'object' as const,
      properties: {
        slug: { type: 'string', description: 'Page slug, e.g. "traders/core-concepts"' },
      },
      required: ['slug'],
    },
  },
  {
    name: 'search_docs',
    description: 'Search RLD documentation by keyword across titles and descriptions',
    inputSchema: {
      type: 'object' as const,
      properties: {
        query: { type: 'string', description: 'Search query' },
      },
      required: ['query'],
    },
  },
];

// ---- Tool implementations ------------------------------------------------

async function listPages() {
  const pages = source.getPages();
  const list = pages.map((p) => ({
    title: p.data.title,
    description: p.data.description ?? '',
    url: `https://docs.rld.fi${p.url}`,
    slug: p.slugs.join('/'),
  }));
  return [{ type: 'text', text: JSON.stringify(list, null, 2) }];
}

async function getPage(slug: string) {
  const segments = slug.split('/').filter(Boolean);
  const page = source.getPage(segments);
  if (!page) {
    return [{ type: 'text', text: `Page not found: ${slug}. Use list_pages to see available pages.` }];
  }
  const text = await getLLMText(page);
  return [{ type: 'text', text }];
}

async function searchDocs(query: string) {
  const pages = source.getPages();
  const q = query.toLowerCase();
  const matches = pages.filter(
    (p) =>
      p.data.title.toLowerCase().includes(q) ||
      (p.data.description ?? '').toLowerCase().includes(q)
  );
  if (matches.length === 0) {
    return [{ type: 'text', text: `No pages found matching "${query}".` }];
  }
  const results = matches.map((p) => ({
    title: p.data.title,
    description: p.data.description ?? '',
    url: `https://docs.rld.fi${p.url}`,
    slug: p.slugs.join('/'),
  }));
  return [{ type: 'text', text: JSON.stringify(results, null, 2) }];
}

// ---- JSON-RPC router -----------------------------------------------------

interface JsonRpcRequest {
  jsonrpc: '2.0';
  id?: string | number;
  method: string;
  params?: Record<string, unknown>;
}

function jsonrpc(id: string | number | undefined, result: unknown) {
  return NextResponse.json({ jsonrpc: '2.0', id: id ?? null, result });
}

function jsonrpcError(id: string | number | undefined, code: number, message: string) {
  return NextResponse.json({ jsonrpc: '2.0', id: id ?? null, error: { code, message } });
}

async function handleRpc(req: JsonRpcRequest): Promise<NextResponse> {
  switch (req.method) {
    case 'initialize':
      return jsonrpc(req.id, {
        protocolVersion: '2025-03-26',
        capabilities: { tools: {} },
        serverInfo: SERVER_INFO,
      });

    case 'notifications/initialized':
      return new NextResponse(null, { status: 204 });

    case 'tools/list':
      return jsonrpc(req.id, { tools: TOOLS });

    case 'tools/call': {
      const params = req.params as { name: string; arguments?: Record<string, string> };
      const args = params.arguments ?? {};

      switch (params.name) {
        case 'list_pages':
          return jsonrpc(req.id, { content: await listPages() });
        case 'get_page':
          return jsonrpc(req.id, { content: await getPage(args.slug ?? '') });
        case 'search_docs':
          return jsonrpc(req.id, { content: await searchDocs(args.query ?? '') });
        default:
          return jsonrpcError(req.id, -32601, `Unknown tool: ${params.name}`);
      }
    }

    default:
      return jsonrpcError(req.id, -32601, `Method not found: ${req.method}`);
  }
}

// ---- HTTP handlers -------------------------------------------------------

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    return handleRpc(body as JsonRpcRequest);
  } catch {
    return jsonrpcError(undefined, -32700, 'Parse error');
  }
}

export async function GET() {
  return NextResponse.json({
    name: SERVER_INFO.name,
    version: SERVER_INFO.version,
    description: 'RLD Protocol documentation MCP server — search and retrieve docs, contract references, and research papers.',
    tools: TOOLS.map((t) => ({ name: t.name, description: t.description })),
    endpoints: {
      post: 'Send JSON-RPC requests to this URL',
    },
  });
}
