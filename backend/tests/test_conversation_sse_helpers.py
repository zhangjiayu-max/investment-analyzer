"""对话 SSE 辅助函数测试。"""


def test_sse_comment_formats_keepalive_comment():
    from routers.conversation.conversations import _sse_comment

    assert _sse_comment("ping") == ": ping\n\n"
