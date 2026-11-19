"""Local web UI (NiceGUI). No business logic here: it calls the service layer."""

from contextlib import contextmanager

from nicegui import app, ui

from apply_spy.db import init_db, make_engine, session_scope
from apply_spy.profiles import (
    ConfirmationRequired,
    DuplicateProfileName,
    InvalidProfileName,
    create_profile,
    delete_profile,
    get_active_profile,
    list_profiles,
)

engine = make_engine()
init_db(engine)


ACTIVE_PROFILE_KEY = "active_profile_id"


@contextmanager
def page_layout():
    """Header shared by every page: app name plus the profile switcher.

    Every page should use this so the active profile name is always visible.
    """
    with session_scope(engine) as session:
        profiles = [(p.id, p.name) for p in list_profiles(session)]
        active = get_active_profile(session, app.storage.user.get(ACTIVE_PROFILE_KEY))
        active_id = active.id if active else None

    def switch(event) -> None:
        app.storage.user[ACTIVE_PROFILE_KEY] = event.value
        ui.navigate.reload()  # reload so everything on the page uses the new profile

    with ui.header().classes("items-center justify-between"):
        ui.label("apply-spy").classes("text-xl font-bold")
        if profiles:
            ui.select(
                dict(profiles), value=active_id, label="Active profile", on_change=switch
            ).props("data-testid=profile-switcher dense")
        else:
            ui.label("No profile yet").props("data-testid=profile-switcher")
    yield active_id


@ui.page("/")
def profiles_page() -> None:
    with page_layout():
        _profiles_content()


def _profiles_content() -> None:
    ui.label("Profiles").classes("text-lg mt-4")

    @ui.refreshable
    def profile_list() -> None:
        with session_scope(engine) as session:
            profiles = list_profiles(session)
        if not profiles:
            ui.label("No profiles yet. Create one below.").props("data-testid=empty")
        for profile in profiles:
            with ui.row().classes("items-center"):
                ui.label(profile.name).props("data-testid=profile-name")
                ui.button(
                    icon="delete",
                    color="negative",
                    on_click=lambda _, p=profile: confirm_delete(p.id, p.name),
                ).props("flat dense data-testid=delete-button")

    def confirm_delete(profile_id: int, name: str) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label(f"Delete {name}?").classes("text-lg font-bold")
            ui.label("This permanently removes the profile and all of its data.")
            confirm = ui.checkbox("Yes, delete this profile and all its data").props(
                "data-testid=confirm-checkbox"
            )

            def do_delete() -> None:
                try:
                    with session_scope(engine) as session:
                        delete_profile(session, profile_id, confirm.value)
                except ConfirmationRequired as error:
                    ui.notify(str(error), type="negative")
                    return
                dialog.close()
                ui.notify(f"Deleted {name}", type="positive")
                ui.navigate.reload()  # header switcher must drop the deleted profile

            with ui.row():
                ui.button("Cancel", on_click=dialog.close).props("flat")
                # The button is only a convenience; the service enforces the check.
                ui.button("Delete", color="negative", on_click=do_delete).props(
                    "data-testid=confirm-delete-button"
                ).bind_enabled_from(confirm, "value")
        dialog.open()

    profile_list()

    def submit() -> None:
        try:
            with session_scope(engine) as session:
                create_profile(session, name_input.value)
        except (InvalidProfileName, DuplicateProfileName) as error:
            ui.notify(str(error), type="negative")
            return
        name_input.value = ""
        ui.notify("Profile created", type="positive")
        profile_list.refresh()

    with ui.row().classes("items-center mt-4"):
        name_input = ui.input("Name").props("data-testid=name-input").on("keydown.enter", submit)
        ui.button("Create profile", on_click=submit).props("data-testid=create-button")


def run() -> None:
    # host=127.0.0.1: only this computer can reach the app (your data stays private).
    # storage_secret signs the cookie that remembers the active profile in this browser.
    # It protects nothing sensitive: the app is only reachable from this computer.
    ui.run(
        title="apply-spy",
        host="127.0.0.1",
        reload=False,
        show=True,
        storage_secret="apply-spy-local",
    )
