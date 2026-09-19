"""The `ninja` command.

    ninja              talk to Ninja in the terminal
    ninja dashboard    the browser cockpit (layer 6)
"""

import argparse

from ninja import agent


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ninja",
        description="Ninja — a personal assistant that is a cast of agents.",
    )
    parser.add_subparsers(dest="command").add_parser(
        "dashboard", help="open the cockpit at localhost:7777"
    )

    if parser.parse_args().command == "dashboard":
        raise SystemExit("the dashboard arrives at layer 6 — not built yet")

    agent.main()
