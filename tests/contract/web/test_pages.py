from pathlib import Path

from fastapi.testclient import TestClient

from pptlib.config import load_settings
from pptlib.web.app import create_app


def test_setup_page_has_local_navigation_and_doctor_action(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    client = TestClient(create_app(settings))

    response = client.get("/")

    assert response.status_code == 200
    assert "PPT 页库" in response.text
    assert 'href="/static/app.css"' in response.text
    assert 'src="/static/app.js"' in response.text
    assert 'data-doctor-url="/api/v1/doctor"' in response.text
    assert 'id="import-form"' in response.text
    assert 'data-import-url="/api/v1/imports"' in response.text
    assert 'value="./sources"' in response.text
    assert 'id="upload-form"' in response.text
    assert 'data-upload-url="/api/v1/imports/files"' in response.text
    assert 'id="source-files"' in response.text
    assert 'data-max-file-bytes="3221225472"' in response.text
    assert "单个文件上限 3 GiB" in response.text
    assert "setup-page" in response.text
    assert "setup-panel" in response.text
    assert "cdn" not in response.text.lower()
