import os
import io
import httpx
import asyncio
import re
import functools
import sys
import logging
import inspect
from pathlib import Path
from typing import Optional, Dict, List, Union, Any
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import html2text
from mcp.server.fastmcp import FastMCP
from rapidfuzz import process, fuzz
from pypdf import PdfReader

# Setup & Configuration
load_dotenv()
if not (os.getenv("CANVAS_API_URL") or os.getenv("canvas_api_url")):
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Timezone Configuration
# Defaulting to Philippine Standard Time (PST, UTC+8) for DLSU
CANVAS_TIMEZONE_OFFSET = int(os.getenv("CANVAS_TIMEZONE_OFFSET", 8))
TARGET_TZ = timezone(timedelta(hours=CANVAS_TIMEZONE_OFFSET))
TARGET_TZ_NAME = os.getenv("CANVAS_TIMEZONE_NAME", "Asia/Manila")  # Used for ICS export

# Ensure logs directory exists
_log_path = Path(__file__).parent / "logs"
_log_path.mkdir(exist_ok=True)

# Update logging to include file output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(_log_path / "system.log"),
    ],
)
logger = logging.getLogger("CanvasMCP")

mcp = FastMCP("canvas-server")

CANVAS_API_URL = (
    os.getenv("CANVAS_API_URL") or os.getenv("canvas_api_url") or ""
).rstrip("/")
CANVAS_API_KEY = os.getenv("CANVAS_API_KEY") or os.getenv("canvas_api_key")
DRIVE_ROOT = os.getenv("DRIVE_ROOT") or os.getenv("drive_root")

USER_AGENT = "CustomCanvasAgent/2.0 (MCP Server; Dynamic Compliance)"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Concurrency & Safety
MAX_CONCURRENT_DOWNLOADS = 5
_download_semaphore: Optional[asyncio.Semaphore] = None
_sync_locks: Dict[int, asyncio.Lock] = {}


def get_sync_lock(cid: int) -> asyncio.Lock:
    if cid not in _sync_locks:
        _sync_locks[cid] = asyncio.Lock()
    return _sync_locks[cid]


def get_download_semaphore() -> asyncio.Semaphore:
    global _download_semaphore
    if _download_semaphore is None:
        _download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    return _download_semaphore


# Error Codes
ERROR_CONFIG_MISSING = "ERR_CONFIG_001"
ERROR_COURSE_NOT_FOUND = "ERR_COURSE_002"
ERROR_FS_PERMISSION = "ERR_FS_003"
ERROR_API_FAILURE = "ERR_API_004"
ERROR_TERM_INACTIVE = "ERR_TERM_005"
ERROR_SYNC_LOCKED = "ERR_SYNC_006"
ERROR_PATH_TOO_LONG = "ERR_FS_007"
ERROR_UNKNOWN = "ERR_GEN_999"

# Utilities


