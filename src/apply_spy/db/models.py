"""Database tables.

Every table that holds personal data gets a profile_id so each person's
data stays separate (see AGENTS.md, "Profile isolation").
"""

from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return f"Profile(id={self.id}, name={self.name!r})"


# Names are unique ignoring upper/lower case ("Haruka" == "haruka").
Index("uq_profiles_name_lower", func.lower(Profile.name), unique=True)


class Application(Base):
    """One job application. Owned by exactly one profile."""

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ON DELETE CASCADE: deleting a profile removes its applications too.
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    company: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(2000))
    location: Mapped[str | None] = mapped_column(String(200))
    salary_range: Mapped[str | None] = mapped_column(String(100))
    resume_version: Mapped[str | None] = mapped_column(String(200))  # free text until story 5.2
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50))
    date_applied: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class StatusEvent(Base):
    """History of an application's status. old_status is empty for the first event."""

    __tablename__ = "status_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    old_status: Mapped[str | None] = mapped_column(String(50))
    new_status: Mapped[str] = mapped_column(String(50))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
