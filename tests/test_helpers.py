"""
Parsing helpers (safe_float / safe_int), ticker normalisation, and the built-in
SC Malaysia list lookup.

Nothing here touches the network.
"""
import math

import pytest

import server as mizan


MISSING_VALUES = [None, "", "None", "N/A", "-", "nan", float("nan")]


class TestSafeFloat:
    def test_zero_is_a_number_not_missing(self):
        # Regression: 0 used to be in the missing-value sentinel list, so a
        # genuine zero debt/dividend/volume came back as the default ("N/A").
        assert mizan.safe_float(0, 99) == 0
        assert mizan.safe_float(0.0, 99) == 0.0
        assert mizan.safe_float("0", 99) == 0.0

    @pytest.mark.parametrize("value", MISSING_VALUES)
    def test_missing_values_return_default(self, value):
        assert mizan.safe_float(value, 7) == 7

    def test_non_numeric_string_returns_default(self):
        assert mizan.safe_float("not a number", 1) == 1

    def test_numeric_string_is_parsed(self):
        assert mizan.safe_float("12.5") == 12.5

    def test_default_is_none_when_not_given(self):
        assert mizan.safe_float(None) is None


class TestSafeInt:
    def test_zero_is_a_number_not_missing(self):
        # Same regression as safe_float: zero is real data.
        assert mizan.safe_int(0, 99) == 0
        assert mizan.safe_int("0", 99) == 0

    @pytest.mark.parametrize("value", MISSING_VALUES)
    def test_missing_values_return_default(self, value):
        assert mizan.safe_int(value, 7) == 7

    def test_truncates_float_string(self):
        assert mizan.safe_int("3.9") == 3

    def test_truncates_float(self):
        assert mizan.safe_int(3.9) == 3

    def test_non_numeric_string_returns_default(self):
        assert mizan.safe_int("not a number", 1) == 1


class TestNormaliseTicker:
    def test_short_numeric_code_is_zero_padded(self):
        assert mizan.normalise_ticker("166") == "0166"

    def test_four_digit_code_is_unchanged(self):
        assert mizan.normalise_ticker("1295") == "1295"

    def test_kl_suffix_is_stripped_and_padded(self):
        assert mizan.normalise_ticker("1295.KL") == "1295"
        assert mizan.normalise_ticker("166.KL") == "0166"

    def test_us_ticker_is_upper_cased(self):
        assert mizan.normalise_ticker("aapl") == "AAPL"

    def test_embedded_spaces_are_removed(self):
        assert mizan.normalise_ticker(" aa pl ") == "AAPL"


class TestIsBursa:
    def test_numeric_symbols_are_bursa(self):
        assert mizan.is_bursa("1295") is True
        assert mizan.is_bursa("1295.KL") is True

    def test_alphabetic_symbols_are_not_bursa(self):
        assert mizan.is_bursa("AAPL") is False


class TestCheckScList:
    def test_compliant_code_is_found(self):
        result = mizan.check_sc_list("1295")
        assert result["found"] is True
        assert result["status"] == "compliant"

    def test_non_compliant_code_is_found(self):
        result = mizan.check_sc_list("3255")
        assert result["found"] is True
        assert result["status"] == "non_compliant"

    def test_unknown_four_digit_code_is_not_found(self):
        result = mizan.check_sc_list("9999")
        assert result["found"] is False
        assert result["status"] == "not_found"

    def test_non_numeric_ticker_is_not_applicable(self):
        # The SC list covers Bursa Malaysia only, so a US ticker is neither
        # compliant nor non-compliant: the question does not apply.
        result = mizan.check_sc_list("AAPL")
        assert result["found"] is False
        assert result["status"] == "not_applicable"

    def test_kl_suffix_is_handled(self):
        assert mizan.check_sc_list("1295.KL")["status"] == "compliant"

    def test_short_numeric_code_is_zero_padded(self):
        assert mizan.check_sc_list("166")["status"] == "compliant"

    def test_kls_suffix_is_stripped_longest_first(self):
        # Regression guard. ".KLS" contains ".KL", so stripping ".KL" first
        # left the trailing "S" behind: "1155.KLS" became "1155S", which is not
        # numeric, and a listed stock was reported as not on the list at all.
        assert mizan.check_sc_list("1155.KLS")["status"] == "compliant"

    def test_suffix_is_case_insensitive(self):
        assert mizan.check_sc_list("1155.kl")["status"] == "compliant"
