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
# Defensive handling of malformed/missing input
#
# The top-level report-root lookup (CrifParser.parse / parse_credit_report)
# raises ValueError when it cannot locate a credit report under any
# supported payload shape -- returning an empty CreditReport here would
# silently render as a blank PDF with no indication anything went wrong.
# Every *field*-level extraction below that point still never raises (see
# TestParseAccountsDefensive etc.).
# ---------------------------------------------------------------------------


class TestParseMalformedInput:
    def test_none_payload_raises(self):
        with pytest.raises(ValueError):
            parse_credit_report(None)

    def test_non_dict_payload_raises(self):
        with pytest.raises(ValueError):
            parse_credit_report("not a dict")

    def test_empty_dict_raises(self):
        with pytest.raises(ValueError):
            parse_credit_report({})

    def test_missing_credit_report_node_raises(self):
        with pytest.raises(ValueError):
            parse_credit_report({"data": {"result_json": {}}})

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


# ---------------------------------------------------------------------------
# Full parse against the real CRIF B2C-REPORT sample payload
# (data.result_json.parsed_data.B2C-REPORT), the shape the live bureau
# API actually returns.
# ---------------------------------------------------------------------------


class TestParseB2CReportSample:
    def test_customer_identity_populated(self, parsed_b2c_report: CreditReport):
        identity = parsed_b2c_report.customer_identity
        assert identity.name == "JYOTI THAKUR"
        assert identity.gender == "Male"

    def test_header_populated(self, parsed_b2c_report: CreditReport):
        header = parsed_b2c_report.header
        assert header.status == "SUCCESS"
        assert header.product_type == "BBC CONSUMER SCORE"
        assert header.date_of_issue == date(2026, 9, 18)

    def test_score_populated(self, parsed_b2c_report: CreditReport):
        score = parsed_b2c_report.score
        assert score.score_type == "PERFORM CONSUMER 2.2"
        assert score.score_value == 794
        assert score.score_description == "B"
        assert score.score_factors

    def test_accounts_parsed(self, parsed_b2c_report: CreditReport):
        accounts = parsed_b2c_report.accounts
        assert len(accounts) == 2
        assert all(a.credit_guarantor == "HDFC BANK LTD" for a in accounts)
        for account in accounts:
            assert isinstance(account, LoanAccount)

    def test_account_payment_history_populated(self, parsed_b2c_report: CreditReport):
        first_account = parsed_b2c_report.accounts[0]
        assert first_account.payment_history
        for entry in first_account.payment_history:
            assert isinstance(entry, PaymentHistoryEntry)
            assert 1 <= entry.month <= 12

    def test_account_numeric_history_populated(self, parsed_b2c_report: CreditReport):
        first_account = parsed_b2c_report.accounts[0]
        assert first_account.high_credit_history
        assert first_account.current_balance_history
        assert any(point.value is not None for point in first_account.current_balance_history)

    def test_inquiries_parsed(self, parsed_b2c_report: CreditReport):
        # The current sample's INQUIRY-HISTORY is empty.
        assert parsed_b2c_report.inquiries == []

    def test_account_summary_matches_source_payload(self, parsed_b2c_report: CreditReport):
        primary = parsed_b2c_report.account_summary.primary
        assert primary.number_of_accounts == 2
        assert primary.active_number_of_accounts == 1
        assert primary.overdue_number_of_accounts == 0

    def test_derived_attributes_matches_source_payload(self, parsed_b2c_report: CreditReport):
        derived = parsed_b2c_report.account_summary.derived_attributes
        assert derived.length_of_credit_history_year == 6

    def test_mfi_group_and_additional_summary_populated(self, parsed_b2c_report: CreditReport):
        summary = parsed_b2c_report.account_summary
        assert summary.mfi_group_summary
        assert summary.additional_summary
        assert ("Num Grantors", "1") in summary.additional_summary

    def test_score_trend_populated(self, parsed_b2c_report: CreditReport):
        points = parsed_b2c_report.score_trend.points
        assert len(points) == 12
        assert points[0].value == 794
        assert points[-1].value == 747

    def test_employment_details_populated(self, parsed_b2c_report: CreditReport):
        records = parsed_b2c_report.employment_details
        assert len(records) == 2
        assert all(record.occupation == "SALARIED" for record in records)
        assert records[0].first_reported is not None
        assert records[0].last_reported is not None
