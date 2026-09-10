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

    native int/bool at the top level        -> rejected
    the same values as strings              -> accepted
    bool inside a dict in `actions` (list)  -> rejected; the list is flattened
    native int inside `push`/`content_state` (dict) -> accepted, not flattened

The list/dict asymmetry is the part that is easy to get wrong, and did get
wrong: `destructive: True` on the Stop button was enough to lose every push
even after the top level had been cleaned up.
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


def test_bools_inside_actions_are_coerced_because_the_list_is_flattened():
    """The correction that cost a release. `actions` is folded into the same FCM
    map, so `destructive: True` is rejected exactly like a top-level bool --
    and it silently took every push with it."""
    out = stringify_data(
        {
            "actions": [
                {
                    "action": "CREALITY_STOP_AB",
                    "title": "Stop",
                    "destructive": True,
                    "authenticationRequired": True,
                }
            ]
        }
    )
    stop = out["actions"][0]
    assert stop["destructive"] == "true"
    assert stop["authenticationRequired"] == "true"
    # The strings that were already fine are untouched.
    assert stop["action"] == "CREALITY_STOP_AB"
    assert stop["title"] == "Stop"


def test_plain_dicts_keep_their_native_types():
    """`push` and `content_state` are not flattened -- measured on-device with
    real ints -- and iOS renders a Live Activity from the latter, so coercing
    its numbers to strings would be the wrong fix."""
    push = {"interruption-level": "time-sensitive"}
    content = {"state": "printing", "progress_pct": 42, "eta_timestamp": 1789012345}
    out = stringify_data({"push": push, "content_state": content})
    assert out["push"] == push
    assert out["content_state"]["progress_pct"] == 42
    assert out["content_state"]["eta_timestamp"] == 1789012345


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
            "actions": [
                {"action": "A", "title": "Pause"},
                {"action": "B", "title": "Stop", "destructive": True},
            ],
        }
    )
    offenders = {
        key: value
        for key, value in out.items()
        if isinstance(value, (bool, int, float))
    }
    assert offenders == {}, f"these would be rejected by the relay: {offenders}"
    # And the same sweep through the flattened list, which is where the second
    # round of rejections came from.
    nested = {
        f"actions[{i}].{k}": v
        for i, entry in enumerate(out["actions"])
        for k, v in entry.items()
        if isinstance(v, (bool, int, float))
    }
    assert nested == {}, f"these would be rejected by the relay: {nested}"


def test_empty_and_missing_data_are_both_empty_dicts():
    assert stringify_data(None) == {}
    assert stringify_data({}) == {}


# --------------------------------------------------------------------------- #
# The self-check that makes a rejection attributable
# --------------------------------------------------------------------------- #


def test_the_guard_names_every_offending_key(caplog):
    """Home Assistant swallows the relay's rejection -- it never reaches the
    integration, and what it logs is a bare "Error sending notification to
    <device>" with the reason only at DEBUG. So this class of bug is invisible
    from here by construction, and twice survived a release for that reason.
    The warning cannot prevent the rejection; it makes it attributable."""
    import logging

    from custom_components.ha_creality_ws.coordinator import _warn_on_unsendable

    with caplog.at_level(logging.WARNING):
        _warn_on_unsendable(
            {
                "tag": "fine",
                "progress": 42,
                "live_update": True,
                "actions": [{"action": "A", "title": "Stop", "destructive": True}],
            },
            "notify.mobile_app_s24",
        )
    assert "progress" in caplog.text
    assert "live_update" in caplog.text
    # The nested one is the half that was missed the first time round.
    assert "actions[0].destructive" in caplog.text
    assert "notify.mobile_app_s24" in caplog.text


def test_the_guard_stays_quiet_for_a_conforming_payload(caplog):
    import logging

    from custom_components.ha_creality_ws.coordinator import _warn_on_unsendable

    with caplog.at_level(logging.WARNING):
        _warn_on_unsendable(
            stringify_data(
                {
                    "tag": "t",
                    "progress": 42,
                    "live_update": True,
                    "push": {"interruption-level": "passive"},
                    "actions": [{"action": "A", "title": "Stop", "destructive": True}],
                }
            ),
            "notify.mobile_app_s24",
        )
    assert caplog.text == ""

