"""Ninja's agent loop.

user prompt -> LLM -> tool call -> result -> LLM -> ... -> reply

The model can now answer with "run this tool" instead of with prose. We run it,
hand the result back, and ask again — until it stops asking, or until the
guardrail stops us.
"""

import anthropic
from dotenv import load_dotenv

from ninja import tools

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


def run_turn(client: anthropic.Anthropic, messages: list) -> str:
    """Loop until the model stops asking for tools. Returns its final text."""
    for _ in range(MAX_STEPS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM,
            tools=tools.SCHEMAS,
            messages=messages,
        )
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
            try:
                output, failed = tools.run(block.name, block.input), False
            except Exception as exc:
                output, failed = str(exc), True
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

    # Still the working memory from layer 1 — it just fills up faster now.
    messages = []

    print(f"ninja | model={MODEL} | ctrl-d to quit\n")

    while True:
        try:
            user_input = input("you> ").strip()
        except EOFError:
            print()
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
        reply = run_turn(client, messages)

        print(f"\nagent> {reply}")
        print(f"[working memory: {len(messages)} messages]\n")
