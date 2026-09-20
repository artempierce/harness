"""The `ninja` command.

    ninja              talk to Ninja in the terminal
    ninja trace        the last 10 turns
    ninja trace 7      one turn, step by step
    ninja dashboard    the browser cockpit (layer 6)

In the REPL: /persona lists the cast, /persona <name> switches.
"""

import argparse

from ninja import agent, trace


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ninja",
        description="Ninja — a personal assistant that is a cast of agents.",
    )
    sub = parser.add_subparsers(dest="command")
    cockpit = sub.add_parser("dashboard", help="open the cockpit at localhost:7777")
    cockpit.add_argument("--port", type=int, default=7777)
    viewer = sub.add_parser("trace", help="what happened on a turn")
    viewer.add_argument("id", nargs="?", type=int, help="a trace id; omit to list recent")

    args = parser.parse_args()

    if args.command == "dashboard":
        from ninja import server  # imported late: needs fastapi, and a key

        server.serve(args.port)
        return
    if args.command == "trace":
        trace.print_one(args.id) if args.id else trace.print_recent()
        return

    agent.main()
