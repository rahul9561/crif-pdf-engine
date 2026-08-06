"""
pdf_engine.parser
==================

Parses a raw CRIF Highmark credit report JSON payload into a normalized,
strongly-typed internal model built from :mod:`dataclasses`.

The raw payload is inconsistent by nature: most fields are optional,
several numeric values arrive as strings (sometimes empty strings instead
of ``"0"``), some structures are inconsistently typed across records (for
example ``security_details`` is sometimes a nested object and sometimes an
empty string), and a handful of sub-sections use bureau-specific,
pipe-delimited micro-formats (payment history, score trend, score
factors). This module is the single place responsible for absorbing that
inconsistency so that every downstream consumer (section builders,
renderer, generator) can rely on a clean, predictable, always-present
shape.

Nothing in this module raises for malformed or missing input. Every
extraction method degrades to a safe default (``None``, ``""``, or an
empty list) and logs a warning/debug message describing what was missing
or unexpected, so callers can render a best-effort report rather than
crash on a single bad field.

Typical usage::

    from pdf_engine.parser import parse_credit_report

    report = parse_credit_report(raw_json)
    print(report.customer_identity.name)
    print(len(report.accounts))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CustomerIdentity",
    "Score",
    "ScoreTrendPoint",
    "ScoreTrend",
    "DerivedAttributes",
    "AccountsSummary",
    "AccountSummary",
    "PersonalVariationEntry",
    "PersonalInfoVariations",
    "EmploymentDetails",
    "SecurityDetail",
    "PaymentHistoryEntry",
    "LoanAccount",
    "InquiryRecord",
    "CreditReport",
    "CrifParser",
    "parse_credit_report",
]

# A raw, not-yet-normalized JSON object. Used purely for readability in
# method signatures below.
RawMapping = dict[str, Any]

# CRIF reports all dates in DD-MM-YYYY form throughout the payload.
DEFAULT_DATE_FORMAT = "%d-%m-%Y"

# Maps the three-letter month abbreviations used inside
# `combined_payment_history` tokens (e.g. "Apr:2026,000/XXX") to their
# calendar month number.
MONTH_ABBR_TO_NUM: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# `personal_info_variation` splits identity-document variations across
# several bureau-specific keys. The printed CRIF report merges all of them
# into a single "ID Variations" table distinguished by a Type column, so
# the parser mirrors that here. Values are the human-readable labels used
# for the merged `id_type` field.
ID_VARIATION_CATEGORIES: dict[str, str] = {
    "pan_variations": "PAN",
    "uid_variations": "UID",
    "other_id_variations": "Other ID",
    "driving_license_variations": "Driving Licence",
    "voter_id_variations": "Voter ID",
    "passport_variations": "Passport",
    "ration_card_variations": "Ration Card",
}


# ---------------------------------------------------------------------------
# Primitive coercion helpers
#
# Small, reusable, side-effect-free functions used throughout the parser to
# safely convert raw JSON values (which are frequently strings, sometimes
# empty strings, occasionally absent) into typed Python values. None of
# these ever raise; unparsable input becomes None (or "" / []) and is
# logged.
# ---------------------------------------------------------------------------


def _to_str(value: Any) -> str:
    """Coerces a raw value to a stripped string, treating ``None`` as ``""``."""
    if value is None:
        return ""
    return str(value).strip()


def _to_decimal(value: Any) -> Decimal | None:
    """
    Safely coerces a raw bureau value into a :class:`Decimal`.

    Returns ``None`` for ``None`` and empty/whitespace-only strings. Comma
    thousands-separators are tolerated defensively even though the raw
    CRIF payload does not appear to use them. Values that cannot be parsed
    as a number are logged as a warning and also yield ``None``, since
    that indicates unexpected upstream data rather than an expected
    absence.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        logger.warning("Could not parse %r as a decimal number", value)
        return None


def _to_int(value: Any) -> int | None:
    """
    Safely coerces a raw bureau value into an ``int``.

    Delegates to :func:`_to_decimal` first so that values reported with a
    trailing ``.0`` (observed on some count fields) are tolerated. Returns
    ``None`` for absent or unparsable values instead of raising.
    """
    decimal_value = _to_decimal(value)
    if decimal_value is None:
        return None
    try:
        return int(decimal_value)
    except (ValueError, InvalidOperation, OverflowError):
        logger.warning("Could not convert %r to int", value)
        return None


