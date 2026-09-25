import os from "node:os";
import path from "node:path";

// mcp-remote persists OAuth metadata even for some anonymous endpoints. Keep
// that runtime state outside the repository and never reuse a developer's
// personal MCP authentication directory.
process.env.MCP_REMOTE_CONFIG_DIR ??= path.join(
  os.tmpdir(),
  "privacytrace-mcp-remote-auth",
);

await import("./node_modules/mcp-remote/dist/proxy.js");
