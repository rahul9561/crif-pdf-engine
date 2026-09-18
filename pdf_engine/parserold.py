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

Every per-field extraction method degrades to a safe default (``None``,
``""``, or an empty list) and logs a warning/debug message describing
what was missing or unexpected, so callers can render a best-effort
report rather than crash on a single bad field. The one exception is
locating the report root itself: :meth:`CrifParser.parse` raises
``ValueError`` when neither of the supported payload shapes (see
:func:`CrifParser._extract_credit_report_node`) can be found, rather than
silently returning an empty report that would render as a blank PDF.

Two raw payload shapes are supported, both rooted at ``data.result_json``:

* The current CRIF Highmark B2C API response --
  ``data.result_json.parsed_data.B2C-REPORT`` -- a deeply nested,
  ``UPPER-HYPHEN-CASE``-keyed structure. :meth:`CrifParser._adapt_b2c_report`
  and its helpers translate this into the flat shape below; no other
  method in this module needs to know this shape exists.
* A legacy flat shape -- ``data.result_json.credit_report`` -- using
  lowercase ``snake_case`` keys matching this module's internal field
  names directly (``customer_identity``, ``scores``, ``account_summary``,
  ``response``, ...). Every ``_parse_*`` method below is written against
  this shape.

Typical usage::

    from pdf_engine.parser import parse_credit_report

    report = parse_credit_report(raw_json)
    print(report.customer_identity.name)
    print(len(report.accounts))
