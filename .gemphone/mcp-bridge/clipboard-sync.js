import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { execSync } from "child_process";
import fs from "fs";
import path from "path";

const SYNC_FILE = path.join(process.env.HOME || "/sdcard", ".gemphone_clipboard_sync.json");

const server = new Server(
  { name: "gemphone-clipboard-sync", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "sync_clipboard_get",
      description: "Get clipboard synchronized with phone",
      inputSchema: { type: "object", properties: {} }
    },
    {
      name: "sync_clipboard_set",
      description: "Set clipboard on phone and sync",
      inputSchema: {
        type: "object",
        properties: { text: { type: "string" } },
        required: ["text"]
      }
    }
  ]
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  try {
    if (name === "sync_clipboard_get") {
      // Try to read from phone clipboard via termux-api
      let text = "";
      try {
        text = execSync("termux-clipboard-get").toString().trim();
      } catch (e) {
        if (fs.existsSync(SYNC_FILE)) {
          const data = JSON.parse(fs.readFileSync(SYNC_FILE, "utf8"));
          text = data.text || "";
        }
      }
      return { content: [{ type: "text", text }] };
    }
    if (name === "sync_clipboard_set") {
      const text = args.text;
      try {
        execSync(`termux-clipboard-set "${text.replace(/"/g, '\\"')}"`);
      } catch (e) {}
      fs.writeFileSync(SYNC_FILE, JSON.stringify({ text, timestamp: Date.now() }));
      return { content: [{ type: "text", text: "Clipboard synced successfully" }] };
    }
    throw new Error(`Unknown tool: ${name}`);
  } catch (error) {
    return { content: [{ type: "text", text: `Error: ${error.message}` }], isError: true };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
console.error("Clipboard Sync MCP running");
