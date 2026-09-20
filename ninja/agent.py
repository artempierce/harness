"""Ninja's agent loop.

user prompt -> LLM -> tool call -> result -> LLM -> ... -> reply

The model can now answer with "run this tool" instead of with prose. We run it,
hand the result back, and ask again — until it stops asking, or until the
guardrail stops us.
"""

import time

import anthropic
from dotenv import load_dotenv

from ninja import episodic, personas, semantic, tools
from ninja.personas import Persona
from ninja.trace import Trace

# Reads .env into the environment. .env is gitignored; the key never
# touches the repo.
load_dotenv()

# Kept for the guardrails panel and the CLI banner. The live values now come
# from whichever persona the turn is running as.
MODEL = personas.DEFAULT_MODEL
SYSTEM = personas.DEFAULT_INSTRUCTIONS

# The guardrail. The real exit is the model deciding it is done; this is the
# backstop for when it gets stuck asking for tools in a cycle.
MAX_STEPS = 6


def ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def build_system(user_input: str, trace: Trace, persona: Persona) -> str:
    """Assemble the system prompt for this turn.

    The persona supplies the instructions; the retrieval gate decides whether
    facts are worth their tokens on top of them.
    """
    retrieve, why, hits = semantic.gate(user_input)
    trace.gate(retrieve, why, len(hits))
    if not retrieve:
        return persona.instructions
    return (
        persona.instructions + "\n\nWhat you know about this person:\n" + semantic.as_context(hits)
    )


def run_turn(
    client: anthropic.Anthropic,
    messages: list,
    trace: Trace,
    persona: Persona,
    system: str | None = None,
) -> str:
    """Loop until the model stops asking for tools. Returns its final text."""
    for _ in range(MAX_STEPS):
        started = time.perf_counter()
        response = client.messages.create(
            model=persona.model,
            max_tokens=2048,
            system=system or persona.instructions,
            tools=persona.schemas(),
            messages=messages,
        )
        trace.model(persona.model, response, ms_since(started))
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
                output, failed = tools.run(block.name, block.input, persona.tools), False
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


def switch(command: str, current: Persona) -> Persona:
    """Handle a /persona line. Returns the persona for the next turn.

    Working memory is untouched: the persona lives in the system prompt, which
    is rebuilt every turn, so swapping hats costs nothing in the transcript.
    """
    _, _, name = command.partition(" ")
    name = name.strip()
    if not name:
        print("  personas:")
        for p in personas.all():
            mark = "*" if p.name == current.name else " "
            print(f"   {mark} {p.name:16} {len(p.tools)} tools · {p.description}")
        return current
    try:
        chosen = personas.load(name)
    except ValueError as exc:
        print(f"  {exc}")
        return current
    print(f"  ↳ persona: {chosen.name} ({len(chosen.tools)} tools)")
    return chosen


def main() -> None:
    client = anthropic.Anthropic()
    session = episodic.new_session()
    messages = episodic.recall()
    persona = personas.load(personas.DEFAULT)

    print(f"ninja | {persona.name} | model={persona.model} | ctrl-d to quit")
    if messages:
        print(f"remembering {len(messages)} earlier messages")
    print()

    while True:
        try:
            user_input = input(f"{persona.name}> ").strip()
        except EOFError:
            print()
            break
        if not user_input:
            continue
        if user_input.startswith("/persona"):
            persona = switch(user_input, persona)
            continue

        messages.append({"role": "user", "content": user_input})
        trace = Trace(user_input)
        reply = run_turn(client, messages, trace, persona,
                         build_system(user_input, trace, persona))
        trace_id = trace.finish(reply)

        episodic.save(session, "user", user_input, trace_id)
        episodic.save(session, "assistant", reply, trace_id)

        print(f"\nagent> {reply}")
        print(
            f"[trace {trace_id} · {len(messages)} messages · "
            f"{trace.input_tokens} in / {trace.output_tokens} out · "
            f"${trace.cost:.5f}]\n"
        )