"""

from __future__ import annotations

import logging
import re
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
    total_secured_outstanding: Decimal | None = None
    total_unsecured_outstanding: Decimal | None = None


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
    total_amt_overdue: Decimal | None = None


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
                produced by ``json.loads(response.text)``.

        Returns:
            A :class:`CreditReport` populated from whatever could be
            recovered from ``raw_json``. Every *field* on it degrades to a
            safe default rather than being omitted, so callers never need
            to guard against missing attributes.

        Raises:
            ValueError: If ``raw_json`` is not a JSON object, or if the
                credit report root cannot be located under any supported
                payload shape (see the module docstring). Raised rather
                than silently returning an empty report, since an empty
                report renders as a blank PDF with no indication anything
                went wrong.
        """
        if not isinstance(raw_json, dict):
            raise ValueError(
                f"CRIF payload must be a JSON object, got {type(raw_json).__name__}"
            )

        credit_report = self._extract_credit_report_node(raw_json)
        if credit_report is None:
            raise ValueError(
                "Unable to locate a CRIF credit report in the payload. Supported "
                "paths: 'data.result_json.parsed_data.B2C-REPORT' (current CRIF "
                "B2C Highmark format) or 'data.result_json.credit_report' "
                "(legacy flat format). Neither was found as a dict in this payload."
            )

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
        Locates the credit report root and returns it in the flat,
        ``snake_case`` shape every ``_parse_*`` method below expects.

        Two payload shapes are tried, both rooted at ``data.result_json``
        (see the module docstring for why each exists):

        1. ``data.result_json.parsed_data.B2C-REPORT`` -- the current CRIF
           Highmark B2C API response. When found, it is translated via
           :meth:`_adapt_b2c_report` before being returned, so every
           downstream ``_parse_*`` method stays unaware this shape exists.
        2. ``data.result_json.credit_report`` -- the legacy flat shape,
           returned as-is.

        Returns ``None`` (rather than raising) if ``data``/``result_json``
        themselves are missing or malformed, or if neither shape above
        yields a dict -- :meth:`parse` is responsible for turning that
        into a loud, user-facing error; this method only concerns itself
        with locating and normalizing the node, logging what it tried
        along the way so the cause is diagnosable from logs alone.
        """
        data = raw_json.get("data")
        if not isinstance(data, dict):
            logger.warning("Expected a dict at 'data', got %s", type(data).__name__)
            return None

        result_json = data.get("result_json")
        if not isinstance(result_json, dict):
            logger.warning(
                "Expected a dict at 'data.result_json', got %s", type(result_json).__name__
            )
            return None

        parsed_data = result_json.get("parsed_data")
        if isinstance(parsed_data, dict):
            b2c_report = parsed_data.get("B2C-REPORT")
            if isinstance(b2c_report, dict):
                return CrifParser._adapt_b2c_report(b2c_report)
            if b2c_report is not None:
                logger.warning(
                    "'B2C-REPORT' node is not a dict: %s", type(b2c_report).__name__
                )

        legacy_report = result_json.get("credit_report")
        if isinstance(legacy_report, dict):
            logger.info("CRIF report root found via legacy 'data.result_json.credit_report'")
            return legacy_report
        if legacy_report is not None:
            logger.warning("'credit_report' node is not a dict: %s", type(legacy_report).__name__)

        return None

    # -- B2C-REPORT structural adapter --------------------------------------
    #
    # The methods below translate the current CRIF B2C API response shape
    # (deeply nested, UPPER-HYPHEN-CASE keys) into the flat, snake_case
    # shape every _parse_* method above already expects and is tested
    # against. This is a pure structural relabeling -- renaming keys,
    # unwrapping single-element lists, merging parallel pipe-delimited
    # arrays back into the combined tokens the rest of the parser already
    # knows how to read -- and performs no type coercion, validation, or
    # defaulting of its own. Every value coercion (dates, decimals, ints)
    # still happens exactly once, in the _parse_* methods above, so this
    # adapter cannot silently diverge from their behavior.

    #: CRIF ID-document TYPE codes are not documented consistently across
    #: payloads, but PAN and Aadhaar (UID) values have fixed, unambiguous
    #: formats -- classifying by value shape is more robust than trusting
    #: an undocumented type code, and works the same for any future payload.
    _PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
    _UID_PATTERN = re.compile(r"^[0-9]{12}$")

    @staticmethod
    def _classify_id_value(value: str) -> str | None:
        """Classifies a raw ID-document value as ``"pan"``, ``"uid"``, or ``None``."""
        text = value.strip().upper()
        if CrifParser._PAN_PATTERN.match(text):
            return "pan"
        if CrifParser._UID_PATTERN.match(text):
            return "uid"
        return None

    @staticmethod
    def _adapt_applicant_identity(applicant: RawMapping) -> RawMapping:
        """Builds a flat ``customer_identity`` node from ``APPLICANT-SEGMENT``."""
        name = " ".join(
            part
            for part in (
                _to_str(applicant.get("FIRST-NAME")),
                _to_str(applicant.get("MIDDLE-NAME")),
                _to_str(applicant.get("LAST-NAME")),
            )
            if part
        )

        dob_node = applicant.get("DOB")
        dob = dob_node.get("DOB-DT") if isinstance(dob_node, dict) else None

        pan = ""
        uid = ""
        ids = applicant.get("IDS")
        if isinstance(ids, list):
            for entry in ids:
                if not isinstance(entry, dict):
                    continue
                value = _to_str(entry.get("VALUE"))
                if not value:
                    continue
                kind = CrifParser._classify_id_value(value)
                if kind == "pan" and not pan:
                    pan = value
                elif kind == "uid" and not uid:
                    uid = value

        emails = applicant.get("EMAILS")
        email = None
        if isinstance(emails, list) and emails and isinstance(emails[0], dict):
            email = emails[0].get("EMAIL")

        phones = applicant.get("PHONES")
        phone = ""
        if isinstance(phones, list):
            phone = ", ".join(
                _to_str(entry.get("VALUE"))
                for entry in phones
                if isinstance(entry, dict) and _to_str(entry.get("VALUE"))
            )

        addresses = applicant.get("ADDRESSES")
        address = ""
        if isinstance(addresses, list) and addresses and isinstance(addresses[0], dict):
            first_address = addresses[0]
            parts = [
                _to_str(first_address.get(key))
                for key in ("ADDRESSTEXT", "CITY", "STATE", "PIN", "COUNTRY")
            ]
            deduped: list[str] = []
            for part in parts:
                if part and (not deduped or deduped[-1].lower() != part.lower()):
                    deduped.append(part)
            address = ", ".join(deduped)

        return {
            "name": name,
            "dob": dob,
            "pan": pan,
            "uid": uid,
            "email": email,
            "address": address,
            "phone": phone,
        }

    @staticmethod
    def _adapt_score(scores: Any) -> RawMapping:
        """Builds a flat ``scores`` node from ``STANDARD-DATA.SCORE`` (a list; the first entry is used)."""
        if not isinstance(scores, list) or not scores or not isinstance(scores[0], dict):
            return {}
        first_score = scores[0]
        factors = first_score.get("FACTORS")
        factor_types = []
        if isinstance(factors, list):
            factor_types = [
                _to_str(factor.get("TYPE"))
                for factor in factors
                if isinstance(factor, dict) and _to_str(factor.get("TYPE"))
            ]
        return {
            "score_type": first_score.get("NAME"),
            "score_value": first_score.get("VALUE"),
            "score_factors": "|".join(factor_types),
        }

    @staticmethod
    def _adapt_trends(trends: Any) -> RawMapping:
        """Builds a flat ``trends`` node from ``REPORT-DATA.TRENDS`` (already parallel pipe strings)."""
        if not isinstance(trends, dict):
            return {}
        return {
            "name": trends.get("NAME"),
            "dates": trends.get("DATES"),
            "values": trends.get("VALUES"),
            "description": trends.get("DESCRIPTION"),
        }

    #: Target ``AccountsSummary`` field name -> source key within
    #: ``PRIMARY-ACCOUNTS-SUMMARY`` / ``SECONDARY-ACCOUNTS-SUMMARY``.
    _ACCOUNTS_SUMMARY_FIELD_MAP: dict[str, str] = {
        "number_of_accounts": "NUMBER-OF-ACCOUNTS",
        "active_number_of_accounts": "ACTIVE-ACCOUNTS",
        "overdue_number_of_accounts": "OVERDUE-ACCOUNTS",
        "secured_number_of_accounts": "SECURED-ACCOUNTS",
        "unsecured_number_of_accounts": "UNSECURED-ACCOUNTS",
        "untagged_number_of_accounts": "UNTAGGED-ACCOUNTS",
        "current_balance": "TOTAL-CURRENT-BALANCE",
        "sanctioned_amount": "TOTAL-SANCTIONED-AMT",
        "disbursed_amount": "TOTAL-DISBURSED-AMT",
        "total_amt_overdue": "TOTAL-AMT-OVERDUE",
    }

    #: Target ``DerivedAttributes`` field name -> source ``ATTR-NAME``
    #: within the ``PERFORM-ATTRIBUTES`` list of ``{ATTR-NAME, ATTR-VALUE}``.
    _DERIVED_ATTRIBUTES_FIELD_MAP: dict[str, str] = {
        "inquiries_in_last_six_months": "INQUIRIES-IN-LAST-SIX-MONTHS",
        "length_of_credit_history_year": "LENGTH-OF-CREDIT-HISTORY-YEAR",
        "length_of_credit_history_month": "LENGTH-OF-CREDIT-HISTORY-MONTH",
        "average_account_age_year": "AVERAGE-ACCOUNT-AGE-YEAR",
        "average_account_age_month": "AVERAGE-ACCOUNT-AGE-MONTH",
        "new_accounts_in_last_six_months": "NEW-ACCOUNTS-IN-LAST-SIX-MONTHS",
        "new_delinq_account_in_last_six_months": "NEW-DELINQ-ACCOUNT-IN-LAST-SIX-MONTHS",
        "total_secured_outstanding": "TOTAL-SECURED-OUTSTANDING",
        "total_unsecured_outstanding": "TOTAL-UNSECURED-OUTSTANDING",
    }

    @staticmethod
    def _adapt_accounts_summary_block(node: Any, prefix: str) -> RawMapping:
        """Builds one ``primary_accounts_summary`` / ``secondary_accounts_summary`` block."""
        if not isinstance(node, dict):
            return {}
        return {
            f"{prefix}{target_suffix}": node.get(source_key)
            for target_suffix, source_key in CrifParser._ACCOUNTS_SUMMARY_FIELD_MAP.items()
        }

    @staticmethod
    def _adapt_perform_attributes(attributes: Any) -> RawMapping:
        """Builds a flat ``derived_attributes`` node from the ``PERFORM-ATTRIBUTES`` list."""
        if not isinstance(attributes, list):
            return {}
        by_name = {
            _to_str(item.get("ATTR-NAME")).upper(): item.get("ATTR-VALUE")
            for item in attributes
            if isinstance(item, dict) and _to_str(item.get("ATTR-NAME"))
        }
        return {
            target_key: by_name.get(source_key)
            for target_key, source_key in CrifParser._DERIVED_ATTRIBUTES_FIELD_MAP.items()
        }

    @staticmethod
    def _adapt_account_summary(accounts_summary: Any) -> RawMapping:
        """Builds a flat ``account_summary`` node from ``REPORT-DATA.ACCOUNTS-SUMMARY``."""
        if not isinstance(accounts_summary, dict):
            return {}
        return {
            "primary_accounts_summary": CrifParser._adapt_accounts_summary_block(
                accounts_summary.get("PRIMARY-ACCOUNTS-SUMMARY"), "primary_"
            ),
            "secondary_accounts_summary": CrifParser._adapt_accounts_summary_block(
                accounts_summary.get("SECONDARY-ACCOUNTS-SUMMARY"), "secondary_"
            ),
            "derived_attributes": CrifParser._adapt_perform_attributes(
                accounts_summary.get("PERFORM-ATTRIBUTES")
            ),
        }

    #: DEMOGS.VARIATIONS entry ``TYPE`` -> flat personal_info_variation key.
    #: Every category CRIF's documentation and this sample payload use is
    #: mapped; an unrecognized type is logged and skipped rather than
    #: raising, so a future bureau-added category degrades gracefully.
    _VARIATION_TYPE_TO_FLAT_KEY: dict[str, str] = {
        "NAME-VARIATIONS": "name_variations",
        "ADDRESS-VARIATIONS": "address_variations",
        "PHONE-VARIATIONS": "phone_number_variations",
        "EMAIL-VARIATIONS": "email_variations",
        "DOB-VARIATIONS": "date_of_birth_variations",
        "PAN-VARIATIONS": "pan_variations",
        "UID-VARIATIONS": "uid_variations",
        "OTHERID-VARIATIONS": "other_id_variations",
        "DRIVINGLICENSE-VARIATIONS": "driving_license_variations",
        "VOTERID-VARIATIONS": "voter_id_variations",
        "PASSPORT-VARIATIONS": "passport_variations",
        "RATIONCARD-VARIATIONS": "ration_card_variations",
    }

    @staticmethod
    def _adapt_variation_entries(entries: Any) -> RawMapping:
        """Builds one ``{"variation": [{"value", "reported_date"}, ...]}`` block."""
        if not isinstance(entries, list):
            return {"variation": []}
        return {
            "variation": [
                {"value": entry.get("VALUE"), "reported_date": entry.get("REPORTED-DT")}
                for entry in entries
                if isinstance(entry, dict)
            ]
        }

    @staticmethod
    def _adapt_personal_info_variation(demogs: Any) -> RawMapping:
        """Builds a flat ``personal_info_variation`` node from ``STANDARD-DATA.DEMOGS``."""
        if not isinstance(demogs, dict):
            return {}
        variation_groups = demogs.get("VARIATIONS")
        if not isinstance(variation_groups, list):
            return {}

        flat: RawMapping = {}
        for group in variation_groups:
            if not isinstance(group, dict):
                continue
            group_type = _to_str(group.get("TYPE")).upper()
            flat_key = CrifParser._VARIATION_TYPE_TO_FLAT_KEY.get(group_type)
            if flat_key is None:
                logger.debug("Unrecognized personal-info variation type: %r", group_type)
                continue
            flat[flat_key] = CrifParser._adapt_variation_entries(group.get("VARIATION"))
        return flat

    @staticmethod
    def _adapt_employment_details(employment: Any) -> RawMapping:
        """
        Builds a flat ``employment_details`` node from
        ``STANDARD-DATA.EMPLOYMENT-DETAILS`` (a list; often empty -- the
        first entry is used when present).
        """
        if isinstance(employment, list):
            employment = employment[0] if employment else {}
        if not isinstance(employment, dict):
            return {}
        return {
            "acct_type": _first_present(employment, "ACCT-TYPE", "ACCOUNT-TYPE"),
            "date_reported": _first_present(employment, "DATE-REPORTED", "REPORTED-DT"),
            "occupation": _first_present(employment, "OCCUPATION"),
        }

    #: Target ``SecurityDetail`` field name (as read by
    #: ``_parse_single_security_detail``) -> source key within one
    #: TRADELINE's ``SECURITY-DETAILS`` entry. Several fields are renamed
    #: between the two shapes (e.g. ``SECURITY-VALUATION`` vs.
    #: ``SECURITY-VALUE``), which is exactly what this map exists to
    #: absorb.
    _SECURITY_DETAIL_FIELD_MAP: dict[str, str] = {
        "SECURITY-TYPE": "SECURITY-TYPE",
        "OWNER-NAME": "OWNER-NAME",
        "SECURITY-VALUE": "SECURITY-VALUATION",
        "DATE-OF-VALUE": "DATE-OF-VALUATION",
        "SECURITY-CHARGE": "SECURITY-CHARGE",
        "PROPERTY-ADDRESS": "PROPERTY-ADDRESS",
        "AUTOMOBILE-TYPE": "AUTOMOBILE-TYPE",
        "YEAR-OF-MANUFACTURE": "YEAR-OF-MANUFACTURING",
        "REGISTRATION-NUMBER": "REGISTRATION-NUMBER",
        "ENGINE-NUMBER": "ENGINE-NUMBER",
        "CHASSIS-NUMBER": "CHASSIE-NUMBER",
    }

    @staticmethod
    def _adapt_security_details(raw_list: Any) -> list[RawMapping]:
        """
        Builds the ``security_details`` value for one flat account dict
        from a TRADELINE's ``SECURITY-DETAILS`` list.

        CRIF reports one (often entirely blank) security-details entry
        per tradeline regardless of whether any collateral is actually
        attached. Entries with no populated field are dropped here so the
        "Collateral/Security Details" section (which renders whenever
        ``account.security_details`` is non-empty) does not show an empty
        table for every account that has no real collateral.
        """
        if not isinstance(raw_list, list):
            return []
        adapted: list[RawMapping] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            remapped = {
                target_key: item.get(source_key)
                for target_key, source_key in CrifParser._SECURITY_DETAIL_FIELD_MAP.items()
            }
            if any(_to_str(value) for value in remapped.values()):
                adapted.append(remapped)
        return adapted

    @staticmethod
    def _adapt_combined_payment_history(history: Any) -> str:
        """
        Rebuilds the single ``"Mon:YYYY,DPD/STATUS|..."`` token string
        :meth:`_parse_payment_history` expects from a TRADELINE's
        ``HISTORY`` list, which reports the same information as two
        separate, positionally-aligned pipe strings (``DATES`` and
        ``VALUES``) under the ``"COMBINED-PAYMENT-HISTORY"`` entry.
        """
        if not isinstance(history, list):
            return ""
        for entry in history:
            if not isinstance(entry, dict):
                continue
            if _to_str(entry.get("NAME")).upper() != "COMBINED-PAYMENT-HISTORY":
                continue
            dates = _split_pipe_positional(entry.get("DATES"))
            values = _split_pipe_positional(entry.get("VALUES"))
            tokens = [
                f"{date_token},{values[index] if index < len(values) else ''}"
                for index, date_token in enumerate(dates)
            ]
            return "|".join(tokens)
        return ""

    #: Target flat account field name (as read by ``_parse_single_account``)
    #: -> source key within one raw TRADELINE entry.
    _ACCOUNT_FIELD_MAP: dict[str, str] = {
        "acct_number": "ACCT-NUMBER",
        "credit_guarantor": "CREDIT-GRANTOR",
        "credit_grantor_group": "CREDIT-GRANTOR-GROUP",
        "credit_grantor_type": "CREDIT-GRANTOR-TYPE",
        "acct_type": "ACCT-TYPE",
        "date_reported": "REPORTED-DT",
        "ownership_ind": "OWNERSHIP-TYPE",
        "account_status": "ACCOUNT-STATUS",
        "disbursed_amt": "DISBURSED-AMT",
        "disbursed_dt": "DISBURSED-DT",
        "last_payment_date": "LAST-PAYMENT-DT",
        "closed_date": "CLOSED-DT",
        "installment_amt": "INSTALLMENT-AMT",
        "overdue_amt": "OVERDUE-AMT",
        "write_off_amt": "WRITE-OFF-AMT",
        "principal_write_off_amt": "PRINCIPAL-WRITE-OFF-AMT",
        "settlement_amt": "SETTLEMENT-AMT",
        "current_bal": "CURRENT-BAL",
        "security_status": "SECURITY-STATUS",
        "account_remarks": "ACCOUNT-REMARKS",
        "acct_in_dispute": "ACCT-IN-DISPUTE",
        "suit_filed_wilful_default_status": "SUIT-FILED-WILFUL-DEFAULT-STATUS",
        "written_off_settled_status": "WRITTEN-OFF-SETTLED-STATUS",
        "write_off_dt": "WRITE-OFF-DT",
        "suit_filed_dt": "SUIT-FILED-DT",
        "last_paid_amount": "LAST-PAID-AMOUNT",
        "obligation": "OBLIGATION",
        "original_term": "ORIGINAL-TERM",
        "term_to_maturity": "TERM-TO-MATURITY",
        "actual_payment": "ACTUAL-PAYMENT",
        "repayment_tenure": "REPAYMENT-TENURE",
        "interest_rate": "INTEREST-RATE",
        "credit_limit": "CREDIT-LIMIT",
        "cash_limit": "CASH-LIMIT",
        "occupation": "OCCUPATION",
        "income_frequency": "INCOME-FREQUENCY",
        "income_amount": "INCOME-AMOUNT",
    }

    @staticmethod
    def _adapt_single_account(raw: RawMapping) -> RawMapping:
        """Builds one flat account dict (as read by ``_parse_single_account``) from a raw TRADELINE."""
        flat: RawMapping = {
            target_key: raw.get(source_key)
            for target_key, source_key in CrifParser._ACCOUNT_FIELD_MAP.items()
        }

        linked = raw.get("LINKED-ACCOUNTS")
        if isinstance(linked, list):
            flat["linked_accounts"] = ", ".join(
                _to_str(item) for item in linked if _to_str(item)
            )
        else:
            flat["linked_accounts"] = linked

        flat["combined_payment_history"] = CrifParser._adapt_combined_payment_history(
            raw.get("HISTORY")
        )
        flat["security_details"] = CrifParser._adapt_security_details(raw.get("SECURITY-DETAILS"))
        return flat

    @staticmethod
    def _adapt_accounts(tradelines: Any) -> list[RawMapping]:
        """Builds the flat ``response`` list from ``STANDARD-DATA.TRADELINES``."""
        if not isinstance(tradelines, list):
            return []
        return [
            CrifParser._adapt_single_account(item) for item in tradelines if isinstance(item, dict)
        ]

    #: Target flat inquiry field name (as read by ``_parse_single_inquiry``
    #: via ``_first_present``) -> source key within one raw
    #: ``INQUIRY-HISTORY`` entry.
    _INQUIRY_FIELD_MAP: dict[str, str] = {
        "member_name": "LENDER-NAME",
        "purpose": "CREDIT-INQ-PURPS-TYPE",
        "inquiry_date": "INQUIRY-DT",
        "account_type": "LOAN-TYPE",
        "amount": "AMOUNT",
        "remark": "REMARK",
    }

    @staticmethod
    def _adapt_inquiries(inquiry_history: Any) -> list[RawMapping]:
        """Builds the flat ``inquiry_history`` list from ``STANDARD-DATA.INQUIRY-HISTORY``."""
        if not isinstance(inquiry_history, list):
            return []
        return [
            {
                target_key: item.get(source_key)
                for target_key, source_key in CrifParser._INQUIRY_FIELD_MAP.items()
            }
            for item in inquiry_history
            if isinstance(item, dict)
        ]

    @staticmethod
    def _adapt_b2c_report(b2c_report: RawMapping) -> RawMapping:
        """
        Translates a raw ``B2C-REPORT`` node into the flat ``credit_report``
        shape every ``_parse_*`` method above expects (see the "B2C-REPORT
        structural adapter" section docstring above for the general
        approach).

        Args:
            b2c_report: The dict at
                ``data.result_json.parsed_data.B2C-REPORT``.

        Returns:
            A flat dict with the same top-level keys
            :meth:`CrifParser.parse` reads off a legacy-shaped
            ``credit_report`` node (``customer_identity``, ``scores``,
            ``trends``, ``account_summary``, ``personal_info_variation``,
            ``employment_details``, ``response``, ``inquiry_history``).
        """
        request_data = b2c_report.get("REQUEST-DATA") or {}
        applicant = request_data.get("APPLICANT-SEGMENT") or {}

        report_data = b2c_report.get("REPORT-DATA") or {}
        standard_data = report_data.get("STANDARD-DATA") or {}

        accounts = CrifParser._adapt_accounts(standard_data.get("TRADELINES"))
        inquiries = CrifParser._adapt_inquiries(standard_data.get("INQUIRY-HISTORY"))
        raw_scores = standard_data.get("SCORE")
        score_value = (
            raw_scores[0].get("VALUE")
            if isinstance(raw_scores, list) and raw_scores and isinstance(raw_scores[0], dict)
            else None
        )

        logger.info(
            "CRIF report root found: B2C-REPORT (tradelines=%d, inquiries=%d, score=%s)",
            len(accounts),
            len(inquiries),
            score_value,
        )

        return {
            "customer_identity": CrifParser._adapt_applicant_identity(applicant),
            "scores": CrifParser._adapt_score(raw_scores),
            "trends": CrifParser._adapt_trends(report_data.get("TRENDS")),
            "account_summary": CrifParser._adapt_account_summary(
                report_data.get("ACCOUNTS-SUMMARY")
            ),
            "personal_info_variation": CrifParser._adapt_personal_info_variation(
                standard_data.get("DEMOGS")
            ),
            "employment_details": CrifParser._adapt_employment_details(
                standard_data.get("EMPLOYMENT-DETAILS")
            ),
            "response": accounts,
            "inquiry_history": inquiries,
        }

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
            total_secured_outstanding=_to_decimal(node.get("total_secured_outstanding")),
            total_unsecured_outstanding=_to_decimal(node.get("total_unsecured_outstanding")),
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
            total_amt_overdue=_to_decimal(get("total_amt_overdue")),
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

        A populated ``inquiry_history.history`` sample confirmed CRIF
        reports this section as ``member_name`` / ``purpose`` /
        ``inquiry_date`` / ``amount`` / ``remark`` -- those are checked
        first. The original best-effort aliases (inferred from the printed
        report's column headings before a populated sample was available)
        are kept as fallbacks in case a differently-shaped payload is ever
        encountered.
        """
        return InquiryRecord(
            credit_grantor=_to_str(
                _first_present(raw, "member_name", "credit_grantor", "credit_guarantor", "grantor")
            ),
            inquiry_type=_to_str(
                _first_present(raw, "purpose", "type", "inquiry_type", "credit_grantor_type")
            ),
            date_of_inquiry=_parse_date(
                _first_present(raw, "inquiry_date", "date_of_inquiry", "date")
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

    Raises:
        ValueError: See :meth:`CrifParser.parse`.
    """
    return CrifParser().parse(raw_json)
