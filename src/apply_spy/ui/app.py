"""Local web UI (NiceGUI). No business logic here: it calls the service layer."""

from nicegui import ui

from apply_spy.db import init_db, make_engine, session_scope
from apply_spy.profiles import (
    DuplicateProfileName,
    InvalidProfileName,
    create_profile,
    list_profiles,
)

engine = make_engine()
init_db(engine)


@ui.page("/")
def profiles_page() -> None:
    ui.label("apply-spy").classes("text-2xl font-bold")
    ui.label("Profiles").classes("text-lg mt-4")

    @ui.refreshable
    def profile_list() -> None:
        with session_scope(engine) as session:
            profiles = list_profiles(session)
        if not profiles:
            ui.label("No profiles yet. Create one below.").props("data-testid=empty")
        for profile in profiles:
            ui.label(profile.name).props("data-testid=profile-name")

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
    ui.run(title="apply-spy", host="127.0.0.1", reload=False, show=True)
