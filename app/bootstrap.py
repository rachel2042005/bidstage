"""Process bootstrap. Must run before any HTTPS client is constructed.

ENV-1: the Netspark TLS proxy on this network re-signs api.openai.com with a
certificate OpenSSL rejects ("Missing Authority Key Identifier"). truststore
routes verification through the Windows trust store, which does trust it.
Calling this late — after a client object already exists — has no effect.
"""

from __future__ import annotations

import truststore
from dotenv import load_dotenv

_initialised = False


def init() -> None:
    """Idempotent. Safe to call from every entry point."""
    global _initialised
    if _initialised:
        return

    truststore.inject_into_ssl()
    load_dotenv()
    _initialised = True
