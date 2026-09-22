from sqlalchemy import Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class HostSample(Base):
    """One reading of the machine's vitals, taken by the sampler while the API runs (for peaks and battery trends)."""

    __tablename__ = "host_samples"
    __table_args__ = (Index("ix_host_samples_taken_at", "taken_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    taken_at: Mapped[float] = mapped_column(Float)  # unix time: no timezone to get wrong
    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)  # average since the previous sample
    memory_used_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_status: Mapped[str | None] = mapped_column(String, nullable=True)
