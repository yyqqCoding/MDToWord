from agent.providers.base import ModelMessage
from agent.providers.jev import _state_from_messages


def test_jev_state_adds_deterministic_markdown_facts():
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

    assert state["facts"] == {
        "source_product": "MDToWord",
        "description_present": True,
        "markdown_present": True,
        "heading_syntax_present": True,
        "heading_levels": [3],
    }
