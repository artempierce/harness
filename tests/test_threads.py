"""Layer 8a: each persona keeps its own transcript.

The point of a thread is that interrupting one conversation does not disturb
another. These are the tests for "does not disturb".
"""

from ninja import episodic


def test_a_message_stays_in_its_own_thread():
    episodic.save("s1", "user", "how do I test a flaky API?", None, "interview-coach")
    episodic.save("s1", "user", "make a note about the call", None, "assistant")

    coach = [m["content"] for m in episodic.recall("interview-coach")]
    assistant = [m["content"] for m in episodic.recall("assistant")]

    assert coach == ["how do I test a flaky API?"]
    assert assistant == ["make a note about the call"]


def test_returning_to_a_thread_finds_it_as_it_was():
    # The interrupt story: talk to the coach, go away, come back.
    episodic.save("s1", "user", "first coaching message", None, "interview-coach")
    episodic.save("s1", "assistant", "a coaching reply", None, "interview-coach")
    episodic.save("s1", "user", "unrelated errand", None, "assistant")
    episodic.save("s1", "assistant", "errand done", None, "assistant")

    assert [m["content"] for m in episodic.recall("interview-coach")] == [
        "first coaching message",
        "a coaching reply",
    ]


def test_an_empty_thread_recalls_nothing():
    episodic.save("s1", "user", "hello", None, "assistant")
    assert episodic.recall("interview-coach") == []


def test_recall_still_refuses_to_start_on_an_assistant_message():
    # Pre-existing rule, per-thread now: an assistant message cannot lead the
    # messages array, so a window starting mid-exchange drops it.
    episodic.save("s1", "assistant", "dangling reply", None, "assistant")
    episodic.save("s1", "user", "a question", None, "assistant")
    assert [m["role"] for m in episodic.recall("assistant")] == ["user"]


def test_the_current_thread_is_the_one_the_last_message_used():
    # Derived, not remembered. A remembered name is the shape of the layer 7
    # bug where a finished turn wrote back a persona and undid a switch.
    episodic.save("s1", "user", "a", None, "assistant")
    episodic.save("s1", "user", "b", None, "interview-coach")
    assert episodic.current_thread("assistant") == "interview-coach"


def test_the_current_thread_falls_back_when_there_is_no_history():
    assert episodic.current_thread("assistant") == "assistant"


def test_stats_still_counts_sessions_not_threads():
    # session_id means one process run and the Episodic panel reports it.
    # The fixture must make the two counts differ, or swapping the column in
    # that query would pass this test.
    episodic.save("s1", "user", "a", None, "assistant")
    episodic.save("s1", "user", "b", None, "interview-coach")
    episodic.save("s1", "user", "c", None, "assistant")
    # one session, two threads
    assert episodic.stats()["sessions"] == 1


def test_history_exposes_the_thread():
    # The cockpit's Episodic panel reads this.
    episodic.save("s1", "user", "a", None, "interview-coach")
    assert episodic.history()[0]["thread"] == "interview-coach"


def test_a_torn_thread_recovers_instead_of_staying_broken():
    # Two turns interleaving on one thread leave user, user, assistant,
    # assistant. The Messages API refuses that, so before alternation was
    # enforced every later turn in the thread failed until the pair scrolled
    # out of the recall window — and it is on disk, so a restart did not help.
    for role, text in [
        ("user", "first question"),
        ("user", "second question"),
        ("assistant", "first answer"),
        ("assistant", "second answer"),
    ]:
        episodic.save("s1", role, text, None, "assistant")

    roles = [m["role"] for m in episodic.recall("assistant")]
    assert roles == ["user", "assistant"], f"recall returned a shape the API rejects: {roles}"


def test_both_halves_of_an_exchange_are_written_together():
    # One transaction, so a crash between them cannot tear the thread.
    episodic.save_exchange("s1", "what is 2+2?", "four", 7, "assistant")
    assert [(m["role"], m["content"]) for m in episodic.recall("assistant")] == [
        ("user", "what is 2+2?"),
        ("assistant", "four"),
    ]
