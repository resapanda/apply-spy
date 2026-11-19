"""The Follow-ups page: every follow-up, grouped by how soon it is due, plus the ones
you have completed."""

from datetime import date

from nicegui import ui

from apply_spy.db import session_scope
from apply_spy.tracker import (
    count_completed_follow_ups,
    later_follow_ups,
    list_completed_follow_ups,
    needs_attention,
)
from apply_spy.ui.dialogs import add_follow_up_dialog
from apply_spy.ui.follow_up_actions import FollowUpActions
from apply_spy.ui.follow_up_html import completed_table_html, group_html
from apply_spy.ui.layout import Shell, app_shell, no_profile_card
from apply_spy.ui.state import engine


@ui.page("/follow-ups")
def follow_ups_page() -> None:
    with app_shell("follow-ups") as shell:
        if shell.profile_id is None:
            no_profile_card()
            return
        _build(shell)


def _build(shell: Shell) -> None:
    profile_id = shell.profile_id

    def refresh_all() -> None:
        content.refresh()
        shell.refresh_badge()

    actions = FollowUpActions(profile_id, refresh_all, lambda: content.refresh())
    actions.register()

    with ui.row().classes("w-full items-center justify-between"):
        with ui.column().classes("gap-1"):
            ui.label("Follow-ups").classes("as-h1")
            ui.label("Everything you planned to follow up on, soonest first.").classes("as-sub")
        ui.button(
            "Add Follow-up",
            icon="add",
            on_click=lambda: add_follow_up_dialog(profile_id, refresh_all),
        ).props("unelevated no-caps color=primary data-testid=add-follow-up-button")

    @ui.refreshable
    def content() -> None:
        with session_scope(engine) as session:
            found = needs_attention(session, profile_id)
            later = later_follow_ups(session, profile_id)
            completed = list_completed_follow_ups(session, profile_id, limit=20)
            completed_count = count_completed_follow_ups(session, profile_id)

        actions.undo_bar()
        groups = (
            ("Overdue", "red", "schedule", "overdue", found.overdue),
            ("Today", "amber", "event", "today", found.due_today),
            ("This Week", "blue", "date_range", "this-week", found.this_week),
            ("Later", "blue", "update", "later", later),
        )
        if not any(apps for *_, apps in groups):
            with ui.element("div").classes("as-card as-empty").props("data-testid=all-clear"):
                ui.label("No follow-ups planned.").classes("as-title")
                ui.label("Set a follow-up date on an application and it appears here.")
        for title, tone, icon_name, key, apps in groups:
            if apps:
                ui.html(
                    group_html(
                        title, tone, icon_name, apps, found.today or date.today(), key, limit=None
                    ),
                    sanitize=False,
                ).classes("w-full")

        with ui.element("div").classes("as-card as-group w-full").props("data-testid=completed"):
            ui.html(
                f'<div class="as-group-title">Recently completed ({completed_count})</div>',
                sanitize=False,
            )
            if completed:
                ui.html(completed_table_html(completed), sanitize=False)
            else:
                ui.html(
                    '<div class="as-empty">Nothing completed yet.</div>',
                    sanitize=False,
                )

    content()
