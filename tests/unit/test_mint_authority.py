from rug_pull_intelligence.mint_authority import parse_mint_authority_flags


def make_rpc_response(mint_authority=None, freeze_authority=None, include_info=True):
    if not include_info:
        return {"result": {"value": {"data": {}}}}  # malformed/unexpected shape

    info = {
        "decimals": 9,
        "isInitialized": True,
        "supply": "1000000000",
    }
    if mint_authority is not None:
        info["mintAuthority"] = mint_authority
    if freeze_authority is not None:
        info["freezeAuthority"] = freeze_authority

    return {
        "result": {
            "value": {
                "data": {"parsed": {"info": info, "type": "mint"}},
            }
        }
    }


def test_both_authorities_active_when_both_present():
    response = make_rpc_response(
        mint_authority="SomeMintAuthorityPubkey", freeze_authority="SomeFreezePubkey"
    )
    mint_active, freeze_active = parse_mint_authority_flags(response)
    assert mint_active is True
    assert freeze_active is True


def test_both_authorities_renounced_when_both_absent():
    """The safe case: neither field present means both authorities have
    been permanently renounced - this is GOOD for token safety."""
    response = make_rpc_response(mint_authority=None, freeze_authority=None)
    mint_active, freeze_active = parse_mint_authority_flags(response)
    assert mint_active is False
    assert freeze_active is False


def test_only_mint_authority_active():
    response = make_rpc_response(mint_authority="SomeMintAuthorityPubkey")
    mint_active, freeze_active = parse_mint_authority_flags(response)
    assert mint_active is True
    assert freeze_active is False


def test_only_freeze_authority_active():
    response = make_rpc_response(freeze_authority="SomeFreezePubkey")
    mint_active, freeze_active = parse_mint_authority_flags(response)
    assert mint_active is False
    assert freeze_active is True


def test_malformed_response_returns_none_none_not_crash():
    """If the response doesn't have the expected shape at all (RPC
    error, wrong account type, etc.) - genuinely unknown, not assumed
    to be either safe or risky."""
    response = {"error": {"code": -32602, "message": "Invalid param"}}
    mint_active, freeze_active = parse_mint_authority_flags(response)
    assert mint_active is None
    assert freeze_active is None


def test_empty_dict_does_not_crash():
    mint_active, freeze_active = parse_mint_authority_flags({})
    assert mint_active is None
    assert freeze_active is None


def test_explicit_null_authority_treated_same_as_absent():
    """Some RPC responses may include the key with an explicit null
    value rather than omitting it entirely - both must mean 'renounced'."""
    response = {
        "result": {
            "value": {
                "data": {
                    "parsed": {
                        "info": {"mintAuthority": None, "freezeAuthority": None},
                        "type": "mint",
                    }
                }
            }
        }
    }
    mint_active, freeze_active = parse_mint_authority_flags(response)
    assert mint_active is False
    assert freeze_active is False
