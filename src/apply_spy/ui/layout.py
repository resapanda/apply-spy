"""The frame around every page: a sidebar (navigation and quick actions) and a top bar
(today's date and the profile menu). Every page runs its content inside app_shell()."""

import html
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date

from nicegui import ui

from apply_spy.db import session_scope
from apply_spy.tracker import count_follow_ups
from apply_spy.ui.dialogs import add_application_dialog, add_follow_up_dialog, confirm_quit
from apply_spy.ui.state import ActiveProfile, engine, load_active_profile, set_active_profile
from apply_spy.ui.theme import apply_colors, icon

NAV = (
    ("dashboard", "Dashboard", "home", "/"),
    ("applications", "Applications", "description", "/applications"),
    ("follow-ups", "Follow-ups", "event_available", "/follow-ups"),
    ("profiles", "Profiles", "person_outline", "/profiles"),
    ("data", "Data", "bar_chart", "/data"),
)


@dataclass
class Shell:
    """What a page gets from the frame."""

    profile: ActiveProfile
    refresh_badge: Callable[[], None]  # call after follow-ups change, to update the badge

    @property
    def profile_id(self) -> int | None:
        return self.profile.id


def _follow_up_count(profile_id: int | None) -> int:
    """How many open follow-ups there are: the same number the Follow-ups page lists."""
    if profile_id is None:
        return 0
    with session_scope(engine) as session:
        return count_follow_ups(session, profile_id)


def _nav_html(active: str, follow_ups: int) -> str:
    links = []
    for key, label, icon_name, href in NAV:
        badge = (
            f'<span class="as-nav-badge">{follow_ups}</span>'
            if key == "follow-ups" and follow_ups
            else ""
        )
        links.append(
            f'<a href="{href}" class="{"active" if key == active else ""}" '
            f'data-testid="nav-{key}">{icon(icon_name)}<span>{html.escape(label)}</span>{badge}</a>'
        )
    return f'<div class="as-nav">{"".join(links)}</div>'


@contextmanager
def app_shell(active: str) -> Iterator[Shell]:
    """Draw the sidebar and top bar, then let the page add its content.

    `active` is the key of the sidebar item to highlight (see NAV).
    """
    apply_colors()
    # Quasar's layout "view": the sidebar runs the full height of the window, with the
    # top bar only over the page content (the logo sits at the very top-left).
    ui.query(".q-layout").props('view="lHh Lpr lFf"')
    profile = load_active_profile()

    def need_profile() -> bool:
        if profile.id is None:
            ui.notify("Create a profile first.", type="warning")
            return False
        return True

    def quick_add_application() -> None:
        if need_profile():
            add_application_dialog(profile.id, lambda: ui.navigate.to("/applications"))

    def quick_add_follow_up() -> None:
        if need_profile():
            add_follow_up_dialog(profile.id, ui.navigate.reload)

    with (
        ui.left_drawer(value=True, fixed=True, bordered=True)
        .props("width=244 breakpoint=700")
        .classes("as-sidebar")
    ):
        ui.html(
            '<div class="as-logo"><div class="as-logo-mark">'
            f"{icon('search')}</div>"
            '<div class="as-logo-text">apply-spy</div></div>',
            sanitize=False,
        )

        @ui.refreshable
        def navigation() -> None:
            ui.html(_nav_html(active, _follow_up_count(profile.id)), sanitize=False)

        navigation()
        ui.html('<div class="as-section-label">Quick Actions</div>', sanitize=False)
        with ui.column().classes("as-quick w-full gap-0"):
            ui.button("Add Application", icon="add", on_click=quick_add_application).props(
                "unelevated no-caps color=primary data-testid=quick-add-application"
            )
            ui.button("Add Follow-up", icon="event", on_click=quick_add_follow_up).props(
                "unelevated no-caps color=blue-1 text-color=primary data-testid=quick-add-follow-up"
            )

    with ui.header(elevated=False).classes("as-topbar"):
        ui.space()
        today = date.today()
        ui.label(f"{today:%a}, {today:%b} {today.day}, {today.year}").classes("as-date").props(
            "data-testid=today"
        )
        with ui.button().props("flat no-caps data-testid=profile-menu").classes("ml-4"):
            ui.avatar("person", color="blue-1", text_color="primary", size="38px")
            ui.label(profile.name or "No profile").classes("ml-2 text-[15px] font-semibold")
            ui.icon("expand_more")
            with ui.menu():
                for profile_id, name in profile.profiles:
                    mark = "check" if profile_id == profile.id else "person_outline"

                    def switch(chosen: int = profile_id) -> None:
                        set_active_profile(chosen)
                        ui.navigate.reload()  # so the whole page uses the new profile

                    with ui.menu_item(on_click=switch).props(f"data-testid=switch-{profile_id}"):
                        ui.icon(mark).classes("mr-2")
                        ui.label(name)
                if profile.profiles:
                    ui.separator()
                ui.menu_item("Manage profiles", on_click=lambda: ui.navigate.to("/profiles"))
                ui.menu_item("Quit apply-spy", on_click=confirm_quit).props(
                    "data-testid=quit-button"
                )

    yield Shell(profile=profile, refresh_badge=navigation.refresh)


def no_profile_card() -> None:
    """What every page shows when there is no profile yet."""
    with ui.element("div").classes("as-card").props("data-testid=no-profile"):
        ui.label("Welcome to apply-spy").classes("as-title")
        ui.label("Create a profile to start tracking your applications.").classes("as-muted")
        ui.button(
            "Create a profile", icon="person_add", on_click=lambda: ui.navigate.to("/profiles")
        ).props("unelevated no-caps color=primary").classes("mt-3")
