import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { readFileSync } from "node:fs";

const REMOTE_URL = process.env.KEENABLE_MCP_URL ?? "https://api.keenable.ai/mcp";
const MAX_CONNECT_ATTEMPTS = 3;

const args = process.argv.slice(2);
let fallbackToolsFile = null;
let fallbackServerName = null;
for (let index = 0; index < args.length; index += 1) {
  if (args[index] === "--fallback-tools-file" && args[index + 1]) {
    fallbackToolsFile = args[index + 1];
    index += 1;
  } else if (args[index] === "--fallback-server-name" && args[index + 1]) {
    fallbackServerName = args[index + 1];
    index += 1;
  }
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

const sleep = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

async function connectRemote() {
  let lastError;

  for (let attempt = 1; attempt <= MAX_CONNECT_ATTEMPTS; attempt += 1) {
    const client = new Client(
      { name: "privacytrace-keenable-adapter", version: "1.0.0" },
      { capabilities: {} },
    );
    const requestInit = process.env.KEENABLE_API_KEY
      ? { headers: { "X-API-Key": process.env.KEENABLE_API_KEY } }
      : {};
    const transport = new StreamableHTTPClientTransport(new URL(REMOTE_URL), {
      requestInit,
    });

    try {
      await client.connect(transport);
      return client;
    } catch (error) {
      lastError = error;
      await client.close().catch(() => undefined);
      if (attempt < MAX_CONNECT_ATTEMPTS) {
        await sleep(1000 * attempt);
      }
    }
  }

  throw lastError;
}

let remoteClient = null;
try {
  remoteClient = await connectRemote();
} catch (error) {
  if (!fallbackTools.length) {
    throw error;
  }
  console.error(
    `Remote tools/list unavailable; using ${fallbackTools.length} cached tool definitions`,
  );
}

async function retryReadOnly(operation) {
  try {
    if (!remoteClient) {
      remoteClient = await connectRemote();
    }
    return await operation(remoteClient);
  } catch {
    await remoteClient?.close().catch(() => undefined);
    remoteClient = await connectRemote();
    return operation(remoteClient);
  }
}

const server = new Server(
  { name: "keenable-web-search", version: "1.0.0" },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => {
  try {
    return await retryReadOnly((client) => client.listTools());
  } catch (error) {
    if (!fallbackTools.length) {
      throw error;
    }
    return { tools: fallbackTools };
  }
});
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  try {
    return await retryReadOnly((client) => client.callTool(request.params));
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
