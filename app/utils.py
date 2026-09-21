from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC now. SQLite drops tzinfo, so everything is stored naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
