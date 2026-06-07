---
name: canvas
description: "Expert assistant for Canvas LMS operations: list courses, check today's snapshot, manage assignments, and sync course materials."
---

# Canvas Skill

## Overview

Expert assistant for Canvas LMS operations. This skill provides comprehensive access to the Canvas learning management system through the Canvas MCP server.

## Server Location

- **Path**: `canvas_server.py` (in the project root)
- **Server Name**: `canvas-server`
- **Framework**: FastMCP (MCP Server)
- **Execution**: STRICTLY use `uv run canvas_server.py` to ensure dependency isolation.

## Configuration

### Required Environment Variables

Set these in your `.env` file:

```env
CANVAS_API_URL=https://canvas.dlsu.edu.ph  # Or your institution's URL
CANVAS_API_KEY=your_canvas_api_token
DRIVE_ROOT=/path/to/your/sync/directory  # Must be an existing directory; used for sync_course and sync_term

# Timezone Configuration (Optional, defaults to PST/Asia/Manila)
CANVAS_TIMEZONE_OFFSET=8
CANVAS_TIMEZONE_NAME=Asia/Manila
```

### Getting Your Canvas API Token

1. Log in to your Canvas account (e.g., https://canvas.dlsu.edu.ph)
2. Go to Account → Settings → Approved Integrations
3. Click "New Access Token"
4. Generate a token and copy it immediately

## Available Tools

### Profile & Status

#### `profile()`
Get your Canvas user profile and settings.

**Returns**: Name, email, login ID, timezone, and ICS calendar feed.

#### `status()`
Quick dashboard showing your name, course count, and unread messages.

**Returns**: Summary line with profile info and Canvas URL.

---

### Course Management

#### `list_courses()`
View all active courses for the current term.

**Returns**: Table with course ID, code, term, and name.

#### `find(name: str)`
Fuzzy search for a course by name.

**Parameters**:
- `name`: Course name or keyword to search for

**Returns**: Ranked matches with similarity scores.

#### `global_search(query: str)`
Federated search across all active courses for assignments, discussions, and wiki pages.

**Parameters**:
- `query`: Search keyword

**Returns**: Table of results showing course, type, and title.

#### `sync_courses()`
Force refresh the cached list of active courses from Canvas.

**Use when**: You've recently added/dropped a course and need immediate updates.

---

### Time Management

#### `todo()`
View your immediate Canvas To-Do list (tasks requiring action now).

**Returns**: Tasks grouped by course with due dates, points, and action type (submitting/grading).

#### `today()`
Snapshot of everything happening today: classes, deadlines, and recent announcements.

**Returns**: Three sections:
- Classes & Events
- Deadlines
- Recent Announcements (last 24h)

#### `week()`
Summary of the current week's schedule and deadlines (Monday to Sunday).

**Returns**: Items grouped by day with types and timestamps.

#### `deadlines()`
See upcoming deadlines (next 50 items).

**Returns**: Table with due date, type, course, and title. Filters for assignments.

#### `calendar(course: Optional[Union[int, str]] = None, days: int = 30)`
Fetch calendar events for a specific course or all active courses.

**Parameters**:
- `course`: Course ID or name (optional - defaults to all courses)
- `days`: Number of days to look ahead (default: 30)

**Returns**: Chronological list of events with timestamps.

---

### Course Content

#### `syllabus(course: Union[int, str])`
Fetch the syllabus body for a specific course.

**Parameters**:
- `course`: Course ID or name

**Returns**: Clean markdown version of the syllabus.

#### `list_modules(course: Union[int, str])`
List all modules in a course (ID or Name).

**Parameters**:
- `course`: Course ID or name

**Returns**: Table with ID, item count, status, and module name.

#### `list_module_items(course: Union[int, str], module_id: int)`
List all items within a specific module.

**Parameters**:
- `course`: Course ID or name
- `module_id`: The module's ID

**Returns**: Table with item ID, type (Page, File, ExternalUrl, etc.), and title.

#### `list_pages(course: Union[int, str])`
List all wiki pages in a course.

**Parameters**:
- `course`: Course ID or name

**Returns**: Table with page URL (ID), last update date, and title.

#### `read_page(course: Union[int, str], page_url: str)`
Read the content of a specific course wiki page.

**Parameters**:
- `course`: Course ID or name
- `page_url`: The page's URL identifier (obtained from `list_pages`)

**Returns**: Markdown formatted page content.

#### `announcements(days: int = 7)`
Fetch recent announcements across all courses.

**Parameters**:
- `days`: Number of days to look back (default: 7)

**Returns**: Announcements sorted by date (newest first).

#### `list_discussions(course: Union[int, str])`
List discussion topics for a specific course (ID or Name).

**Parameters**:
- `course`: Course ID or name

**Returns**: Table with ID, last post date, lock status, and title.

#### `post_discussion_reply(course: Union[int, str], topic_id: int, message: str)`
Post a reply to a discussion topic.

**Parameters**:
- `course`: Course ID or name
- `topic_id`: The discussion topic's ID
- `message`: The HTML or text content of your reply

**Returns**: Success or error message.

#### `assignments(course: Union[int, str])`
List all assignments for a course.

**Parameters**:
- `course`: Course ID or name

**Returns**: Table with assignment ID, due date, and name.


#### `assignment_details(course: Union[int, str], assignment_id: int)`
Get full verbatim instructions and details for a specific assignment.

**Parameters**:
- `course`: Course ID or name
- `assignment_id`: The assignment's ID

**Returns**: Complete instructions, due date, points, and attached files with metadata.

#### `submit_assignment(course: Union[int, str], assignment_id: int, submission_type: str, body: Optional[str] = None, url: Optional[str] = None, file_path: Optional[str] = None)`
Submit an assignment (text, URL, or file).

**Parameters**:
- `course`: Course ID or name
- `assignment_id`: The assignment's ID
- `submission_type`: One of 'online_text_entry', 'online_url', 'online_upload'
- `body`: Text content (for 'online_text_entry')
- `url`: Submission URL (for 'online_url')
- `file_path`: Absolute local path to file (for 'online_upload')

**Returns**: Success or error message.

#### `participants(course: Union[int, str])`
List instructors and TAs for a specific course.

**Parameters**:
- `course`: Course ID or name

**Returns**: List of staff members with their login IDs.

---

### Grades & Feedback

#### `grades(course: Optional[Union[int, str]] = None)`
Get current grades for a specific course or all active courses.

**Parameters**:
- `course`: Course ID or name (optional - defaults to all courses)

**Returns**: Table with course ID, score, grade, and course name.

#### `feedback(course: Union[int, str], assignment_id: int)`
Read grades and instructor comments for a specific assignment.

**Parameters**:
- `course`: Course ID or name
- `assignment_id`: The assignment's ID

**Returns**: Grade, score, and all feedback comments with timestamps.

#### `missing()`
View overdue/missing assignments.

**Returns**: Table of missing items with due dates, course IDs, and names.

---

### File Operations

#### `search_files(course: Union[int, str], query: str)`
Search for files within a specific course by name.

**Parameters**:
- `course`: Course ID or name
- `query`: Search term for file names

**Returns**: Table with file ID, size (MB), and display name.

#### `read_file(file_id: int)`
Extract content or metadata from a file (PDF, text, Office docs).

**Parameters**:
- `file_id`: The file's ID

**Returns**: 
- **PDFs**: Full extracted text
- **Text files**: Complete content
- **Office docs**: Metadata with type and size
- **Binary files**: Notification with extraction notice

#### `sync_course(course: Union[int, str])`
Download and sync all course materials asynchronously with high-performance concurrency.

**Parameters**:
- `course`: Course ID or name

**Syncs**:
- Syllabus (as `SYLLABUS.md`)
- Announcements (in `ANNOUNCEMENTS/` folder)
- Resources/Files (in `RESOURCES/` folder, preserving structure)
- Assignments with instructions and attachments (in `ASSIGNMENTS/` folder)
- Your submissions (in `ASSIGNMENTS/{name}/submissions/`)

**Directory Structure**:
```
DRIVE_ROOT/
└── AY 2025-2026/
    └── Term 2/
        └── Course Name/
            ├── SYLLABUS.md
            ├── ANNOUNCEMENTS/
            ├── RESOURCES/
            └── ASSIGNMENTS/
```

**Features**:
- Concurrency limit: 5 parallel downloads
- Retry logic: 3 attempts with exponential backoff
- Integrity checks: Skips files with matching sizes
- Path truncation: Handles macOS filename limits
- Term validation: Only syncs active terms

#### `sync_term(term: Optional[str] = None)`
Sync all courses belonging to a specific term asynchronously.

**Parameters**:
- `term`: Term name (e.g., "Term 2"). Auto-detects current term if not specified.

**Returns**: Batch sync results for all courses in the term.

---

### Communication

#### `inbox()`
Check unread messages.

**Returns**: List of unread conversations with subject and message preview.

#### `read_message(conversation_id: int)`
Read full message threads from your inbox.

**Parameters**:
- `conversation_id`: The conversation's ID

**Returns**: Complete message thread with authors and timestamps.

---

### Zoom Integration

> [!NOTE]
> **Important Guidance on Zoom Links**: Professors vary widely in how they publish Zoom meetings:
> - **Varied Locations**: Links may be permanently pinned in the **Syllabus**, within the **Modules** tab, as one-off **Announcements**, on custom wiki **Pages**, or inside pinned **Discussions**.
> - **Changeability**: Some courses use a single static Zoom link for the entire term, while others use the official Canvas Zoom integration with changing IDs, or update the links dynamically in weekly announcements.
> - **Verification**: Always run the `zoom_links` tool to scan all syllabus, announcements, calendar, and module data dynamically to ensure you locate the current, active link.

#### `zoom_links(course: Optional[Union[int, str]] = None)`
Deep scrape Zoom links and schedules from announcements, pages, modules, syllabus, and calendar.

**Parameters**:
- `course`: Course ID or name (optional - defaults to all courses)

**Sources**:
1. Syllabus body
2. Recent announcements (last 5)
3. Calendar events (past 7 days to next 21 days)
4. Course modules (if specific course requested)

**Returns**: Organized list by course with:
- Zoom meeting links
- Extracted schedules

#### `export_calendar(course: Optional[Union[int, str]] = None, filename: str = "canvas_zoom_links.ics")`
Gather discovered Zoom schedules and export as an ICS file.

**Parameters**:
- `course`: Course ID or name (optional - defaults to all courses)
- `filename`: Output filename (default: `canvas_zoom_links.ics`)

**Features**:
- Auto-calculates term end dates based on current month
- Creates weekly recurring events
- Saves to Desktop
- Timezone: Configurable via `CANVAS_TIMEZONE_NAME` (default: `Asia/Manila`)

**Returns**: Path to exported ICS file.

---

### Activity Monitoring

#### `activity(limit: int = 20)`
View recent activity stream (new grades, announcements, discussions, messages).

**Parameters**:
- `limit`: Number of items to fetch (default: 20)

**Returns**: Chronological feed with timestamps and activity types.

## Usage Patterns

### Quick Daily Check
```
1. todo() - See what needs action now
2. today() - Get today's snapshot
3. inbox() - Check unread messages
```

### Assignment Workflow
```
1. assignments("Course Name") - List all assignments
2. assignment_details("Course Name", 123) - Read full instructions
3. read_file(456) - Download attached resources
4. feedback("Course Name", 123) - Check grade after submission
```


### Course Backup
```
1. sync_course("Course Name") - Download all materials
2. sync_term("Term 2") - Backup entire term's courses
```

### Zoom Meeting Prep
```
1. zoom_links() - Get all Zoom links
2. export_calendar() - Create recurring calendar events
```

### Grade Check
```
1. grades() - View all course grades
2. missing() - Check overdue work
3. feedback("Course", 123) - Read instructor comments
```

## Error Handling

### Common Errors

| Error Code | Meaning | Resolution |
|------------|---------|------------|
| `ERR_CONFIG_001` | Missing configuration | Set `DRIVE_ROOT` in `.env` |
| `ERR_COURSE_002` | Course not found | Use `list_courses()` or `find()` to verify name |
| `ERR_FS_003` | Filesystem permission error | Check write permissions for `DRIVE_ROOT` |
| `ERR_API_004` | API request failed | Verify API credentials and network |
| `ERR_TERM_005` | Term inactive | Course is archived; use `sync_course(include_archived=True)` |
| `ERR_SYNC_006` | Sync locked | Another sync is in progress; wait and retry |
| `ERR_FS_007` | Path too long | Filename auto-truncated; check output |

### Course Resolution

The skill uses fuzzy matching for course names. If a course isn't found:
1. Try using the course code instead (e.g., "CS101" vs "Introduction to Programming")
2. Use `list_courses()` to see exact names
3. Use `find("keyword")` for fuzzy search with suggestions

## Performance Notes

- **Course caching**: Active courses cached for 1 hour
- **Concurrent downloads**: Max 5 parallel file downloads
- **Pagination**: Auto-handles Canvas pagination (100 items/page)
- **Retry logic**: 3 attempts with exponential backoff for network issues
- **File integrity**: Skips re-downloading files with matching sizes

## Best Practices

1. **Use course codes** for faster resolution (e.g., "CS101" instead of full name)
2. **Sync regularly** to maintain local backups before deadlines
3. **Check `missing()`** before starting new work
4. **Use `today()`** for daily planning instead of multiple queries
5. **Export calendar once** per term for recurring Zoom meetings
