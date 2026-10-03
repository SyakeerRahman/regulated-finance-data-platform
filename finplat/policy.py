"""The internal card fraud policy, and the rule each alert breaks.

The policy is invented. A real bank's policy is confidential, and a portfolio cannot show one.
The shape is real: numbered rules, each with a plain condition an auditor can check by hand.

The citation is code, not a language model. A regulator asks "which rule did this break", and
the answer has to be the same on every replay and checkable without trusting a model. The model
writes the sentences around the rule. It never chooses the rule.

Twelve rules fit in one prompt, so there is no vector index. See
`brain/decisions/2026-10-03-the-llm-narrates-and-code-cites.md`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

POLICY_NAME = "FP-2026 Card Fraud Review Policy"

# The jurisdictions this invented bank reviews on every card-not-present payment.
HIGH_RISK_COUNTRIES = {"NG"}

SPIKE = 5.0
ELEVATED = 3.0
LARGE_AMOUNT = 500.0
NEW_ACCOUNT_TRANSACTIONS = 3
NEW_ACCOUNT_AMOUNT = 150.0


@dataclass(frozen=True)
class Rule:
    rule_id: str
    title: str
    text: str
    applies: Callable[[dict], bool] = field(repr=False, compare=False)
    # The model features this rule is about. The rule whose features pushed the score up the most
    # is the one an alert cites, so the citation agrees with the SHAP reasons on the same card.
    drivers: tuple[str, ...] = ()

    def public(self) -> dict:
        return {"rule_id": self.rule_id, "title": self.title, "text": self.text, "drivers": list(self.drivers)}


def _get(facts: dict, key: str, default=None):
    value = facts.get(key, default)
    return default if value is None else value


def _spike(facts: dict, level: float) -> bool:
    return float(_get(facts, "amount_vs_account", 0.0)) >= level


RULES = [
    Rule(
        "FP-1",
        "Spend spike",
        f"Review a payment of {SPIKE:g} or more times the account's average spend before the account "
        "makes another payment.",
        lambda f: _spike(f, SPIKE),
        ("amount_vs_account",),
    ),
    Rule(
        "FP-2",
        "Spend spike outside Malaysia",
        f"Hold the card when a payment outside Malaysia is {SPIKE:g} or more times the account's average "
        "spend. Contact the cardholder before the hold is removed.",
        lambda f: _spike(f, SPIKE) and bool(_get(f, "is_abroad", False)),
        ("amount_vs_account", "is_abroad"),
    ),
    Rule(
        "FP-3",
        "Card not present, outside Malaysia",
        "Review an online payment to a merchant outside Malaysia. Confirm that the cardholder is travelling "
        "or has bought from this merchant before.",
        lambda f: bool(_get(f, "is_online", False)) and bool(_get(f, "is_abroad", False)),
        ("is_online", "is_abroad"),
    ),
    Rule(
        "FP-4",
        "Resellable goods, card not present",
        "Review an online payment for electronics, jewellery or gaming credit. These goods are easy to "
        "resell, so stolen card details are often spent on them first.",
        lambda f: bool(_get(f, "is_online", False)) and bool(_get(f, "is_risky_category", False)),
        ("is_online", "is_risky_category"),
    ),
    Rule(
        "FP-5",
        "Night-time spend spike",
        f"Review a payment between 00:00 and 06:00 that is {ELEVATED:g} or more times the account's average spend.",
        lambda f: bool(_get(f, "is_night", False)) and _spike(f, ELEVATED),
        ("is_night", "amount_vs_account"),
    ),
    Rule(
        "FP-6",
        "High-risk jurisdiction",
        "Review every card payment to a merchant in a high-risk jurisdiction. The list is set by the compliance team.",
        lambda f: _get(f, "country") in HIGH_RISK_COUNTRIES,
        ("is_abroad",),
    ),
    Rule(
        "FP-7",
        "Large single payment",
        f"Review a single card payment of MYR {LARGE_AMOUNT:,.0f} or more, whatever the account's history.",
        lambda f: float(_get(f, "amount", 0.0)) >= LARGE_AMOUNT,
        ("amount",),
    ),
    Rule(
        "FP-8",
        "Large spend on a new account",
        f"Review a payment of MYR {NEW_ACCOUNT_AMOUNT:,.0f} or more on an account with fewer than "
        f"{NEW_ACCOUNT_TRANSACTIONS} earlier payments.",
        lambda f: (
            int(_get(f, "prior_transactions", NEW_ACCOUNT_TRANSACTIONS)) < NEW_ACCOUNT_TRANSACTIONS
            and float(_get(f, "amount", 0.0)) >= NEW_ACCOUNT_AMOUNT
        ),
        ("prior_transactions", "amount"),
    ),
    Rule(
        "FP-9",
        "Resellable goods outside Malaysia",
        "Review a payment for electronics, jewellery or gaming credit to a merchant outside Malaysia.",
        lambda f: bool(_get(f, "is_risky_category", False)) and bool(_get(f, "is_abroad", False)),
        ("is_risky_category", "is_abroad"),
    ),
    Rule(
        "FP-10",
        "Card not present at night",
        "Review an online payment made between 00:00 and 06:00.",
        lambda f: bool(_get(f, "is_night", False)) and bool(_get(f, "is_online", False)),
        ("is_night", "is_online"),
    ),
    Rule(
        "FP-11",
        "Elevated spend on resellable goods",
        f"Review a payment for electronics, jewellery or gaming credit that is {ELEVATED:g} or more times "
        "the account's average spend.",
        lambda f: bool(_get(f, "is_risky_category", False)) and _spike(f, ELEVATED),
        ("is_risky_category", "amount_vs_account"),
    ),
    Rule(
        "FP-12",
        "Model referral",
        "Review a payment that the fraud model scores at or above its live threshold, when no other rule "
        "in this policy applies.",
        lambda f: True,
    ),
]

BY_ID = {rule.rule_id: rule for rule in RULES}
FALLBACK = RULES[-1]


def cite(facts: dict, contributions: dict) -> list[Rule]:
    """Every rule this payment breaks, the one to cite first.

    `facts` holds the model features and the payment fields (country, amount). A missing fact
    makes a rule not apply, never apply by accident. Ties go to the rule with more conditions,
    then to the lower number, so the same alert always cites the same rule.
    """
    broken = [rule for rule in RULES[:-1] if rule.applies(facts)]
    if not broken:
        return [FALLBACK]

    def weight(rule: Rule) -> float:
        # Only what raised the score counts. A feature that pulled it down does not make a rule apply more.
        return sum(max(float(contributions.get(name, 0.0)), 0.0) for name in rule.drivers)

    return sorted(broken, key=lambda rule: (-weight(rule), -len(rule.drivers), RULES.index(rule)))


def as_text() -> str:
    """The whole policy, for a prompt or a reader."""
    lines = [POLICY_NAME, ""]
    lines += [f"{rule.rule_id} {rule.title}: {rule.text}" for rule in RULES]
    return "\n".join(lines)
