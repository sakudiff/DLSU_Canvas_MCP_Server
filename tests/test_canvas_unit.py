import sys
import pytest
import httpx
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(str(Path(__file__).parent.parent))
import canvas_server


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    monkeypatch.setenv("CANVAS_API_URL", "https://mock.canvas.com")
    monkeypatch.setenv("CANVAS_API_KEY", "mock_key")
    monkeypatch.setenv("DRIVE_ROOT", "/mock/drive/root")
    canvas_server.CANVAS_API_URL = "https://mock.canvas.com"
    canvas_server.CANVAS_API_KEY = "mock_key"
    canvas_server.DRIVE_ROOT = "/mock/drive/root"
    canvas_server._client = None
    canvas_server._global_courses = []
    canvas_server._last_course_fetch = None
    canvas_server._course_cache = {}


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.async_request = AsyncMock()
    client.async_get_paginated = AsyncMock()
    client.async_upload_file = AsyncMock()
    client.download_file_async = AsyncMock()

    def set_json_response(data, status_code=200):
        res = MagicMock(spec=httpx.Response)
        res.status_code = status_code
        res.json = MagicMock(return_value=data)
        client.async_request.return_value = res
        return res

    client.set_json_response = set_json_response

    with patch("canvas_server.get_client", return_value=client):
        yield client


@pytest.mark.asyncio
async def test_profile(mock_client):
    mock_client.set_json_response(
        {
            "name": "Test User",
            "primary_email": "test@dlsu.edu.ph",
            "login_id": "12345",
            "time_zone": "Asia/Manila",
            "calendar": {"ics": "https://ics.url"},
        }
    )
    res = await canvas_server.profile()
    assert "Test User" in res
    assert "test@dlsu.edu.ph" in res
    assert "12345" in res
    assert "Asia/Manila" in res


@pytest.mark.asyncio
async def test_todo(mock_client):
    mock_client.async_get_paginated.return_value = [
        {
            "context_name": "CS101",
            "ignore": "submitting",
            "assignment": {
                "name": "Assignment 1",
                "due_at": "2026-06-07T12:00:00Z",
                "points_possible": 100,
            },
        }
    ]
    res = await canvas_server.todo()
    assert "CS101" in res
    assert "Assignment 1" in res


@pytest.mark.asyncio
async def test_today(mock_client):
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "name": "CS101",
                "course_code": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [
            {
                "start_at": "2026-06-07T12:00:00Z",
                "context_name": "CS101",
                "title": "Lecture",
            }
        ],
        [],
        [],
    ]
    res = await canvas_server.today()
    assert "CS101" in res
    assert "Lecture" in res


@pytest.mark.asyncio
async def test_week(mock_client):
    mock_client.async_get_paginated.return_value = [
        {
            "plannable_date": "2026-06-07T12:00:00Z",
            "plannable": {"title": "Lab Exam"},
            "plannable_type": "quiz",
        }
    ]
    res = await canvas_server.week()
    assert "Lab Exam" in res


@pytest.mark.asyncio
async def test_status(mock_client):
    mock_client.set_json_response({"name": "Test User"})
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "term": {"name": "Term 2"},
                "name": "CS101",
                "course_code": "CS101",
            }
        ],
        [{"id": 999}],
    ]
    res = await canvas_server.status()
    assert "Test User" in res
    assert "Courses: 1" in res
    assert "Unread: 1" in res


@pytest.mark.asyncio
async def test_list_courses(mock_client):
    mock_client.async_get_paginated.return_value = [
        {
            "id": 101,
            "course_code": "CS101",
            "term": {"name": "Term 2"},
            "name": "Intro to CS",
        }
    ]
    res = await canvas_server.list_courses()
    assert "CS101" in res
    assert "Intro to CS" in res


@pytest.mark.asyncio
async def test_announcements(mock_client):
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [
            {
                "posted_at": "2026-06-07T12:00:00Z",
                "context_name": "CS101",
                "title": "Announcement 1",
            }
        ],
    ]
    res = await canvas_server.announcements(days=7)
    assert "Announcement 1" in res


