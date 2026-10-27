# Importing the modules registers their commands. The order here is the order in !help.
from . import general, fun, utilities, games, dev, devtools, service, claude, config, spy, moderation, github, football, uid, network, fred, web, history, gta, youtube  # noqa: F401
from .registry import CommandContext, all_commands, command, get_command

__all__ = ["CommandContext", "all_commands", "command", "get_command"]
