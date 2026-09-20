from ninja import semantic, tools


def facts(*items):
    for item in items:
        semantic.remember(item)


def test_gate_skips_when_nothing_is_remembered():
    retrieve, why, hits = semantic.gate("what am I building?")
    assert retrieve is False
    assert why == "nothing remembered yet"
    assert hits == []


def test_gate_skips_a_question_no_fact_answers():
    facts("Sol is building an agent harness called Ninja")
    retrieve, why, _ = semantic.gate("what is 2 + 2?")
    assert retrieve is False
    assert why == "no fact matched"


def test_gate_retrieves_a_relevant_fact():
    facts("Sol is building an agent harness called Ninja",
          "The deploy pipeline keeps failing on Tuesdays")
    retrieve, _, hits = semantic.gate("what am I building?")
    assert retrieve is True
    assert "Ninja" in hits[0][1]


def test_stopwords_do_not_cause_a_match():
    # "tell me about the X" shares "the" with almost any fact. Matching on it
    # produces a confident retrieval built on nothing.
    facts("The deploy pipeline keeps failing on Tuesdays")
    assert semantic.gate("tell me about the weather")[0] is False


def test_query_survives_punctuation():
    # Apostrophes and dashes are FTS5 syntax, not text. Raw input would raise.
    facts("Sol worked at Sam's Club")
    for text in ["what's Sol's job?", "tell me -- anything", 'a "quoted" thing', "!!!"]:
        semantic.gate(text)


def test_remember_tool_stores_a_fact():
    out = tools.run("remember", {"fact": "Sol prefers concise explanations"},
                    [s["name"] for s in tools.SCHEMAS])
    assert "remembered" in out
    assert semantic.count() == 1
    assert "concise" in semantic.all_facts()[0]["content"]


def test_retrieved_facts_reach_the_system_prompt():
    from ninja import agent, trace
    facts("Sol is building an agent harness called Ninja")
    turn = trace.Trace("x")
    prompt = agent.build_system("what am I building?", turn)
    assert "Ninja" in prompt
    assert turn.events[0]["type"] == "gate"
    assert turn.events[0]["retrieve"] is True


def test_a_skip_is_recorded_as_a_decision():
    from ninja import agent, trace
    facts("Sol is building an agent harness called Ninja")
    turn = trace.Trace("x")
    prompt = agent.build_system("what is 2 + 2?", turn)
    assert "Ninja" not in prompt
    # A skip must be visible in the trace — an absence would be invisible.
    assert turn.events[0]["retrieve"] is False
    assert turn.events[0]["why"]
