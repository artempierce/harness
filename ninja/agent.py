"""Ninja's agent loop.

user prompt -> LLM -> tool call -> result -> LLM -> ... -> reply

The model can now answer with "run this tool" instead of with prose. We run it,
hand the result back, and ask again — until it stops asking, or until the
guardrail stops us.
"""

import dataclasses
import sqlite3
import sys
import time

import anthropic
from dotenv import load_dotenv

from ninja import consolidation, episodic, mirror, personas, router, rules, semantic, skills, tools
from ninja.personas import Persona
from ninja.trace import PRICING, Trace

# Reads .env into the environment. .env is gitignored; the key never
# touches the repo.
load_dotenv()

# The guardrail. The real exit is the model deciding it is done; this is the
# backstop for when it gets stuck asking for tools in a cycle.
MAX_STEPS = 6

# Stop reasons where the text that came back is not the whole answer. A list,
# not "anything but end_turn": pause_turn means resume, not failure.
CUT_SHORT = ("max_tokens", "refusal", "model_context_window_exceeded")

# MAX_STEPS bounds one loop, not a tree of them: three levels of six steps is
# up to 216 calls. These bound the tree. MAX_TURN_COST_USD is a guess — the
# arithmetic says a busy delegating turn is about $0.11 — and wants replacing
# with a measured number.
MAX_DEPTH = 2
MAX_DELEGATIONS_PER_TURN = 3
MAX_TURN_COST_USD = 0.25

# A child reads and reasons. Writes wait for the gate in layers 9 and 13-15, and
# leaving them off also closes a cross-persona path: a hijacked orchestrator
# cannot ask the coach to add_rule.
WRITE_TOOLS = {"remember", "add_rule", "propose_skill"}


def ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _reduced(child: Persona, depth: int) -> Persona:
    """What a persona may actually call when it runs at `depth`.

    The frozen copy is what the child loop runs with, so the schema filter and
    the allowlist in tools.run both enforce it — a child cannot call a tool it
    was never given, even by name.
    """
    drop = WRITE_TOOLS | ({"delegate"} if depth >= MAX_DEPTH else set())
    return dataclasses.replace(child, tools=tuple(t for t in child.tools if t not in drop))


def _excess(child: Persona, depth: int, parent: Persona) -> list[str]:
    """Tools the child could call that the parent could not. Empty means it fits."""
    return sorted(set(_reduced(child, depth).tools) - set(parent.tools))


def _refusal(child: Persona, depth: int, parent: Persona) -> str | None:
    """Why `parent` may not delegate to `child` at `depth`, or None. The one
    rule, so the list the model is offered and the refusal it would get agree."""
    if child.name == parent.name:
        return f"{parent.name} cannot delegate to itself."
    # An unpriced model adds $0 to the trace, so the budget would never trip.
    # A lone persona is the trace's business to flag; fan-out is where an
    # unbounded spend becomes multiplicative, so this is where it fails closed.
    for who in (parent, child):
        if who.model not in PRICING:
            return (
                f"{who.name} runs on {who.model}, which has no price, so its spend "
                "cannot be held to the turn budget. Price it in trace.PRICING first."
            )
    # Refuse rather than trim: quietly dropping a tool changes what the persona
    # does without anyone having decided that.
    extra = _excess(child, depth, parent)
    if extra:
        return (
            f"{child.name} holds tools that {parent.name} does not: {', '.join(extra)}. "
            "A delegate cannot have more privilege than the persona that asks."
        )
    return None


def _instructions(persona: Persona, depth: int) -> str:
    """The persona's instructions, its learned rules, and who it can hand work to."""
    system = persona.instructions
    learned = rules.rules_for(persona.name)
    if learned:
        system += (
            "\n\nRules you have learned about how this person wants you to behave:\n" + learned
        )
    if "delegate" in persona.tools and depth < MAX_DEPTH:
        # Only the ones the subset rule would accept: offering a target that is
        # then refused teaches the model nothing but a wasted step.
        targets = [
            f"- {p.name}: {p.description}"
            for p in personas.all()
            if not _refusal(p, depth + 1, persona)
        ]
        if targets:
            system += "\n\nYou can delegate a self-contained job to:\n" + "\n".join(targets)
    return system


