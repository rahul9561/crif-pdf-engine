"""
Tests for deriving Primary Account Summary totals (current balance,
sanctioned amount, disbursed amount) from active TRADELINES when Digitap's
CRIF payload reports them as ``"0.0"``.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import pytest

from pdf_engine import helpers as h
from pdf_engine.parser import CrifParser, _to_amount, parse_credit_report


def _tradeline(
    acct_type: str,
    status: str,
    current_bal: Any,
    disbursed: Any,
    high_credit: str | None = None,
    credit_limit: Any = "",
) -> dict[str, Any]:
    history = []
    if high_credit is not None:
        history.append(
            {"NAME": "HIGH-CREDIT-HISTORY", "DATES": "Sep:2026|Aug:2026", "VALUES": high_credit}
        )
    return {
        "ACCT-TYPE": acct_type,
        "ACCOUNT-STATUS": status,
        "CURRENT-BAL": current_bal,
        "DISBURSED-AMT": disbursed,
        "CREDIT-LIMIT": credit_limit,
        "HISTORY": history,
    }


#: SOHAN KUMAR (mobile 9896931579): three active loans plus one closed
#: loan that must be excluded from every total.
SOHAN_TRADELINES = [
    _tradeline("Mudra Loans - Shishu / Kishor / Tarun", "Active", "3,42,782", "5,00,000",
               high_credit="5,00,000|5,00,000"),
    _tradeline("GECL Loan secured", "Active", "91,122", "97,146", high_credit="97,146|97,146"),
    _tradeline("Used Car Loan", "ACTIVE", "2,57,924", "2,89,511", high_credit="2,89,511|"),
    _tradeline("Personal Loan", "Closed", "0", "1,50,000", high_credit="1,50,000|1,50,000"),
]


def _b2c_payload(primary_summary: dict[str, Any], tradelines: list) -> dict[str, Any]:
    return {
        "data": {
            "result_json": {
                "parsed_data": {
                    "B2C-REPORT": {
                        "REQUEST-DATA": {
                            "APPLICANT-SEGMENT": {
                                "APPLICANT-NAME": {"NAME1": "SOHAN KUMAR"},
                                "PHONES": [{"VALUE": "9896931579"}],
                            }
                        },
                        "REPORT-DATA": {
                            "ACCOUNTS-SUMMARY": {
                                "PRIMARY-ACCOUNTS-SUMMARY": primary_summary,
                                "SECONDARY-ACCOUNTS-SUMMARY": {
                                    "NUMBER-OF-ACCOUNTS": "0",
                                    "TOTAL-CURRENT-BALANCE": "0.0",
                                    "TOTAL-SANCTIONED-AMT": "0.0",
                                    "TOTAL-DISBURSED-AMT": "0.0",
                                },
                            },
                            "STANDARD-DATA": {"TRADELINES": tradelines},
                        },
                    }
                }
            }
        }
    }


ZERO_SUMMARY = {
    "NUMBER-OF-ACCOUNTS": "4",
    "ACTIVE-ACCOUNTS": "3",
    "TOTAL-CURRENT-BALANCE": "0.0",
    "TOTAL-SANCTIONED-AMT": "0.0",
    "TOTAL-DISBURSED-AMT": "0.0",
    "CURRENT-BALANCE-SECURED": "691828.0",
    "CURRENT-BALANCE-UNSECURED": "0.0",
}


@pytest.fixture
def sohan_summary():
    report = parse_credit_report(_b2c_payload(ZERO_SUMMARY, copy.deepcopy(SOHAN_TRADELINES)))
    return report.account_summary


class TestToAmount:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("3,42,782", Decimal("342782")),
            ("5,00,000", Decimal("500000")),
            ("0.0", Decimal("0")),
            ("", Decimal("0")),
            ("   ", Decimal("0")),
            (None, Decimal("0")),
            ("N/A", Decimal("0")),
            (1234, Decimal("1234")),
        ],
    )
    def test_parses_or_defaults_to_zero(self, raw, expected):
        assert _to_amount(raw) == expected


class TestPrimarySummaryFallback:
    def test_zero_summary_values_computed_from_active_tradelines(self, sohan_summary):
        primary = sohan_summary.primary
        assert primary.current_balance == Decimal("691828")
        assert primary.sanctioned_amount == Decimal("886657")
        assert primary.disbursed_amount == Decimal("886657")

    def test_current_balance_matches_secured_plus_unsecured(self, sohan_summary):
        expected = _to_amount(ZERO_SUMMARY["CURRENT-BALANCE-SECURED"]) + _to_amount(
            ZERO_SUMMARY["CURRENT-BALANCE-UNSECURED"]
        )
        assert sohan_summary.primary.current_balance == expected

    def test_totals_render_in_indian_format(self, sohan_summary):
        primary = sohan_summary.primary
        assert h.safe_decimal(primary.current_balance) == "6,91,828"
        assert h.safe_decimal(primary.sanctioned_amount) == "8,86,657"
        assert h.safe_decimal(primary.disbursed_amount) == "8,86,657"

    @pytest.mark.parametrize("missing", [None, "", "0", "0.0"])
    def test_missing_or_empty_summary_values_also_fall_back(self, missing):
        summary = {
            "TOTAL-CURRENT-BALANCE": missing,
            "TOTAL-SANCTIONED-AMT": missing,
            "TOTAL-DISBURSED-AMT": missing,
        }
        primary = parse_credit_report(
            _b2c_payload(summary, copy.deepcopy(SOHAN_TRADELINES))
        ).account_summary.primary
        assert primary.current_balance == Decimal("691828")
        assert primary.sanctioned_amount == Decimal("886657")
        assert primary.disbursed_amount == Decimal("886657")

    def test_non_zero_summary_values_kept_unchanged(self):
        summary = dict(
            ZERO_SUMMARY,
            **{
                "TOTAL-CURRENT-BALANCE": "1,11,111",
                "TOTAL-SANCTIONED-AMT": "222222.0",
                "TOTAL-DISBURSED-AMT": "333333",
            },
        )
        primary = parse_credit_report(
            _b2c_payload(summary, copy.deepcopy(SOHAN_TRADELINES))
        ).account_summary.primary
        assert primary.current_balance == Decimal("111111")
        assert primary.sanctioned_amount == Decimal("222222.0")
        assert primary.disbursed_amount == Decimal("333333")

    def test_closed_accounts_excluded(self):
        closed_only = [t for t in SOHAN_TRADELINES if t["ACCOUNT-STATUS"] == "Closed"]
        totals = CrifParser._active_tradeline_totals(closed_only)
        assert totals == {
            "current_balance": Decimal(0),
            "sanctioned_amount": Decimal(0),
            "disbursed_amount": Decimal(0),
        }

    def test_secondary_summary_left_as_reported(self, sohan_summary):
        secondary = sohan_summary.secondary
        assert secondary.current_balance == Decimal("0.0")
        assert secondary.sanctioned_amount == Decimal("0.0")
        assert secondary.disbursed_amount == Decimal("0.0")

    def test_empty_null_and_comma_amounts_in_tradelines(self):
        tradelines = [
            _tradeline("A", "Active", "", "1,00,000"),
            _tradeline("B", "Active", None, None),
            _tradeline("C", "Active", "12,34,567", "garbage"),
        ]
        totals = CrifParser._active_tradeline_totals(tradelines)
        assert totals["current_balance"] == Decimal("1234567")
        assert totals["disbursed_amount"] == Decimal("100000")


class TestSanctionedAmount:
    def test_uses_latest_non_empty_high_credit(self):
        tradeline = _tradeline("X", "Active", "0", "50,000", high_credit="|75,000|60,000")
        assert CrifParser._sanctioned_amount(tradeline) == Decimal("75000")

    def test_falls_back_to_credit_limit(self):
        tradeline = _tradeline("X", "Active", "0", "50,000", high_credit="||", credit_limit="80,000")
        assert CrifParser._sanctioned_amount(tradeline) == Decimal("80000")

    def test_falls_back_to_disbursed_amount(self):
        tradeline = _tradeline("X", "Active", "0", "50,000")
        assert CrifParser._sanctioned_amount(tradeline) == Decimal("50000")

    def test_all_missing_is_zero(self):
        tradeline = _tradeline("X", "Active", "0", None)
        assert CrifParser._sanctioned_amount(tradeline) == Decimal(0)
