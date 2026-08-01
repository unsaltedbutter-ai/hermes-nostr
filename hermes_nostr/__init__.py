"""Hermes Agent — Nostr (NIP-17 private DM) gateway platform plugin.

The Hermes plugin loader imports this package and calls :func:`register`
(via the ``hermes_agent.plugins`` entry point, a ``~/.hermes/plugins/``
drop-in, or a bundled scan).
"""

from .adapter import NostrAdapter, register

__all__ = ["register", "NostrAdapter"]
__version__ = "2.0.0"