@pytest.mark.asyncio
async def test_list_discussions(mock_client):
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [
            {
                "id": 201,
                "last_reply_at": "2026-06-07T12:00:00Z",
                "locked": False,
                "title": "Discussion 1",
            }
        ],
    ]
    res = await canvas_server.list_discussions(course="CS101")
    assert "Discussion 1" in res


@pytest.mark.asyncio
async def test_post_discussion_reply(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "CS101", "term": {"name": "Term 2"}}
    ]
    mock_client.set_json_response({}, status_code=201)

    res = await canvas_server.post_discussion_reply(
        course="CS101", topic_id=201, message="Hello"
    )
    assert "Successfully posted reply" in res


@pytest.mark.asyncio
async def test_syllabus(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "CS101", "term": {"name": "Term 2"}}
    ]
    mock_client.set_json_response({"syllabus_body": "<p>Syllabus Content</p>"})
    res = await canvas_server.syllabus(course="CS101")
    assert "Syllabus Content" in res


@pytest.mark.asyncio
async def test_list_modules(mock_client):
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [{"id": 301, "items_count": 2, "published": True, "name": "Module 1"}],
    ]
    res = await canvas_server.list_modules(course="CS101")
    assert "Module 1" in res


@pytest.mark.asyncio
async def test_list_module_items(mock_client):
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [{"id": 401, "type": "Page", "title": "Page 1"}],
    ]
    res = await canvas_server.list_module_items(course="CS101", module_id=301)
    assert "Page 1" in res


@pytest.mark.asyncio
async def test_list_pages(mock_client):
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [
            {
                "url": "intro",
                "updated_at": "2026-06-07T12:00:00Z",
                "title": "Introduction",
            }
        ],
    ]
    res = await canvas_server.list_pages(course="CS101")
    assert "Introduction" in res


@pytest.mark.asyncio
async def test_read_page(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "CS101", "term": {"name": "Term 2"}}
    ]
    mock_client.set_json_response(
        {
            "title": "Introduction",
            "updated_at": "2026-06-07T12:00:00Z",
            "body": "<p>Intro body</p>",
        }
    )
    res = await canvas_server.read_page(course="CS101", page_url="intro")
    assert "Intro body" in res


@pytest.mark.asyncio
async def test_calendar(mock_client):
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [
            {
                "start_at": "2026-06-07T12:00:00Z",
                "context_name": "CS101",
                "title": "Lecture",
            }
        ],
    ]
    res = await canvas_server.calendar(course="CS101")
    assert "Lecture" in res


@pytest.mark.asyncio
async def test_zoom_links(mock_client):
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [],
        [],
    ]
    mock_client.set_json_response(
        {
            "id": 101,
            "name": "CS101",
            "syllabus_body": "<p>Zoom at https://zoom.us/j/123456 on Mondays 10:00 AM</p>",
        }
    )
    res = await canvas_server.zoom_links(course="CS101")
    assert "zoom.us/j/123456" in res
    assert "Mondays 10:00 AM" in res


@pytest.mark.asyncio
async def test_export_calendar(mock_client):
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [],
        [],
    ]
    mock_client.set_json_response(
        {
            "id": 101,
            "name": "CS101",
            "syllabus_body": "<p>Zoom at https://zoom.us/j/123456 at 2026-06-07T10:00:00Z</p>",
        }
    )

    with patch("builtins.open", mock_open := MagicMock()):
        res = await canvas_server.export_calendar(
            course="CS101", filename="test_export.ics"
        )
        assert "Exported" in res
        mock_open.assert_called_once()


@pytest.mark.asyncio
async def test_deadlines(mock_client):
    mock_client.async_get_paginated.return_value = [
        {
            "plannable_id": 1,
            "plannable_date": "2026-06-07T12:00:00Z",
            "plannable_type": "assignment",
            "context_name": "CS101",
            "plannable": {"title": "Lab 1"},
        }
    ]
    res = await canvas_server.deadlines()
    assert "Lab 1" in res


