"""Layer 1: the bare agent run.

user prompt + system prompt + chat history -> LLM -> reply

Everything lives in one Python list that dies when the process does.
"""

import anthropic

# Cheapest current model, per the cost rule. Swap to "claude-opus-5" when you
# want the good one.
MODEL = "claude-haiku-4-5"

SYSTEM = "You are a helpful assistant. Keep answers short."


def main() -> None:
    client = anthropic.Anthropic()

    # This list IS the working memory. Nothing else persists.
    messages = []

    print(f"harness layer 1 | model={MODEL} | ctrl-d to quit\n")

    while True:
        try:
            user_input = input("you> ").strip()
        except EOFError:
            print()
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM,
            messages=messages,
        )

        reply = "".join(b.text for b in response.content if b.type == "text")
        messages.append({"role": "assistant", "content": reply})

        print(f"\nagent> {reply}")
        print(
            f"[working memory: {len(messages)} messages | "
            f"{response.usage.input_tokens} tokens in, "
            f"{response.usage.output_tokens} out]\n"
        )


if __name__ == "__main__":
    main()
