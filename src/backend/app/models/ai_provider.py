from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin, new_uuid


class AiProviderProfile(TimestampMixin, Base):
    __tablename__ = "ai_provider_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    provider_type: Mapped[str] = mapped_column(String(30), nullable=False, default="openai-compatible")
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)
    custom_headers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    manual_models: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    discovered_models: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_refreshed_at: Mapped[str | None] = mapped_column(String(40))


class AiPluginDefault(TimestampMixin, Base):
    __tablename__ = "ai_plugin_defaults"

    plugin_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_provider_profiles.id", ondelete="SET NULL"), index=True
    )
    model: Mapped[str | None] = mapped_column(String(255))
    repeat_model: Mapped[str | None] = mapped_column(String(255))
