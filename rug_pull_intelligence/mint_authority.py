"""
Pure logic for interpreting a Solana RPC getAccountInfo (jsonParsed)
response for an SPL Token mint account - no I/O, no network.

Why this is more trustworthy than a third-party security API (see
ADR-027): mint authority and freeze authority are literal fields
encoded directly in the token mint's on-chain account data. Reading
them via Solana's own standard JSON-RPC `getAccountInfo` with
`encoding: "jsonParsed"` means the RPC node parses the raw account
bytes using the SPL Token program's own well-known, stable account
layout - this is core, standardized Solana RPC behavior, not a vendor-
specific API surface that can silently change field names or require a
paid tier. Any Solana RPC provider (Helius, or otherwise) returns the
same shape, because it's the protocol's own account format being
decoded, not an opinion about it.

Response shape (stable, well-established Solana RPC behavior):

    {
      "result": {
        "value": {
          "data": {
            "parsed": {
              "info": {
                "mintAuthority": "<pubkey>" | absent/None if renounced,
                "freezeAuthority": "<pubkey>" | absent/None if renounced,
                "decimals": int,
                "supply": "<string>",
                "isInitialized": bool
              },
              "type": "mint"
            }
          }
        }
      }
    }

A KEY interpretation point: the ABSENCE of mintAuthority/freezeAuthority
(or an explicit null) means that authority has been permanently
renounced - this is GOOD for token safety (supply can never be
inflated, accounts can never be frozen). PRESENCE of a non-null
authority pubkey means that power still exists and could be exercised -
this is the risk signal.
"""

from __future__ import annotations


def parse_mint_authority_flags(
    rpc_response: dict,
) -> tuple[bool | None, bool | None]:
    """
    Returns (mint_authority_active, freeze_authority_active).

    Both None if the response doesn't have the expected shape at all
    (e.g. the address wasn't actually a token mint, or the RPC call
    itself failed upstream and returned an error response) - this is a
    "we genuinely don't know" case, handled the same way as every other
    missing-signal case in this project: contributes nothing, recorded
    as a gap by the caller, never assumed to be the worst case.
    """
    try:
        info = rpc_response["result"]["value"]["data"]["parsed"]["info"]
    except (KeyError, TypeError):
        return None, None

    mint_authority = info.get("mintAuthority")
    freeze_authority = info.get("freezeAuthority")

    mint_active = mint_authority is not None
    freeze_active = freeze_authority is not None

    return mint_active, freeze_active
