"""Phase E1a: the judge — a binary verdict against a named criterion.

A judge is not a conversation. It never sees the expected answer, only the
criterion and the output; a wrong verdict is explainable from its own trace
row the same way a wrong match is explainable in ninja/skills.py.
"""

from dataclasses import dataclass

from ninja.agent import run_turn
from ninja.personas import Persona
from ninja.trace import Trace

# Not a personas/judge/PERSONA.md. personas.all() has no "internal only"
# concept — it feeds the router, /persona's listing, and the delegation
# subset rule — so a real file here would make "judge" a routable
# conversation thread a real message could land in by accident, where its
# strict PASS/FAIL-only instructions would be a useless reply to a person.
# A Python constant gets the same review a PERSONA.md would and none of the
# exposure.
PERSONA = Persona(
    name="judge",
    description="Internal: scores one output against one criterion. Never "
                 "routed to, never delegated to — not a conversational persona.",
    instructions=(
        "You are a strict binary judge. You will be given a criterion and an "
        "output to check it against. Reply with exactly one word: PASS if the "
        "output satisfies the criterion, FAIL if it does not. Nothing else — "
        "no explanation, no punctuation."
    ),
    tools=(),
    model="claude-haiku-4-5",
)


@dataclass(frozen=True)
class Verdict:
    passed: bool
    criterion: str
    trace_id: int


class JudgeParseError(ValueError):
    """The judge's reply didn't parse as PASS/FAIL. `trace_id` points at the
    full prompt/model/reply — `ninja trace <id>` to see why."""

    def __init__(self, reply: str, trace_id: int):
        super().__init__(
            f"could not parse PASS/FAIL from the judge's reply — see trace "
            f"{trace_id}: {reply!r}"
        )
        self.trace_id = trace_id


def judge(client, criterion: str, output: str) -> Verdict:
    """Score `output` against `criterion`. Always finishes its own trace row
    before returning or raising — a failed parse is diagnosable, not silent.
    """
    prompt = f"Criterion: {criterion}\n\nOutput to judge:\n{output}"
    trace = Trace(prompt)
    reply = run_turn(client, [{"role": "user", "content": prompt}], trace, PERSONA)
    cleaned = reply.strip().strip(".").upper()
    if cleaned not in ("PASS", "FAIL"):
        trace_id = trace.finish(reply)
        raise JudgeParseError(reply, trace_id)
    passed = cleaned == "PASS"
    trace.judge(criterion, passed)
    trace_id = trace.finish(reply)
    return Verdict(passed=passed, criterion=criterion, trace_id=trace_id)
