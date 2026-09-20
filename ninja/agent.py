"""Ninja's agent loop.

user prompt -> LLM -> tool call -> result -> LLM -> ... -> reply

The model can now answer with "run this tool" instead of with prose. We run it,
hand the result back, and ask again — until it stops asking, or until the
guardrail stops us.
"""

import time

import anthropic
from dotenv import load_dotenv

from ninja import episodic, semantic, tools
from ninja.trace import Trace

# Reads .env into the environment. .env is gitignored; the key never
# touches the repo.
load_dotenv()

# Cheapest current model, per the cost rule. Swap to "claude-opus-5" when you
# want the good one.
MODEL = "claude-haiku-4-5"

SYSTEM = (
    "You are a helpful assistant with read access to this project's files. "
    "Keep answers short."
)

# The guardrail. The real exit is the model deciding it is done; this is the
# backstop for when it gets stuck asking for tools in a cycle.
MAX_STEPS = 6


def ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def build_system(user_input: str, trace: Trace) -> str:
    """Assemble the system prompt for this turn.

    This is the retrieval gate: most turns get the plain prompt. Retrieving on
    every turn would spend tokens and push irrelevant facts at the model, which
    makes answers worse, not just slower.
    """
    retrieve, why, hits = semantic.gate(user_input)
    trace.gate(retrieve, why, len(hits))
    if not retrieve:
        return SYSTEM
    return SYSTEM + "\n\nWhat you know about this person:\n" + semantic.as_context(hits)


def run_turn(
    client: anthropic.Anthropic, messages: list, trace: Trace, system: str | None = None
) -> str:
    """Loop until the model stops asking for tools. Returns its final text."""
    for _ in range(MAX_STEPS):
        started = time.perf_counter()
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system or SYSTEM,
            tools=tools.SCHEMAS,
            messages=messages,
        )
        trace.model(MODEL, response, ms_since(started))
        # Append the blocks, not the text — the tool_use blocks have to go back
        # so the model can see its own request alongside our result.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"  ↳ {block.name}({block.input})")
            started = time.perf_counter()
            try:
                output, failed = tools.run(block.name, block.input), False
            except Exception as exc:
                output, failed = str(exc), True
            trace.tool(block.name, block.input, not failed, output, ms_since(started))
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                    "is_error": failed,
                }
            )

        # All results go back in ONE user message, even when there are several.
        # Splitting them teaches the model to stop asking for parallel calls.
        messages.append({"role": "user", "content": results})

    return f"[stopped: hit the {MAX_STEPS}-step guardrail]"


def main() -> None:
    client = anthropic.Anthropic()
    session = episodic.new_session()

    # Working memory no longer starts empty: layer 4 puts recent turns back
    # before the first round. This is the pattern every memory layer follows —
    # something decides what goes into the array before the loop runs.
    messages = episodic.recall()

    print(f"ninja | model={MODEL} | ctrl-d to quit")
    if messages:
        print(f"remembering {len(messages)} earlier messages")
    print()

    while True:
        try:
            user_input = input("you> ").strip()
        except EOFError:
            print()
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
        trace = Trace(user_input)
        reply = run_turn(client, messages, trace, build_system(user_input, trace))
        trace_id = trace.finish(reply)

        episodic.save(session, "user", user_input, trace_id)
        episodic.save(session, "assistant", reply, trace_id)

        print(f"\nagent> {reply}")
        print(
            f"[trace {trace_id} · {len(messages)} messages · "
            f"{trace.input_tokens} in / {trace.output_tokens} out · "
            f"${trace.cost:.5f}]\n"
        )
