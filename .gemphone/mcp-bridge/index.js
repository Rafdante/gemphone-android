import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { execFileSync } from "child_process";

// Все внешние команды запускаются через execFileSync с массивом аргументов:
// shell не участвует, поэтому метасимволы из аргументов не выполняются.

const MAX_TEXT_LENGTH = 4000;

function requireText(value, name) {
  if (typeof value !== "string") throw new Error(`${name} должен быть строкой`);
  if (value.length > MAX_TEXT_LENGTH) throw new Error(`${name} слишком длинный`);
  return value;
}

function requireCoord(value, name) {
  const n = Number(value);
  if (!Number.isInteger(n) || n < 0 || n > 100000) {
    throw new Error(`${name} должен быть целым числом 0..100000`);
  }
  return n;
}

// adb shell склеивает аргументы и отдаёт строку на выполнение устройству (sh).
// Поэтому текст оборачиваем в одинарные кавычки; пробелы кодируем как %s
// (так работает `input text`), а одинарные кавычки экранируем.
function deviceShellQuote(text) {
  return "'" + text.replace(/ /g, "%s").replace(/'/g, `'\\''`) + "'";
}

const server = new Server(
  {
    name: "gemphone-mcp-bridge",
    version: "1.0.1",
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      {
        name: "phone_clipboard_get",
        description: "Get Android clipboard content",
        inputSchema: { type: "object", properties: {} }
      },
      {
        name: "phone_clipboard_set",
        description: "Set Android clipboard content",
        inputSchema: {
          type: "object",
          properties: {
            text: { type: "string", description: "Text to set" }
          },
          required: ["text"]
        }
      },
      {
        name: "phone_battery",
        description: "Get phone battery status",
        inputSchema: { type: "object", properties: {} }
      },
      {
        name: "adb_screenshot",
        description: "Take a screenshot using ADB and return path",
        inputSchema: { type: "object", properties: {} }
      },
      {
        name: "adb_tap",
        description: "Tap at X, Y coordinates on screen",
        inputSchema: {
          type: "object",
          properties: {
            x: { type: "integer", minimum: 0 },
            y: { type: "integer", minimum: 0 }
          },
          required: ["x", "y"]
        }
      },
      {
        name: "adb_type",
        description: "Type text on phone screen",
        inputSchema: {
          type: "object",
          properties: {
            text: { type: "string" }
          },
          required: ["text"]
        }
      }
    ]
  };
});

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args = {} } = request.params;
  try {
    if (name === "phone_clipboard_get") {
      const res = execFileSync("termux-clipboard-get").toString();
      return { content: [{ type: "text", text: res }] };
    }
    if (name === "phone_clipboard_set") {
      const text = requireText(args.text, "text");
      // termux-clipboard-set читает текст из stdin — аргументы не нужны.
      execFileSync("termux-clipboard-set", { input: text });
      return { content: [{ type: "text", text: "Clipboard set successfully" }] };
    }
    if (name === "phone_battery") {
      const res = execFileSync("termux-battery-status").toString();
      return { content: [{ type: "text", text: res }] };
    }
    if (name === "adb_screenshot") {
      execFileSync("adb", ["shell", "screencap", "-p", "/sdcard/screenshot.png"]);
      execFileSync("adb", ["pull", "/sdcard/screenshot.png", "./screenshot.png"]);
      return { content: [{ type: "text", text: "Screenshot saved to ./screenshot.png" }] };
    }
    if (name === "adb_tap") {
      const x = requireCoord(args.x, "x");
      const y = requireCoord(args.y, "y");
      execFileSync("adb", ["shell", "input", "tap", String(x), String(y)]);
      return { content: [{ type: "text", text: `Tapped at ${x}, ${y}` }] };
    }
    if (name === "adb_type") {
      const text = requireText(args.text, "text");
      execFileSync("adb", ["shell", `input text ${deviceShellQuote(text)}`]);
      return { content: [{ type: "text", text: `Typed: ${text}` }] };
    }
    throw new Error(`Unknown tool: ${name}`);
  } catch (error) {
    return {
      content: [{ type: "text", text: `Error: ${error.message}` }],
      isError: true,
    };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
console.error("GemPhone MCP Bridge running on stdio");