def build_system(user_input: str, trace: Trace, persona: Persona) -> str:
    """Assemble the system prompt for this turn.

    The persona supplies the instructions; the retrieval gate decides whether
    facts are worth their tokens on top of them; matched skills go last.
    """
    try:
        retrieve, why, hits = semantic.gate(user_input)
    except sqlite3.Error as exc:
        # Facts are optional context: a locked or corrupt database costs the
        # turn its memory, not the turn. Recorded as its own reason so an error
        # is never mistaken for a real "no fact matched".
        retrieve, why, hits = False, f"retrieval error: {exc}", []
        # Loud, every time: if this is a schema fault rather than a lock, the
        # turn "works" with memory silently off, and a trace line nobody reads
        # is how that goes unnoticed for weeks.
        print(f"  ! retrieval failed, continuing without facts: {exc}", file=sys.stderr)
    trace.gate(retrieve, why, len(hits))
    system = _instructions(persona, 0)
    if retrieve:
        system += "\n\nWhat you know about this person:\n" + semantic.as_context(hits)
    matched = skills.match(user_input, skills.load_all())
    if matched:
        trace.skills([s.name for s in matched])
        system += "\n\n" + skills.format_section(matched)
    return system


def run_turn(
    client: anthropic.Anthropic,
    messages: list,
    trace: Trace,
    persona: Persona,
    system: str | None = None,
    depth: int = 0,
) -> str:
    """Loop until the model stops asking for tools. Returns its final text."""
    def spawn(name: str, task: str) -> str:
        return _delegate(client, trace, persona, depth, name, task)

    for _ in range(MAX_STEPS):
        # Before the call, at every depth: the cost is a property of the turn,
        # so a child that spends the budget ends its parent's next call too.
        if trace.cost >= MAX_TURN_COST_USD:
            return _stop(messages, f"turn budget of ${MAX_TURN_COST_USD:.2f} reached")
        started = time.perf_counter()
        response = client.messages.create(
            model=persona.model,
            max_tokens=2048,
            system=system or persona.instructions,
            tools=persona.schemas(),
            messages=messages,
        )
        trace.model(persona.model, response, ms_since(started), persona.name, depth)
        # Append the blocks, not the text — the tool_use blocks have to go back
        # so the model can see its own request alongside our result.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            reply = "".join(b.text for b in response.content if b.type == "text")
            # A reply cut off at the token cap, refused or paused reads as a
            # finished answer if only the text comes back — and it is saved and
            # replayed as one. Say why it stopped in the text itself.
            if response.stop_reason in CUT_SHORT:
                reply = f"{reply}\n[stopped early: {response.stop_reason}]".strip()
            # An empty reply is saved as the assistant's message and replayed on
            # every later turn in the thread. The API rejects an empty text
            # block, so the thread would fail until the message scrolled out of
            # the recall window — and it is on disk, so a restart does not help.
            return reply or f"[no text in the reply: stop_reason={response.stop_reason}]"

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"{'  ' * depth}  ↳ {block.name}({block.input})")
            started = time.perf_counter()
            # Only the ways a model-supplied name or argument can be wrong: a
            # refusal or a bad path (ValueError), a missing required argument
            # (KeyError), a path that is not there or not readable (OSError).
            # Those are the model's mistakes and belong back in the transcript
            # for it to explain. Anything else — a wrong signature, a bug in a
            # tool — is ours, and catching it here would file it as an ordinary
            # tool error that looks exactly like a legitimate refusal.
            try:
                output = tools.run(
                    block.name, block.input, persona.tools, persona=persona.name, spawn=spawn
                )
                failed = False
            except (ValueError, KeyError, OSError) as exc:
                output, failed = str(exc), True
            trace.tool(block.name, block.input, not failed, output, ms_since(started), depth)
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
    return _stop(messages, f"hit the {MAX_STEPS}-step guardrail")


def _stop(messages: list, why: str) -> str:
    stopped = f"[stopped: {why}]"
    messages.append({"role": "assistant", "content": stopped})
    return stopped