@pytest.mark.asyncio
async def test_inbox(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"id": 901, "subject": "Question", "last_message": "Is class cancelled?"}
    ]
    res = await canvas_server.inbox()
    assert "Question" in res


@pytest.mark.asyncio
async def test_read_message(mock_client):
    mock_client.set_json_response(
        {
            "subject": "Question",
            "messages": [
                {
                    "author_id": 12,
                    "created_at": "2026-06-07T12:00:00Z",
                    "body": "Is class cancelled?",
                }
            ],
        }
    )
    res = await canvas_server.read_message(conversation_id=901)
    assert "Is class cancelled?" in res


@pytest.mark.asyncio
async def test_assignments(mock_client):
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [{"id": 501, "due_at": "2026-06-07T12:00:00Z", "name": "Lab 1"}],
    ]
    res = await canvas_server.assignments(course="CS101")
    assert "Lab 1" in res


@pytest.mark.asyncio
async def test_list_quizzes(mock_client):
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [
            {
                "id": 601,
                "due_at": "2026-06-07T12:00:00Z",
                "points_possible": 10,
                "title": "Quiz 1",
            }
        ],
    ]
    res = await canvas_server.list_quizzes(course="CS101")
    assert "Quiz 1" in res


@pytest.mark.asyncio
async def test_quiz_details(mock_client):
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [
            {
                "id": 701,
                "points_possible": 5,
                "question_type": "multiple_choice_question",
                "question_text": "1+1?",
                "answers": [{"id": 1, "text": "2"}],
            }
        ],
    ]
    mock_client.set_json_response(
        {
            "title": "Quiz 1",
            "points_possible": 10,
            "time_limit": 60,
            "allowed_attempts": 1,
            "one_question_at_a_time": False,
            "cant_go_back": False,
            "description": "Quiz instructions",
        }
    )
    res = await canvas_server.quiz_details(course="CS101", quiz_id=601)
    assert "Quiz 1" in res
    assert "1+1?" in res


@pytest.mark.asyncio
async def test_start_quiz_submission(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "CS101", "term": {"name": "Term 2"}}
    ]
    mock_client.set_json_response(
        {
            "quiz_submissions": [
                {
                    "id": 801,
                    "validation_token": "token123",
                    "workflow_state": "untaken",
                    "end_at": "2026-06-07T13:00:00Z",
                }
            ],
            "quizzes": [{"one_question_at_a_time": False}],
        }
    )
    res = await canvas_server.start_quiz_submission(course="CS101", quiz_id=601)
    assert "Quiz submission started" in res
    assert "token123" in res


@pytest.mark.asyncio
async def test_get_quiz_submission_state(mock_client):
    mock_client.set_json_response(
        {
            "quiz_submissions": [
                {
                    "time_left": 600,
                    "attempt": 1,
                    "workflow_state": "active",
                    "fudge_points": 0,
                }
            ]
        }
    )
    res = await canvas_server.get_quiz_submission_state(
        submission_id=801, validation_token="token123"
    )
    assert "10 minutes" in res


@pytest.mark.asyncio
async def test_answer_quiz_question(mock_client):
    mock_client.set_json_response({}, status_code=200)
    res = await canvas_server.answer_quiz_question(
        submission_id=801, validation_token="token123", question_id=701, answer=1
    )
    assert "answered successfully" in res


@pytest.mark.asyncio
async def test_answer_quiz_questions_bulk(mock_client):
    mock_client.set_json_response({}, status_code=200)
    res = await canvas_server.answer_quiz_questions_bulk(
        submission_id=801,
        validation_token="token123",
        answers=[{"id": 701, "answer": 1}],
    )
    assert "answered successfully in bulk" in res


@pytest.mark.asyncio
async def test_complete_quiz_submission(mock_client):
    mock_client.set_json_response({}, status_code=200)
    res = await canvas_server.complete_quiz_submission(
        submission_id=801, validation_token="token123"
    )
    assert "finalized and submitted" in res


