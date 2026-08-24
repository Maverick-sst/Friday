"""Money helpers.

All amounts in the system are integers in **minor units** (paise for INR).
Formatting for humans happens only at the presentation boundary.
"""

from decimal import ROUND_HALF_UP, Decimal

_CURRENCY_SYMBOLS = {
    "INR": "\u20b9",
    "USD": "$",
    "EUR": "\u20ac",
    "GBP": "\u00a3",
}


def format_minor(amount_minor: int, currency: str = "INR") -> str:
    symbol = _CURRENCY_SYMBOLS.get(currency.upper(), "")
    major = Decimal(amount_minor) / 100
    return f"{symbol}{major:,.2f}"


def major_to_minor(amount_major: str | float | Decimal) -> int:
    d = Decimal(str(amount_major))
    return int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def minor_to_major_str(amount_minor: int) -> str:
    return str(Decimal(amount_minor) / 100)
