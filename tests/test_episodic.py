from ninja import episodic


def test_nothing_recalled_when_empty():
    assert episodic.recall("assistant") == []


def test_turns_survive_a_restart():
    session = episodic.new_session()
    episodic.save(session, "user", "my name is Sol", None, "assistant")
    episodic.save(session, "assistant", "Noted.", None, "assistant")

    # A "restart" is just calling recall() again with a fresh list.
    recalled = episodic.recall("assistant")
    assert [m["role"] for m in recalled] == ["user", "assistant"]
    assert recalled[0]["content"] == "my name is Sol"


def test_recall_never_starts_with_assistant():
    # The window can open mid-exchange, and an assistant message cannot lead
    # the messages array — the API rejects it.
    session = episodic.new_session()
    for i in range(6):
        episodic.save(session, "user", f"q{i}", None, "assistant")
        episodic.save(session, "assistant", f"a{i}", None, "assistant")
    assert episodic.recall("assistant", limit=5)[0]["role"] == "user"
    assert episodic.recall("assistant", limit=4)[0]["role"] == "user"


def test_recall_is_oldest_first():
    session = episodic.new_session()
    for i in range(3):
        episodic.save(session, "user", f"message {i}", None, "assistant")
    assert [m["content"] for m in episodic.recall("assistant")] == [
        "message 0", "message 1", "message 2"
    ]


def test_stats_counts_sessions():
    for session in (episodic.new_session(), episodic.new_session()):
        episodic.save(session, "user", "hi", None, "assistant")
    stats = episodic.stats()
    assert stats["messages"] == 2
    assert stats["sessions"] == 2
