from sqlalchemy import CheckConstraint, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin


class DashboardLayout(TimestampMixin, Base):
    """The installation-wide home layout, edited only by administrators."""

    __tablename__ = "dashboard_layouts"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_dashboard_layout_singleton"),
        CheckConstraint("revision >= 0", name="ck_dashboard_layout_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    widgets: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