def get_api_now_iso() -> str:
    """Returns current UTC time in Canvas-compatible ISO8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_term_active(term_obj: Dict) -> bool:
    """Check if the current date is within the term start and end dates or matches current AY."""
    now = datetime.now(TARGET_TZ)
    start_at = term_obj.get("start_at")
    end_at = term_obj.get("end_at")
    name = term_obj.get("name", "")

    # Heuristic: If name contains a past AY, it's inactive regardless of missing dates
    ay_match = re.search(r"AY (\d{4})-\d{4}", name)
    if ay_match:
        ay_start = int(ay_match.group(1))
        # If the academic year started more than 1 year ago, it's likely old.
        # e.g., If current year is 2026, AY 2024-2025 is old.
        if ay_start < now.year - 1:
            return False

    if not start_at or not end_at:
        # For non-standard terms, if it doesn't have an AY in name, assume active (orientation, etc.)
        return True

    try:
        start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
        return start_dt <= now <= end_dt
    except Exception:
        return True


def get_current_term(courses: List[Dict]) -> Optional[str]:
    """Find the most likely current term name from active courses."""
    for c in courses:
        term = c.get("term", {})
        if is_term_active(term):
            return term.get("name")
    return None


def sanitize_filename(name: str) -> str:
    """Remove illegal characters for file names on any OS."""
    if not name:
        return "Untitled"
    # Replace common illegal chars with underscore
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    # Strip leading/trailing whitespace and dots
    return name.strip(" .")


def truncate_filename(base_path: Path, filename: str) -> str:
    """Truncate filename if the total path length exceeds macOS limits."""
    # Hard limit for path length is usually 1024, but filenames are 255.
    # We'll be conservative to avoid Drive issues.
    max_path_len = 250
    full_path = str(base_path / filename)
    if len(full_path) <= max_path_len:
        return filename

    # Keep the extension
    stem = Path(filename).stem
    suffix = Path(filename).suffix

    # Calculate how much to truncate from stem
    allowed_len = max_path_len - len(str(base_path)) - len(suffix) - 1
    if allowed_len <= 0:
        return filename[:10]  # Fallback

    return stem[:allowed_len] + suffix


_course_cache: Dict[str, int] = {}
_global_courses: List[Dict] = []
_last_course_fetch: Optional[datetime] = None


async def get_active_courses(
    force_refresh: bool = False, include_archived: bool = False
) -> List[Dict]:
    global _global_courses, _last_course_fetch, _course_cache
    now = datetime.now()
    if (
        force_refresh
        or not _global_courses
        or not _last_course_fetch
        or (now - _last_course_fetch) > timedelta(hours=1)
    ):
        c = get_client()
        try:
            params = {
                "enrollment_state": "active",
                "per_page": 100,
                "include[]": ["term", "total_scores"],
            }
            courses = await c.async_get_paginated("courses", params)

            # Deduplicate and Filter
            unique_courses = {}
            for co in courses:
                cid = co.get("id")
                if not cid or cid in unique_courses:
                    continue

                term = co.get("term", {})
                # If not include_archived, skip if the term is objectively over
                if not include_archived and not is_term_active(term):
                    continue

                unique_courses[cid] = co

            _global_courses = list(unique_courses.values())
            _last_course_fetch = now
            _course_cache.clear()

            # Re-seed name-to-id cache
            for co in _global_courses:
                cid = co["id"]
                name = co.get("name")
                code = co.get("course_code")
                if name:
                    _course_cache[name] = cid
                if code:
                    _course_cache[code] = cid
        except Exception as e:
            logger.error(f"Failed to fetch active courses: {str(e)}")
            if not _global_courses:
                raise RuntimeError(f"Failed to fetch courses from Canvas: {str(e)}")
    return _global_courses


def clean_html(html: str) -> str:
    """Convert HTML to clean Markdown for LLM consumption (no summarization)."""
    if not html:
        return ""

    # Use html2text for high-fidelity conversion
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0  # No word wrapping
    h.protect_links = True
    h.ul_item_mark = "•"

    # Pre-clean script/style tags which html2text sometimes leaves
    html = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE
    )

    try:
        text = h.handle(html).strip()
    except Exception:
        # Fallback to basic regex if html2text fails
        text = re.sub(r"<[^>]+>", "", html)

    # Standardize spacing but preserve structure
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text


def get_client() -> "CanvasClient":
    global _client
    if _client is None:
        if not CANVAS_API_URL or not CANVAS_API_KEY:
            raise EnvironmentError(
                "Error: Canvas credentials missing.\n"
                "Please ensure CANVAS_API_URL and CANVAS_API_KEY are set in your .env file.\n"
                f"Current URL: {CANVAS_API_URL or 'NOT SET'}"
            )
        _client = CanvasClient(CANVAS_API_URL, CANVAS_API_KEY)
    return _client


async def _resolve_cid(course: Optional[Union[int, str]]) -> Optional[int]:
    """Helper to turn a name, code, or ID into a verified course ID with suggestions."""
    if course is None:
        return None

    # If it looks like an ID, return it directly
    if isinstance(course, int) or (
        isinstance(course, str) and course.strip().isdigit()
    ):
        return int(str(course).strip())

    query = str(course).strip()

    # Fast path if exact match in cache
    if query in _course_cache:
        return _course_cache[query]

    # Ensure global cache is populated
    courses = await get_active_courses()

    cmap = {}
    for co in courses:
        cid = co["id"]
        name = co.get("name")
        code = co.get("course_code")
        if name:
            cmap[name] = cid
        if code:
            cmap[code] = cid

    if not cmap:
        raise ValueError("No active courses found in your Canvas account.")

    # Try exact match again (just in case cache was rebuilt)
    if query in cmap:
        _course_cache[query] = cmap[query]
        return cmap[query]

    # Try fuzzy matching
    matches = process.extract(query, cmap.keys(), scorer=fuzz.WRatio, limit=3)

    # If top match is strong enough (>80), cache and return
    if matches and matches[0][1] > 80:
        cid = cmap[matches[0][0]]
        _course_cache[query] = cid
        return cid

    # Otherwise, build a helpful error message
    suggestions = "\n".join(
        [f"  • {m[0]} (Score: {int(m[1])})" for m in matches if m[1] > 20]
    )
    available = "\n".join(
        [f"  • {name}" for name in sorted(list(set(cmap.keys())))[:10]]
    )

    error_msg = f"Error: Could not find course matching '{query}'."
    if suggestions:
        error_msg += f"\n\nDid you mean:\n{suggestions}"
    else:
        error_msg += f"\n\nActive courses available:\n{available}\n  ... (use 'list_courses' for full list)"

    raise ValueError(error_msg)


def format_ts(ts_str: Optional[str]) -> str:
    """Human-friendly timestamp with relative logic: 'Today at 11:59 PM'"""
    if not ts_str:
        return "N/A"
    try:
        dt_utc = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        # Localize to target timezone
        dt_local = dt_utc.astimezone(TARGET_TZ)
        now_local = datetime.now(TARGET_TZ)
        diff = dt_local.date() - now_local.date()

        time_str = dt_local.strftime("%I:%M %p")
        if diff.days == 0:
            return f"Today at {time_str}"
        elif diff.days == 1:
            return f"Tomorrow at {time_str}"
        elif diff.days == -1:
            return f"Yesterday at {time_str}"
        elif 0 < diff.days < 7:
            return f"{dt_local.strftime('%A')} at {time_str}"
        else:
            return dt_local.strftime("%b %d, %I:%M %p")
    except Exception:
        return ts_str


class CanvasClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url if "/api/v1" in base_url else f"{base_url}/api/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        self.client = httpx.Client(
            headers=self.headers, timeout=TIMEOUT, follow_redirects=True
        )
        self.async_client = httpx.AsyncClient(
            headers=self.headers, timeout=TIMEOUT, follow_redirects=True
        )

    def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        res = self.client.request(method, url, params=params, data=data)
        res.raise_for_status()
        return res

    async def async_request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        res = await self.async_client.request(method, url, params=params, data=data)
        res.raise_for_status()
        return res

    def get_paginated(
        self, path: str, params: Optional[dict] = None, limit: int = 100
    ) -> List[dict]:
        results: List[dict] = []
        url: Optional[str] = f"{self.base_url}/{path.lstrip('/')}"
        curr_params = params.copy() if params else {}
        if "per_page" not in curr_params:
            curr_params["per_page"] = 100

        while url and len(results) < limit:
            try:
                # Basic retry for transient network issues
                res = None
                for attempt in range(3):
                    try:
                        res = self.client.get(url, params=curr_params)
                        res.raise_for_status()
                        break
                    except (httpx.ConnectError, httpx.TimeoutException) as e:
                        if attempt == 2:
                            raise
                        logger.warning(
                            f"Network error in get_paginated: {e}. Retrying {attempt + 1}/3..."
                        )
                        import time

                        time.sleep(2**attempt)

                if not res:
                    break
                data = res.json()
                if isinstance(data, list):
                    results.extend(data)
                else:
                    return [data]

                curr_params = {}
                link_header = res.headers.get("Link")
                url = None
                if link_header:
                    for link in link_header.split(","):
                        if 'rel="next"' in link:
                            url = link[link.find("<") + 1 : link.find(">")]
                            break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403:
                    logger.warning(f"Access forbidden for {path}")
                    break
                raise
        return results[:limit]

    async def async_get_paginated(
        self, path: str, params: Optional[dict] = None, limit: int = 100
    ) -> List[dict]:
        results: List[dict] = []
        url: Optional[str] = f"{self.base_url}/{path.lstrip('/')}"
        curr_params = params.copy() if params else {}
        if "per_page" not in curr_params:
            curr_params["per_page"] = 100

        while url and len(results) < limit:
            try:
                res = None
                for attempt in range(3):
                    try:
                        res = await self.async_client.get(url, params=curr_params)
                        res.raise_for_status()
                        break
                    except (httpx.ConnectError, httpx.TimeoutException) as e:
                        if attempt == 2:
                            raise
                        logger.warning(
                            f"Network error in async_get_paginated: {e}. Retrying {attempt + 1}/3..."
                        )
                        await asyncio.sleep(2**attempt)

                if not res:
                    break
                data = res.json()
                if isinstance(data, list):
                    results.extend(data)
                else:
                    return [data]

                curr_params = {}
                link_header = res.headers.get("Link")
                url = None
                if link_header:
                    for link in link_header.split(","):
                        if 'rel="next"' in link:
                            url = link[link.find("<") + 1 : link.find(">")]
                            break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403:
                    logger.warning(f"Access forbidden for {path}")
                    break
                raise
        return results[:limit]

    async def async_upload_file(
        self,
        path: str,
        file_path: Union[str, Path],
        params: Optional[dict] = None,
    ) -> Dict:
        """
        Upload a file to Canvas using the 3-step handshake.
        Step 1: Tell Canvas about the file.
        Step 2: Upload the file to the provided URL (S3/local).
        Step 3: Confirm the upload (if necessary).
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Step 1: Notify Canvas
        upload_params = {
            "name": file_path.name,
            "size": file_path.stat().st_size,
            "content_type": "application/octet-stream",  # Generic
        }
        if params:
            upload_params.update(params)

        res = await self.async_request("POST", path, data=upload_params)
        data = res.json()

        upload_url = data.get("upload_url")
        upload_params_canvas = data.get("upload_params", {})

        if not upload_url:
            raise ValueError(f"Canvas did not return an upload URL: {data}")

        # Step 2: Upload to S3/Storage
        # We must send 'file' as the last parameter
        files = {"file": open(file_path, "rb")}

        # httpx doesn't automatically handle the params as form data if files is present in the same way
        # Canvas expects for some storage backends. We use the data field for params.
        upload_res = await httpx.AsyncClient().post(
            upload_url, data=upload_params_canvas, files=files, timeout=TIMEOUT
        )

        # Step 3: Handle redirects or completion
        if upload_res.status_code in (201, 301, 308):
            # If 201, it's done. If 3xx, we might need to follow it to get the final JSON.
            if "Location" in upload_res.headers:
                final_res = await self.async_client.get(upload_res.headers["Location"])
                final_res.raise_for_status()
                return final_res.json()
            return upload_res.json()

        upload_res.raise_for_status()
        return upload_res.json()

    async def download_file_async(
        self, url: str, local_path: Path, expected_size: Optional[int] = None
    ) -> bool:
        """Download a file from Canvas asynchronously. Skips if local file exists with matching size."""
        # Check size-based skip logic (integrity: 0 bytes but expected size > 0 should re-download)
        if local_path.exists():
            current_size = local_path.stat().st_size
            if expected_size is not None and expected_size > 0:
                if current_size == expected_size:
                    return True  # Skip redundant download
                else:
                    logger.info(
                        f"Integrity check failed for {local_path} (Size: {current_size}, Expected: {expected_size}). Re-downloading..."
                    )
            elif current_size > 0:
                # If no expected_size but we have something, don't re-download
                return True

        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Concurrency & Retry Logic
        max_retries = 3
        async with get_download_semaphore():
            for attempt in range(max_retries):
                try:
                    async with self.async_client.stream(
                        "GET", url, follow_redirects=True
                    ) as response:
                        response.raise_for_status()
                        with open(local_path, "wb") as f:
                            async for chunk in response.aiter_bytes():
                                if chunk:
                                    f.write(chunk)
                    return True
                except Exception as e:
                    if attempt < max_retries - 1:
                        wait = 2**attempt
                        logger.warning(
                            f"Download failed for {local_path}. Retrying in {wait}s... Error: {e}"
                        )
                        await asyncio.sleep(wait)
                        continue
                    logger.error(
                        f"Download failed after {max_retries} attempts for {local_path}: {e}"
                    )
                    return False
        return False

    def download_file(
        self, url: str, local_path: Path, expected_size: Optional[int] = None
    ) -> bool:
        """Sync wrapper for the async downloader (legacy support)."""
        return asyncio.run(self.download_file_async(url, local_path, expected_size))


