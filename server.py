"""
iMessage MCP Server — read and send iMessages on macOS.
Exposes tools via MCP stdio transport for use with Claude Code and other MCP clients.
"""

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

CHATDB_PATH = Path.home() / "Library" / "Messages" / "chat.db"
APPLE_EPOCH = 978307200  # 2001-01-01 00:00:00 UTC


def _apple_ts_to_iso(apple_date: int) -> str:
    """Convert Apple nanosecond timestamp to ISO 8601 string."""
    if apple_date is None:
        return ""
    unix_ts = apple_date / 1_000_000_000 + APPLE_EPOCH
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()


def _get_db():
    """Open chat.db read-only."""
    db = sqlite3.connect(f"file:{CHATDB_PATH}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _escape_applescript(text: str) -> str:
    """Escape text for safe AppleScript string interpolation."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# --- MCP Server ---

mcp = FastMCP(
    "imessage",
    instructions="iMessage MCP server running on a macOS machine. Can read and send iMessages.",
)


@mcp.tool()
async def list_conversations(limit: int = 20) -> str:
    """List recent iMessage/SMS conversations with last message preview.

    Args:
        limit: Max number of conversations to return (default 20)
    """
    db = _get_db()
    rows = db.execute(
        """
        SELECT
            c.chat_identifier,
            c.display_name,
            c.service_name,
            MAX(m.date) as last_date,
            COUNT(m.ROWID) as message_count,
            (SELECT m2.text FROM message m2
             JOIN chat_message_join cmj2 ON cmj2.message_id = m2.ROWID
             WHERE cmj2.chat_id = c.ROWID AND m2.text IS NOT NULL
             ORDER BY m2.date DESC LIMIT 1) as last_message
        FROM chat c
        LEFT JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
        LEFT JOIN message m ON cmj.message_id = m.ROWID
        GROUP BY c.ROWID
        ORDER BY last_date DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    db.close()

    conversations = []
    for r in rows:
        conversations.append(
            {
                "chat_identifier": r["chat_identifier"],
                "display_name": r["display_name"] or "",
                "service": r["service_name"] or "",
                "message_count": r["message_count"],
                "last_message_date": _apple_ts_to_iso(r["last_date"]),
                "last_message": (r["last_message"] or "")[:200],
            }
        )
    return json.dumps(conversations, indent=2)


@mcp.tool()
async def get_messages(
    chat_identifier: str,
    limit: int = 50,
) -> str:
    """Get messages from a specific conversation.

    Args:
        chat_identifier: Phone number (e.g. +15551234567), email, or group chat identifier
        limit: Max messages to return (default 50, most recent first)
    """
    db = _get_db()
    rows = db.execute(
        """
        SELECT
            m.text,
            m.date as apple_date,
            m.is_from_me,
            COALESCE(h.id, 'me') as sender,
            m.associated_message_type,
            m.balloon_bundle_id
        FROM message m
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        LEFT JOIN chat c ON c.ROWID = cmj.chat_id
        WHERE (c.chat_identifier = ? OR h.id = ?)
          AND m.text IS NOT NULL
          AND m.text != ''
        ORDER BY m.date DESC
        LIMIT ?
        """,
        (chat_identifier, chat_identifier, limit),
    ).fetchall()
    db.close()

    messages = []
    for r in rows:
        messages.append(
            {
                "text": r["text"],
                "date": _apple_ts_to_iso(r["apple_date"]),
                "from_me": bool(r["is_from_me"]),
                "sender": r["sender"] if not r["is_from_me"] else "me",
            }
        )
    # Return in chronological order
    messages.reverse()
    return json.dumps(messages, indent=2)


@mcp.tool()
async def search_messages(
    query: str,
    limit: int = 30,
) -> str:
    """Search across all iMessage conversations for messages containing a query string.

    Args:
        query: Text to search for (case-insensitive)
        limit: Max results to return (default 30)
    """
    db = _get_db()
    rows = db.execute(
        """
        SELECT
            m.text,
            m.date as apple_date,
            m.is_from_me,
            COALESCE(h.id, 'me') as sender,
            c.chat_identifier,
            c.display_name
        FROM message m
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        LEFT JOIN chat c ON c.ROWID = cmj.chat_id
        WHERE m.text LIKE '%' || ? || '%'
        ORDER BY m.date DESC
        LIMIT ?
        """,
        (query, limit),
    ).fetchall()
    db.close()

    results = []
    for r in rows:
        results.append(
            {
                "text": r["text"],
                "date": _apple_ts_to_iso(r["apple_date"]),
                "from_me": bool(r["is_from_me"]),
                "sender": r["sender"] if not r["is_from_me"] else "me",
                "chat_identifier": r["chat_identifier"] or "",
                "chat_name": r["display_name"] or "",
            }
        )
    return json.dumps(results, indent=2)


@mcp.tool()
async def get_contact_info(identifier: str) -> str:
    """Look up a contact/handle by phone number or email and return conversation stats.

    Args:
        identifier: Phone number (e.g. +15551234567) or email address
    """
    db = _get_db()
    row = db.execute(
        """
        SELECT
            h.id,
            h.service,
            h.country,
            COUNT(m.ROWID) as total_messages,
            SUM(CASE WHEN m.is_from_me = 1 THEN 1 ELSE 0 END) as sent,
            SUM(CASE WHEN m.is_from_me = 0 THEN 1 ELSE 0 END) as received,
            MIN(m.date) as first_message,
            MAX(m.date) as last_message
        FROM handle h
        LEFT JOIN message m ON m.handle_id = h.ROWID
        WHERE h.id = ?
        GROUP BY h.ROWID
        """,
        (identifier,),
    ).fetchone()
    db.close()

    if not row:
        return json.dumps({"error": f"No contact found for {identifier}"})

    return json.dumps(
        {
            "identifier": row["id"],
            "service": row["service"],
            "country": row["country"] or "",
            "total_messages": row["total_messages"],
            "sent": row["sent"],
            "received": row["received"],
            "first_message": _apple_ts_to_iso(row["first_message"]),
            "last_message": _apple_ts_to_iso(row["last_message"]),
        },
        indent=2,
    )


@mcp.tool()
async def send_message(recipient: str, text: str) -> str:
    """Send an iMessage to a phone number or email address.

    Args:
        recipient: Phone number (e.g. +15551234567) or email address
        text: Message text to send
    """
    escaped_recipient = _escape_applescript(recipient)
    escaped_text = _escape_applescript(text)

    script = (
        'tell application "Messages"\n'
        f'  send "{escaped_text}" to buddy "{escaped_recipient}" '
        'of (service 1 whose service type is iMessage)\n'
        "end tell"
    )

    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        return json.dumps(
            {"error": f"Failed to send: {stderr.decode().strip()}"}
        )

    return json.dumps({"status": "sent", "recipient": recipient})


@mcp.tool()
async def send_group_message(group_name: str, text: str) -> str:
    """Send a message to a named group chat.

    Args:
        group_name: The display name of the group chat
        text: Message text to send
    """
    # Resolve group name to chat identifier
    db = _get_db()
    row = db.execute(
        "SELECT chat_identifier FROM chat WHERE display_name = ? LIMIT 1",
        (group_name,),
    ).fetchone()
    db.close()

    if not row:
        return json.dumps({"error": f"Group chat '{group_name}' not found"})

    escaped_text = _escape_applescript(text)
    chat_id = row["chat_identifier"]

    script = (
        'tell application "Messages"\n'
        f'  send "{escaped_text}" to chat id "{chat_id}"\n'
        "end tell"
    )

    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        return json.dumps(
            {"error": f"Failed to send to group: {stderr.decode().strip()}"}
        )

    return json.dumps(
        {"status": "sent", "group": group_name, "chat_identifier": chat_id}
    )


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
