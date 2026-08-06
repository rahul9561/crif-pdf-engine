"""
Tests for pdf_engine.parser.

Covers both the real sample payload (input/crif_response.json) and the
parser's defensive handling of malformed/missing input, without modifying
any parser logic.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pdf_engine.parser import (
    CreditReport,
    CrifParser,
    InquiryRecord,
    LoanAccount,
    PaymentHistoryEntry,
    SecurityDetail,
    _first_present,
    _parse_date,
    _split_pipe,
    _split_pipe_positional,
    _to_decimal,
    _to_int,
    _to_str,
    parse_credit_report,
)

# ---------------------------------------------------------------------------
# Primitive coercion helpers
# ---------------------------------------------------------------------------


class TestToStr:
    def test_none_becomes_empty_string(self):
        assert _to_str(None) == ""

    def test_strips_whitespace(self):
        assert _to_str("  ANAND GOYAL  ") == "ANAND GOYAL"

    def test_coerces_non_string(self):
        assert _to_str(42) == "42"


class TestToDecimal:
    def test_none_is_none(self):
        assert _to_decimal(None) is None

    def test_empty_string_is_none(self):
        assert _to_decimal("") is None
        assert _to_decimal("   ") is None

    def test_parses_plain_number_string(self):
        assert _to_decimal("111600") == Decimal("111600")

    def test_tolerates_comma_grouping(self):
        assert _to_decimal("1,11,600") == Decimal("111600")

    def test_accepts_int_and_float(self):
        assert _to_decimal(100) == Decimal("100")
        assert _to_decimal(1.5) == Decimal("1.5")

    def test_unparsable_value_is_none_not_raise(self):
        assert _to_decimal("not-a-number") is None


class TestToInt:
    def test_none_is_none(self):
        assert _to_int(None) is None

    def test_parses_trailing_dot_zero(self):
        assert _to_int("10.0") == 10

    def test_unparsable_value_is_none_not_raise(self):
        assert _to_int("abc") is None


class TestParseDate:
    def test_parses_dd_mm_yyyy(self):
        assert _parse_date("31-03-2026") == date(2026, 3, 31)

    def test_empty_and_none_are_none(self):
        assert _parse_date("") is None
        assert _parse_date(None) is None

    def test_malformed_date_is_none_not_raise(self):
        assert _parse_date("not-a-date") is None
        assert _parse_date("2026/03/31") is None


class TestSplitPipe:
    def test_drops_empty_tokens_and_strips(self):
        assert _split_pipe("SF03|SF11|") == ["SF03", "SF11"]

    def test_none_and_non_string_yield_empty_list(self):
        assert _split_pipe(None) == []
        assert _split_pipe(123) == []


class TestSplitPipePositional:
    def test_drops_only_single_trailing_empty_token(self):
        assert _split_pipe_positional("774|800|796|") == ["774", "800", "796"]

    def test_keeps_internal_empty_tokens_for_positional_alignment(self):
        assert _split_pipe_positional("a||c") == ["a", "", "c"]

    def test_none_and_non_string_yield_empty_list(self):
        assert _split_pipe_positional(None) == []


class TestFirstPresent:
    def test_returns_first_non_empty_value(self):
        node = {"a": "", "b": None, "c": "value"}
        assert _first_present(node, "a", "b", "c") == "value"

    def test_returns_none_when_nothing_present(self):
        assert _first_present({}, "a", "b") is None


# ---------------------------------------------------------------------------
# Full parse against the real sample payload
# ---------------------------------------------------------------------------


class TestParseRealSample:
    def test_returns_credit_report_instance(self, parsed_report: CreditReport):
        assert isinstance(parsed_report, CreditReport)

    def test_customer_identity_populated(self, parsed_report: CreditReport):
        identity = parsed_report.customer_identity
        assert identity.name
        assert isinstance(identity.dob, date) or identity.dob is None

    def test_score_populated(self, parsed_report: CreditReport):
        score = parsed_report.score
        assert score.score_type == "PERFORM CONSUMER 2.2"
        assert score.score_value == 800
        # Trailing empty pipe token is dropped.
        assert score.score_factors == ["SF03", "SF11"]

    def test_score_trend_points_are_positionally_aligned(self, parsed_report: CreditReport):
        points = parsed_report.score_trend.points
        assert len(points) > 0
        for point in points:
            assert point.as_of is None or isinstance(point.as_of, date)

    def test_accounts_parsed(self, parsed_report: CreditReport):
        assert len(parsed_report.accounts) == 10
        for account in parsed_report.accounts:
            assert isinstance(account, LoanAccount)

    def test_account_payment_history_entries_have_valid_month(self, parsed_report: CreditReport):
        accounts_with_history = [a for a in parsed_report.accounts if a.payment_history]
        assert accounts_with_history
        for account in accounts_with_history:
            for entry in account.payment_history:
                assert isinstance(entry, PaymentHistoryEntry)
                assert 1 <= entry.month <= 12

    def test_inquiries_is_empty_list_not_none(self, parsed_report: CreditReport):
        # The real sample's inquiry_history.history is [].
        assert parsed_report.inquiries == []

    def test_account_summary_present(self, parsed_report: CreditReport):
        summary = parsed_report.account_summary
        assert summary.primary.number_of_accounts is not None


# ---------------------------------------------------------------------------
# Defensive handling of malformed/missing input -- never raises
# ---------------------------------------------------------------------------


class TestParseMalformedInput:
    def test_none_payload_returns_empty_report(self):
        report = parse_credit_report(None)
        assert isinstance(report, CreditReport)
        assert report.accounts == []
        assert report.customer_identity.name == ""

    def test_non_dict_payload_returns_empty_report(self):
        report = parse_credit_report("not a dict")
        assert isinstance(report, CreditReport)

    def test_empty_dict_returns_empty_report(self):
        report = parse_credit_report({})
        assert isinstance(report, CreditReport)

    def test_missing_credit_report_node_returns_empty_report(self):
        report = parse_credit_report({"data": {"result_json": {}}})
        assert isinstance(report, CreditReport)

    def test_non_list_accounts_does_not_raise(self):
        parser = CrifParser()
        accounts = parser._parse_accounts({"not": "a list"})
        assert accounts == []

    def test_non_dict_account_entries_are_skipped(self):
        parser = CrifParser()
        accounts = parser._parse_accounts([{"acct_number": "1"}, "garbage", 42, None])
        assert len(accounts) == 1
        assert accounts[0].acct_number == "1"

    def test_malformed_payment_history_token_is_skipped(self):
        parser = CrifParser()
        entries = parser._parse_payment_history("Apr:2026,000/XXX|garbage-token|Mar:2026,010/STD")
        assert len(entries) == 2
        assert entries[0].month == 4
        assert entries[1].asset_classification == "STD"

    def test_security_details_empty_string_yields_empty_list(self):
        parser = CrifParser()
        assert parser._parse_security_details("") == []

    def test_security_details_single_dict_shape(self):
        parser = CrifParser()
        raw = {"SECURITY-DETAIL": {"SECURITY-TYPE": "Vehicle", "SECURITY-VALUE": "50000"}}
        details = parser._parse_security_details(raw)
        assert len(details) == 1
        assert isinstance(details[0], SecurityDetail)
        assert details[0].security_type == "Vehicle"
        assert details[0].security_value == Decimal("50000")

    def test_security_details_list_shape(self):
        parser = CrifParser()
        raw = [
            {"SECURITY-DETAIL": {"SECURITY-TYPE": "Vehicle"}},
            {"SECURITY-DETAIL": {"SECURITY-TYPE": "Property"}},
        ]
        details = parser._parse_security_details(raw)
        assert len(details) == 2

    def test_non_list_inquiry_history_does_not_raise(self):
        parser = CrifParser()
        assert parser._parse_inquiries({"history": "not-a-list"}) == []

    def test_single_inquiry_uses_key_aliases(self):
        parser = CrifParser()
        record = parser._parse_single_inquiry(
            {"credit_guarantor": "Bank A", "inquiry_date": "01-01-2026", "amount": "5000"}
        )
        assert isinstance(record, InquiryRecord)
        assert record.credit_grantor == "Bank A"
        assert record.date_of_inquiry == date(2026, 1, 1)
        assert record.amount == Decimal("5000")

    def test_malformed_account_entry_is_skipped_not_fatal(self):
        # A dict account entry that raises during coercion of one field
        # must not sink parsing of the rest of the accounts list.
        parser = CrifParser()
        accounts = parser._parse_accounts(
            [
                {"acct_number": "GOOD-1"},
                {"acct_number": "GOOD-2", "security_details": object()},
            ]
        )
        acct_numbers = {a.acct_number for a in accounts}
        assert "GOOD-1" in acct_numbers


@pytest.mark.parametrize(
    "bad_dates_node",
    [
        {"dates": "31-03-2026|30-09-2025", "values": "774"},  # length mismatch
        {"dates": "", "values": ""},
        {},
    ],
)
def test_score_trend_length_mismatch_does_not_raise(bad_dates_node):
    parser = CrifParser()
    trend = parser._parse_score_trend(bad_dates_node)
    assert trend is not None
