"""The push relay's data contract, which is not negotiable.

Android delivery is an FCM data message, whose payload is a map<string, string>.
A native int or bool anywhere at the top level of `data` makes the relay reject
the **entire** push with "data must only contain string values", and Home
Assistant surfaces that as a bare "Error sending notification to <device>" with
the reason only at DEBUG -- so the failure is both total and near-silent.

That is not hypothetical: every live-card push to a Galaxy S24 failed this way,
which is also why a swiped-away card never came back. The rules here were
established by sending real payloads to a real device and reading the relay's
replies, so they are a record of observed behaviour rather than of the docs:

    native int/bool at the top level      -> rejected
    the same values as strings            -> accepted
    nested list/dict (actions, push)      -> accepted untouched, bools and all
"""

from custom_components.ha_creality_ws.notification_rules import stringify_data


def test_bools_become_lowercase_strings():
    """The companion app compares against "true"/"false", not Python's repr."""
    assert stringify_data({"live_update": True, "alert_once": False}) == {
        "live_update": "true",
        "alert_once": "false",
    }


def test_ints_become_strings():
    out = stringify_data({"progress": 42, "progress_max": 100, "when": 1789012345})
    assert out == {"progress": "42", "progress_max": "100", "when": "1789012345"}


def test_strings_pass_through_untouched():
    data = {"tag": "ha_creality_ws_abc_live", "notification_icon": "mdi:printer-3d"}
    assert stringify_data(data) == data


def test_nested_structures_are_left_alone():
    """Verified on-device: the relay only validates the top level, so `actions`
    keeps the real bools that iOS needs (`destructive`, `authenticationRequired`)
    and a stringified "true" there would be wrong."""
    actions = [
        {"action": "CREALITY_STOP_AB", "title": "Stop", "destructive": True},
    ]
    push = {"interruption-level": "time-sensitive"}
    out = stringify_data({"actions": actions, "push": push})
    assert out["actions"] == actions
    assert out["actions"][0]["destructive"] is True
    assert out["push"] == push


def test_none_drops_the_key_rather_than_sending_the_word_none():
    """str(None) would arrive as the four characters "None", which the companion
    app treats as a value -- an icon_url of "None" fetches nothing and an
    entityId of "None" opens a broken dialog."""
    assert stringify_data({"icon_url": None, "tag": "t"}) == {"tag": "t"}


def test_no_native_scalar_survives_a_realistic_live_payload():
    """The regression guard: one leaked bool costs every Android notification,
    so assert the absence of the whole class rather than key by key."""
    out = stringify_data(
        {
            "tag": "ha_creality_ws_abc_live",
            "live_update": True,
            "progress": 42,
            "progress_max": 100,
            "chronometer": True,
            "when": 1789012345,
            "alert_once": True,
            "importance": "low",
            "actions": [{"action": "A", "title": "Pause"}],
        }
    )
    offenders = {
        key: value
        for key, value in out.items()
        if isinstance(value, (bool, int, float))
    }
    assert offenders == {}, f"these would be rejected by the relay: {offenders}"


def test_empty_and_missing_data_are_both_empty_dicts():
    assert stringify_data(None) == {}
    assert stringify_data({}) == {}
