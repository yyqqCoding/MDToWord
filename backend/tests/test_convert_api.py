from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.pandoc_runner import ConversionError


client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "engine": "pandoc",
    }


def test_convert_rejects_empty_markdown():
    response = client.post(
        "/convert",
        json={
            "title": "empty",
            "markdown": "   ",
            "options": {
                "filename": "empty.docx",
            },
        },
    )

    assert response.status_code == 422


def test_convert_returns_docx_file():
    fake_docx = b"PK fake docx bytes"

    with patch("app.main.convert_markdown_to_docx", return_value=fake_docx):
        response = client.post(
            "/convert",
            json={
                "title": "example",
                "markdown": "质能方程是 $E = mc^2$。",
                "options": {
                    "filename": "example.docx",
                },
            },
        )

    assert response.status_code == 200
    assert response.content == fake_docx
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert "example.docx" in response.headers["content-disposition"]


def test_convert_returns_structured_error_on_conversion_failure():
    with patch(
        "app.main.convert_markdown_to_docx",
        side_effect=ConversionError("Pandoc failed.", ["line 3: unexpected token"]),
    ):
        response = client.post(
            "/convert",
            json={
                "title": "bad",
                "markdown": "$$\\bad$$",
                "options": {
                    "filename": "bad.docx",
                },
            },
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": "conversion_failed",
        "message": "Pandoc failed.",
        "details": ["line 3: unexpected token"],
    }


def test_convert_api_allows_extension_origin_preflight():
    response = client.options(
        "/convert",
        headers={
            "Origin": "chrome-extension://example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
