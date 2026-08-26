"""Contract + behavior tests for the standalone hermes-nostr plugin.

Run inside a Hermes-enabled environment (the host provides ``gateway.*`` /
``hermes_cli.*``):

    pip install -e '.[dev]'
    pytest -q
"""
import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

from gateway.platform_registry import platform_registry, PlatformEntry
import hermes_nostr


class _Ctx:
    """Minimal stand-in for the Hermes PluginContext."""

    def __init__(self):
        self.registered = []

    def register_platform(self, **kw):
        platform_registry.register(PlatformEntry(**kw))
        self.registered.append(kw["name"])


@pytest.fixture(autouse=True, scope="module")
def _registered():
    """Register the plugin once so the dynamic ``Platform('nostr')`` member is
    minted (the adapter resolves it lazily at construction)."""
    hermes_nostr.register(_Ctx())
    yield


def _entry():
    return platform_registry.get("nostr")


def _config(token="a" * 64, relays="wss://relay.example.com", extra=None):
    from gateway.config import PlatformConfig
    e = {"relays": relays}
    if extra:
        e.update(extra)
    return PlatformConfig(enabled=True, token=token, extra=e)


# --- contract -------------------------------------------------------------

def test_register_is_callable():
    assert callable(hermes_nostr.register)


def test_register_produces_valid_entry():
    e = _entry()
    assert e is not None
    assert e.name == "nostr" and e.label
    assert callable(e.adapter_factory) and callable(e.check_fn)
    # the hooks that make it work with zero core edits
    assert callable(e.is_connected)
    assert callable(e.env_enablement_fn)
    assert callable(e.standalone_sender_fn)
    assert callable(e.setup_fn)
    assert e.cron_deliver_env_var == "NOSTR_HOME_CHANNEL"
    assert e.allowed_users_env == "NOSTR_ALLOWED_NPUBS"
    assert e.max_message_length == 4096
    assert e.platform_hint


def test_platform_enum_resolves_after_register():
    from gateway.config import Platform
    assert Platform("nostr") is Platform("nostr")
    assert Platform("nostr").value == "nostr"


def test_factory_builds_adapter_on_empty_config():
    from gateway.platforms.base import BasePlatformAdapter
    cfg = MagicMock()
    cfg.extra = {}
    cfg.token = None
    cfg.enabled = True
    cfg.home_channel = None
    cfg.reply_to_mode = "first"
    a = _entry().adapter_factory(cfg)
    assert isinstance(a, BasePlatformAdapter)
    assert hasattr(a, "connect") and hasattr(a, "send") and hasattr(a, "name")


def test_check_fn_and_is_connected_return_bool():
    e = _entry()
    assert isinstance(e.check_fn(), bool)
    assert isinstance(e.is_connected(_config()), bool)


# --- authorization (security-critical) ------------------------------------

def test_allowlist_denies_by_default():
    from hermes_nostr.adapter import NostrAdapter
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("NOSTR_ALLOWED_NPUBS", None)
        os.environ.pop("NOSTR_ALLOW_ALL_USERS", None)
        a = NostrAdapter(_config())
    assert not a._allow_all_npubs
    assert len(a._allowed_pubkeys) == 0


def test_allowlist_star_allows_all():
    from hermes_nostr.adapter import NostrAdapter
    with patch.dict(os.environ, {"NOSTR_ALLOWED_NPUBS": "*"}, clear=False):
        a = NostrAdapter(_config())
    assert a._allow_all_npubs


def test_allow_all_users_boolean_opens_gate():
    from hermes_nostr.adapter import NostrAdapter
    with patch.dict(os.environ, {"NOSTR_ALLOW_ALL_USERS": "true"}, clear=False):
        os.environ.pop("NOSTR_ALLOWED_NPUBS", None)
        a = NostrAdapter(_config())
    assert a._allow_all_npubs


# --- lenient construction + deferred validation ---------------------------

def test_missing_key_is_lenient_and_fails_connect():
    from hermes_nostr.adapter import NostrAdapter
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("NOSTR_PRIVATE_KEY", None)
        a = NostrAdapter(_config(token=""))
        assert a._keys is None  # no raise at construction
        assert asyncio.run(a.connect()) is False
    assert a.fatal_error_message and "NOSTR_PRIVATE_KEY" in a.fatal_error_message


def test_missing_relays_is_lenient_and_fails_connect():
    from hermes_nostr.adapter import NostrAdapter
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("NOSTR_RELAYS", None)
        a = NostrAdapter(_config(relays=""))
        assert a._relay_urls == []
        assert asyncio.run(a.connect()) is False
    assert a.fatal_error_message and "NOSTR_RELAYS" in a.fatal_error_message


