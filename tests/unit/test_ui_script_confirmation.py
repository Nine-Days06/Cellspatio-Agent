"""脚本确认 UI 纯函数测试（不依赖 Streamlit 运行时）"""


def test_format_script_confirmation_message():
    from src.ui.app import _format_script_confirmation

    msg = _format_script_confirmation(
        {"script": "plot(1)", "message": "已生成 R 脚本，请审阅并确认执行"}
    )
    assert "已生成 R 脚本" in msg
    assert "plot(1)" in msg