@pytest.mark.asyncio
async def test_assignment_details(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "CS101", "term": {"name": "Term 2"}}
    ]
    mock_client.set_json_response(
        {
            "name": "Lab 1",
            "due_at": "2026-06-07T12:00:00Z",
            "points_possible": 100,
            "description": "Do lab 1 instructions",
            "attachments": [],
        }
    )
    res = await canvas_server.assignment_details(course="CS101", assignment_id=501)
    assert "Lab 1" in res
    assert "Do lab 1 instructions" in res


@pytest.mark.asyncio
async def test_submit_assignment_text(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "CS101", "term": {"name": "Term 2"}}
    ]
    mock_client.set_json_response({}, status_code=201)

    res = await canvas_server.submit_assignment(
        course="CS101",
        assignment_id=501,
        submission_type="online_text_entry",
        body="My answer",
    )
    assert "Successfully submitted" in res


@pytest.mark.asyncio
async def test_submit_assignment_file(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "CS101", "term": {"name": "Term 2"}}
    ]
    mock_client.async_upload_file.return_value = {"id": 111}
    mock_client.set_json_response({}, status_code=201)

    with patch("pathlib.Path.exists", return_value=True):
        res = await canvas_server.submit_assignment(
            course="CS101",
            assignment_id=501,
            submission_type="online_upload",
            file_path="my_doc.pdf",
        )
        assert "Successfully submitted" in res


@pytest.mark.asyncio
async def test_feedback(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "CS101", "term": {"name": "Term 2"}}
    ]
    mock_client.set_json_response(
        {
            "grade": "A",
            "score": 95,
            "submission_comments": [
                {
                    "author_name": "Prof",
                    "comment": "Outstanding work!",
                    "created_at": "2026-06-07T12:00:00Z",
                }
            ],
        }
    )
    res = await canvas_server.feedback(course="CS101", assignment_id=501)
    assert "Grade: A" in res
    assert "Outstanding work!" in res


@pytest.mark.asyncio
async def test_read_file_text(mock_client):
    mock_client.set_json_response(
        {
            "url": "https://mock.canvas.com/file",
            "display_name": "note.txt",
            "size": 1024,
        }
    )

    mock_res = MagicMock(spec=httpx.Response)
    mock_res.headers = {"Content-Type": "text/plain"}
    mock_res.content = b"Mock text file contents"

    with patch("httpx.AsyncClient.get", return_value=mock_res):
        res = await canvas_server.read_file(file_id=111)
        assert "note.txt" in res
        assert "Mock text file contents" in res


@pytest.mark.asyncio
async def test_read_file_pdf(mock_client):
    mock_client.set_json_response(
        {
            "url": "https://mock.canvas.com/file.pdf",
            "display_name": "slides.pdf",
            "size": 1024,
        }
    )

    mock_res = MagicMock(spec=httpx.Response)
    mock_res.headers = {"Content-Type": "application/pdf"}
    mock_res.content = b"PDF data"

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Extracted PDF contents"
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    with patch("httpx.AsyncClient.get", return_value=mock_res):
        with patch("canvas_server.PdfReader", return_value=mock_reader):
            res = await canvas_server.read_file(file_id=111)
            assert "slides.pdf" in res
            assert "Extracted PDF contents" in res


@pytest.mark.asyncio
async def test_missing(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"due_at": "2026-06-07T12:00:00Z", "course_id": 101, "name": "Overdue Lab"}
    ]
    res = await canvas_server.missing()
    assert "Overdue Lab" in res


@pytest.mark.asyncio
async def test_sync_course(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "CS101", "term": {"name": "Term 2"}}
    ]
    mock_client.set_json_response(
        {
            "id": 101,
            "name": "Course 101",
            "syllabus_body": "Syllabus details",
            "term": {
                "name": "AY 2025-2026 Term 2",
                "start_at": "2026-01-01T00:00:00Z",
                "end_at": "2026-06-30T00:00:00Z",
            },
        }
    )

    with patch("pathlib.Path.mkdir"), patch("builtins.open", MagicMock()):
        with patch("pathlib.Path.exists", return_value=True):
            res = await canvas_server.sync_course(course="CS101")
            assert "Sync Complete" in res


