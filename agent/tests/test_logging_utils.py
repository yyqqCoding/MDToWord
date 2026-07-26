from agent.logging_utils import sanitize


def test_sensitive_keys_dropped():
    data = {
        "contact": "user@example.com",
        "api_key": "sk-x",
        "apikey": "sk-y",
        "authorization": "Bearer x",
        "claim_token": "uuid",
        "github_token": "ghp_x",
        "supabase_secret": "x",
        "password": "x",
    }
    assert sanitize(data) == {}


def test_token_usage_counts_survive():
    data = {"input_tokens": 120, "output_tokens": 30, "model": "m"}
    assert sanitize(data) == data


def test_sanitize_recurses_into_nested_structures():
    data = {"stage_timings": {"classify": {"input_tokens": 10, "api_key": "sk"}},
            "rounds": [{"output_tokens": 5, "claim_token": "t"}]}
    assert sanitize(data) == {
        "stage_timings": {"classify": {"input_tokens": 10}},
        "rounds": [{"output_tokens": 5}],
    }
