"""Ninja's agent loop.

user prompt -> LLM -> tool call -> result -> LLM -> ... -> reply

The model can now answer with "run this tool" instead of with prose. We run it,
hand the result back, and ask again — until it stops asking, or until the
guardrail stops us.
"""

import time

import anthropic
from dotenv import load_dotenv

from ninja import consolidation, episodic, personas, router, rules, semantic, tools
from ninja.personas import Persona
from ninja.trace import Trace

# Reads .env into the environment. .env is gitignored; the key never
# touches the repo.
load_dotenv()

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
    system = persona.instructions
    learned = rules.rules_for(persona.name)
    if learned:
        system += (
            "\n\nRules you have learned about how this person wants you to behave:\n" + learned
        )
    if not retrieve:
        return system
    return system + "\n\nWhat you know about this person:\n" + semantic.as_context(hits)


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
        trace.model(persona.model, response, ms_since(started), persona.name)
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
            # Only the ways a model-supplied name or argument can be wrong: a
            # refusal or a bad path (ValueError), a missing required argument
            # (KeyError), a path that is not there or not readable (OSError).
            # Those are the model's mistakes and belong back in the transcript
            # for it to explain. Anything else — a wrong signature, a bug in a
            # tool — is ours, and catching it here would file it as an ordinary
            # tool error that looks exactly like a legitimate refusal.
            try:
                output = tools.run(block.name, block.input, persona.tools, persona=persona.name)
                failed = False
            except (ValueError, KeyError, OSError) as exc:
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

    # Every other exit appends the assistant message before returning it. This
    # one used to return a reply the user saw and the transcript never held,
    # leaving the last message a batch of tool results with no assistant turn
    # after it — so the next question is asked of a conversation that stops
    # mid-exchange, and working memory disagrees with what was saved.
    stopped = f"[stopped: hit the {MAX_STEPS}-step guardrail]"
    messages.append({"role": "assistant", "content": stopped})
    return stopped


def switch(command: str, current: Persona) -> Persona:
    """Handle a /persona line. Returns the persona for the next turn.

    Working memory is untouched: the persona lives in the system prompt, which
    is rebuilt every turn, so swapping hats costs nothing in the transcript.
    """
    _, _, name = command.partition(" ")
    name = name.strip()
    if not name:
        # One unreadable PERSONA.md must not end the session. Listing the cast
        # reads every file, and an uncaught load error here would take the REPL
        # down with the transcript still in it.
        try:
            cast = personas.all()
        except ValueError as exc:
            print(f"  {exc}")
            return current
        print("  personas:")
        for p in cast:
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
    cast = personas.all()
    persona = personas.load(episodic.current_thread(personas.DEFAULT))
    forced = False

    print(f"ninja | {persona.name} | model={persona.model} | ctrl-d to quit")
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
            # An override: the next turn runs where you put it, without the
            # router second-guessing the instruction. A bare /persona only
            # lists the cast, so it is not an instruction to go anywhere.
            forced = user_input.strip() != "/persona"
            continue

        turn = Trace(user_input)
        if forced:
            # An override is still a decision, and the trace should say which
            # one. Skipping the router silently leaves no record of why a turn
            # went where it did — the same reason a gate skip is recorded
            # rather than simply not happening.
            turn.route(persona.name, persona.name, "explicit /persona", router.MODEL, None, 0)
        else:
            persona = personas.load(
                router.route(client, user_input, persona.name, cast, turn)
            )
        forced = False

        # The transcript comes from the thread, not from a list carried across
        # switches. Returning to a conversation finds it as it was.
        messages = [*episodic.recall(persona.name), {"role": "user", "content": user_input}]
        reply = run_turn(client, messages, turn, persona,
                         build_system(user_input, turn, persona))
        consolidation.run_if_due(client, turn)
        trace_id = turn.finish(reply)

        episodic.save(session, "user", user_input, trace_id, persona.name)
        episodic.save(session, "assistant", reply, trace_id, persona.name)

        print(f"\n{persona.name}> {reply}")
        print(
            f"[trace {trace_id} · {len(messages)} messages · "
            f"{turn.input_tokens} in / {turn.output_tokens} out · "
            f"${turn.cost:.5f}]\n"
        )