@pytest.mark.asyncio
async def test_sync_term(mock_client):
    mock_client.async_get_paginated.return_value = [
        {
            "id": 101,
            "name": "Course 101",
            "course_code": "CS101",
            "term": {
                "name": "AY 2025-2026 Term 2",
                "start_at": "2026-01-01T00:00:00Z",
                "end_at": "2026-06-30T00:00:00Z",
            },
        }
    ]

    with patch("canvas_server.sync_course", return_value="Sync complete."):
        res = await canvas_server.sync_term(term="Term 2")
        assert "Starting Batch Async Sync" in res
        assert "Course 101" in res


@pytest.mark.asyncio
async def test_find(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "Computer Science"}
    ]
    res = await canvas_server.find(name="Computer")
    assert "Computer Science" in res


@pytest.mark.asyncio
async def test_global_search(mock_client):
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "Computer Science"}
    ]

    mock_res = MagicMock(spec=httpx.Response)
    mock_res.status_code = 200
    mock_res.json.return_value = [
        {"name": "Quiz 1", "quiz_type": "assignment"},
        {"title": "Intro Page"},
    ]
    mock_client.async_request.return_value = mock_res

    res = await canvas_server.global_search(query="Intro")
    assert "Quiz 1" in res
    assert "Intro Page" in res


@pytest.mark.asyncio
async def test_sync_courses(mock_client) -> None:
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "name": "Course 101", "term": {"name": "Term 2"}}
    ]
    res = await canvas_server.sync_courses()
    assert "Successfully synced" in res


@pytest.mark.asyncio
async def test_search_files(mock_client) -> None:
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [{"id": 12345, "display_name": "syllabus.pdf", "size": 2 * 1024 * 1024}],
    ]
    res = await canvas_server.search_files(course="CS101", query="syllabus")
    assert "12345" in res
    assert "2.00 MB" in res
    assert "syllabus.pdf" in res


@pytest.mark.asyncio
async def test_search_files_empty(mock_client) -> None:
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [],
    ]
    res = await canvas_server.search_files(course="CS101", query="nonexistent")
    assert "No files found" in res


@pytest.mark.asyncio
async def test_grades_all_courses(mock_client) -> None:
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
                "enrollments": [
                    {
                        "type": "student",
                        "computed_current_score": 92.5,
                        "computed_current_grade": "A",
                    }
                ],
            }
        ]
    ]
    res = await canvas_server.grades()
    assert "101" in res
    assert "92.5" in res
    assert "A" in res


@pytest.mark.asyncio
async def test_grades_single_course(mock_client) -> None:
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [
            {
                "type": "student",
                "computed_current_score": 88.0,
                "computed_current_grade": "B",
            }
        ],
    ]
    mock_client.set_json_response({"id": 101, "name": "CS101", "enrollments": []})
    res = await canvas_server.grades(course="CS101")
    assert "101" in res
    assert "88.0" in res
    assert "B" in res


@pytest.mark.asyncio
async def test_activity(mock_client) -> None:
    mock_client.async_get_paginated.return_value = [
        {
            "created_at": "2026-06-07T12:00:00Z",
            "type": "Announcement",
            "title": "Welcome to CS101",
        },
        {
            "created_at": "2026-06-07T12:05:00Z",
            "type": "DiscussionTopic",
            "title": "Introduce Yourself",
        },
    ]
    res = await canvas_server.activity(limit=2)
    assert "[Announcement]" in res
    assert "Welcome to CS101" in res
    assert "[Discussion]" in res
    assert "Introduce Yourself" in res


@pytest.mark.asyncio
async def test_activity_empty(mock_client) -> None:
    mock_client.async_get_paginated.return_value = []
    res = await canvas_server.activity()
    assert "No recent activity" in res