def _parse_date(value: Any, date_format: str = DEFAULT_DATE_FORMAT) -> date | None:
    """
    Safely parses a CRIF date string (default format ``DD-MM-YYYY``).

    Returns ``None`` for empty/missing values and for values that fail to
    parse. Parse failures are logged at debug level rather than warning,
    since blank or placeholder date fields are common and expected
    throughout the payload.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, date_format).date()
    except ValueError:
        logger.debug("Could not parse %r as a date using format %r", value, date_format)
        return None


def _split_pipe(value: Any) -> list[str]:
    """
    Splits one of CRIF's pipe-delimited strings (e.g. ``"SF03|SF11|"``)
    into a list of non-empty, stripped tokens.

    Use this for token lists where each token is independently meaningful
    and blank tokens carry no information (e.g. ``score_factors``, merged
    ID-type variation lists). For parallel arrays where positional
    alignment between siblings matters, use :func:`_split_pipe_positional`
    instead.
    """
    if not value or not isinstance(value, str):
        return []
    return [token.strip() for token in value.split("|") if token.strip()]


def _split_pipe_positional(value: Any) -> list[str]:
    """
    Splits a pipe-delimited string into tokens while preserving positional
    alignment with parallel sibling arrays (e.g. ``trends.dates`` /
    ``trends.values`` / ``trends.description``, which must stay index
    for index in sync).

    Only a single trailing empty token produced by a trailing delimiter
    (e.g. ``"774|800|796|"``) is dropped; internal empty tokens are kept
    so that index ``i`` still refers to the same sample across all three
    arrays.
    """
    if not value or not isinstance(value, str):
        return []
    tokens = value.split("|")
    if tokens and tokens[-1] == "":
        tokens = tokens[:-1]
    return [token.strip() for token in tokens]


def _first_present(node: RawMapping, *keys: str) -> Any:
    """Returns the first non-empty value found in ``node`` among ``keys``, or ``None``."""
    for key in keys:
        value = node.get(key)
        if value not in (None, ""):
            return value
    return None


# ---------------------------------------------------------------------------
# Normalized data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CustomerIdentity:
    """Applicant identity fields, from ``credit_report.customer_identity``."""

    name: str = ""
    dob: date | None = None
    pan: str = ""
    uid: str = ""
    email: str = ""
    address: str = ""
    phone: str = ""


@dataclass(frozen=True)
class Score:
    """A single bureau score, from ``credit_report.scores``."""

    score_type: str = ""
    score_value: int | None = None
    score_factors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScoreTrendPoint:
    """One historical sample from the score trend series."""

    as_of: date | None = None
    value: int | None = None
    description: str = ""


@dataclass(frozen=True)
class ScoreTrend:
    """
    The full historical score trend series, from ``credit_report.trends``,
    in the order reported by the bureau.
    """

    name: str = ""
    points: list[ScoreTrendPoint] = field(default_factory=list)


@dataclass(frozen=True)
class DerivedAttributes:
    """Bureau-derived credit history attributes ("Perform Attributes")."""

    inquiries_in_last_six_months: int | None = None
    length_of_credit_history_year: int | None = None
    length_of_credit_history_month: int | None = None
    average_account_age_year: int | None = None
    average_account_age_month: int | None = None
    new_accounts_in_last_six_months: int | None = None
    new_delinq_account_in_last_six_months: int | None = None


@dataclass(frozen=True)
class AccountsSummary:
    """
    Shared shape for both the primary and secondary account summary
    blocks (``primary_accounts_summary`` / ``secondary_accounts_summary``).
    """

    number_of_accounts: int | None = None
    active_number_of_accounts: int | None = None
    overdue_number_of_accounts: int | None = None
    secured_number_of_accounts: Decimal | None = None
    unsecured_number_of_accounts: int | None = None
    untagged_number_of_accounts: int | None = None
    current_balance: Decimal | None = None
    sanctioned_amount: Decimal | None = None
    disbursed_amount: Decimal | None = None


@dataclass(frozen=True)
class AccountSummary:
    """The complete ``credit_report.account_summary`` block."""

    derived_attributes: DerivedAttributes = field(default_factory=DerivedAttributes)
    primary: AccountsSummary = field(default_factory=AccountsSummary)
    secondary: AccountsSummary = field(default_factory=AccountsSummary)


@dataclass(frozen=True)
class PersonalVariationEntry:
    """One reported value for a personal-information variation category."""

    value: str = ""
    reported_date: date | None = None
    # Populated only for entries in PersonalInfoVariations.id_variations,
    # where it distinguishes which identity document (PAN, UID, ...) this
    # entry belongs to.
    id_type: str = ""


@dataclass(frozen=True)
class PersonalInfoVariations:
    """
    All personal-information variation categories reported by the bureau,
    from ``credit_report.personal_info_variation``.

    ``id_variations`` merges every identity-document category (PAN, UID,
    voter ID, passport, driving licence, ration card, other) into a single
    list, matching how the printed CRIF report presents one unified
    "ID Variations" table with a Type column rather than one table per
    document kind.
    """

    name_variations: list[PersonalVariationEntry] = field(default_factory=list)
    address_variations: list[PersonalVariationEntry] = field(default_factory=list)
    phone_variations: list[PersonalVariationEntry] = field(default_factory=list)
    email_variations: list[PersonalVariationEntry] = field(default_factory=list)
    date_of_birth_variations: list[PersonalVariationEntry] = field(default_factory=list)
    id_variations: list[PersonalVariationEntry] = field(default_factory=list)


@dataclass(frozen=True)
class EmploymentDetails:
    """From ``credit_report.employment_details``."""

    acct_type: str = ""
    date_reported: date | None = None
    occupation: str = ""


@dataclass(frozen=True)
class SecurityDetail:
    """One collateral/security record attached to a loan account."""

    security_type: str = ""
    owner_name: str = ""
    security_value: Decimal | None = None
    date_of_value: date | None = None
    security_charge: str = ""
    property_address: str = ""
    automobile_type: str = ""
    year_of_manufacture: str = ""
    registration_number: str = ""
    engine_number: str = ""
    chassis_number: str = ""


@dataclass(frozen=True)
class PaymentHistoryEntry:
    """
    One monthly entry parsed out of an account's
    ``combined_payment_history`` string.
    """

    month: int  # 1-12
    year: int
    days_past_due: str  # kept as text: the bureau uses non-numeric codes too
    asset_classification: str  # e.g. "STD", "XXX"


@dataclass(frozen=True)
class LoanAccount:
    """A single tradeline reported against the applicant."""

    acct_number: str = ""
    credit_guarantor: str = ""
    credit_grantor_group: str = ""
    credit_grantor_type: str = ""
    acct_type: str = ""
    date_reported: date | None = None
    ownership_ind: str = ""
    account_status: str = ""
    disbursed_amt: Decimal | None = None
    disbursed_dt: date | None = None
    last_payment_date: date | None = None
    closed_date: date | None = None
    installment_amt: str = ""
    overdue_amt: Decimal | None = None
    write_off_amt: Decimal | None = None
    principal_write_off_amt: Decimal | None = None
    settlement_amt: Decimal | None = None
    current_bal: Decimal | None = None
    matched_type: str = ""
    linked_accounts: str = ""
    security_status: str = ""
    account_remarks: str = ""
    acct_in_dispute: str = ""
    suit_filed_wilful_default_status: str = ""
    written_off_settled_status: str = ""
    write_off_dt: date | None = None
    suit_filed_dt: date | None = None
    last_paid_amount: Decimal | None = None
    obligation: Decimal | None = None
    original_term: int | None = None
    term_to_maturity: int | None = None
    actual_payment: Decimal | None = None
    repayment_tenure: int | None = None
    interest_rate: Decimal | None = None
    credit_limit: Decimal | None = None
    cash_limit: Decimal | None = None
    occupation: str = ""
    income_frequency: str = ""
    income_amount: Decimal | None = None
    payment_history: list[PaymentHistoryEntry] = field(default_factory=list)
    security_details: list[SecurityDetail] = field(default_factory=list)


@dataclass(frozen=True)
class InquiryRecord:
    """One credit inquiry record, from ``credit_report.inquiry_history``."""

    credit_grantor: str = ""
    inquiry_type: str = ""
    date_of_inquiry: date | None = None
    account_type: str = ""
    amount: Decimal | None = None
    remark: str = ""


@dataclass(frozen=True)
class CreditReport:
    """The complete normalized CRIF Highmark credit report."""

    customer_identity: CustomerIdentity = field(default_factory=CustomerIdentity)
    score: Score = field(default_factory=Score)
    score_trend: ScoreTrend = field(default_factory=ScoreTrend)
    account_summary: AccountSummary = field(default_factory=AccountSummary)
    personal_info_variations: PersonalInfoVariations = field(default_factory=PersonalInfoVariations)
    employment_details: EmploymentDetails = field(default_factory=EmploymentDetails)
    accounts: list[LoanAccount] = field(default_factory=list)
    inquiries: list[InquiryRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class CrifParser:
    """
    Parses raw CRIF Highmark API JSON responses into :class:`CreditReport`
    objects.

    The parser is stateless: all section-parsing logic is implemented as
    static methods operating only on their input, so individual sections
    can be tested and reused independently of a full payload. :meth:`parse`
    is the single public entrypoint.
    """

    def parse(self, raw_json: RawMapping | None) -> CreditReport:
        """
        Parses a raw CRIF Highmark API response into a normalized
        :class:`CreditReport`.

        Args:
            raw_json: The full decoded JSON response body, e.g. the object
                produced by ``json.loads(response.text)``. May be ``None``
                or malformed; this method never raises for that reason
                alone.

        Returns:
            A :class:`CreditReport` populated from whatever could be
            recovered from ``raw_json``. Every field degrades to a safe
            default rather than being omitted, so callers never need to
            guard against missing attributes.
        """
        if not isinstance(raw_json, dict):
            logger.error(
                "CRIF payload is not a JSON object (got %s); returning an empty report",
                type(raw_json).__name__,
            )
            return CreditReport()

        credit_report = self._extract_credit_report_node(raw_json)
        if credit_report is None:
            logger.error(
                "Could not locate 'data.result_json.credit_report' in payload; "
                "returning an empty report"
            )
            return CreditReport()

        return CreditReport(
            customer_identity=self._parse_customer_identity(
                credit_report.get("customer_identity") or {}
            ),
            score=self._parse_score(credit_report.get("scores") or {}),
            score_trend=self._parse_score_trend(credit_report.get("trends") or {}),
            account_summary=self._parse_account_summary(
                credit_report.get("account_summary") or {}
            ),
            personal_info_variations=self._parse_personal_info_variations(
                credit_report.get("personal_info_variation") or {}
            ),
            employment_details=self._parse_employment_details(
                credit_report.get("employment_details") or {}
            ),
            accounts=self._parse_accounts(credit_report.get("response")),
            inquiries=self._parse_inquiries(credit_report.get("inquiry_history")),
        )

    # -- payload navigation --------------------------------------------------

    @staticmethod
    def _extract_credit_report_node(raw_json: RawMapping) -> RawMapping | None:
        """
        Safely descends ``data -> result_json -> credit_report``.

        Returns ``None`` (rather than raising ``KeyError``/``TypeError``)
        if any level is missing or is not a dict, logging what went wrong
        so the cause is diagnosable from logs alone.
        """
        node: Any = raw_json
        for key in ("data", "result_json", "credit_report"):
            if not isinstance(node, dict):
                logger.warning(
                    "Expected a dict while descending to '%s', got %s", key, type(node).__name__
                )
                return None
            node = node.get(key)
        if not isinstance(node, dict):
            logger.warning("'credit_report' node is not a dict: %s", type(node).__name__)
            return None
        return node

    # -- 1. Customer Identity --------------------------------------------------

    @staticmethod
    def _parse_customer_identity(node: RawMapping) -> CustomerIdentity:
        """Parses ``credit_report.customer_identity``."""
        return CustomerIdentity(
            name=_to_str(node.get("name")),
            dob=_parse_date(node.get("dob")),
            pan=_to_str(node.get("pan")),
            uid=_to_str(node.get("uid")),
            email=_to_str(node.get("email")),
            address=_to_str(node.get("address")),
            phone=_to_str(node.get("phone")),
        )

    # -- 2. Score ---------------------------------------------------------------

    @staticmethod
    def _parse_score(node: RawMapping) -> Score:
        """Parses ``credit_report.scores``."""
        return Score(
            score_type=_to_str(node.get("score_type")),
            score_value=_to_int(node.get("score_value")),
            score_factors=_split_pipe(node.get("score_factors")),
        )

    # -- 3. Score Trend -----------------------------------------------------

    @staticmethod
    def _parse_score_trend(node: RawMapping) -> ScoreTrend:
        """
        Parses ``credit_report.trends``.

        ``dates``, ``values`` and ``description`` are parallel
        pipe-delimited strings that must stay index-aligned, so they are
        split with :func:`_split_pipe_positional` and zipped by position
        rather than independently filtered.
        """
        dates = _split_pipe_positional(node.get("dates"))
        values = _split_pipe_positional(node.get("values"))
        descriptions = _split_pipe_positional(node.get("description"))

        if len(dates) != len(values):
            logger.warning(
                "Score trend 'dates' and 'values' length mismatch: %d vs %d",
                len(dates),
                len(values),
            )

        points: list[ScoreTrendPoint] = []
        for index, raw_date in enumerate(dates):
            raw_value = values[index] if index < len(values) else None
            raw_description = descriptions[index] if index < len(descriptions) else ""
            points.append(
                ScoreTrendPoint(
                    as_of=_parse_date(raw_date),
                    value=_to_int(raw_value),
                    description=_to_str(raw_description),
                )
            )

        return ScoreTrend(name=_to_str(node.get("name")), points=points)

    # -- 4. Account Summary -------------------------------------------------

    @staticmethod
    def _parse_derived_attributes(node: RawMapping) -> DerivedAttributes:
        """Parses ``credit_report.account_summary.derived_attributes``."""
        return DerivedAttributes(
            inquiries_in_last_six_months=_to_int(node.get("inquiries_in_last_six_months")),
            length_of_credit_history_year=_to_int(node.get("length_of_credit_history_year")),
            length_of_credit_history_month=_to_int(node.get("length_of_credit_history_month")),
            average_account_age_year=_to_int(node.get("average_account_age_year")),
            average_account_age_month=_to_int(node.get("average_account_age_month")),
            new_accounts_in_last_six_months=_to_int(node.get("new_accounts_in_last_six_months")),
            new_delinq_account_in_last_six_months=_to_int(
                node.get("new_delinq_account_in_last_six_months")
            ),
        )

    @staticmethod
    def _parse_accounts_summary_block(node: RawMapping, prefix: str) -> AccountsSummary:
        """
        Parses a primary/secondary account summary block.

        Both ``primary_accounts_summary`` and ``secondary_accounts_summary``
        share an identical shape, differing only by a ``primary_`` /
        ``secondary_`` key prefix, so one generic method handles both.
        """

        def get(suffix: str) -> Any:
            return node.get(f"{prefix}{suffix}")

        return AccountsSummary(
            number_of_accounts=_to_int(get("number_of_accounts")),
            active_number_of_accounts=_to_int(get("active_number_of_accounts")),
            overdue_number_of_accounts=_to_int(get("overdue_number_of_accounts")),
            # Observed as either an integer-like string ("10") or a
            # float-like string ("0.0") depending on primary/secondary,
            # hence Decimal rather than int.
            secured_number_of_accounts=_to_decimal(get("secured_number_of_accounts")),
            unsecured_number_of_accounts=_to_int(get("unsecured_number_of_accounts")),
            untagged_number_of_accounts=_to_int(get("untagged_number_of_accounts")),
            current_balance=_to_decimal(get("current_balance")),
            sanctioned_amount=_to_decimal(get("sanctioned_amount")),
            disbursed_amount=_to_decimal(get("disbursed_amount")),
        )

    @staticmethod
    def _parse_account_summary(node: RawMapping) -> AccountSummary:
        """Parses ``credit_report.account_summary``."""
        return AccountSummary(
            derived_attributes=CrifParser._parse_derived_attributes(
                node.get("derived_attributes") or {}
            ),
            primary=CrifParser._parse_accounts_summary_block(
                node.get("primary_accounts_summary") or {}, "primary_"
            ),
            secondary=CrifParser._parse_accounts_summary_block(
                node.get("secondary_accounts_summary") or {}, "secondary_"
            ),
        )

    # -- 5. Personal Information Variations ----------------------------------

    @staticmethod
    def _parse_variation_entries(node: RawMapping) -> list[PersonalVariationEntry]:
        """
        Parses one ``{"variation": [{"value": ..., "reported_date": ...}]}``
        block, shared by every variation category.
        """
        raw_list = node.get("variation") if isinstance(node, dict) else None
        if not isinstance(raw_list, list):
            return []

        entries: list[PersonalVariationEntry] = []
        for item in raw_list:
            if not isinstance(item, dict):
                logger.warning("Skipping non-dict variation entry: %s", type(item).__name__)
                continue
            entries.append(
                PersonalVariationEntry(
                    value=_to_str(item.get("value")),
                    reported_date=_parse_date(item.get("reported_date")),
                )
            )
        return entries

    @staticmethod
    def _parse_personal_info_variations(node: RawMapping) -> PersonalInfoVariations:
        """
        Parses ``credit_report.personal_info_variation``, merging every
        identity-document category into ``id_variations`` (see
        :data:`ID_VARIATION_CATEGORIES`).
        """
        id_variations: list[PersonalVariationEntry] = []
        for category_key, label in ID_VARIATION_CATEGORIES.items():
            for entry in CrifParser._parse_variation_entries(node.get(category_key) or {}):
                id_variations.append(
                    PersonalVariationEntry(
                        value=entry.value,
                        reported_date=entry.reported_date,
                        id_type=label,
                    )
                )

        return PersonalInfoVariations(
            name_variations=CrifParser._parse_variation_entries(node.get("name_variations") or {}),
            address_variations=CrifParser._parse_variation_entries(
                node.get("address_variations") or {}
            ),
            phone_variations=CrifParser._parse_variation_entries(
                node.get("phone_number_variations") or {}
            ),
            email_variations=CrifParser._parse_variation_entries(node.get("email_variations") or {}),
            date_of_birth_variations=CrifParser._parse_variation_entries(
                node.get("date_of_birth_variations") or {}
            ),
            id_variations=id_variations,
        )

    # -- 6. Employment Details ------------------------------------------------

    @staticmethod
    def _parse_employment_details(node: RawMapping) -> EmploymentDetails:
        """Parses ``credit_report.employment_details``."""
        return EmploymentDetails(
            acct_type=_to_str(node.get("acct_type")),
            date_reported=_parse_date(node.get("date_reported")),
            occupation=_to_str(node.get("occupation")),
        )

    # -- 7. Loan Accounts (+ 8. Payment History, 9. Security Details) -------

    @staticmethod
    def _parse_payment_history(raw: Any) -> list[PaymentHistoryEntry]:
        """
        Parses an account's ``combined_payment_history`` micro-format into
        structured entries.

        Expected token format: ``"Mon:YYYY,DPD/STATUS"``, with tokens
        separated by ``|``, e.g. ``"Apr:2026,000/XXX|Mar:2026,000/XXX|"``.
        Individual malformed tokens are skipped (and logged) rather than
        discarding the whole account's history.
        """
        if not raw or not isinstance(raw, str):
            return []

        entries: list[PaymentHistoryEntry] = []
        for token in _split_pipe(raw):
            try:
                period_part, status_part = token.split(",", 1)
                month_abbr, year_str = period_part.split(":", 1)
                days_past_due, _, classification = status_part.partition("/")
            except ValueError:
                logger.warning("Skipping malformed payment history token: %r", token)
                continue

            month_num = MONTH_ABBR_TO_NUM.get(month_abbr.strip().lower())
            year = _to_int(year_str)
            if month_num is None or year is None:
                logger.warning(
                    "Skipping payment history token with unrecognized month/year: %r", token
                )
                continue

            entries.append(
                PaymentHistoryEntry(
                    month=month_num,
                    year=year,
                    days_past_due=days_past_due.strip(),
                    asset_classification=classification.strip(),
                )
            )
        return entries

    @staticmethod
    def _parse_single_security_detail(node: RawMapping) -> SecurityDetail:
        """Parses one flattened ``SECURITY-DETAIL`` object."""
        return SecurityDetail(
            security_type=_to_str(node.get("SECURITY-TYPE")),
            owner_name=_to_str(node.get("OWNER-NAME")),
            security_value=_to_decimal(node.get("SECURITY-VALUE")),
            date_of_value=_parse_date(node.get("DATE-OF-VALUE")),
            security_charge=_to_str(node.get("SECURITY-CHARGE")),
            property_address=_to_str(node.get("PROPERTY-ADDRESS")),
            automobile_type=_to_str(node.get("AUTOMOBILE-TYPE")),
            year_of_manufacture=_to_str(node.get("YEAR-OF-MANUFACTURE")),
            registration_number=_to_str(node.get("REGISTRATION-NUMBER")),
            engine_number=_to_str(node.get("ENGINE-NUMBER")),
            chassis_number=_to_str(node.get("CHASSIS-NUMBER")),
        )

    @staticmethod
    def _parse_security_details(raw: Any) -> list[SecurityDetail]:
        """
        Normalizes the ``security_details`` field on a raw account record.

        CRIF represents this field inconsistently: it is an empty string
        when no collateral is attached, and otherwise a dict shaped like
        ``{"SECURITY-DETAIL": {...}}`` (observed as a single object;
        handled defensively as a possible list of objects too, in case a
        multi-collateral account is ever encountered). This method accepts
        all of these shapes and always returns a list, empty when there is
        nothing to report.
        """
        if raw is None:
            return []

        if isinstance(raw, str):
            if raw.strip():
                logger.warning("Unexpected non-empty string security_details value: %r", raw)
            return []

        if isinstance(raw, list):
            details: list[SecurityDetail] = []
            for item in raw:
                details.extend(CrifParser._parse_security_details(item))
            return details

        if isinstance(raw, dict):
            node = raw.get("SECURITY-DETAIL", raw)
            if isinstance(node, dict):
                return [CrifParser._parse_single_security_detail(node)]
            if isinstance(node, list):
                return [
                    CrifParser._parse_single_security_detail(item)
                    for item in node
                    if isinstance(item, dict)
                ]
            logger.warning("Unexpected SECURITY-DETAIL value shape: %s", type(node).__name__)
            return []

        logger.warning("Unexpected security_details type: %s", type(raw).__name__)
        return []

    @staticmethod
    def _parse_single_account(raw: RawMapping) -> LoanAccount:
        """Parses one raw tradeline object from ``credit_report.response``."""
        return LoanAccount(
            acct_number=_to_str(raw.get("acct_number")),
            credit_guarantor=_to_str(raw.get("credit_guarantor")),
            credit_grantor_group=_to_str(raw.get("credit_grantor_group")),
            credit_grantor_type=_to_str(raw.get("credit_grantor_type")),
            acct_type=_to_str(raw.get("acct_type")),
            date_reported=_parse_date(raw.get("date_reported")),
            ownership_ind=_to_str(raw.get("ownership_ind")),
            account_status=_to_str(raw.get("account_status")),
            disbursed_amt=_to_decimal(raw.get("disbursed_amt")),
            disbursed_dt=_parse_date(raw.get("disbursed_dt")),
            last_payment_date=_parse_date(raw.get("last_payment_date")),
            closed_date=_parse_date(raw.get("closed_date")),
            installment_amt=_to_str(raw.get("installment_amt")),
            overdue_amt=_to_decimal(raw.get("overdue_amt")),
            write_off_amt=_to_decimal(raw.get("write_off_amt")),
            principal_write_off_amt=_to_decimal(raw.get("principal_write_off_amt")),
            settlement_amt=_to_decimal(raw.get("settlement_amt")),
            current_bal=_to_decimal(raw.get("current_bal")),
            matched_type=_to_str(raw.get("matched_type")),
            linked_accounts=_to_str(raw.get("linked_accounts")),
            security_status=_to_str(raw.get("security_status")),
            account_remarks=_to_str(raw.get("account_remarks")),
            acct_in_dispute=_to_str(raw.get("acct_in_dispute")),
            suit_filed_wilful_default_status=_to_str(raw.get("suit_filed_wilful_default_status")),
            written_off_settled_status=_to_str(raw.get("written_off_settled_status")),
            write_off_dt=_parse_date(raw.get("write_off_dt")),
            suit_filed_dt=_parse_date(raw.get("suit_filed_dt")),
            last_paid_amount=_to_decimal(raw.get("last_paid_amount")),
            obligation=_to_decimal(raw.get("obligation")),
            original_term=_to_int(raw.get("original_term")),
            term_to_maturity=_to_int(raw.get("term_to_maturity")),
            actual_payment=_to_decimal(raw.get("actual_payment")),
            repayment_tenure=_to_int(raw.get("repayment_tenure")),
            interest_rate=_to_decimal(raw.get("interest_rate")),
            credit_limit=_to_decimal(raw.get("credit_limit")),
            cash_limit=_to_decimal(raw.get("cash_limit")),
            occupation=_to_str(raw.get("occupation")),
            income_frequency=_to_str(raw.get("income_frequency")),
            income_amount=_to_decimal(raw.get("income_amount")),
            payment_history=CrifParser._parse_payment_history(raw.get("combined_payment_history")),
            security_details=CrifParser._parse_security_details(raw.get("security_details")),
        )

    @staticmethod
    def _parse_accounts(raw_accounts: Any) -> list[LoanAccount]:
        """
        Parses ``credit_report.response``, the (unbounded) list of loan
        accounts.

        A single malformed account entry is logged and skipped rather than
        aborting the parse of the entire report.
        """
        if not isinstance(raw_accounts, list):
            if raw_accounts not in (None, []):
                logger.warning(
                    "Expected a list for accounts ('response'), got %s",
                    type(raw_accounts).__name__,
                )
            return []

        accounts: list[LoanAccount] = []
        for index, raw_account in enumerate(raw_accounts):
            if not isinstance(raw_account, dict):
                logger.warning(
                    "Skipping non-dict account entry at index %d: %s",
                    index,
                    type(raw_account).__name__,
                )
                continue
            try:
                accounts.append(CrifParser._parse_single_account(raw_account))
            except Exception:
                # Defensive boundary: one malformed tradeline must not sink
                # parsing of the rest of the report.
                logger.exception(
                    "Failed to parse account at index %d (acct_number=%r); skipping",
                    index,
                    raw_account.get("acct_number"),
                )
        return accounts

    # -- 10. Enquiry History --------------------------------------------------

    @staticmethod
    def _parse_single_inquiry(raw: RawMapping) -> InquiryRecord:
        """
        Parses one raw inquiry record.

        Note: no populated sample of ``inquiry_history.history`` was
        available in the reference payload used to build this parser (the
        list is empty there), so field-name resolution below uses a
        best-effort set of key aliases inferred from the printed report's
        column headings (Credit Grantor, Type, Date of Inquiry, Account
        Type, Amount, Remark). Revisit this method's key aliases once a
        populated sample is available.
        """
        return InquiryRecord(
            credit_grantor=_to_str(
                _first_present(raw, "credit_grantor", "credit_guarantor", "grantor")
            ),
            inquiry_type=_to_str(_first_present(raw, "type", "inquiry_type", "credit_grantor_type")),
            date_of_inquiry=_parse_date(
                _first_present(raw, "date_of_inquiry", "inquiry_date", "date")
            ),
            account_type=_to_str(_first_present(raw, "account_type", "acct_type")),
            amount=_to_decimal(_first_present(raw, "amount", "inquiry_amount")),
            remark=_to_str(_first_present(raw, "remark", "remarks")),
        )

    @staticmethod
    def _parse_inquiries(raw: Any) -> list[InquiryRecord]:
        """
        Parses ``credit_report.inquiry_history``.

        Accepts either the raw ``{"history": [...]}`` wrapper or an
        already-unwrapped list, for resilience against minor payload shape
        changes.
        """
        history: Any = raw.get("history") if isinstance(raw, dict) else raw
        if not isinstance(history, list):
            if history not in (None, []):
                logger.warning(
                    "Expected a list for inquiry history, got %s", type(history).__name__
                )
            return []

        inquiries: list[InquiryRecord] = []
        for index, item in enumerate(history):
            if not isinstance(item, dict):
                logger.warning(
                    "Skipping non-dict inquiry entry at index %d: %s", index, type(item).__name__
                )
                continue
            try:
                inquiries.append(CrifParser._parse_single_inquiry(item))
            except Exception:
                logger.exception("Failed to parse inquiry at index %d; skipping", index)
        return inquiries


def parse_credit_report(raw_json: RawMapping | None) -> CreditReport:
    """
    Convenience wrapper around ``CrifParser().parse(raw_json)``.

    This is the intended integration point for ``generator.py`` and any
    other caller that only needs a one-shot parse without managing a
    parser instance.

    Args:
        raw_json: The full decoded CRIF Highmark API response body.

    Returns:
        A normalized :class:`CreditReport`.
    """
    return CrifParser().parse(raw_json)