def _delegate(client, trace: Trace, parent: Persona, depth: int, name: str, task: str) -> str:
    """Run `name` on `task` as a child loop. Every refusal is a ValueError.

    That is what tools.run's caller turns into a tool error the orchestrator can
    read and recover from; anything else would fail the whole turn.
    """
    # Before anything counts: a child that started past the ceiling would only
    # return its "[stopped" line, which the trace would record as a success.
    if trace.cost >= MAX_TURN_COST_USD:
        raise ValueError(f"the turn budget of ${MAX_TURN_COST_USD:.2f} is already spent.")
    child_depth = depth + 1
    if child_depth > MAX_DEPTH:
        raise ValueError(f"delegation is limited to {MAX_DEPTH} levels deep.")
    if trace.delegations >= MAX_DELEGATIONS_PER_TURN:
        raise ValueError(f"at most {MAX_DELEGATIONS_PER_TURN} delegations per turn.")
    # Unknown and path-like names are refused inside load(), before any read.
    child = _reduced(personas.load(name), child_depth)
    refusal = _refusal(child, child_depth, parent)
    if refusal:
        raise ValueError(refusal)
    trace.delegations += 1
    started = time.perf_counter()
    ok = False
    try:
        # A fresh list holding only the brief. The parent's messages are being
        # mutated by its own loop and none of it is the child's business. No
        # retrieved facts either: the task is the whole interface.
        reply = run_turn(
            client,
            [{"role": "user", "content": task}],
            trace,
            child,
            _instructions(child, child_depth),
            child_depth,
        )
        ok = True
        return reply
    finally:
        # An API error in the child still propagates and fails the turn, as a
        # parent's would; this only makes sure the attempt is on the record.
        trace.delegate(name, child_depth, task, ok, ms_since(started))


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


def review_skill(command: str) -> None:
    """Handle /approve-skill and /reject-skill. Both list what's pending when
    given no name — there is one shared queue, so either command is a fair
    way to ask what's in it. Like /persona, this never becomes part of the
    conversation.
    """
    verb, _, name = command.partition(" ")
    if verb not in ("/approve-skill", "/reject-skill"):
        print(f"  unknown command: {verb}")
        return
    name = name.strip()
    if not name:
        pending = skills.load_all(skills.PENDING_DIR)
        if not pending:
            print("  no pending skill proposals")
            return
        print("  pending skill proposals:")
        for s in pending:
            print(f"   - {s.name}: {s.description}")
        return
    if verb == "/approve-skill":
        existed = (skills.DIR / name / "SKILL.md").exists()
        try:
            skills.approve(name)
        except (ValueError, OSError) as exc:
            print(f"  {exc}")
            return
        suffix = " (replaced an existing skill)" if existed else ""
        print(f"  ↳ skill approved: {name}{suffix}")
        return
    try:
        skills.reject(name)
    except (ValueError, OSError) as exc:
        print(f"  {exc}")
        return
    print(f"  ↳ skill rejected: {name}")


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
        if user_input.startswith("/approve-skill") or user_input.startswith("/reject-skill"):
            review_skill(user_input)
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
        try:
            reply = run_turn(client, messages, turn, persona,
                             build_system(user_input, turn, persona))
        except Exception as exc:
            # Record the turn before moving on, as the server does: the router
            # call and every model call before the failure are already paid
            # for, and a failed turn that leaves no trace looks free. Nothing
            # is saved to the thread — the messages were a local list.
            turn.finish(f"[failed: {exc}]")
            print(f"  the turn failed: {exc}\n")
            continue
        consolidation.run_if_due(client, turn)
        trace_id = turn.finish(reply)

        # One transaction: two separate saves can be torn apart, leaving the
        # thread reading user, user, assistant, which the API rejects.
        episodic.save_exchange(session, user_input, reply, trace_id, persona.name)
        mirror.write()

        print(f"\n{persona.name}> {reply}")
        print(
            f"[trace {trace_id} · {len(messages)} messages · "
            f"{turn.input_tokens} in / {turn.output_tokens} out · "
            f"${turn.cost:.5f}]\n"
        )