_client: Optional[CanvasClient] = None


def tool_wrapper(func):
    """Clean error reporting for the UI."""
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                return f"Error: {type(e).__name__}: {str(e)}"

        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            return f"Error: {type(e).__name__}: {str(e)}"

    return sync_wrapper


def extract_zoom(text: str) -> List[str]:
    if not text:
        return []
    pattern = r'https?://[a-zA-Z0-9.-]*zoom\.us/[^\s"<>"]+'
    return list(set(re.findall(pattern, text)))


def extract_schedules(text: str) -> List[str]:
    if not text:
        return []
    # Match days and time ranges
    patterns = [
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)s?\s*\d{1,2}:\d{2}\s*[APM]{2}",
        r"\d{1,2}:\d{2}\s*[APM]{2}\s*-\s*\d{1,2}:\d{2}\s*[APM]{2}",
        r"(?:M|T|W|Th|F|Sa|S)\s*/\s*(?:M|T|W|Th|F|Sa|S)\s*\d{1,2}:\d{2}",
    ]
    found = []
    for p in patterns:
        found.extend(re.findall(p, text, re.IGNORECASE))
    return list(set(found))


# Tools


@mcp.tool()
@tool_wrapper
async def profile() -> str:
    """Get your Canvas user profile and settings."""
    c = get_client()
    res = await c.async_request("GET", "users/self/profile")
    user = res.json()

    return (
        f"Name: {user.get('name', 'Unknown')}\n"
        f"Email: {user.get('primary_email', 'No Email')}\n"
        f"Login ID: {user.get('login_id', 'N/A')}\n"
        f"Timezone: {user.get('time_zone', 'N/A')}\n"
        f"ICS Feed: {user.get('calendar', {}).get('ics', 'N/A')}"
    )


