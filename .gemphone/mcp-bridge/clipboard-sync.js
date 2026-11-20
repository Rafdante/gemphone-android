import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { execFileSync } from "child_process";
import fs from "fs";
import path from "path";

const SYNC_FILE = path.join(process.env.HOME || "/sdcard", ".gemphone_clipboard_sync.json");
const MAX_TEXT_LENGTH = 4000;

const server = new Server(
  { name: "gemphone-clipboard-sync", version: "1.0.1" },
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
  const { name, arguments: args = {} } = request.params;
  try {
    if (name === "sync_clipboard_get") {
      // Сначала читаем системный буфер через termux-api, при ошибке — файл синхронизации.
      let text = "";
      try {
        text = execFileSync("termux-clipboard-get").toString().trim();
      } catch (e) {
        if (fs.existsSync(SYNC_FILE)) {
          const data = JSON.parse(fs.readFileSync(SYNC_FILE, "utf8"));
          text = typeof data.text === "string" ? data.text : "";
        }
      }
      return { content: [{ type: "text", text }] };
    }
    if (name === "sync_clipboard_set") {
      if (typeof args.text !== "string") throw new Error("text должен быть строкой");
      if (args.text.length > MAX_TEXT_LENGTH) throw new Error("text слишком длинный");
      const text = args.text;
      try {
        // Текст передаётся через stdin, а не через строку команды — экранирование не нужно.
        execFileSync("termux-clipboard-set", { input: text });
      } catch (e) {
        // termux-api может быть недоступен — тогда остаётся только файл синхронизации.
      }
      fs.writeFileSync(SYNC_FILE, JSON.stringify({ text, timestamp: Date.now() }), { mode: 0o600 });
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
