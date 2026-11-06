from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UsageDailyRollup(Base):
    """One day of runs for one member, assistant, runtime, kind and model.

    Built from ``agent_executions`` by ``app/services/usage_rollup_service.py``
    (recomputed for recent days every hour and before old executions are
    pruned), so the administration Usage panel reads a few hundred rows per
    period instead of every execution, and the sums survive retention. The
    dimension columns use sentinels (0, "") instead of NULL so the unique
    constraint holds on every database.
    """

    __tablename__ = "usage_daily_rollups"
    __table_args__ = (
        UniqueConstraint("day", "user_id", "agent_id", "runtime_type", "kind", "model", name="uq_usage_daily_rollups_dims"),
        Index("ix_usage_daily_rollups_day", "day"),
        Index("ix_usage_daily_rollups_user_day", "user_id", "day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    runtime_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    executions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    estimated_credits: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    actual_credits: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Copilot's own charge where it reported one, the estimate otherwise: the figure to show.
    credits: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
