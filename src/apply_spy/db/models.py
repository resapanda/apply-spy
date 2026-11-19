"""Database tables.

Every table that holds personal data gets a profile_id so each person's
data stays separate (see AGENTS.md, "Profile isolation").
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String, func
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
