"""Command-line interface. Thin wrapper over the service layer."""

import argparse
import os
import socket
import sys
from pathlib import Path

from apply_spy.backup import (
    BackupError,
    check_database,
    create_backup,
    has_live_data,
    restore_backup,
)
from apply_spy.config import DATABASE_FILENAME, NICEGUI_DIRNAME, PORT, data_dir
from apply_spy.db import init_db, make_engine, session_scope
from apply_spy.launcher import LauncherError, install_icon
from apply_spy.profiles import ProfileNotFound, create_profile, delete_profile, list_profiles
from apply_spy.tracker import export_applications_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apply-spy")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("serve", help="start the local web UI")
    export = commands.add_parser("export", help="export a profile's applications to CSV")
    export.add_argument("id", type=int, help="profile id (see 'profile list')")
    export.add_argument("--output", "-o", help="file to write (default: print to the screen)")
    backup = commands.add_parser("backup", help="save a timestamped archive of all your data")
    backup.add_argument("--output", "-o", help="folder for the archive (default: data/backups)")
    restore = commands.add_parser("restore", help="restore all data from a backup archive")
    restore.add_argument("archive", help="the .zip file made by 'backup'")
    commands.add_parser("check", help="check the database and show row counts")
    icon = commands.add_parser("install-icon", help="make a Mac app icon that starts apply-spy")
    icon.add_argument("--dest", help="folder for the app (default: ~/Applications)")
    profile = commands.add_parser("profile", help="manage profiles")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    add = profile_commands.add_parser("add", help="create a profile")
    add.add_argument("name")
    profile_commands.add_parser("list", help="list profiles")
    delete = profile_commands.add_parser("delete", help="delete a profile and all its data")
    delete.add_argument("id", type=int, help="profile id (see 'profile list')")

    args = parser.parse_args(argv)

    if args.command == "serve":
        return _serve()

    # These run before the database is opened: opening it would create an empty one,
    # which would make a restore into an empty folder look like an overwrite.
    if args.command in ("backup", "restore", "check"):
        return _data_command(args)
    if args.command == "install-icon":
        return _install_icon(args.dest)

    engine = make_engine()
    init_db(engine)
    if args.command == "export":
        return _export(engine, args.id, args.output)
    if args.profile_command == "add":
        try:
            with session_scope(engine) as session:
                created = create_profile(session, args.name)
        except ValueError as error:
            print(f"Error: {error}")
            return 1
        print(f"Created profile {created.name!r} (id {created.id}).")
    elif args.profile_command == "delete":
        return _delete(engine, args.id)
    else:
        with session_scope(engine) as session:
            for p in list_profiles(session):
                print(f"{p.id}\t{p.name}")
    return 0


def _install_icon(dest: str | None) -> int:
    if sys.platform != "darwin":
        print("Error: the icon is only available on macOS for now.")
        return 1
    try:
        app = install_icon(Path(dest) if dest else None)
    except LauncherError as error:
        print(f"Error: {error}")
        return 1
    print(f"Created {app}")
    print("Double-click it to start apply-spy (drag it to the Dock to keep it handy).")
    print("It runs quietly in the background; use the Quit button in the page to stop it.")
    print("If you move the apply-spy project folder, run this command again.")
    return 0


def _already_running() -> bool:
    """True if something is already listening on the web UI's port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


def _serve() -> int:
    if _already_running():
        print(f"apply-spy seems to be running already: open http://localhost:{PORT}")
        print("To stop it, use the Quit button in the page (or press Ctrl+C where it runs).")
        return 1
    folder = data_dir()
    # NiceGUI keeps its own session state (active profile, filters) in a folder it picks
    # when it is first imported, relative to the current folder by default. Point it into
    # the data folder, before the import below, so it never depends on where we started.
    os.environ.setdefault("NICEGUI_STORAGE_PATH", str(folder / NICEGUI_DIRNAME))
    print(f"Data folder: {folder}")

    from apply_spy.ui.app import run

    run()
    return 0


def _print_counts(counts: dict[str, int]) -> None:
    for table, count in counts.items():
        print(f"  {table}: {count}")


def _upgrade_existing_database(folder: Path) -> None:
    """Bring a database made by an older version up to date (adds newer tables and
    columns). Does nothing if there is no database yet, so a first backup or check
    still reports "no database" instead of quietly creating an empty one."""
    if (folder / DATABASE_FILENAME).exists():
        engine = make_engine()
        try:
            init_db(engine)
        finally:
            engine.dispose()


def _data_command(args) -> int:
    folder = data_dir()
    try:
        if args.command in ("backup", "check"):
            _upgrade_existing_database(folder)
        if args.command == "backup":
            output = Path(args.output) if args.output else None
            archive, result = create_backup(folder, output)
            print(f"Backup saved to {archive}")
            _print_counts(result.counts)
            return 0

        if args.command == "check":
            result = check_database(folder / DATABASE_FILENAME)
            _print_counts(result.counts)
            for table in result.missing_tables:
                print(f"  MISSING table: {table}")
            print("Integrity: " + ("ok" if result.integrity_ok else "FAILED"))
            print("Foreign keys: " + ("ok" if result.foreign_keys_ok else "FAILED"))
            return 0 if result.ok else 1

        overwrite = False
        if has_live_data(folder):
            print(f"{folder} already has data. Restoring replaces it.")
            print("Your current data is moved to a folder next to it, not deleted.")
            answer = input("Type 'yes' to replace it (anything else cancels): ")
            if answer.strip().lower() != "yes":
                print("Cancelled: nothing was changed.")
                return 1
            overwrite = True
        result = restore_backup(Path(args.archive), folder, overwrite=overwrite)
        print("Restored. Row counts now (checked against the backup):")
        _print_counts(result.counts)
        return 0
    except BackupError as error:
        print(f"Error: {error}")
        return 1


def _export(engine, profile_id: int, output: str | None) -> int:
    try:
        with session_scope(engine) as session:
            text = export_applications_csv(session, profile_id)
    except ProfileNotFound as error:
        print(f"Error: {error}")
        return 1
    if output is None:
        print(text, end="")
    else:
        # newline="" so line breaks inside notes are written exactly as the csv module made them
        with open(output, "w", encoding="utf-8", newline="") as file:
            file.write(text)
        print(f"Exported applications to {output}")
    return 0


def _delete(engine, profile_id: int) -> int:
    with session_scope(engine) as session:
        match = [p for p in list_profiles(session) if p.id == profile_id]
    if not match:
        print(f"Error: No profile with id {profile_id}.")
        return 1
    name = match[0].name
    print(f"This permanently deletes profile {name!r} and all of its data.")
    answer = input("Type 'yes' to confirm (anything else cancels): ")
    try:
        with session_scope(engine) as session:
            delete_profile(session, profile_id, answer.strip().lower() == "yes")
    except ValueError as error:
        print(f"Cancelled: {error}")
        return 1
    print(f"Deleted profile {name!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
