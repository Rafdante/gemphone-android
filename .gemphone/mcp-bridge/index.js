import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { execSync } from "child_process";

const server = new Server(
  {
    name: "gemphone-mcp-bridge",
    version: "1.0.0",
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
            x: { type: "number" },
            y: { type: "number" }
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
  const { name, arguments: args } = request.params;
  try {
    if (name === "phone_clipboard_get") {
      const res = execSync("termux-clipboard-get").toString();
      return { content: [{ type: "text", text: res }] };
    }
    if (name === "phone_clipboard_set") {
      execSync(`termux-clipboard-set "${args.text}"`);
      return { content: [{ type: "text", text: "Clipboard set successfully" }] };
    }
    if (name === "phone_battery") {
      const res = execSync("termux-battery-status").toString();
      return { content: [{ type: "text", text: res }] };
    }
    if (name === "adb_screenshot") {
      execSync("adb shell screencap -p /sdcard/screenshot.png && adb pull /sdcard/screenshot.png ./screenshot.png");
      return { content: [{ type: "text", text: "Screenshot saved to ./screenshot.png" }] };
    }
    if (name === "adb_tap") {
      execSync(`adb shell input tap ${args.x} ${args.y}`);
      return { content: [{ type: "text", text: `Tapped at ${args.x}, ${args.y}` }] };
    }
    if (name === "adb_type") {
      execSync(`adb shell input text "${args.text}"`);
      return { content: [{ type: "text", text: `Typed: ${args.text}` }] };
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
