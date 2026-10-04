"""
The Shariah screening rules engine: verdict contract, the wrong-data bugs that
were just fixed (each test is a regression guard), threshold boundaries, and
sweeps of the built-in databases.

No test reaches the network: the Bursa live quote and the Tiingo client are
monkeypatched to return nothing, which is the same shape as an offline run.
"""
import pytest

import server as mizan


# The SC list does not cover US tickers, so this is the normal "not applicable"
# result and keeps the SC check out of the way of the behaviour under test.
NOT_APPLICABLE = {"found": False, "status": "not_applicable"}

KNOWN_VERDICTS = {"Potentially Halal", "Not Halal", "Doubtful"}


def _clean(**overrides):
    """A clean, fully-populated input that screens as Potentially Halal."""
    base = dict(
        name="Clean Co",
        sector="Technology",
        industry="Software",
        description="",
        debt_ratio=0.10,
        interest_ratio=0.01,
        pe_ratio=15.0,
        profit_margin=0.20,
        sc_check=NOT_APPLICABLE,
    )
    base.update(overrides)
    return base


def _check(screening, prefix):
    matches = [c for c in screening["checks"] if c["name"].startswith(prefix)]
    assert matches, f"no check named like {prefix!r}: {[c['name'] for c in screening['checks']]}"
    return matches[0]


# ── Verdict contract ──────────────────────────────────────

def test_no_issues_and_no_warnings_is_potentially_halal():
    result = mizan.screen_halal(**_clean())
    assert result["verdict"] == "Potentially Halal"
    assert result["vClass"] == "halal"


def test_any_issue_is_not_halal():
    result = mizan.screen_halal(**_clean(flags=["conventional_banking"]))
    assert result["verdict"] == "Not Halal"
    assert result["vClass"] == "haram"


def test_warnings_without_issues_are_doubtful():
    result = mizan.screen_halal(**_clean(sc_check={"found": False, "status": "not_found"}))
    assert result["verdict"] == "Doubtful"
    assert result["vClass"] == "doubtful"


# ── Business activity: the description is never scanned ───

def test_prohibited_word_in_description_does_not_condemn():
    # Regression: the keyword scan once ran over the free-text description, so
    # Walmart's blurb ("Sells alcohol (some stores) but primary business is
    # retail") reported as Not Halal. The scan now covers sector, industry and
    # company name only.
    result = mizan.screen_halal(
        **_clean(description="Sells alcohol (some stores) but primary business is retail")
    )
    assert result["verdict"] != "Not Halal"
    assert "haram_industry" not in result["issues"]


def test_prohibited_word_in_industry_does_condemn():
    result = mizan.screen_halal(**_clean(industry="Brewers"))
    assert result["verdict"] == "Not Halal"
    assert "haram_industry" in result["issues"]


def test_prohibited_word_in_company_name_does_condemn():
    result = mizan.screen_halal(**_clean(name="Carlsberg Brewery Berhad"))
    assert result["verdict"] == "Not Halal"
    assert "haram_industry" in result["issues"]


def test_explicit_flag_condemns_even_with_clean_sector():
    result = mizan.screen_halal(**_clean(flags=["conventional_banking"]))
    assert result["verdict"] == "Not Halal"
    assert "haram_industry" in result["issues"]
    assert _check(result, "Business Activity")["status"] == "fail"


# ── Missing vs. genuine zero (the "unverified scored as safe" bug) ──

def test_missing_debt_is_unverified_not_low_risk():
    # Regression: debt_ratio=None used to be scored as zero debt, i.e. LOW risk,
    # making the least verified company look like the safest.
    result = mizan.screen_halal(**_clean(debt_ratio=None))
    assert result["risk"] != "LOW"
    assert "debtRatio" in result["missingInputs"]
    assert result["dataQuality"] == "partial"


def test_zero_debt_is_a_real_zero_and_low_risk():
    result = mizan.screen_halal(**_clean(debt_ratio=0.0))
    assert result["risk"] == "LOW"
    assert "debtRatio" not in result["missingInputs"]


# ── Income test: banks are not warned about a test they are exempt from ──

def test_bank_without_income_data_is_na_not_a_warning():
    result = mizan.screen_halal(
        **_clean(name="Public Bank Berhad", sector="Financial Services",
                 industry="Banks", interest_ratio=None)
    )
    income = _check(result, "Non-Permissible Income")
    assert income["status"] == "n/a"
    assert "no_income_data" not in result["warnings"]


def test_non_bank_without_income_data_warns():
    result = mizan.screen_halal(**_clean(interest_ratio=None))
    income = _check(result, "Non-Permissible Income")
    assert income["status"] == "warn"
    assert "no_income_data" in result["warnings"]


# ── SC Malaysia list is authoritative ─────────────────────