@mcp.tool()
@tool_wrapper
async def todo() -> str:
    """View your immediate Canvas To-Do list (tasks that need action now)."""
    c = get_client()
    todos = await c.async_get_paginated("users/self/todo")

    if not todos:
        return "Inbox zero! No immediate tasks pending."

    out = ["--- Immediate Action Required ---"]
    for t in todos:
        course = t.get("context_name", "Unknown Course")
        task_type = (
            str(t.get("ignore", "")).split("/")[-1].split("?")[0].upper()
        )  # e.g., 'submitting', 'grading'

        assn = t.get("assignment", {})
        if assn:
            name = assn.get("name", "Unknown Assignment")
            due = format_ts(assn.get("due_at"))
            pts = assn.get("points_possible", 0)
            out.append(f"[{due}] {course}\n  > {name} ({pts} pts) - {task_type}")
        else:
            out.append(f"[No Date] {course}\n  > {t.get('type')} task")

    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def today() -> str:
    """Snapshot of everything happening today: Classes, deadlines, and announcements."""
    c = get_client()
    now_local = datetime.now(TARGET_TZ)

    # Start/End of local day converted to UTC for the API
    start_of_day = (
        now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    end_of_day = (
        now_local.replace(hour=23, minute=59, second=59, microsecond=999999)
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    # 1. Courses - Unique set
    courses = await get_active_courses()
    unique_cids = [course["id"] for course in courses if "id" in course]

    events = []
    # Batch calendar events fetch
    for cid in unique_cids:
        try:
            res = await c.async_get_paginated(
                "calendar_events",
                {
                    "context_codes[]": [f"course_{cid}"],
                    "start_date": start_of_day,
                    "end_date": end_of_day,
                    "type": "event",
                },
            )
            events.extend(res)
        except Exception:
            continue

    # 2. Deadlines
    planner = await c.async_get_paginated(
        "planner/items", {"start_date": start_of_day, "end_date": end_of_day}
    )

    # 3. Recent Announcements (Last 24h)
    start_date_ann = (
        (now_local - timedelta(days=1))
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    anns = []
    batch_size = 10
    for i in range(0, len(unique_cids), batch_size):
        batch = unique_cids[i : i + batch_size]
        codes = [f"course_{cid}" for cid in batch]
        try:
            res = await c.async_get_paginated(
                "announcements",
                {"context_codes[]": codes, "start_date": start_date_ann},
            )
            anns.extend(res)
        except Exception:
            continue

    out = ["TODAY'S SNAPSHOT"]
    out.append("=" * 20)

    out.append("\nCLASSES & EVENTS:")
    if not events:
        out.append("  (No events scheduled)")
    for e in events:
        out.append(
            f"  • [{format_ts(e.get('start_at'))}] {e.get('context_name', 'N/A')}: {e.get('title')}"
        )

    out.append("\nDEADLINES:")
    if not planner:
        out.append("  (No deadlines today)")
    for p in planner:
        title = p.get("plannable", {}).get("title") or p.get("plannable", {}).get(
            "name", "Untitled"
        )
        out.append(
            f"  • [{format_ts(p.get('plannable_date'))}] {p.get('context_name', 'N/A')}: {title}"
        )

    if anns:
        out.append("\nRECENT ANNOUNCEMENTS:")
        for a in anns:
            out.append(
                f"  • [{format_ts(a.get('posted_at'))}] {a.get('context_name', 'N/A')}: {a.get('title')}"
            )

    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def week() -> str:
    """Summary of the current week's schedule and deadlines."""
    c = get_client()
    now_local = datetime.now(TARGET_TZ)
    # Week start (Monday) to Week end (Sunday) in local time, converted to UTC
    start_of_week = (
        (now_local - timedelta(days=now_local.weekday()))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    end_of_week = (
        (now_local + timedelta(days=6 - now_local.weekday()))
        .replace(hour=23, minute=59, second=59, microsecond=999999)
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    planner = await c.async_get_paginated(
        "planner/items", {"start_date": start_of_week, "end_date": end_of_week}
    )

    if not planner:
        return "Clear week ahead! No items found in planner."

    out = ["THIS WEEK'S PLANNER"]
    out.append("=" * 25)

    # Group by day
    days: Dict[str, List[Dict]] = {}
    for p in planner:
        date_str = p.get("plannable_date")
        if not date_str:
            continue
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        day_key = dt.strftime("%A, %b %d")
        if day_key not in days:
            days[day_key] = []
        days[day_key].append(p)

    for day, items in sorted(days.items()):
        out.append(f"\n{day}:")
        for i in items:
            title = i.get("plannable", {}).get("title") or i.get("plannable", {}).get(
                "name", "Untitled"
            )
            type_ = i.get("plannable_type", "item").capitalize()
            out.append(f"  • [{format_ts(i.get('plannable_date'))}] {type_}: {title}")

    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def status() -> str:
    """Quick dashboard: Name, Courses, Unread messages."""
    c = get_client()
    res = await c.async_request("GET", "users/self/profile")
    user = res.json()
    courses = await get_active_courses()
    unread = await c.async_get_paginated("conversations", {"scope": "unread"}, limit=10)

    return (
        f"User: {user.get('name')} | Courses: {len(courses)} | Unread: {len(unread)}\n"
        f"URL: {CANVAS_API_URL.replace('/api/v1', '')}"
    )


@mcp.tool()
@tool_wrapper
async def list_courses() -> str:
    """View all active courses for the current term."""
    courses = await get_active_courses()
    if not courses:
        return "No active courses found for the current term."

    out = ["ID       | CODE       | TERM                 | NAME"]
    out.append("-" * 70)
    for c in courses:
        term_name = c.get("term", {}).get("name", "N/A")[:20]
        out.append(
            f"{str(c['id']).ljust(8)} | {str(c.get('course_code', '')).ljust(10)} | {term_name.ljust(20)} | {c.get('name')}"
        )
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def announcements(days: int = 7) -> str:
    """Fetch recent announcements across all courses."""
    c = get_client()
    courses = await get_active_courses()
    # Unique IDs only
    cids = [course["id"] for course in courses if "id" in course]

    start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    # Batch IDs to avoid HTTP 400 URL length errors
    all_anns = []
    batch_size = 10
    for i in range(0, len(cids), batch_size):
        batch = cids[i : i + batch_size]
        codes = [f"course_{cid}" for cid in batch]
        try:
            res = await c.async_get_paginated(
                "announcements", {"context_codes[]": codes, "start_date": start_date}
            )
            all_anns.extend(res)
        except Exception as e:
            logger.error(f"Failed to fetch announcements for batch: {str(e)}")
            continue

    if not all_anns:
        return f"No announcements in the last {days} days."

    # Sort by date
    all_anns.sort(key=lambda x: x.get("posted_at") or "", reverse=True)

    out = [f"--- Announcements (Last {days} Days) ---"]
    for a in all_anns:
        out.append(
            f"[{format_ts(a.get('posted_at'))}] {a.get('context_name', 'N/A')}: {a.get('title')}"
        )
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def list_discussions(course: Union[int, str]) -> str:
    """List discussion topics for a specific course (ID or Name)."""
    cid = await _resolve_cid(course)
    c = get_client()
    topics = await c.async_get_paginated(f"courses/{cid}/discussion_topics")
    if not topics:
        return f"No discussion topics found for {course}."

    out = ["ID       | LAST POST      | LOCKED | TITLE"]
    out.append("-" * 75)
    for t in topics:
        lp = format_ts(t.get("last_reply_at"))[:15]
        locked = "[LOCKED]" if t.get("locked") else "[UNLOCKED]"
        title = t.get("title", "Untitled")[:40]
        out.append(
            f"{str(t.get('id')).ljust(8)} | {lp.ljust(15)} | {locked.ljust(6)} | {title}"
        )
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def post_discussion_reply(
    course: Union[int, str], topic_id: int, message: str
) -> str:
    """Post a reply to a discussion topic."""
    cid = await _resolve_cid(course)
    c = get_client()
    res = await c.async_request(
        "POST",
        f"courses/{cid}/discussion_topics/{topic_id}/entries",
        data={"message": message},
    )
    if res.status_code == 201:
        return f"Success: Successfully posted reply to topic {topic_id} in {course}."
    return f"Error: Failed to post reply: {res.text}"


@mcp.tool()
@tool_wrapper
async def syllabus(course: Union[int, str]) -> str:
    """Fetch the syllabus body for a specific course (ID or Name)."""
    cid = await _resolve_cid(course)
    c = get_client()
    res = await c.async_request(
        "GET", f"courses/{cid}", params={"include[]": "syllabus_body"}
    )
    detail = res.json()
    body = detail.get("syllabus_body", "")
    if not body:
        return "No syllabus content found."
    return clean_html(body)


@mcp.tool()
@tool_wrapper
async def list_modules(course: Union[int, str]) -> str:
    """List all modules in a course (ID or Name)."""
    cid = await _resolve_cid(course)
    c = get_client()
    modules = await c.async_get_paginated(f"courses/{cid}/modules")
    if not modules:
        return f"No modules found for {course}."

    out = ["ID       | ITEMS | STATUS | NAME"]
    out.append("-" * 60)
    for m in modules:
        items_count = str(m.get("items_count", 0)).ljust(5)
        status = "Success:" if m.get("published") else "Error:"
        name = m.get("name", "Untitled")[:40]
        out.append(
            f"{str(m.get('id')).ljust(8)} | {items_count} | {status.ljust(6)} | {name}"
        )
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def list_module_items(course: Union[int, str], module_id: int) -> str:
    """List all items within a specific module."""
    cid = await _resolve_cid(course)
    c = get_client()
    items = await c.async_get_paginated(f"courses/{cid}/modules/{module_id}/items")
    if not items:
        return f"No items found in module {module_id}."

    out = ["ID       | TYPE           | TITLE"]
    out.append("-" * 60)
    for i in items:
        itype = i.get("type", "Other")[:14]
        title = i.get("title", "Untitled")[:40]
        out.append(f"{str(i.get('id')).ljust(8)} | {itype.ljust(14)} | {title}")
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def list_pages(course: Union[int, str]) -> str:
    """List all wiki pages in a course."""
    cid = await _resolve_cid(course)
    c = get_client()
    pages = await c.async_get_paginated(f"courses/{cid}/pages")
    if not pages:
        return f"No pages found for {course}."

    out = ["URL (ID)                           | UPDATED        | TITLE"]
    out.append("-" * 75)
    for p in pages:
        url = p.get("url", "N/A")[:30]
        updated = format_ts(p.get("updated_at"))[:14]
        title = p.get("title", "Untitled")[:30]
        out.append(f"{url.ljust(34)} | {updated.ljust(14)} | {title}")
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def read_page(course: Union[int, str], page_url: str) -> str:
    """Read the content of a specific course page."""
    cid = await _resolve_cid(course)
    c = get_client()
    res = await c.async_request("GET", f"courses/{cid}/pages/{page_url}")
    page = res.json()

    body = clean_html(page.get("body", "No content available."))
    return f"# {page.get('title')}\n\nLast Updated: {format_ts(page.get('updated_at'))}\n\n---\n\n{body}"


@mcp.tool()
@tool_wrapper
async def calendar(course: Optional[Union[int, str]] = None, days: int = 30) -> str:
    """Fetch calendar events for a specific course or all active courses."""
    c = get_client()
    cid_target = await _resolve_cid(course)

    if cid_target:
        cids = [cid_target]
    else:
        courses = await get_active_courses()
        cids = [course["id"] for course in courses if "id" in course]

    start = get_api_now_iso()
    end = (datetime.now(timezone.utc) + timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    all_events = []
    for cid in cids:
        try:
            events = await c.async_get_paginated(
                "calendar_events",
                {
                    "context_codes[]": [f"course_{cid}"],
                    "start_date": start,
                    "end_date": end,
                    "type": "event",
                },
            )
            all_events.extend(events)
        except Exception:
            continue

    if not all_events:
        return f"No calendar events found for the next {days} days."

    out = [f"--- Calendar Events (Next {days} Days) ---"]
    for e in all_events:
        out.append(
            f"[{format_ts(e.get('start_at'))}] {e.get('context_name', 'N/A')}: {e.get('title')}"
        )
    return "\n".join(out)


async def get_zoom_data(course: Optional[Union[int, str]] = None) -> List[Dict]:
    """Helper to gather structured Zoom link and schedule data."""
    c = get_client()
    cid_target = await _resolve_cid(course)

    if cid_target:
        # If specific course, fetch full details
        try:
            res = await c.async_request("GET", f"courses/{cid_target}")
            course_obj = res.json()
            courses = [course_obj]
        except Exception:
            return []
    else:
        courses = await get_active_courses()

    results = []
    for course_obj in courses:
        cid = course_obj["id"]
        cname = course_obj.get("name", f"Course_{cid}")
        data = {"id": cid, "name": cname, "links": set(), "scheds": set()}

        # 1. Syllabus - High priority
        try:
            res = await c.async_request(
                "GET", f"courses/{cid}", params={"include[]": "syllabus_body"}
            )
            s_detail = res.json()
            s_body = s_detail.get("syllabus_body", "")
            data["links"].update(extract_zoom(s_body))
            data["scheds"].update(extract_schedules(clean_html(s_body)))
        except Exception:
            pass

        # 2. Announcements - Fetch last 5 (more efficient than all)
        try:
            anns = await c.async_get_paginated(
                f"courses/{cid}/discussion_topics",
                {"only_announcements": True},
                limit=5,
            )
            for a in anns:
                msg = a.get("message", "")
                found_l = extract_zoom(msg)
                if found_l:
                    data["links"].update(found_l)
                found_s = extract_schedules(clean_html(msg))
                if found_s:
                    data["scheds"].update([f"Ann '{a['title']}': {s}" for s in found_s])
        except Exception:
            pass

        # 3. Calendar - High priority for recurring sessions
        try:
            now_utc = datetime.now(timezone.utc)
            start = (now_utc - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            end = (now_utc + timedelta(days=21)).strftime("%Y-%m-%dT%H:%M:%SZ")
            events = await c.async_get_paginated(
                "calendar_events",
                {
                    "context_codes[]": [f"course_{cid}"],
                    "start_date": start,
                    "end_date": end,
                    "type": "event",
                },
            )
            for e in events:
                desc = e.get("description", "") or ""
                title = e.get("title", "") or ""
                if "zoom" in (title + desc).lower():
                    data["links"].update(extract_zoom(desc))
                    data["scheds"].add(
                        f"Event: {title} at {format_ts(e.get('start_at'))}"
                    )
        except Exception:
            pass

        # 4. Modules/Pages - ONLY if specifically requested (too slow for global list)
        if cid_target:
            try:
                mods = await c.async_get_paginated(
                    f"courses/{cid}/modules", {"include[]": "items"}
                )
                for m in mods:
                    for item in m.get("items", []):
                        if item.get("external_url") and "zoom.us" in item.get(
                            "external_url"
                        ):
                            data["links"].add(item["external_url"])
            except Exception:
                pass

        if data["links"] or data["scheds"]:
            data["links"] = sorted(list(data["links"]))
            data["scheds"] = sorted(list(data["scheds"]))
            results.append(data)

    return results


@mcp.tool()
@tool_wrapper
async def zoom_links(course: Optional[Union[int, str]] = None) -> str:
    """Deep scrape Zoom links and schedules from announcements, pages, modules, syllabus, and calendar."""
    data = await get_zoom_data(course)
    if not data:
        return "No Zoom links or schedules found."

    out = []
    for c in data:
        out.append(f"\nCourse: {c['name']}")
        for link in c["links"]:
            out.append(f"  Link: {link}")
        for s in c["scheds"]:
            out.append(f"  Schedule: {s}")
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def search_files(course: Union[int, str], query: str) -> str:
    """Search for files within a specific course (ID or Name) by name."""
    cid = await _resolve_cid(course)
    c = get_client()
    files = await c.async_get_paginated(f"courses/{cid}/files", {"search_term": query})
    if not files:
        return f"No files found matching '{query}' in course {cid}."

    out = ["ID       | SIZE     | NAME"]
    out.append("-" * 45)
    for f in files:
        size_mb = f.get("size", 0) / (1024 * 1024)
        out.append(
            f"{str(f['id']).ljust(8)} | {size_mb:6.2f} MB | {f.get('display_name')}"
        )
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def grades(course: Optional[Union[int, str]] = None) -> str:
    """Get a summary of current grades for a specific course or all active courses."""
    c = get_client()
    cid_target = await _resolve_cid(course)

    if cid_target:
        res = await c.async_request(
            "GET", f"courses/{cid_target}", params={"include[]": "total_scores"}
        )
        courses = [res.json()]
    else:
        courses = await get_active_courses()

    out = ["COURSE ID | SCORE  | GRADE | NAME"]
    out.append("-" * 55)
    for course_obj in courses:
        enrollments = course_obj.get("enrollments", [])
        if not enrollments and cid_target:
            enrollments = await c.async_get_paginated(
                f"courses/{course_obj['id']}/enrollments", {"user_id": "self"}
            )

        for e in enrollments:
            if e.get("type") == "student":
                score = str(e.get("computed_current_score", "N/A")).ljust(6)
                grade = str(e.get("computed_current_grade", "N/A")).ljust(5)
                name = course_obj.get("name", "N/A")[:30]
                out.append(
                    f"{str(course_obj['id']).ljust(9)} | {score} | {grade} | {name}"
                )
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def activity(limit: int = 20) -> str:
    """View the recent activity stream (new grades, announcements, discussions)."""
    items = await get_client().async_get_paginated(
        "users/self/activity_stream", limit=limit
    )
    if not items:
        return "No recent activity."

    out = [f"--- Recent Activity (Last {limit} items) ---"]
    for i in items:
        ts = format_ts(i.get("created_at"))
        type_ = (
            i.get("type", "Unknown")
            .replace("Announcement", "[Announcement]")
            .replace("DiscussionTopic", "[Discussion]")
            .replace("Submission", "[Submission]")
            .replace("Conversation", "[Message]")
        )
        title = i.get("title", "No Title")
        out.append(f"[{ts}] {type_} | {title}")
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def participants(course: Union[int, str]) -> str:
    """List instructors and TAs for a specific course (ID or Name)."""
    cid = await _resolve_cid(course)
    c = get_client()
    users = await c.async_get_paginated(
        f"courses/{cid}/users", {"enrollment_type[]": ["teacher", "ta"]}
    )
    if not users:
        return "No instructors/TAs found."

    out = [f"--- Staff for Course {cid} ---"]
    for u in users:
        out.append(f"• {u.get('name')} (@{u.get('login_id', 'N/A')})")
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def export_calendar(
    course: Optional[Union[int, str]] = None,
    filename: str = "canvas_zoom_links.ics",
) -> str:
    """Gather discovered Zoom schedules and export as an ICS file. Term dates are auto-calculated."""
    data = await get_zoom_data(course)
    if not data:
        return "No data to export."

    now = datetime.now(TARGET_TZ)
    # General heuristics for term end dates (adapts to current year)
    curr_year = now.year
    if now.month <= 4:
        until_dt = datetime(curr_year, 4, 11)
    elif now.month <= 8:
        until_dt = datetime(curr_year, 8, 15)
    else:
        until_dt = datetime(curr_year, 12, 15)

    until = until_dt.strftime("%Y%m%dT000000Z")

    ics = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Custom//Canvas Zoom Integration//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for c in data:
        if c["links"]:
            link = c["links"][0]
            for s in c["scheds"]:
                if f"at {curr_year}-" in s:
                    try:
                        ts_str = s.split("at ")[1]
                        # Assume ts_str is ISO since format_ts returns it on error
                        dt_utc = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        dt_local = dt_utc.astimezone(TARGET_TZ)
                        ics.append("BEGIN:VEVENT")
                        ics.append(f"SUMMARY:{c['name']}")
                        ics.append(f"DESCRIPTION:Zoom Link: {link}\nSchedule: {s}")
                        ics.append(f"LOCATION:{link}")
                        ics.append(
                            f"DTSTART;TZID={TARGET_TZ_NAME}:{dt_local.strftime('%Y%m%dT%H%M%S')}"
                        )
                        ics.append(
                            f"DTEND;TZID={TARGET_TZ_NAME}:{(dt_local + timedelta(minutes=90)).strftime('%Y%m%dT%H%M%S')}"
                        )
                        ics.append(f"RRULE:FREQ=WEEKLY;UNTIL={until}")
                        ics.append("END:VEVENT")
                    except Exception:
                        continue

    ics.append("END:VCALENDAR")
    ics_content = "\n".join(ics)

    desktop = os.path.expanduser("~/Desktop")
    out_path = os.path.join(desktop, filename)
    with open(out_path, "w") as f:
        f.write(ics_content)

    return f"Success: Exported {len(data)} courses to {out_path}"


@mcp.tool()
@tool_wrapper
async def deadlines() -> str:
    """See upcoming deadlines (next 50 items)."""
    start_date = get_api_now_iso()

    # Filter for assignment-like types to avoid announcement bloat in the deadline list
    params = {"start_date": start_date, "filter": "assignment,quiz"}

    items = await get_client().async_get_paginated("planner/items", params)
    if not items:
        return "Clear skies! No upcoming assignments or quizzes found."

    # Deduplicate items by ID if they appear multiple times
    seen_ids = set()
    unique_items = []
    for i in items:
        pid = i.get("plannable_id")
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            unique_items.append(i)

    out = ["DUE DATE       | TYPE | COURSE  | TITLE"]
    out.append("-" * 55)
    for i in unique_items:
        date = format_ts(i.get("plannable_date")).ljust(14)
        type_ = i.get("plannable_type", "").ljust(4)
        course = (i.get("context_name", "N/A")[:7]).ljust(7)
        title = (
            i.get("plannable", {}).get("title")
            or i.get("plannable", {}).get("name")
            or "Untitled"
        )
        out.append(f"{date} | {type_} | {course} | {title}")
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def inbox() -> str:
    """Check unread messages. Use 'read_message' with the ID for full content."""
    convs = await get_client().async_get_paginated("conversations", {"scope": "unread"})
    if not convs:
        return "Inbox Zero! Nice!"

    out = [f"Unread messages: {len(convs)}"]
    for c in convs:
        sub = c.get("subject", "No Subject")
        last = c.get("last_message", "")[:150]
        out.append(f"• [{c['id']}] {sub}: {last}...")
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def read_message(conversation_id: int) -> str:
    """Read full message threads (conversations) from your inbox."""
    c = get_client()
    res = await c.async_request("GET", f"conversations/{conversation_id}")
    conv = res.json()

    out = [f"Subject: {conv.get('subject', 'No Subject')}"]
    msgs = conv.get("messages", [])
    if not msgs:
        out.append(f"\n{conv.get('last_message', '(Empty)')}")
    else:
        for m in msgs:
            author = m.get("author_id", "System")
            ts = format_ts(m.get("created_at"))
            body = m.get("body", "")
            out.append(f"\n--- {author} ({ts}) ---\n{body}")

    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def assignments(course: Union[int, str]) -> str:
    """List assignments for a course (ID or Name)."""
    cid = await _resolve_cid(course)
    assns = await get_client().async_get_paginated(f"courses/{cid}/assignments")
    if not assns:
        return "No assignments found."

    out = ["ID       | DUE DATE       | NAME"]
    out.append("-" * 45)
    for a in assns:
        out.append(
            f"{str(a['id']).ljust(8)} | {format_ts(a.get('due_at')).ljust(14)} | {a['name']}"
        )
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def list_quizzes(course: Union[int, str]) -> str:
    """List quizzes for a course (ID or Name)."""
    cid = await _resolve_cid(course)
    c = get_client()
    quizzes = await c.async_get_paginated(f"courses/{cid}/quizzes")
    if not quizzes:
        return f"No quizzes found for {course}."

    out = ["ID       | DUE            | PTS | TITLE"]
    out.append("-" * 65)
    for q in quizzes:
        due = format_ts(q.get("due_at"))[:15]
        pts = str(q.get("points_possible") or 0).ljust(3)
        title = q.get("title", "Untitled")[:35]
        out.append(f"{str(q.get('id')).ljust(8)} | {due.ljust(15)} | {pts} | {title}")
    return "\n".join(out)


def clean_html_robust(html: str) -> str:
    """Enhanced HTML to Markdown conversion: preserves images, tables, and lists."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        # Preserve image info
        for img in soup.find_all("img"):
            src = img.get("src", "")
            alt = img.get("alt", "image")
            img.replace_with(f"\n[IMAGE: {alt} ({src})]\n")

        # Basic list preservation
        for li in soup.find_all("li"):
            li.insert_before("  • ")
            li.append("\n")

        return soup.get_text(separator="\n", strip=True)
    except Exception:
        # Fallback to basic regex if bs4 fails
        return clean_html(html)


@mcp.tool()
@tool_wrapper
async def quiz_details(course: Union[int, str], quiz_id: int) -> str:
    """Get rich details and questions for a specific quiz (includes images and IDs)."""
    cid = await _resolve_cid(course)
    c = get_client()

    # Meta
    quiz_res = await c.async_request("GET", f"courses/{cid}/quizzes/{quiz_id}")
    quiz = quiz_res.json()

    # Questions
    questions = await c.async_get_paginated(
        f"courses/{cid}/quizzes/{quiz_id}/questions"
    )

    out = [
        f"# Quiz: {quiz.get('title')}",
        f"**Course**: {course} (ID: {cid})",
        f"**Points**: {quiz.get('points_possible')}",
        f"**Time Limit**: {quiz.get('time_limit') or 'No limit'} minutes",
        f"**Allowed Attempts**: {quiz.get('allowed_attempts')}",
        f"**Navigation**: {'One Question at a Time' if quiz.get('one_question_at_a_time') else 'All at Once'}",
        f"**Constraint**: {'CANNOT GO BACK' if quiz.get('cant_go_back') else 'Can go back'}",
        f"\n## Instructions\n{clean_html_robust(quiz.get('description', 'No instructions provided.'))}",
        "\n## Questions",
    ]

    for i, q in enumerate(questions, 1):
        q_text = clean_html_robust(q.get("question_text", ""))
        out.append(
            f"### Question {i} ({q.get('points_possible')} pts) [ID: {q.get('id')}]"
        )
        out.append(f"Type: {q.get('question_type')}")
        out.append(f"{q_text}")

        answers = q.get("answers", [])
        if answers:
            out.append("**Options:**")
            for a in answers:
                atext = clean_html_robust(a.get("text", a.get("html", "N/A")))
                out.append(f"  [{a.get('id')}] {atext}")
        out.append("-" * 20)

    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def start_quiz_submission(course: Union[int, str], quiz_id: int) -> str:
    """
    Start a quiz submission session. Returns the submission ID and validation token.
    Reports time limits and navigation constraints immediately.
    """
    cid = await _resolve_cid(course)
    c = get_client()
    res = await c.async_request("POST", f"courses/{cid}/quizzes/{quiz_id}/submissions")
    data = res.json()

    subs = data.get("quiz_submissions", [])
    if not subs:
        return f"Error: Failed to start quiz submission: {res.text}"

    sub = subs[0]
    sub_id = sub.get("id")
    token = sub.get("validation_token")
    end_at = format_ts(sub.get("end_at"))

    msg = [
        f"Success: Quiz submission started! (ID: {sub_id})",
        f"Validation Token: {token}",
        f"Status: {sub.get('workflow_state')}",
        f"Due/Ends at: {end_at}",
        f"Navigation Mode: {'One Question at a Time' if data.get('quizzes', [{}])[0].get('one_question_at_a_time') else 'All at Once'}",
    ]
    return "\n".join(msg)


@mcp.tool()
@tool_wrapper
async def get_quiz_submission_state(submission_id: int, validation_token: str) -> str:
    """Check time remaining and current progress of an active quiz."""
    c = get_client()
    res = await c.async_request(
        "GET",
        f"quiz_submissions/{submission_id}",
        params={"validation_token": validation_token},
    )
    data = res.json()
    sub = data.get("quiz_submissions", [{}])[0]

    time_left = sub.get("time_left", 0)
    minutes = int(time_left / 60) if time_left else 0

    out = [
        f"Time Left: {minutes} minutes ({time_left} seconds)",
        f"Attempt: {sub.get('attempt')}",
        f"Workflow State: {sub.get('workflow_state')}",
        f"Fudge Points: {sub.get('fudge_points') or 0}",
    ]
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def answer_quiz_question(
    submission_id: int,
    validation_token: str,
    question_id: int,
    answer: Union[int, str, List[int]],
) -> str:
    """
    Answer a specific question in an active quiz submission.
    Use answer_quiz_questions_bulk for multiple questions.
    """
    c = get_client()
    payload = {
        "validation_token": validation_token,
        "quiz_questions": [{"id": question_id, "answer": answer}],
    }

    res = await c.async_request(
        "POST", f"quiz_submissions/{submission_id}/questions", data=payload
    )
    if res.status_code == 200:
        return f"Success: Question {question_id} answered successfully."
    return f"Error: Failed to answer question: {res.text}"


@mcp.tool()
@tool_wrapper
async def answer_quiz_questions_bulk(
    submission_id: int, validation_token: str, answers: List[Dict[str, Any]]
) -> str:
    """
    Submit multiple quiz answers in one call for efficiency.
    'answers' list format: [{'id': question_id, 'answer': answer_val}, ...]
    """
    c = get_client()
    payload = {"validation_token": validation_token, "quiz_questions": answers}

    res = await c.async_request(
        "POST", f"quiz_submissions/{submission_id}/questions", data=payload
    )
    if res.status_code == 200:
        return f"Success: {len(answers)} questions answered successfully in bulk."
    return f"Error: Bulk answer failed: {res.text}"


@mcp.tool()
@tool_wrapper
async def complete_quiz_submission(submission_id: int, validation_token: str) -> str:
    """Finalize and submit the entire quiz."""
    c = get_client()
    payload = {
        "validation_token": validation_token,
        "attempt": 1,
    }  # Usually attempt 1 unless resumed

    res = await c.async_request(
        "POST", f"quiz_submissions/{submission_id}/complete", data=payload
    )
    if res.status_code == 200:
        return f"Success: Quiz submission {submission_id} finalized and submitted!"
    return f"Error: Failed to finalize quiz: {res.text}"


@mcp.tool()
@tool_wrapper
async def assignment_details(course: Union[int, str], assignment_id: int) -> str:
    """Get full verbatim instructions and details for a specific assignment."""
    cid = await _resolve_cid(course)
    c = get_client()
    res = await c.async_request("GET", f"courses/{cid}/assignments/{assignment_id}")
    a = res.json()

    desc = a.get("description", "")
    out = [f"Assignment: {a.get('name', f'ID: {assignment_id}')}"]
    out.append(f"Due: {format_ts(a.get('due_at'))}")
    out.append(f"Points: {a.get('points_possible', 0)}")
    out.append("-" * 30)

    if not desc:
        out.append("(No instructions provided in the description field)")
    else:
        out.append(clean_html(desc))

    atts = a.get("attachments", [])
    if atts:
        out.append("\nFiles provided with instructions:")
        for att in atts:
            size_kb = att.get("size", 0) / 1024
            out.append(
                f"  • {att.get('display_name')} (ID: {att.get('id')}, Size: {size_kb:.1f} KB)"
            )

    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def submit_assignment(
    course: Union[int, str],
    assignment_id: int,
    submission_type: str,
    body: Optional[str] = None,
    url: Optional[str] = None,
    file_path: Optional[str] = None,
) -> str:
    """
    Submit an assignment.
    Types: 'online_text_entry' (requires body), 'online_url' (requires url), 'online_upload' (requires file_path).
    """
    cid = await _resolve_cid(course)
    c = get_client()

    payload = {"submission[submission_type]": submission_type}

    if submission_type == "online_text_entry":
        if not body:
            return "Error: body required for online_text_entry"
        payload["submission[body]"] = body
    elif submission_type == "online_url":
        if not url:
            return "Error: url required for online_url"
        payload["submission[url]"] = url
    elif submission_type == "online_upload":
        if not file_path:
            return "Error: file_path required for online_upload"

        # Upload file first
        try:
            upload_res = await c.async_upload_file(
                f"courses/{cid}/assignments/{assignment_id}/submissions/self/files",
                file_path,
            )
            file_id = upload_res.get("id")
            if not file_id:
                return f"Error: Failed to upload file: {upload_res}"
            payload["submission[file_ids][]"] = [file_id]
        except Exception as e:
            return f"Error: Upload error: {str(e)}"

    res = await c.async_request(
        "POST", f"courses/{cid}/assignments/{assignment_id}/submissions", data=payload
    )
    if res.status_code in (200, 201):
        return f"Success: Successfully submitted assignment {assignment_id}."
    return f"Error: Failed to submit: {res.text}"


@mcp.tool()
@tool_wrapper
async def feedback(course: Union[int, str], assignment_id: int) -> str:
    """Read grades and instructor comments."""
    cid = await _resolve_cid(course)
    res = await get_client().async_request(
        "GET",
        f"courses/{cid}/assignments/{assignment_id}/submissions/self",
        params={"include[]": "submission_comments"},
    )
    s = res.json()

    out = [f"Grade: {s.get('grade', 'N/A')} ({s.get('score', 'N/A')} pts)"]
    comments = s.get("submission_comments", [])
    if comments:
        out.append("\nFeedback:")
        for c in comments:
            out.append(
                f'- {c.get("author_name")}: "{c.get("comment")}" ({format_ts(c.get("created_at"))})'
            )
    else:
        out.append("\n(No feedback comments)")
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def read_file(file_id: int) -> str:
    """Extract content or metadata from a file (PDF, Text, Office docs)."""
    c = get_client()
    res_info = await c.async_request("GET", f"files/{file_id}")
    info = res_info.json()
    url = info.get("url")
    if not url:
        return "Error: Download URL missing."

    res = await httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True).get(url)
    res.raise_for_status()

    ctype = res.headers.get("Content-Type", "").lower()
    fname = info.get("display_name", "Unknown File")
    text = ""

    if "pdf" in ctype:
        try:
            reader = PdfReader(io.BytesIO(res.content))
            for page in reader.pages:
                text += page.extract_text() + "\n"
        except Exception as e:
            text = f"[Error parsing PDF: {str(e)}]"
    elif "text" in ctype or "json" in ctype or "javascript" in ctype:
        text = res.content.decode("utf-8", errors="ignore")
    elif any(
        ext in ctype for ext in ["msword", "officedocument", "powerpoint", "excel"]
    ):
        # We don't have native parsers for all Office types here, so provide metadata
        size_mb = info.get("size", 0) / (1024 * 1024)
        text = (
            f"Warning: Binary Office Document detected ({fname})\n"
            f"Type: {ctype}\n"
            f"Size: {size_mb:.2f} MB\n\n"
            "Full content extraction for Word/PPT/Excel requires specialized libraries. "
            "The file has been downloaded successfully to your local drive if using 'sync_course'."
        )
    else:
        text = f"[Binary file of type {ctype}. Use 'sync_course' to archive and view locally.]"

    return f"File: {fname} ({ctype})\n" + "=" * 40 + "\n" + text[:45000]


@mcp.tool()
@tool_wrapper
async def missing() -> str:
    """Overdue assignments."""
    miss = await get_client().async_get_paginated("users/self/missing_submissions")
    if not miss:
        return "Zero missing assignments. Nice!"

    out = ["DUE DATE       | COURSE | NAME"]
    out.append("-" * 45)
    for a in miss:
        out.append(
            f"{format_ts(a.get('due_at')).ljust(14)} | {str(a.get('course_id')).ljust(6)} | {a['name']}"
        )
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def sync_course(course: Union[int, str]) -> str:
    """Download and sync all course materials asynchronously with high-performance concurrency."""
    if not DRIVE_ROOT:
        logger.error(
            f"[{ERROR_CONFIG_MISSING}] DRIVE_ROOT environment variable not set."
        )
        return f"Error {ERROR_CONFIG_MISSING}: DRIVE_ROOT not set. Check .env."

    c = get_client()
    try:
        maybe_cid = await _resolve_cid(course)
        if maybe_cid is None:
            return f"Error {ERROR_COURSE_NOT_FOUND}: Could not find course '{course}'."
        cid: int = maybe_cid
    except Exception as e:
        logger.error(
            f"[{ERROR_COURSE_NOT_FOUND}] Failed to resolve course '{course}': {e}"
        )
        return f"Error {ERROR_COURSE_NOT_FOUND}: Could not find course '{course}'."

    # 1. Atomic Sync Lock per course
    lock = get_sync_lock(cid)
    if lock.locked():
        return f"Warning {ERROR_SYNC_LOCKED}: Sync for course {cid} is already in progress."

    async with lock:
        try:
            # 1. Fetch Course & Term Info
            res = await c.async_request(
                "GET", f"courses/{cid}", params={"include[]": ["term", "syllabus_body"]}
            )
            detail = res.json()
            cname = detail.get("name", f"Course_{cid}")
            term_obj = detail.get("term", {})
            term_name = term_obj.get("name", "Unknown Term")

            # Regex extraction for term standards (e.g. "AY 2025-2026, Term 2")
            ay_match = re.search(r"AY \d{4}-\d{4}", term_name)
            term_match = re.search(r"Term \d+", term_name)

            ay = ay_match.group(0) if ay_match else "Unknown AY"
            term_id = term_match.group(0) if term_match else "Unknown Term"

            # Date Logic Verification
            if not is_term_active(term_obj):
                logger.warning(f"Archived Course Sync Blocked: {cname} ({term_name})")
                return f"Warning {ERROR_TERM_INACTIVE}: Course '{cname}' is archived or from a past/future term ({term_name}). Syncing is restricted to current active terms."

            base_path = Path(DRIVE_ROOT) / ay / term_id / sanitize_filename(cname)
            if base_path.exists():
                logger.info(
                    f"Sync Update: {cname} (Found existing directory: {base_path})"
                )
            else:
                logger.info(f"New Sync: {cname} (Creating directory: {base_path})")
                base_path.mkdir(parents=True, exist_ok=True)

        except Exception as e:
            logger.exception(
                f"[{ERROR_API_FAILURE}] Failed to initialize sync for ID {cid}: {e}"
            )
            return (
                f"Error {ERROR_API_FAILURE}: API or Filesystem error. See system.log."
            )

        stats = {"ann": 0, "assn": 0, "res": 0, "sub": 0}

        # 2. Sync Syllabus
        try:
            syllabus_body = detail.get("syllabus_body", "")
            if syllabus_body:
                syllabus_file = base_path / "SYLLABUS.md"
                with open(syllabus_file, "w") as f:
                    f.write(f"# Syllabus: {cname}\n\n{clean_html(syllabus_body)}")
        except Exception as e:
            logger.warning(f"Syllabus sync failed: {e}")

        # 3. Sync Announcements
        try:
            ann_dir = base_path / "ANNOUNCEMENTS"
            if not ann_dir.exists():
                ann_dir.mkdir(parents=True, exist_ok=True)

            anns = await c.async_get_paginated(
                f"courses/{cid}/discussion_topics", {"only_announcements": True}
            )
            for a in anns:
                posted_at = a.get("posted_at")
                date_str = (posted_at or "0000-00-00").split("T")[0]
                fname = truncate_filename(
                    ann_dir,
                    sanitize_filename(f"{date_str}_{a.get('title', 'Untitled')}.md"),
                )
                with open(ann_dir / fname, "w") as f:
                    f.write(
                        f"# {a.get('title')}\n**Date:** {format_ts(a.get('posted_at'))}\n\n{clean_html(a.get('message', ''))}"
                    )
                stats["ann"] += 1
        except Exception as e:
            logger.warning(f"Announcements sync failed for {cname}: {e}")

        # 4. Sync Resources (Files) with parallel batching
        try:
            folders = await c.async_get_paginated(f"courses/{cid}/folders")
            folder_map = {
                folder["id"]: str(folder.get("full_name", ""))
                .replace("course files", "")
                .lstrip("/")
                for folder in folders
            }
            files = await c.async_get_paginated(f"courses/{cid}/files")
            res_root = base_path / "RESOURCES"

            tasks = []
            for file_obj in files:
                rel_dir = folder_map.get(file_obj["folder_id"], "")
                fname = truncate_filename(res_root / rel_dir, file_obj["display_name"])
                target_path = res_root / rel_dir / fname
                tasks.append(
                    c.download_file_async(
                        file_obj["url"], target_path, expected_size=file_obj.get("size")
                    )
                )

            if tasks:
                results = await asyncio.gather(*tasks)
                stats["res"] = sum(1 for r in results if r)
        except Exception as e:
            logger.warning(f"Resource sync failed for {cname}: {e}")

        # 5. Sync Assignments & Submissions
        try:
            assns = await c.async_get_paginated(
                f"courses/{cid}/assignments", params={"include[]": ["attachments"]}
            )
            assn_root = base_path / "ASSIGNMENTS"

            async def process_assignment(a):
                nonlocal stats
                a_name = a.get("name", f"Assignment_{a['id']}")
                a_dir = assn_root / sanitize_filename(a_name)
                if not a_dir.exists():
                    a_dir.mkdir(parents=True, exist_ok=True)

                # 1. Save Instructions (Markdown & HTML for word-for-word fidelity)
                desc = a.get("description", "")
                with open(a_dir / "instructions.md", "w") as f:
                    f.write(
                        f"# {a_name}\n**Due:** {format_ts(a.get('due_at'))}\n\n{clean_html(desc)}"
                    )
                if desc:
                    with open(a_dir / "instructions.html", "w") as f:
                        f.write(desc)

                # 2. Sync Assignment-level Attachments (e.g. prompt PDFs, Rubrics)
                # Note: These are different from 'submission' attachments.
                for att in a.get("attachments", []):
                    inst_dir = a_dir / "instruction_files"
                    fname = truncate_filename(inst_dir, att["display_name"])
                    await c.download_file_async(
                        att["url"],
                        inst_dir / fname,
                        expected_size=att.get("size"),
                    )

                stats["assn"] += 1

                # 3. Sync Student's Own Submissions
                try:
                    res_sub = await c.async_request(
                        "GET",
                        f"courses/{cid}/assignments/{a['id']}/submissions/self",
                        params={"include[]": "attachments"},
                    )
                    sub = res_sub.json()
                    sub_tasks = []
                    for att in sub.get("attachments", []):
                        sub_dir = a_dir / "submissions"
                        fname = truncate_filename(sub_dir, att["display_name"])
                        sub_tasks.append(
                            c.download_file_async(
                                att["url"],
                                sub_dir / fname,
                                expected_size=att.get("size"),
                            )
                        )
                    if sub_tasks:
                        results = await asyncio.gather(*sub_tasks)
                        return sum(1 for r in results if r)
                except Exception:
                    return 0
                return 0

            if assns:
                sub_counts = await asyncio.gather(
                    *[process_assignment(a) for a in assns]
                )
                stats["sub"] = sum(sub_counts)
        except Exception as e:
            logger.warning(f"Assignment sync failed for {cname}: {e}")

        return (
            f"Success: Sync Complete: {cname}\n"
            f"Path: `{base_path}`\n\n"
            f"Stats: Ann: {stats['ann']} | Files: {stats['res']} | Assn: {stats['assn']} | Sub: {stats['sub']}\n"
            f"Hierarchy: {ay} > {term_id}"
        )


@mcp.tool()
@tool_wrapper
async def sync_term(term: Optional[str] = None) -> str:
    """Sync all courses belonging to a specific term (e.g. 'Term 2') asynchronously."""
    courses = await get_active_courses()

    target_term = term
    if not target_term:
        target_term = get_current_term(courses)
        if target_term:
            logger.info(f"Detected current term: {target_term}")
        else:
            return (
                "Error: Could not auto-detect current term. Please specify a term name."
            )

    # Filter courses matching the term
    to_sync = []
    for c in courses:
        c_term = c.get("term", {}).get("name", "")
        if target_term.lower() in c_term.lower():
            to_sync.append(c)

    if not to_sync:
        return f"Error: No courses found for term: {target_term}"

    out = [f"Starting Batch Async Sync for Term: {target_term}"]
    out.append(f"Found {len(to_sync)} courses.")
    out.append("-" * 30)

    # Parallel sync for courses (each will handle its own locking)
    tasks = [sync_course(c["id"]) for c in to_sync]
    results = await asyncio.gather(*tasks)

    for c, res in zip(to_sync, results):
        # Extract summary from sync_course result
        if "Stats:" in res:
            summary = res.split("Stats:")[-1].strip()
        elif "Error:" in res:
            summary = res.split("Error:")[-1].strip()
        else:
            summary = "Sync complete."
        out.append(f"• {c.get('name')}: {summary}")

    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def find(name: str) -> str:
    """Fuzzy search for a course."""
    courses = await get_active_courses()
    cmap = {c.get("name", "Unknown"): c["id"] for c in courses if "id" in c}
    matches = process.extract(name, cmap.keys(), scorer=fuzz.WRatio, limit=5)
    if not matches:
        return "No matches."

    out = ["SCORE | ID       | NAME"]
    out.append("-" * 35)
    for m in matches:
        out.append(f"{str(m[1]).ljust(5)} | {str(cmap[m[0]]).ljust(8)} | {m[0]}")
    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def global_search(query: str) -> str:
    """Federated search across all active courses for assignments, quizzes, and pages."""
    courses = await get_active_courses()
    c = get_client()

    async def search_course(co):
        cid = co["id"]
        cname = co.get("course_code") or co.get("name", f"ID: {cid}")
        results = []

        # Parallel searches within course
        try:
            tasks = [
                c.async_request(
                    "GET", f"courses/{cid}/assignments", params={"search_term": query}
                ),
                c.async_request(
                    "GET", f"courses/{cid}/pages", params={"search_term": query}
                ),
                c.async_request(
                    "GET", f"courses/{cid}/quizzes", params={"search_term": query}
                ),
            ]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            for res in responses:
                if isinstance(res, httpx.Response) and res.status_code == 200:
                    data = res.json()
                    for item in data:
                        # Determine type
                        itype = "Page"
                        if "due_at" in item and "points_possible" in item:
                            itype = "Assignment"
                        if "quiz_type" in item:
                            itype = "Quiz"

                        results.append(
                            {
                                "Course": cname,
                                "Type": itype,
                                "Title": item.get("name")
                                or item.get("title")
                                or "Untitled",
                            }
                        )
        except Exception:
            pass
        return results

    # Fan out across all courses
    all_results_lists = await asyncio.gather(*(search_course(co) for co in courses))
    all_results = [item for sublist in all_results_lists for item in sublist]

    if not all_results:
        return f"No results found for '{query}' across all active courses."

    out = ["COURSE       | TYPE       | TITLE"]
    out.append("-" * 60)
    for r in all_results[:50]:  # Limit output
        out.append(
            f"{r['Course'][:12].ljust(12)} | {r['Type'].ljust(10)} | {r['Title'][:35]}"
        )

    if len(all_results) > 50:
        out.append(f"\n... and {len(all_results) - 50} more results.")

    return "\n".join(out)


@mcp.tool()
@tool_wrapper
async def sync_courses() -> str:
    """Force refresh the cached list of active courses from Canvas. Use this when a user adds/drops a course."""
    try:
        courses = await get_active_courses(force_refresh=True)
        return f"Success: Successfully synced {len(courses)} active courses. Cache updated."
    except Exception as e:
        return f"Error: Failed to sync courses: {str(e)}"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse"])
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if args.transport == "sse":
        logger.info("Starting SSE server...")
        mcp.run(transport="sse")
    else:
        mcp.run()
