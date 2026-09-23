# Importing the modules registers their commands. The order here is the order in !help.
from . import general, fun, games, dev, devtools, service, github, football  # noqa: F401
from .registry import CommandContext, all_commands, command, get_command

__all__ = ["CommandContext", "all_commands", "command", "get_command"]
