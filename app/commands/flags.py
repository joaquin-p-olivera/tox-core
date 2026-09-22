def normalize_flag(text: str) -> str:
    """Phone keyboards turn "--" into an em dash and "-" into an en dash: undo that, and ignore case."""
    return text.replace("—", "--").replace("–", "-").lower()
