"""main.py 退出码：全文失败必须让调用方看见。"""

import pytest

import main as etl_main


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """把目录与数据库指向临时目录，避免触碰仓库内产物。"""
    for name in ("RAW_XML_DIR", "OUTPUT_DIR", "LOG_DIR"):
        directory = tmp_path / name.lower()
        monkeypatch.setattr(etl_main, name, directory)
    monkeypatch.setattr(etl_main, "DB_PATH", tmp_path / "etl.db")
    monkeypatch.setattr(etl_main, "init_db", lambda _path: None)
    monkeypatch.setattr(etl_main.logger, "info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(etl_main.logger, "error", lambda *_args, **_kwargs: None)
    return tmp_path


def _run(monkeypatch, step: str) -> int:
    monkeypatch.setattr("sys.argv", ["main.py", "--step", step])
    with pytest.raises(SystemExit) as exc:  # noqa: PT011 - 需要具体退出码
        etl_main.main()
    return int(exc.value.code)


def test_pdf_failures_exit_non_zero(isolated, monkeypatch):
    monkeypatch.setattr(etl_main, "step_pdf", lambda: 2)

    assert _run(monkeypatch, "pdf") == 1


def test_pdf_success_exits_zero(isolated, monkeypatch):
    monkeypatch.setattr(etl_main, "step_pdf", lambda: 0)
    monkeypatch.setattr("sys.argv", ["main.py", "--step", "pdf"])

    etl_main.main()  # 不抛 SystemExit


def test_pdf_retry_failures_exit_non_zero(isolated, monkeypatch):
    monkeypatch.setattr(etl_main, "step_pdf_retry", lambda: 3)

    assert _run(monkeypatch, "pdf-retry") == 1