@pytest.mark.asyncio
async def test_participants(mock_client) -> None:
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [{"name": "Dr. Juan Dela Cruz", "login_id": "juan_delacruz"}],
    ]
    res = await canvas_server.participants(course="CS101")
    assert "Dr. Juan Dela Cruz" in res
    assert "@juan_delacruz" in res


@pytest.mark.asyncio
async def test_participants_empty(mock_client) -> None:
    mock_client.async_get_paginated.side_effect = [
        [
            {
                "id": 101,
                "course_code": "CS101",
                "name": "CS101",
                "term": {"name": "Term 2"},
            }
        ],
        [],
    ]
    res = await canvas_server.participants(course="CS101")
    assert "No instructors/TAs found" in res


def test_is_term_active() -> None:
    term_past = {
        "name": "AY 2023-2024 Term 1",
        "start_at": "2023-09-01T00:00:00Z",
        "end_at": "2023-12-31T00:00:00Z",
    }
    assert not canvas_server.is_term_active(term_past)

    term_current = {
        "name": "AY 2026-2027 Term 1",
        "start_at": "2026-06-01T00:00:00Z",
        "end_at": "2026-10-30T00:00:00Z",
    }
    assert canvas_server.is_term_active(term_current)

    term_no_dates = {"name": "AY 2026-2027 Orientation"}
    assert canvas_server.is_term_active(term_no_dates)


def test_sanitize_filename() -> None:
    assert (
        canvas_server.sanitize_filename("CS 101: Intro/Advanced?")
        == "CS 101_ Intro_Advanced_"
    )
    assert canvas_server.sanitize_filename("") == "Untitled"


def test_truncate_filename() -> None:
    base = Path("/mock/drive")
    long_name = "a" * 300 + ".txt"
    truncated = canvas_server.truncate_filename(base, long_name)
    assert len(truncated) < len(long_name)
    assert truncated.endswith(".txt")


def test_format_ts() -> None:
    assert canvas_server.format_ts(None) == "N/A"
    assert canvas_server.format_ts("invalid-iso-date") == "invalid-iso-date"

    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert "Today" in canvas_server.format_ts(today_str)


@pytest.mark.asyncio
async def test_resolve_cid_suggestions(mock_client) -> None:
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "Computer Science 1"}
    ]
    with pytest.raises(ValueError) as exc:
        await canvas_server._resolve_cid("Intro to Math")
    assert "Could not find course matching" in str(exc.value)
    assert "Computer Science 1" in str(exc.value)


@pytest.mark.asyncio
async def test_read_file_office_doc(mock_client) -> None:
    mock_client.set_json_response(
        {
            "url": "https://mock.canvas.com/file.docx",
            "display_name": "assignment.docx",
            "size": 5 * 1024 * 1024,
        }
    )
    mock_res = MagicMock(spec=httpx.Response)
    mock_res.headers = {
        "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    }
    mock_res.content = b"docx data"

    with patch("httpx.AsyncClient.get", return_value=mock_res):
        res = await canvas_server.read_file(file_id=123)
        assert "Binary Office Document detected" in res
        assert "assignment.docx" in res


@pytest.mark.asyncio
async def test_read_file_other_binary(mock_client) -> None:
    mock_client.set_json_response(
        {
            "url": "https://mock.canvas.com/image.png",
            "display_name": "image.png",
            "size": 512 * 1024,
        }
    )
    mock_res = MagicMock(spec=httpx.Response)
    mock_res.headers = {"Content-Type": "image/png"}
    mock_res.content = b"png data"

    with patch("httpx.AsyncClient.get", return_value=mock_res):
        res = await canvas_server.read_file(file_id=124)
        assert "Binary file of type image/png" in res


@pytest.mark.asyncio
async def test_submit_assignment_url(mock_client) -> None:
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "CS101", "term": {"name": "Term 2"}}
    ]
    mock_client.set_json_response({}, status_code=201)
    res = await canvas_server.submit_assignment(
        course="CS101",
        assignment_id=501,
        submission_type="online_url",
        url="https://github.com/my-repo",
    )
    assert "Successfully submitted" in res


