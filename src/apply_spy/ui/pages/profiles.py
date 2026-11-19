"""The Profiles page: create, switch and delete profiles."""

from nicegui import ui

from apply_spy.db import session_scope
from apply_spy.profiles import (
    ConfirmationRequired,
    DuplicateProfileName,
    InvalidProfileName,
    create_profile,
    delete_profile,
    list_profiles,
)
from apply_spy.ui.layout import app_shell
from apply_spy.ui.state import engine, set_active_profile


@ui.page("/profiles")
def profiles_page() -> None:
    with app_shell("profiles") as shell:
        _build(shell.profile_id)


def _build(active_id: int | None) -> None:
    with ui.column().classes("gap-1"):
        ui.label("Profiles").classes("as-h1")
        ui.label("Each person keeps their own applications and follow-ups.").classes("as-sub")

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
                ui.navigate.reload()  # the menu and sidebar must drop the deleted profile

            with ui.row():
                ui.button("Cancel", on_click=dialog.close).props("flat")
                # The button is only a convenience; the service enforces the check.
                ui.button("Delete", color="negative", on_click=do_delete).props(
                    "data-testid=confirm-delete-button"
                ).bind_enabled_from(confirm, "value")
        dialog.open()

    def switch_to(profile_id: int) -> None:
        set_active_profile(profile_id)
        ui.navigate.reload()

    with ui.element("div").classes("as-card w-full"):
        with session_scope(engine) as session:
            profiles = [(p.id, p.name) for p in list_profiles(session)]
        if not profiles:
            ui.label("No profiles yet. Create one below.").props("data-testid=empty")
        for profile_id, name in profiles:
            with ui.row().classes("w-full items-center gap-3 py-2"):
                ui.avatar("person", color="blue-1", text_color="primary", size="40px")
                ui.label(name).classes("text-[16px] font-semibold").props(
                    "data-testid=profile-name"
                )
                if profile_id == active_id:
                    ui.badge("Active", color="primary").props("rounded")
                ui.space()
                if profile_id != active_id:
                    ui.button("Switch to", on_click=lambda _, i=profile_id: switch_to(i)).props(
                        "outline no-caps color=primary dense data-testid=switch-button"
                    )
                ui.button(
                    icon="delete",
                    color="negative",
                    on_click=lambda _, i=profile_id, n=name: confirm_delete(i, n),
                ).props("flat dense round data-testid=delete-button")

    def submit() -> None:
        try:
            with session_scope(engine) as session:
                create_profile(session, name_input.value)
        except (InvalidProfileName, DuplicateProfileName) as error:
            ui.notify(str(error), type="negative")
            return
        ui.notify("Profile created", type="positive")
        ui.navigate.reload()

    with ui.element("div").classes("as-card w-full"):
        ui.label("Add a profile").classes("as-title mb-2")
        with ui.row().classes("items-center gap-3"):
            name_input = (
                ui.input("Name")
                .props("outlined dense data-testid=name-input")
                .on("keydown.enter", submit)
            )
            ui.button("Create profile", icon="add", on_click=submit).props(
                "unelevated no-caps color=primary data-testid=create-button"
            )
