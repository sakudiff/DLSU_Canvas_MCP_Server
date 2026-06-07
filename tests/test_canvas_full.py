import asyncio
import sys
import os
import logging
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
import canvas_server

logging.basicConfig(level=logging.INFO)


async def _run_tool(name, func, *args, **kwargs):
    print("\n" + "=" * 50)
    print(f"TESTING TOOL: {name}")
    print("=" * 50)
    try:
        if asyncio.iscoroutinefunction(func):
            res = await func(*args, **kwargs)
        else:
            res = func(*args, **kwargs)

        if isinstance(res, str) and res.startswith("Error:"):
            print(f"TOOL RETURNED ERROR: {res}")
            return False

        print(f"SUCCESS. Output length: {len(str(res))}")
        print(f"Preview: {str(res)[:200]}...")
        return True
    except Exception as e:
        print(f"CRITICAL EXCEPTION in {name}: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return False


async def main():
    course_id = int(os.getenv("CANVAS_TEST_COURSE_ID", 123456))
    results = {}

    results["sync_courses"] = await _run_tool(
        "sync_courses", canvas_server.sync_courses
    )

    results["profile"] = await _run_tool("profile", canvas_server.profile)
    results["status"] = await _run_tool("status", canvas_server.status)
    results["list_courses"] = await _run_tool(
        "list_courses", canvas_server.list_courses
    )
    results["find"] = await _run_tool("find", canvas_server.find, "DATA")

    results["todo"] = await _run_tool("todo", canvas_server.todo)
    results["today"] = await _run_tool("today", canvas_server.today)
    results["week"] = await _run_tool("week", canvas_server.week)
    results["deadlines"] = await _run_tool("deadlines", canvas_server.deadlines)
    results["missing"] = await _run_tool("missing", canvas_server.missing)
    results["calendar"] = await _run_tool("calendar", canvas_server.calendar, course_id)

    results["inbox"] = await _run_tool("inbox", canvas_server.inbox)
    results["announcements"] = await _run_tool(
        "announcements", canvas_server.announcements, 7
    )
    results["activity"] = await _run_tool("activity", canvas_server.activity, 10)

    results["syllabus"] = await _run_tool("syllabus", canvas_server.syllabus, course_id)
    results["participants"] = await _run_tool(
        "participants", canvas_server.participants, course_id
    )
    results["assignments"] = await _run_tool(
        "assignments", canvas_server.assignments, course_id
    )
    results["grades"] = await _run_tool("grades", canvas_server.grades, course_id)

    results["search_files"] = await _run_tool(
        "search_files", canvas_server.search_files, course_id, "Assignment"
    )
    results["zoom_links"] = await _run_tool(
        "zoom_links", canvas_server.zoom_links, course_id
    )
    results["export_calendar"] = await _run_tool(
        "export_calendar", canvas_server.export_calendar, course_id, "test_full.ics"
    )

    results["list_discussions"] = await _run_tool(
        "list_discussions", canvas_server.list_discussions, course_id
    )
    results["list_modules"] = await _run_tool(
        "list_modules", canvas_server.list_modules, course_id
    )
    results["list_pages"] = await _run_tool(
        "list_pages", canvas_server.list_pages, course_id
    )
    results["list_quizzes"] = await _run_tool(
        "list_quizzes", canvas_server.list_quizzes, course_id
    )
    results["global_search"] = await _run_tool(
        "global_search", canvas_server.global_search, "Assignment"
    )

    print("\n--- Fetching dynamic IDs for specialized tests ---")
    c = canvas_server.get_client()
    try:
        files = await c.async_get_paginated(f"courses/{course_id}/files", limit=1)
        if files:
            results["read_file"] = await _run_tool(
                "read_file", canvas_server.read_file, files[0]["id"]
            )

        assns = await c.async_get_paginated(f"courses/{course_id}/assignments", limit=1)
        if assns:
            results["feedback"] = await _run_tool(
                "feedback", canvas_server.feedback, course_id, assns[0]["id"]
            )
            results["assignment_details"] = await _run_tool(
                "assignment_details",
                canvas_server.assignment_details,
                course_id,
                assns[0]["id"],
            )

        modules = await c.async_get_paginated(f"courses/{course_id}/modules", limit=1)
        if modules:
            results["list_module_items"] = await _run_tool(
                "list_module_items",
                canvas_server.list_module_items,
                course_id,
                modules[0]["id"],
            )

        pages = await c.async_get_paginated(f"courses/{course_id}/pages", limit=1)
        if pages:
            results["read_page"] = await _run_tool(
                "read_page", canvas_server.read_page, course_id, pages[0]["url"]
            )

        quizzes = await c.async_get_paginated(f"courses/{course_id}/quizzes", limit=1)
        if quizzes:
            results["quiz_details"] = await _run_tool(
                "quiz_details", canvas_server.quiz_details, course_id, quizzes[0]["id"]
            )
    except Exception:
        print("Failed to fetch dynamic IDs.")

    results["sync_course"] = await _run_tool(
        "sync_course", canvas_server.sync_course, course_id
    )

    print("\n" + "=" * 50)
    print("FINAL TEST SUMMARY")
    print("=" * 50)
    failed = [k for k, v in results.items() if not v]
    if not failed:
        print("ALL TOOLS PASSED")
    else:
        print(f"FAILED TOOLS: {', '.join(failed)}")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