def test_sc_listed_compliant_does_not_also_report_doubtful_sector():
    # Regression: listed Islamic banks such as Public Bank and Maybank reported
    # Doubtful directly beneath a check that read "Listed as Shariah-compliant
    # by SC Malaysia", because the "Financial Services" sector warning was
    # applied on top of the authoritative listing.
    sc = {"found": True, "status": "compliant", "note": "Listed."}
    result = mizan.screen_halal(
        **_clean(name="Public Bank Berhad", sector="Financial Services",
                 industry="Banks", interest_ratio=None, sc_check=sc)
    )
    assert "doubtful_sector" not in result["warnings"]
    assert result["verdict"] != "Doubtful"
    assert _check(result, "Business Activity")["status"] == "pass"


def test_missing_sector_is_unverified_not_a_pass():
    # Regression: reporting "no prohibited activity detected" for data that was
    # never supplied is a false all-clear.
    result = mizan.screen_halal(**_clean(sector="N/A", industry="N/A"))
    activity = _check(result, "Business Activity")
    assert activity["status"] == "warn"
    assert "no_business_data" in result["warnings"]


# ── Threshold boundaries (off-by-one guards) ──────────────

@pytest.mark.parametrize(
    "ratio,expected_status,expected_issue,expected_warning",
    [
        (0.33, "pass", None, None),
        (0.3301, "warn", None, "marginal_debt"),
        (0.50, "warn", None, "marginal_debt"),
        (0.51, "fail", "high_debt", None),
    ],
)
def test_debt_ratio_boundaries(ratio, expected_status, expected_issue, expected_warning):
    result = mizan.screen_halal(**_clean(debt_ratio=ratio))
    assert _check(result, "Debt-to-Assets")["status"] == expected_status
    if expected_issue:
        assert expected_issue in result["issues"]
    if expected_warning:
        assert expected_warning in result["warnings"]


@pytest.mark.parametrize(
    "ratio,expected_status,expected_issue,expected_warning",
    [
        (0.05, "pass", None, None),
        (0.0501, "warn", None, "marginal_interest"),
        (0.20, "warn", None, "marginal_interest"),
        (0.21, "fail", "high_interest", None),
    ],
)
def test_interest_ratio_boundaries(ratio, expected_status, expected_issue, expected_warning):
    result = mizan.screen_halal(**_clean(interest_ratio=ratio))
    assert _check(result, "Non-Permissible Income")["status"] == expected_status
    if expected_issue:
        assert expected_issue in result["issues"]
    if expected_warning:
        assert expected_warning in result["warnings"]


# ── Whole-database sweep ──────────────────────────────────

@pytest.mark.parametrize("code", sorted(mizan.BURSA_DB))
def test_every_bursa_entry_screens_without_raising(code, monkeypatch):
    # The live Yahoo quote is the only network call on this path.
    monkeypatch.setattr(mizan, "fetch_bursa_live", lambda _code: None)
    screening = mizan.fetch_bursa_stock(code)["screening"]
    assert screening["verdict"] in KNOWN_VERDICTS


@pytest.mark.parametrize("ticker", ["JPM", "BAC"])
def test_us_flagged_banks_are_not_halal(ticker, monkeypatch):
    # The live price call is the only network call on this path.
    monkeypatch.setattr(mizan, "tiingo_get", lambda path, params=None: None)
    assert mizan.US_DB[ticker]["haramFlags"] == ["conventional_banking"]
    screening = mizan.fetch_us_db_stock(ticker)["screening"]
    assert screening["verdict"] == "Not Halal"
    assert "haram_industry" in screening["issues"]


def test_wmt_does_not_trip_the_keyword_scan(monkeypatch):
    # Walmart's description mentions alcohol in some stores, which is not the
    # same as alcohol being the core business.
    monkeypatch.setattr(mizan, "tiingo_get", lambda path, params=None: None)
    screening = mizan.fetch_us_db_stock("WMT")["screening"]
    assert "haram_industry" not in screening["issues"]


# ── Purification ──────────────────────────────────────────

def test_purification_normal_case():
    result = mizan.calc_purification(100.0, 0.10)
    assert result["purifyAmount"] == pytest.approx(10.0)
    assert result["keepAmount"] == pytest.approx(90.0)
    assert result["keepAmount"] + result["purifyAmount"] == pytest.approx(100.0)
    assert result["isRequired"] is True


@pytest.mark.parametrize("ratio", [None, 0, 0.0])
def test_purification_not_required(ratio):
    result = mizan.calc_purification(100.0, ratio)
    assert result["isRequired"] is False
    assert result["purifyAmount"] == 0
    assert result["keepAmount"] == 100.0


def test_high_ratio_uses_significant_wording():
    result = mizan.calc_purification(100.0, 0.10)
    assert "Significant" in result["note"]


def test_ratio_at_or_under_five_percent_uses_small_wording():
    for ratio in (0.05, 0.04):
        result = mizan.calc_purification(100.0, ratio)
        assert "Small" in result["note"]
        assert "Significant" not in result["note"]
