# hermes-nostr

A **Nostr** gateway platform plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).
It lets your Hermes agent send and receive **NIP-17 end-to-end-encrypted private
direct messages** — talk to your agent from any Nostr client (Damus, Amethyst,
Snort, …), and have cron jobs and notifications delivered to you as DMs.

This is a **standalone, drop-in plugin** — it requires **zero changes to
Hermes core**. It registers itself through Hermes's platform registry
(`register()`), the same mechanism every gateway channel uses.

## Features

- 🔒 **NIP-17 private DMs** — gift-wrapped (kind 1059), sealed, signed; the
  relay never sees plaintext or the conversation graph.
- 📇 **Profile + inbox advertisement** — publishes a Kind 0 profile (name,
  about, picture, NIP-05, LUD-16, website) and a Kind 10050 DM-inbox relay list
  on connect, so compliant clients know where to reach the bot.
- ✂️ **Long-message chunking** — splits over `MAX_MESSAGE_LENGTH` (4096),
  preserving code-fence boundaries, with `(1/N)` markers.
- ⏲️ **NIP-40 expiration** + **NIP-59 timestamp obfuscation** on outbound DMs.
- 🛟 **Self-healing relay watchdog** — supervises the notification loop and
  reconnects with backoff, matching the other persistent-loop adapters.
- ✅ **Allowlist, deny-by-default** — only npubs you list (or `*`) can talk to
  the bot. `npub` and hex forms are treated as the same identity.
- 🗓️ **Cron / notification delivery** to your `NOSTR_HOME_CHANNEL`.
- 🧩 **Setup wizard integration** — `hermes setup` walks you through key
  generation, relays, and the allowlist.

## Requirements

