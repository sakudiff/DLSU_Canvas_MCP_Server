# DLSU Canvas MCP Server

A standalone, public-ready Model Context Protocol (MCP) server for De La Salle University (DLSU) Canvas LMS integration. This server allows LLM-based coding agents (like Claude Desktop or Gemini Antigravity) to read course files, sync active term structures to local disk, check todo lists, submit assignments, and scrape Zoom schedules.

## Features

- **Course Navigation**: List, find, and search active courses fuzzy-matched by name or course code.
- **Task & Schedule Tracking**: Check daily snapshots, week-at-a-glance planner, immediate actions needed (`todo()`), and upcoming deadlines.
- **Syllabus & Material Sync**: Concurrent downloading and syncing of announcements, resources, and assignment instructions to a local structured directory.
- **Grades & Feedback**: Query scores and comments for individual tasks or overall course averages.
- **Communication & Inbox**: Monitor unread Canvas messages and inspect full message threads.

- **Zoom Link Extractor**: Deep scrape Zoom links and schedules from syllabus pages, announcements, and calendar events. Export as a `.ics` calendar file saved to your Desktop.

## Prerequisites

- **Python**: Version 3.11 or higher.
- **Package Manager**: [uv](https://github.com/astral-sh/uv) — install it for your platform:

  | Platform | Install command |
  |---|---|
  | macOS / Linux | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
  | Windows (PowerShell) | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |

  After installation, restart your terminal so `uv` is on `PATH`.

## Setup & Installation

### Step 1 — Clone the repository

```bash
git clone https://github.com/your-org/DLSU_Canvas_MCP_Server.git
cd DLSU_Canvas_MCP_Server
```

### Step 2 — Configure environment variables

Create a `.env` file in the project root:

```env
CANVAS_API_URL=https://canvas.dlsu.edu.ph
CANVAS_API_KEY=your_canvas_api_token
DRIVE_ROOT=/absolute/path/to/your/sync/folder

# Optional: Timezone overrides (defaults to Manila UTC+8)
CANVAS_TIMEZONE_OFFSET=8
CANVAS_TIMEZONE_NAME=Asia/Manila
```

> **Placeholder Reference — replace every value in this table before running:**
>
> | Placeholder | What to replace it with |
> |---|---|
> | `https://canvas.dlsu.edu.ph` | Your institution's Canvas base URL (no trailing slash) |
> | `your_canvas_api_token` | Your personal Canvas API token (see below) |
> | `/absolute/path/to/your/sync/folder` | Absolute path to an **existing** local directory where synced materials will be saved |
>
> **Platform-specific `DRIVE_ROOT` examples:**
>
> | Platform | Example value |
> |---|---|
> | macOS | `/Users/yourusername/Documents/Canvas` |
> | Linux | `/home/yourusername/Documents/Canvas` |
> | Windows | `C:/Users/yourusername/Documents/Canvas` (use forward slashes inside `.env`) |

**To generate a Canvas API token:**
1. Log in to Canvas → Account → Settings → Approved Integrations.
2. Click **New Access Token**, set an expiry, and copy the token immediately.
3. Paste it as the value of `CANVAS_API_KEY` in your `.env`.

### Step 3 — Install dependencies

```bash
uv sync
```

### Step 4 — Verify the server starts

```bash
uv run canvas_server.py --help
```

The server should print usage and exit cleanly. If it throws a credentials error, re-check your `.env` values.

---

### How to Find a Course ID

When calling course-specific tools you will need a Canvas Course ID. Two ways to find it:

- **Web URL**: Open the course in your browser. The URL has the format `https://canvas.dlsu.edu.ph/courses/<COURSE_ID>`. The number after `/courses/` is the ID.
- **Server tools**: Call `list_courses` or `find` from within your LLM agent to list all active courses with their numeric IDs.

---

## Running the Server

### Stdio transport (default)

Standard input/output mode — used by most LLM agent clients:

```bash
uv run canvas_server.py
```

### SSE transport (optional)

Local HTTP server-sent events mode for clients that need a persistent HTTP endpoint:

```bash
uv run canvas_server.py --transport sse --port 8080
```

---

## Client Integration

> **Before saving any config below, replace these placeholders:**
>
> | Placeholder | Replace with |
> |---|---|
> | `/path/to/DLSU_Canvas_MCP_Server/canvas_server.py` | Absolute path to `canvas_server.py` on your machine (see OS examples below) |
> | `https://canvas.dlsu.edu.ph` | Your institution's Canvas URL |
> | `your_canvas_api_token` | Your Canvas API token |
> | `/absolute/path/to/your/sync/folder` | Your local sync directory |
>
> **Path to `canvas_server.py` by OS:**
>
> | Platform | Example |
> |---|---|
> | macOS | `/Users/yourusername/Code/DLSU_Canvas_MCP_Server/canvas_server.py` |
> | Linux | `/home/yourusername/Code/DLSU_Canvas_MCP_Server/canvas_server.py` |
> | Windows | `C:/Users/yourusername/Code/DLSU_Canvas_MCP_Server/canvas_server.py` |
>
> On Windows, use **forward slashes** (`/`) in JSON config values to avoid escaping issues.

### 1. Claude Desktop

**Config file location:**

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/claude/claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "dlsu-canvas": {
      "command": "uv",
      "args": [
        "run",
        "--active",
        "/path/to/DLSU_Canvas_MCP_Server/canvas_server.py"
      ],
      "env": {
        "CANVAS_API_URL": "https://canvas.dlsu.edu.ph",
        "CANVAS_API_KEY": "your_canvas_api_token",
        "DRIVE_ROOT": "/absolute/path/to/your/sync/folder"
      }
    }
  }
}
```

### 2. Cursor / VSCode MCP

**Via `.cursor/mcp.json`** (works on all platforms — place this file in the project root or your home directory):

```json
{
  "mcpServers": {
    "dlsu-canvas": {
      "command": "uv",
      "args": [
        "run",
        "/path/to/DLSU_Canvas_MCP_Server/canvas_server.py"
      ],
      "env": {
        "CANVAS_API_URL": "https://canvas.dlsu.edu.ph",
        "CANVAS_API_KEY": "your_canvas_api_token",
        "DRIVE_ROOT": "/absolute/path/to/your/sync/folder"
      }
    }
  }
}
```

**Via the Cursor UI:** Open Cursor Settings → Features → MCP → Add new MCP Server, then set the command to `uv`, args to `run /path/to/canvas_server.py`, and add the three environment variables manually.

### 3. Antigravity 2.0 / Opencode / Pi Coding Agent / Qwen Code

These four clients share the same JSON config structure. Add the block below to your config file, then locate that file using the table.

```json
{
  "mcpServers": {
    "dlsu-canvas": {
      "command": "uv",
      "args": [
        "run",
        "/path/to/DLSU_Canvas_MCP_Server/canvas_server.py"
      ],
      "env": {
        "CANVAS_API_URL": "https://canvas.dlsu.edu.ph",
        "CANVAS_API_KEY": "your_canvas_api_token",
        "DRIVE_ROOT": "/absolute/path/to/your/sync/folder"
      }
    }
  }
}
```

**Config file locations:**

| Client | macOS / Linux | Windows |
|---|---|---|
| Google Antigravity 2.0 | `~/.gemini/antigravity-cli/mcp_config.json` (or run `antigravity config edit`) | `%USERPROFILE%\.gemini\antigravity-cli\mcp_config.json` |
| Opencode | `~/.opencode/config.json` | `%USERPROFILE%\.opencode\config.json` |
| Pi Coding Agent | `.pi/config.json` in your Pi workspace root | `.pi\config.json` in your Pi workspace root |
| Qwen Code | `~/.qwen/config.json` (or Qwen VSCode extension settings → MCP Servers) | `%USERPROFILE%\.qwen\config.json` |

---

## Development & Testing

This project includes a test suite under `tests/` using `pytest`.

### Unit tests (mocked, no credentials needed)

```bash
uv run pytest tests/test_canvas_unit.py -v
```

### Integration test (hits live Canvas API)

Requires valid credentials in your `.env` and a real course ID from `list_courses()`:

**macOS / Linux:**
```bash
export CANVAS_TEST_COURSE_ID=123456
uv run python tests/test_canvas_full.py
```

**Windows (PowerShell):**
```powershell
$env:CANVAS_TEST_COURSE_ID = "123456"
uv run python tests/test_canvas_full.py
```

Replace `123456` with an actual course ID obtained from `list_courses()`.

---

## Error Reference

| Error Code | Meaning | Resolution |
|---|---|---|
| `ERR_CONFIG_001` | `DRIVE_ROOT` not set | Add `DRIVE_ROOT` to `.env` and point it to an existing directory |
| `ERR_COURSE_002` | Course not found | Run `list_courses()` or `find()` to get the exact name or ID |
| `ERR_FS_003` | Filesystem permission error | Check write permissions on the `DRIVE_ROOT` directory |
| `ERR_API_004` | API request failed | Verify `CANVAS_API_KEY` is valid and not expired |
| `ERR_TERM_005` | Term inactive | The course is from a past or future term; it cannot be synced |
| `ERR_SYNC_006` | Sync already in progress | Another sync is running for this course; wait and retry |
| `ERR_FS_007` | Path too long | Filename was auto-truncated; check the output path |

---

## License

MIT License. Feel free to adapt it for your local university's Canvas instance.
