import itertools

import pytest

from app.domain.enums import TransactionStatus as S
from app.domain.state_machine import (
    InvalidTransition,
    can_transition,
    is_terminal,
    validate_transition,
)


def test_happy_path_chain_is_valid():
    path = [
        S.DISCOVERED,
        S.PRODUCT_SELECTED,
        S.QUOTE_CREATED,
        S.POLICY_EVALUATED,
        S.AUTHORIZED,
        S.CART_CREATED,
        S.PAYMENT_PENDING,
        S.PAYMENT_SUCCESS,
        S.COMPLETED,
    ]
    for cur, nxt in itertools.pairwise(path):
        assert can_transition(cur, nxt), f"{cur} -> {nxt} must be allowed"


def test_blocked_path_from_policy_evaluation():
    assert can_transition(S.POLICY_EVALUATED, S.BLOCKED)
    assert is_terminal(S.BLOCKED)
    with pytest.raises(InvalidTransition):
        validate_transition(S.BLOCKED, S.AUTHORIZED)


def test_payment_never_skips_authorization():
    """Core invariant: no route from pre-authorization states to payment."""
    for state in (S.DISCOVERED, S.PRODUCT_SELECTED, S.QUOTE_CREATED, S.POLICY_EVALUATED):
        assert not can_transition(state, S.PAYMENT_PENDING)
        assert not can_transition(state, S.PAYMENT_SUCCESS)

    def path_to(status: S) -> list[S]:
        return [
            S.DISCOVERED,
            S.PRODUCT_SELECTED,
            S.QUOTE_CREATED,
            S.POLICY_EVALUATED,
            S.AUTHORIZED,
            status,
        ]

    # ...but the full authorized chain does reach payment states.
    assert can_transition(S.AUTHORIZED, S.CART_CREATED)
    assert can_transition(*path_to(S.CART_CREATED)[-2:])
    assert can_transition(S.CART_CREATED, S.PAYMENT_PENDING)


def test_terminal_states():
    for terminal in (S.BLOCKED, S.PAYMENT_FAILED, S.COMPLETED):
        assert is_terminal(terminal)
        assert not can_transition(terminal, S.DISCOVERED)


def test_invalid_jump_raises_with_detail():
    with pytest.raises(InvalidTransition) as err:
        validate_transition(S.QUOTE_CREATED, S.PAYMENT_PENDING)
    assert "QUOTE_CREATED" in str(err.value)
    assert "PAYMENT_PENDING" in str(err.value)


def test_payment_failed_is_terminal_in_v0():
    assert can_transition(S.PAYMENT_PENDING, S.PAYMENT_FAILED)
    assert is_terminal(S.PAYMENT_FAILED)
