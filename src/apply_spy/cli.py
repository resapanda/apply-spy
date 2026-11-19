"""Command-line interface. Thin wrapper over the service layer."""

import argparse

from apply_spy.db import init_db, make_engine, session_scope
from apply_spy.profiles import create_profile, list_profiles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apply-spy")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("serve", help="start the local web UI")
    profile = commands.add_parser("profile", help="manage profiles")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    add = profile_commands.add_parser("add", help="create a profile")
    add.add_argument("name")
    profile_commands.add_parser("list", help="list profiles")

    args = parser.parse_args(argv)

    if args.command == "serve":
        from apply_spy.ui.app import run

        run()
        return 0

    engine = make_engine()
    init_db(engine)
    if args.profile_command == "add":
        try:
            with session_scope(engine) as session:
                created = create_profile(session, args.name)
        except ValueError as error:
            print(f"Error: {error}")
            return 1
        print(f"Created profile {created.name!r} (id {created.id}).")
    else:
        with session_scope(engine) as session:
            for p in list_profiles(session):
                print(f"{p.id}\t{p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