@pytest.mark.asyncio
async def test_submit_assignment_errors(mock_client) -> None:
    mock_client.async_get_paginated.return_value = [
        {"id": 101, "course_code": "CS101", "name": "CS101", "term": {"name": "Term 2"}}
    ]

    res = await canvas_server.submit_assignment(
        course="CS101", assignment_id=501, submission_type="online_text_entry", body=""
    )
    assert "Error: body required" in res

    res = await canvas_server.submit_assignment(
        course="CS101", assignment_id=501, submission_type="online_url", url=""
    )
    assert "Error: url required" in res

    res = await canvas_server.submit_assignment(
        course="CS101", assignment_id=501, submission_type="online_upload", file_path=""
    )
    assert "Error: file_path required" in res


@pytest.mark.asyncio
async def test_sync_course_failures(mock_client) -> None:
    canvas_server.DRIVE_ROOT = ""
    res = await canvas_server.sync_course(course="CS101")
    assert "DRIVE_ROOT not set" in res

    canvas_server.DRIVE_ROOT = "/mock/drive/root"
    mock_client.async_get_paginated.return_value = []
    res = await canvas_server.sync_course(course="NonexistentCourse")
    assert "Could not find course" in res


@pytest.mark.asyncio
async def test_canvas_client_retries(monkeypatch) -> None:
    client = canvas_server.CanvasClient("https://mock.canvas.com", "mock_key")

    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = [{"id": 1}]
    resp.headers = httpx.Headers({})

    mock_get = AsyncMock()
    mock_get.side_effect = [
        httpx.ConnectError("Connection failed"),
        httpx.TimeoutException("Timeout"),
        resp,
    ]
    monkeypatch.setattr(client.async_client, "get", mock_get)

    res = await client.async_get_paginated("courses")
    assert len(res) == 1
    assert res[0]["id"] == 1
    assert mock_get.call_count == 3


def test_canvas_client_sync_retries(monkeypatch) -> None:
    client = canvas_server.CanvasClient("https://mock.canvas.com", "mock_key")

    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = [{"id": 2}]
    resp.headers = httpx.Headers({})

    mock_get = MagicMock()
    mock_get.side_effect = [
        httpx.ConnectError("Connection failed"),
        httpx.TimeoutException("Timeout"),
        resp,
    ]
    monkeypatch.setattr(client.client, "get", mock_get)

    res = client.get_paginated("courses")
    assert len(res) == 1
    assert res[0]["id"] == 2
    assert mock_get.call_count == 3


def test_clean_html() -> None:
    html = "<p>Hello <b>World</b></p><script>console.log('hi');</script><style>body{}</style>"
    cleaned = canvas_server.clean_html(html)
    assert "Hello **World**" in cleaned
    assert "console.log" not in cleaned
    assert "body{}" not in cleaned


def test_extract_zoom() -> None:
    text = "Join Zoom at https://dlsu.zoom.us/j/999111222 or https://zoom.us/j/12345"
    links = canvas_server.extract_zoom(text)
    assert len(links) == 2
    assert "https://dlsu.zoom.us/j/999111222" in links
    assert "https://zoom.us/j/12345" in links


def test_extract_schedules() -> None:
    text = "Class is on Mondays 10:00 AM and Wed 02:00 PM."
    scheds = canvas_server.extract_schedules(text)
    assert len(scheds) >= 1


def test_get_sync_lock() -> None:
    lock1 = canvas_server.get_sync_lock(101)
    lock2 = canvas_server.get_sync_lock(101)
    lock3 = canvas_server.get_sync_lock(102)
    assert lock1 is lock2
    assert lock1 is not lock3


def test_get_download_semaphore() -> None:
    sem = canvas_server.get_download_semaphore()
    assert isinstance(sem, asyncio.Semaphore)
