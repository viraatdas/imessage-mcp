# imessage-mcp

MCP server that exposes iMessage read/send capabilities on macOS. Runs on a Mac with Messages.app and can be accessed remotely via SSH over Tailscale.

## Tools

| Tool | Description |
|------|-------------|
| `list_conversations` | List recent conversations with last message preview |
| `get_messages` | Get messages from a specific conversation |
| `search_messages` | Full-text search across all messages |
| `get_contact_info` | Look up contact stats by phone/email |
| `send_message` | Send an iMessage to a phone number or email |
| `send_group_message` | Send a message to a named group chat |

## Setup (local)

```bash
uv run imessage-mcp
```

Requires macOS with Messages.app and Full Disk Access for the terminal process.

## Claude Code config (local)

```json
{
  "mcpServers": {
    "imessage": {
      "command": "uv",
      "args": ["--directory", "/path/to/imessage-mcp", "run", "imessage-mcp"]
    }
  }
}
```

## Claude Code config (remote via SSH)

From any device on the Tailscale network:

```json
{
  "mcpServers": {
    "imessage": {
      "command": "ssh",
      "args": [
        "mba-server",
        "/Users/viraat/.local/bin/uv --directory /Users/viraat/Documents/imessage-mcp run imessage-mcp"
      ]
    }
  }
}
```

This pipes MCP stdio through SSH — the server runs on the Mac, reads chat.db, and sends via AppleScript.
