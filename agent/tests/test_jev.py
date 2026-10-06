from agent.providers.base import ModelMessage
from agent.providers.jev import _state_from_messages


def test_jev_state_contains_trusted_product_context_and_user_state():
    state = _state_from_messages(
        (
            ModelMessage(
                role="user",
                content=(
                    '<untrusted-feedback>{"feedback_type":"bug",'
                    '"description":"无法识别三级标题",'
                    '"markdown_content":"### 7.1 标题"}</untrusted-feedback>'
                ),
            ),
        )
    )

    assert state["product_context"]["name"] == "MDToWord"
    assert state["product_context"]["purpose"]
    assert state["user_state"] == {
        "feedback_type": "bug",
        "description": "无法识别三级标题",
        "markdown_content": "### 7.1 标题",
    }
    assert "facts" not in state
