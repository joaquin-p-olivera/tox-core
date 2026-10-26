import pytest

from app.models import ChatSetting
from app.services import chat_config

ADMIN = "admin1@lid"
SETTING = "CLAUDE_INTERJECT_CHANCE"


def test_a_chat_without_a_row_is_worth_what_the_env_says(db_session, settings):
    assert chat_config.value(db_session, settings, "whatsapp:g1", SETTING) == settings.CLAUDE_INTERJECT_CHANCE


def test_the_value_of_one_chat_does_not_reach_the_others(db_session, settings):
    chat_config.set_value(db_session, "whatsapp:g1", SETTING, "0.5", "whatsapp:admin1@lid")
    assert chat_config.value(db_session, settings, "whatsapp:g1", SETTING) == 0.5
    assert chat_config.value(db_session, settings, "whatsapp:g2", SETTING) == settings.CLAUDE_INTERJECT_CHANCE


def test_what_is_not_on_the_whitelist_cannot_be_set_from_a_chat(db_session):
    """The command only offers the talking settings, but the service is what must refuse the rest: an API key or the
    list of admins is never something a message can change."""
    for setting in ("API_KEY", "ADMIN_USER_IDS", "CLAUDE_WORKDIR"):
        with pytest.raises(ValueError):
            chat_config.parse(setting, "whatever")
        with pytest.raises(ValueError):
            chat_config.set_value(db_session, "whatsapp:g1", setting, "whatever", "whatsapp:admin1@lid")


def test_a_value_the_env_itself_would_refuse_is_refused_here_too(db_session):
    with pytest.raises(ValueError):
        chat_config.set_value(db_session, "whatsapp:g1", SETTING, "5", "whatsapp:admin1@lid")  # a share goes to 1
    with pytest.raises(ValueError):
        chat_config.set_value(db_session, "whatsapp:g1", "CLAUDE_CONTEXT_MESSAGES", "9999", "whatsapp:admin1@lid")
    assert db_session.get(ChatSetting, ("whatsapp:g1", SETTING)) is None


def test_a_stored_value_that_stopped_being_valid_is_ignored_instead_of_breaking_the_chat(db_session, settings):
    """A range narrowed in a new version must not take a chat down with it: the general value applies again."""
    db_session.add(ChatSetting(chat_key="whatsapp:g1", key=SETTING, value="7", set_by="whatsapp:admin1@lid"))
    db_session.commit()
    assert chat_config.value(db_session, settings, "whatsapp:g1", SETTING) == settings.CLAUDE_INTERJECT_CHANCE
    assert chat_config.overrides(db_session, "whatsapp:g1") == {}


def test_reset_leaves_nothing_behind(db_session, settings):
    chat_config.set_value(db_session, "whatsapp:g1", SETTING, "0.5", "whatsapp:admin1@lid")
    assert chat_config.clear(db_session, "whatsapp:g1", SETTING) is True
    assert chat_config.clear(db_session, "whatsapp:g1", SETTING) is False  # nothing left to drop
    assert chat_config.value(db_session, settings, "whatsapp:g1", SETTING) == settings.CLAUDE_INTERJECT_CHANCE


def test_the_command_lists_every_option_with_where_its_value_comes_from(send):
    answer = send("!config", user=ADMIN)[0]
    for name in ("charla", "espera", "perfiles", "respuestas", "audio", "contexto", "horas"):
        assert f"*{name}*" in answer
    assert "general" in answer


def test_the_command_sets_a_percentage_and_the_chat_keeps_it(send, db_session, settings):
    assert "15%" in send("!config charla 15%", user=ADMIN)[0]
    assert chat_config.value(db_session, settings, "whatsapp:g1", SETTING) == 0.15
    assert "_(este chat)_" in send("!config", user=ADMIN)[0]

    assert "vuelve a lo general" in send("!config charla -reset", user=ADMIN)[0]
    assert chat_config.value(db_session, settings, "whatsapp:g1", SETTING) == settings.CLAUDE_INTERJECT_CHANCE


def test_off_and_on_switch_the_butting_in_of_this_chat_only(send, db_session, settings):
    send("!config -off", user=ADMIN)
    assert chat_config.value(db_session, settings, "whatsapp:g1", SETTING) == 0
    assert chat_config.value(db_session, settings, "whatsapp:g2", SETTING) == settings.CLAUDE_INTERJECT_CHANCE

    send("!config -on", user=ADMIN)
    assert chat_config.value(db_session, settings, "whatsapp:g1", SETTING) == settings.CLAUDE_INTERJECT_CHANCE


def test_a_value_out_of_range_is_answered_with_what_it_does_take(send, db_session):
    answer = send("!config espera 9999", user=ADMIN)[0]
    assert "1440" in answer  # the le of the field itself, not a number written by hand here
    assert db_session.get(ChatSetting, ("whatsapp:g1", "CLAUDE_INTERJECT_COOLDOWN_MINUTES")) is None


def test_only_an_admin_configures_a_chat(send):
    from tests.conftest import REFUSED
    assert send("!config charla 100%") == REFUSED
