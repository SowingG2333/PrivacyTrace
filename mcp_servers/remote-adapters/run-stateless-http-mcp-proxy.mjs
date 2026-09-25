import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { readFileSync } from "node:fs";
import { EnvHttpProxyAgent, fetch } from "undici";

const proxyDispatcher = [
  "HTTP_PROXY",
  "HTTPS_PROXY",
  "ALL_PROXY",
  "http_proxy",
  "https_proxy",
  "all_proxy",
].some((name) => process.env[name])
  ? new EnvHttpProxyAgent()
  : undefined;

const args = process.argv.slice(2);
const endpoint = args[0];
if (!endpoint) {
  console.error(
    "Usage: node run-stateless-http-mcp-proxy.mjs <endpoint> " +
      "[--ignore-tool <name>] [--timeout-ms <milliseconds>]",
  );
  process.exit(2);
}

const ignoredTools = new Set();
let timeoutMs = 90_000;
let fallbackToolsFile = null;
let fallbackServerName = null;
for (let index = 1; index < args.length; index += 1) {
  if (args[index] === "--ignore-tool" && args[index + 1]) {
    ignoredTools.add(args[index + 1]);
    index += 1;
  } else if (args[index] === "--timeout-ms" && args[index + 1]) {
    timeoutMs = Number(args[index + 1]);
    index += 1;
  } else if (args[index] === "--fallback-tools-file" && args[index + 1]) {
    fallbackToolsFile = args[index + 1];
    index += 1;
  } else if (args[index] === "--fallback-server-name" && args[index + 1]) {
    fallbackServerName = args[index + 1];
    index += 1;
  }
}
if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
  console.error("--timeout-ms must be a positive number");
  process.exit(2);
}

let fallbackTools = [];
if (fallbackToolsFile && fallbackServerName) {
  const snapshot = JSON.parse(readFileSync(fallbackToolsFile, "utf8"));
  fallbackTools = (Array.isArray(snapshot.tools) ? snapshot.tools : [])
    .filter((tool) => tool.server_name === fallbackServerName)
    .map((tool) => ({
      name: tool.tool_name,
      description: tool.description || "",
      inputSchema: tool.input_schema || { type: "object", properties: {} },
    }));
}

let requestId = 0;
function parseResponseBody(body) {
  try {
    return JSON.parse(body);
  } catch {
    // Some stateless MCP endpoints return a single JSON-RPC response wrapped
    // in a text/event-stream event even when application/json was requested.
  }

  for (const event of body.split(/\r?\n\r?\n/)) {
    const data = event
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice("data:".length).trimStart())
      .join("\n");
    if (!data || data === "[DONE]") {
      continue;
    }
    try {
      const payload = JSON.parse(data);
      if (payload && typeof payload === "object") {
        return payload;
      }
    } catch {
      // Continue in case the response contains another parseable SSE event.
    }
  }

  throw new Error(
    `Remote MCP returned invalid JSON/SSE: ${body.slice(0, 500)}`,
  );
}

async function forward(method, params = {}) {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      accept: "application/json, text/event-stream",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: (requestId += 1),
      method,
      params,
    }),
    signal: AbortSignal.timeout(timeoutMs),
    dispatcher: proxyDispatcher,
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(`Remote MCP HTTP ${response.status}: ${body.slice(0, 500)}`);
  }

  let payload;
  try {
    payload = parseResponseBody(body);
  } catch (error) {
    throw error;
  }
  if (payload.error) {
    const message =
      payload.error.message || JSON.stringify(payload.error).slice(0, 500);
    throw new Error(`Remote MCP error: ${message}`);
  }
  if (!payload.result || typeof payload.result !== "object") {
    throw new Error("Remote MCP response is missing an object result");
  }
  return payload.result;
}

const server = new Server(
  { name: "stateless-http-mcp-proxy", version: "1.0.0" },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => {
  let result;
  try {
    result = await forward("tools/list");
  } catch (error) {
    if (!fallbackTools.length) {
      throw error;
    }
    console.error(
      `Remote tools/list unavailable; using ${fallbackTools.length} cached tool definitions`,
    );
    result = { tools: fallbackTools };
  }
  const tools = Array.isArray(result.tools) ? result.tools : [];
  return {
    ...result,
    tools: tools.filter((tool) => !ignoredTools.has(tool.name)),
  };
});

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  if (ignoredTools.has(request.params.name)) {
    return {
      isError: true,
      content: [
        {
          type: "text",
          text: `Tool ${request.params.name} is disabled by local policy`,
        },
      ],
    };
  }
  try {
    return await forward("tools/call", request.params);
  } catch (error) {
    return {
      isError: true,
      content: [
        {
          type: "text",
          text: error instanceof Error ? error.message : String(error),
        },
      ],
    };
  }
});

await server.connect(new StdioServerTransport());