def test_lookback_defaults_to_48h():
    from hermes_nostr.adapter import NostrAdapter, NIP59_MIN_LOOKBACK_MINUTES
    a = NostrAdapter(_config())
    assert a._lookback_seconds == NIP59_MIN_LOOKBACK_MINUTES * 60


# --- env enablement -------------------------------------------------------

def test_env_enablement_seeds_relays_and_home():
    from hermes_nostr.adapter import _env_enablement
    env = {
        "NOSTR_PRIVATE_KEY": "a" * 64,
        "NOSTR_RELAYS": "wss://relay.example.com",
        "NOSTR_BOT_NAME": "TestBot",
        "NOSTR_HOME_CHANNEL": "a" * 64,
    }
    with patch.dict(os.environ, env, clear=False):
        seed = _env_enablement()
    assert seed["relays"] == "wss://relay.example.com"
    assert seed["name"] == "TestBot"
    assert "home_channel" in seed and seed["home_channel"]["chat_id"]


def test_env_enablement_returns_none_without_required():
    from hermes_nostr.adapter import _env_enablement
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("NOSTR_PRIVATE_KEY", None)
        os.environ.pop("NOSTR_RELAYS", None)
        assert _env_enablement() is None


# --- watchdog hardening backport (stall detection + flapping escalation) ---

def _adapter():
    from hermes_nostr.adapter import NostrAdapter
    with patch.dict(os.environ, {"NOSTR_ALLOWED_NPUBS": "a" * 64}, clear=False):
        return NostrAdapter(_config())


def _run(coro):
    return asyncio.run(coro)


def test_handle_msg_refreshes_activity_heartbeat():
    a = _adapter()
    a._last_activity_monotonic = 0.0

    async def go():
        await a.handle_msg("wss://relay.example.com", None)
        assert a._last_activity_monotonic > 0.0

    _run(go())


def test_is_loop_healthy_true_with_fresh_activity():
    import time
    a = _adapter()
    a._watchdog_stall_seconds = 60.0
    a._last_activity_monotonic = time.monotonic()  # fresh

    class FakeRelay:
        def status(self):
            return a._CONNECTABLE_RELAY_STATUSES[0]

    class FakeClient:
        async def relays(self):
            return {"r": FakeRelay()}

    a._client = FakeClient()

    async def go():
        a._notif_task = asyncio.create_task(asyncio.sleep(30))
        return await a._is_loop_healthy()

    assert _run(go()) is True


def test_is_loop_healthy_flags_stalled_silent_loop():
    import time
    a = _adapter()
    a._watchdog_stall_seconds = 60.0
    a._last_activity_monotonic = time.monotonic() - 120.0  # stale -> hung

    class FakeRelay:
        def status(self):
            return a._CONNECTABLE_RELAY_STATUSES[0]

    class FakeClient:
        async def relays(self):
            return {"r": FakeRelay()}

    a._client = FakeClient()

    async def go():
        a._notif_task = asyncio.create_task(asyncio.sleep(30))
        return await a._is_loop_healthy()

    assert _run(go()) is False


def test_attempt_recovery_escalates_to_full_rebuild_on_flap():
    import time
    a = _adapter()
    a._running = True
    a._watchdog_recovery_delays_initial = (0.0,)
    a._watchdog_recovery_delay_steady = 0.0
    a._consecutive_recoveries = 2
    a._last_recovery_ts = time.monotonic()

    calls = []

    async def soft():
        calls.append("soft")

    async def full():
        calls.append("full")

    async def healthy():
        return True

    a._soft_reconnect_cycle = soft
    a._full_reconnect_cycle = full
    a._is_loop_healthy = healthy

    _run(a._attempt_recovery())

    assert calls == ["full"], f"expected escalation to full rebuild, got {calls}"


def test_attempt_recovery_uses_soft_cycle_on_fresh_failure():
    import time
    a = _adapter()
    a._running = True
    a._watchdog_recovery_delays_initial = (0.0,)
    a._watchdog_recovery_delay_steady = 0.0
    a._consecutive_recoveries = 0
    a._last_recovery_ts = 0.0

    calls = []

    async def soft():
        calls.append("soft")

    async def full():
        calls.append("full")

    async def healthy():
        return True

    a._soft_reconnect_cycle = soft
    a._full_reconnect_cycle = full
    a._is_loop_healthy = healthy

    _run(a._attempt_recovery())

    assert calls == ["soft"], f"expected soft cycle on fresh failure, got {calls}"