- A working **Hermes Agent** install (this plugin runs inside it).
- **Python ≥ 3.10**.
- **[`nostr-sdk`](https://pypi.org/project/nostr-sdk/) ≥ 0.45.0a7, < 0.46** — a
  Rust/PyO3 binding distributed as **wheels only** (no source build). Installed
  automatically with this package. Platforms without a wheel (e.g. Termux/
  Android) are unsupported.

  0.45 is required: it is the release that lets a NIP-40 expiration be set on
  the *outside* of a gift wrap, which is the only place a relay can see it.
  **Upgrading an existing install?** 0.44 is not compatible, and on 0.44 this
  adapter degrades to disabled rather than erroring — the interactive setup
  detects that case and upgrades for you.

## Installation

Install as a drop-in directory under your Hermes plugins folder:

```bash
git clone https://github.com/unsaltedbutter-ai/hermes-nostr.git
cp -r hermes-nostr/hermes_nostr ~/.hermes/plugins/nostr
# install the one runtime dependency into the interpreter Hermes runs on
# (e.g. ~/.hermes/hermes-agent/venv):
python -m pip install 'nostr-sdk>=0.45.0a7,<0.46'
```

## Enable it

Like every non-bundled plugin, it is **opt-in**:

```bash
hermes plugins enable nostr
```

…or add it to `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - nostr
```

## Configure

Run the wizard (generates/imports a key, sets relays + allowlist + home channel):

```bash
hermes setup        # choose Nostr
```

…or set environment variables in `~/.hermes/.env`:

| Variable | Required | Description |
|---|---|---|
| `NOSTR_PRIVATE_KEY` | ✅ | Bot's private key — `nsec…` bech32 or 64-char hex. **Secret.** |
| `NOSTR_RELAYS` | ✅ | Comma-separated `wss://` relay URLs (≥1). e.g. `wss://relay.damus.io,wss://nos.lol` |
| `NOSTR_ALLOWED_NPUBS` | | Comma-separated npub/hex allowed to DM the bot. `*` = open; **empty = deny all** (default). |
| `NOSTR_ALLOW_ALL_USERS` | | `true` is a synonym for `NOSTR_ALLOWED_NPUBS=*` (dev only). |
| `NOSTR_HOME_CHANNEL` | | Your npub/hex — default recipient for cron + notifications. |
| `NOSTR_HOME_CHANNEL_NAME` | | Display name for the home channel. |
| `NOSTR_BOT_NAME` | | Kind 0 display name (default: the bot's npub). |
| `NOSTR_BOT_ABOUT` | | Kind 0 bio. |
| `NOSTR_BOT_PICTURE` | | Kind 0 avatar URL. |
| `NOSTR_BOT_WEBSITE` | | NIP-24 website URL. |
| `NOSTR_NIP05` | | NIP-05 identifier, e.g. `bot@yourdomain.com`. |
| `NOSTR_LUD16` | | LUD-16 Lightning address for zaps. |
| `NOSTR_EXPIRATION_MINUTES` | | NIP-40 TTL for outbound DMs (default `10080` = 7 days). |
| `NOSTR_SEEN_MAX` | | Dedup cache size for processed events (default `1000`). |
| `NOSTR_LOOKBACK_MINUTES` | | Relay replay window on connect (default `2880` = 48h, NIP-59 minimum). |

YAML also works under `platforms.nostr.extra:` (env vars take precedence), and
`platforms.nostr.allow_from` maps to `NOSTR_ALLOWED_NPUBS`.

## Usage

1. Start the gateway: `hermes gateway start` (check `hermes gateway status` — Nostr
   should read as connected).
2. From a Nostr client whose npub is in your allowlist, **DM the bot's npub**
   (printed during setup). It replies as an encrypted DM.
3. **Cron / notifications**: jobs delivered to `nostr` land in your
   `NOSTR_HOME_CHANNEL` as a DM (e.g. *"remind me at 9am"*).

## How it works

Everything is wired through one `register(ctx)` call (see `hermes_nostr/adapter.py`):

- `adapter_factory` / `check_fn` / `is_connected` — construction + readiness.
- `env_enablement_fn` + `apply_yaml_config_fn` — config from env/YAML.
- `standalone_sender_fn` — out-of-process (cron) delivery, reusing the live
  adapter when the gateway is running.
- `cron_deliver_env_var=NOSTR_HOME_CHANNEL` — cron `deliver=nostr` support.
- `allowed_users_env` / `allow_all_env` — authorization.
- `setup_fn`, `platform_hint`, `max_message_length`, `emoji`.

The `Platform("nostr")` enum member is minted dynamically by Hermes's enum
`_missing_` hook once the plugin registers — so no core enum edit is needed.

## Limitations

- **Plain text only.** Nostr DMs have no markdown, no groups, no threads, and no
  native media uploads. Media is not sent.
- **Proactive messaging to an arbitrary recipient via the `send_message` tool is
  not supported from an external plugin yet.** Hermes core's `_parse_target_ref`
  (and the cron target parser) don't expose a hook for a plugin's custom address
  format, so an explicit `nostr:<npub>` target can't be resolved without a core
  change. **What works:** receiving DMs, all in-conversation replies, and
  delivery to your configured `NOSTR_HOME_CHANNEL`. (Tracking a small upstream
  hook — an optional `target_parse_fn` on `PlatformEntry` — that would lift this.)
- No reactions / read receipts / typing indicators (no NIP-17 equivalents; and
  public reactions would leak who you DM).

## Security

- **Deny-by-default**: an empty `NOSTR_ALLOWED_NPUBS` rejects all inbound DMs.
  Set your own npub first.
- The bot's `NOSTR_PRIVATE_KEY` is its identity — keep it secret; never paste it
  where a pubkey is expected (the setup wizard guards against this).
- The adapter holds a per-identity lock so two gateways can't drive the same key.

## Development

Run the tests inside a Hermes-enabled environment (the host provides
`gateway.*`), with `nostr-sdk` and `pytest` installed:

```bash
pip install pytest
PYTHONPATH=. pytest -q
```

The tests exercise the plugin contract (register → valid entry → factory builds
a `BasePlatformAdapter`), the deny-by-default allowlist, lenient construction +
deferred `connect()` validation, env-enablement, and lookback handling.

## License

[MIT](LICENSE) © unsaltedbutter
