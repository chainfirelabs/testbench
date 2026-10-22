from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, TimestampMixin, new_uuid


class TestRun(TimestampMixin, Base):
    """One execution of a pipeline or suite, grouping the tests it recorded.

    Files that describe the whole run, such as a pytest report, belong here
    rather than on each test, and every test in the run can reach them.
    """

    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255))
    # The caller's own identity for the run, such as a CI pipeline ID. Unique,
    # so every job of one pipeline that starts "its" run joins the same one.
    external_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    url: Mapped[str | None] = mapped_column(String(2048))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    # Where the run's files go in the bucket, fixed when the run is created so
    # renaming it later does not split its files across two folders.
    storage_folder: Mapped[str] = mapped_column(String(512))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    creator: Mapped["User | None"] = relationship("User", foreign_keys=[created_by], lazy="selectin")

    @property
    def created_by_username(self): return self.creator.username if self.creator else None


class TestRunArtifact(TimestampMixin, Base):
    """A file shared by every test in a run."""

    __tablename__ = "test_run_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    test_run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    bucket: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(1024), unique=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


from .user import User  # noqa: E402
