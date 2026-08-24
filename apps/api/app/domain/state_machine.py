"""Transaction state machine (PRD §15).

The financial pipeline is a strict, deterministic state machine enforced at
the service layer. No payment call is permitted unless the transaction has
reached AUTHORIZED and the transition path proves it.
"""

from app.domain.enums import TransactionStatus

S = TransactionStatus

ALLOWED_TRANSITIONS: dict[TransactionStatus, frozenset[TransactionStatus]] = {
    S.DISCOVERED: frozenset({S.PRODUCT_SELECTED}),
    S.PRODUCT_SELECTED: frozenset({S.QUOTE_CREATED}),
    S.QUOTE_CREATED: frozenset({S.POLICY_EVALUATED}),
    S.POLICY_EVALUATED: frozenset({S.AUTHORIZED, S.BLOCKED}),
    S.AUTHORIZED: frozenset({S.CART_CREATED}),
    S.BLOCKED: frozenset(),  # terminal for V0
    S.CART_CREATED: frozenset({S.PAYMENT_PENDING}),
    S.PAYMENT_PENDING: frozenset({S.PAYMENT_SUCCESS, S.PAYMENT_FAILED}),
    S.PAYMENT_SUCCESS: frozenset({S.COMPLETED}),
    S.PAYMENT_FAILED: frozenset(),
    S.COMPLETED: frozenset(),
}


class InvalidTransition(Exception):
    def __init__(self, current: TransactionStatus, target: TransactionStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid transaction transition {current.value} -> {target.value}"
        )


def can_transition(current: TransactionStatus, target: TransactionStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def validate_transition(current: TransactionStatus, target: TransactionStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)


def is_terminal(status: TransactionStatus) -> bool:
    return len(ALLOWED_TRANSITIONS.get(status, frozenset())) == 0
