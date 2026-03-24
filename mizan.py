"""
╔══════════════════════════════════════════════════════════╗
║        MIZAN — Halal Investment Screener                 ║
║        Bursa Malaysia Edition                            ║
║        ميزان — The Balance                              ║
╚══════════════════════════════════════════════════════════╝

A real-data, halal-compliant stock screener for Bursa Malaysia.
Uses Yahoo Finance (yfinance) for live market data.

DISCLAIMER: This is NOT financial advice. Always consult a
qualified Islamic finance scholar and a licensed financial
advisor before making any investment decisions.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Dependency check ──────────────────────────────────────
def check_deps():
    missing = []
    for pkg in ["yfinance", "pandas", "requests"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"\n[SETUP] Missing packages: {', '.join(missing)}")
        print("Run this first:\n")
        print(f"  pip install {' '.join(missing)}\n")
        sys.exit(1)

check_deps()

import yfinance as yf
import pandas as pd

# ═══════════════════════════════════════════════════════════
#  CONSTANTS & CONFIGURATION
# ═══════════════════════════════════════════════════════════

WATCHLIST_FILE = Path(__file__).parent / "watchlist.json"

# Debt-to-assets threshold (AAOIFI / DJIM standard)
DEBT_THRESHOLD   = 0.33   # 33%
# Non-permissible income threshold
INCOME_THRESHOLD = 0.05   # 5%
# Cash + receivables threshold
LIQUID_THRESHOLD = 0.33   # 33%

# Bursa Malaysia tickers end with .KL on Yahoo Finance
BURSA_SUFFIX = ".KL"

# ── Known Haram sectors / keywords ────────────────────────
HARAM_SECTORS = {
    "alcohol", "brewery", "distillery", "beverage alcohol",
    "tobacco", "cigarettes",
    "gambling", "casino", "lottery", "gaming",  # gaming ≠ video games but flagged for review
    "pork", "swine",
    "banking", "conventional bank", "finance conventional",
    "insurance",                    # conventional insurance
    "defense", "weapons", "arms",
    "adult entertainment", "pornography",
}

HARAM_KEYWORDS = [
    "alcohol", "beer", "wine", "spirit", "brew", "distill",
    "tobacco", "cigarette", "cigar",
    "casino", "gambling", "lottery", "bet",
    "pork", "swine", "pig",
    "bank", "insurance",            # broad — refined by sector check
    "weapon", "arms", "defence", "defense",
    "adult", "entertainment adult",
]

# Sectors that need extra scrutiny but aren't categorical bans
DOUBTFUL_SECTORS = {
    "financial services",           # check for riba
    "media",                        # check content
    "food & beverage",              # check ingredients
    "hotel",                        # check alcohol service
    "hospitality",
}

# ── ANSI colours ──────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    GOLD   = "\033[33m"
    DIM    = "\033[2m"
    BLUE   = "\033[94m"

def clr(text, colour):
    return f"{colour}{text}{C.RESET}"

# ═══════════════════════════════════════════════════════════
#  DATA FETCHING
# ═══════════════════════════════════════════════════════════

def bursa_ticker(symbol: str) -> str:
    """Normalise input to Yahoo Finance Bursa ticker format."""
    symbol = symbol.upper().strip()
    if symbol.endswith(".KL"):
        return symbol
    # If it's a pure number (KLSE code) pad to 4 digits
    if symbol.isdigit():
        symbol = symbol.zfill(4)
    return symbol + BURSA_SUFFIX


def fetch_stock_data(symbol: str) -> dict:
    """
    Fetch live data from Yahoo Finance.
    Returns a structured dict or raises on failure.
    """
    ticker_str = bursa_ticker(symbol)
    print(clr(f"\n  Fetching live data for {ticker_str} …", C.DIM))

    try:
        tk = yf.Ticker(ticker_str)
        info = tk.info

        # Validate we got real data
        if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
            return {"error": f"No data found for '{ticker_str}'. Check the ticker symbol."}

        # ── Price data ────────────────────────────────────
        price       = info.get("currentPrice") or info.get("regularMarketPrice", 0)
        prev_close  = info.get("previousClose", price)
        change_pct  = ((price - prev_close) / prev_close * 100) if prev_close else 0
        week52_high = info.get("fiftyTwoWeekHigh", 0)
        week52_low  = info.get("fiftyTwoWeekLow", 0)
        volume      = info.get("volume", 0)
        market_cap  = info.get("marketCap", 0)

        # ── Company info ──────────────────────────────────
        name        = info.get("longName") or info.get("shortName", ticker_str)
        sector      = info.get("sector", "Unknown")
        industry    = info.get("industry", "Unknown")
        description = info.get("longBusinessSummary", "")

        # ── Financial ratios (balance sheet) ─────────────
        # Debt-to-equity proxy via totalDebt / totalAssets
        total_debt   = info.get("totalDebt", 0) or 0
        total_assets = info.get("totalAssets", 0) or 0
        total_equity = info.get("bookValue", 0) or 0

        debt_ratio = (total_debt / total_assets) if total_assets else None

        # Revenue & income
        total_revenue    = info.get("totalRevenue", 0) or 0
        interest_expense = info.get("interestExpense", 0) or 0
        # Approximate non-halal income ratio from interest expense vs revenue
        # (real screeners use full income statement breakdown)
        interest_ratio   = abs(interest_expense / total_revenue) if total_revenue else None

        # Profitability
        profit_margin    = info.get("profitMargins", None)
        return_on_equity = info.get("returnOnEquity", None)
        return_on_assets = info.get("returnOnAssets", None)
        pe_ratio         = info.get("trailingPE", None)
        pb_ratio         = info.get("priceToBook", None)
        dividend_yield   = info.get("dividendYield", None)

        # ── Historical price (6 months) ───────────────────
        hist = tk.history(period="6mo")
        hist_data = []
        if not hist.empty:
            # Monthly summary
            hist["Month"] = hist.index.to_period("M")
            monthly = hist.groupby("Month")["Close"].last()
            hist_data = [(str(m), round(float(v), 4)) for m, v in monthly.items()]

        return {
            "ticker":          ticker_str,
            "symbol_input":    symbol,
            "name":            name,
            "sector":          sector,
            "industry":        industry,
            "description":     description[:300] if description else "",
            "price":           price,
            "prev_close":      prev_close,
            "change_pct":      change_pct,
            "week52_high":     week52_high,
            "week52_low":      week52_low,
            "volume":          volume,
            "market_cap":      market_cap,
            "total_debt":      total_debt,
            "total_assets":    total_assets,
            "total_equity":    total_equity,
            "total_revenue":   total_revenue,
            "interest_expense":interest_expense,
            "debt_ratio":      debt_ratio,
            "interest_ratio":  interest_ratio,
            "profit_margin":   profit_margin,
            "return_on_equity":return_on_equity,
            "return_on_assets":return_on_assets,
            "pe_ratio":        pe_ratio,
            "pb_ratio":        pb_ratio,
            "dividend_yield":  dividend_yield,
            "history":         hist_data,
            "fetched_at":      datetime.now().isoformat(),
            "error":           None,
        }

    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
#  HALAL SCREENING ENGINE
# ═══════════════════════════════════════════════════════════

def screen_halal(data: dict) -> dict:
    """
    Apply multi-layer Shariah screening to company data.
    Returns verdict dict with detailed breakdown.
    """
    checks   = []
    issues   = []   # categorical failures → NOT HALAL
    warnings = []   # borderline → DOUBTFUL

    sector   = (data.get("sector") or "").lower()
    industry = (data.get("industry") or "").lower()
    name     = (data.get("name") or "").lower()
    desc     = (data.get("description") or "").lower()

    combined = f"{sector} {industry} {name} {desc}"

    # ── Check 1: Categorical haram industry ───────────────
    haram_match = None
    for kw in HARAM_KEYWORDS:
        if kw in combined:
            haram_match = kw
            break

    if haram_match:
        checks.append({
            "name":   "Industry / Business Activity",
            "status": "FAIL",
            "detail": f"Keyword '{haram_match}' detected in sector/industry/description. "
                      f"Core business may involve a categorically prohibited activity."
        })
        issues.append("haram_industry")
    else:
        checks.append({
            "name":   "Industry / Business Activity",
            "status": "PASS",
            "detail": f"Sector ({data.get('sector','?')}) / Industry ({data.get('industry','?')}) "
                      f"does not match categorical haram criteria."
        })

    # Flag doubtful sectors for extra scrutiny
    for ds in DOUBTFUL_SECTORS:
        if ds in combined and not haram_match:
            checks[-1]["status"] = "WARN"
            checks[-1]["detail"] += f" ⚠ Sector '{ds}' requires additional manual verification."
            warnings.append("doubtful_sector")
            break

    # ── Check 2: Debt-to-assets ratio ─────────────────────
    dr = data.get("debt_ratio")
    if dr is None:
        checks.append({
            "name":   "Debt-to-Assets Ratio (AAOIFI ≤ 33%)",
            "status": "WARN",
            "detail": "Balance sheet data unavailable from Yahoo Finance for this ticker. "
                      "Verify manually via Bursa KLSE disclosures."
        })
        warnings.append("no_debt_data")
    elif dr > DEBT_THRESHOLD:
        status = "FAIL" if dr > 0.50 else "WARN"
        checks.append({
            "name":   "Debt-to-Assets Ratio (AAOIFI ≤ 33%)",
            "status": status,
            "detail": f"Ratio is {dr*100:.1f}% — {'significantly ' if dr>0.50 else 'marginally '}exceeds "
                      f"the {DEBT_THRESHOLD*100:.0f}% AAOIFI threshold. Interest-bearing debt is high."
        })
        if status == "FAIL":
            issues.append("high_debt")
        else:
            warnings.append("marginal_debt")
    else:
        checks.append({
            "name":   "Debt-to-Assets Ratio (AAOIFI ≤ 33%)",
            "status": "PASS",
            "detail": f"Ratio is {dr*100:.1f}% — within the permissible {DEBT_THRESHOLD*100:.0f}% limit."
        })

    # ── Check 3: Non-permissible income ratio ─────────────
    ir = data.get("interest_ratio")
    if ir is None:
        checks.append({
            "name":   "Non-Permissible Income (≤ 5% of Revenue)",
            "status": "WARN",
            "detail": "Income statement data unavailable. Verify interest income manually."
        })
        warnings.append("no_income_data")
    elif ir > INCOME_THRESHOLD:
        status = "FAIL" if ir > 0.20 else "WARN"
        checks.append({
            "name":   "Non-Permissible Income (≤ 5% of Revenue)",
            "status": status,
            "detail": f"Estimated interest expense is {ir*100:.1f}% of revenue — "
                      f"{'well above' if ir>0.20 else 'above'} the 5% threshold."
        })
        if status == "FAIL":
            issues.append("high_interest")
        else:
            warnings.append("marginal_interest")
    else:
        checks.append({
            "name":   "Non-Permissible Income (≤ 5% of Revenue)",
            "status": "PASS",
            "detail": f"Estimated interest component is {ir*100:.1f}% of revenue — within 5% limit."
        })

    # ── Check 4: Business model / value creation ──────────
    pe = data.get("pe_ratio")
    if pe and pe < 0:
        checks.append({
            "name":   "Profitability & Real Value Creation",
            "status": "WARN",
            "detail": "Negative P/E ratio — company is currently loss-making. "
                      "Speculative investment increases risk. Exercise caution (avoid gharar)."
        })
        warnings.append("loss_making")
    else:
        checks.append({
            "name":   "Profitability & Real Value Creation",
            "status": "PASS",
            "detail": "Company appears to generate real economic value. "
                      + (f"P/E ratio: {pe:.1f}x." if pe else "P/E data unavailable.")
        })

    # ── Determine overall verdict ──────────────────────────
    if issues:
        verdict       = "NOT HALAL"
        verdict_color = C.RED
        verdict_icon  = "✗"
        verdict_reason = "Fails one or more categorical Shariah screening criteria."
    elif warnings:
        verdict       = "DOUBTFUL"
        verdict_color = C.YELLOW
        verdict_icon  = "◐"
        verdict_reason = "Some criteria are borderline. Consult a qualified Islamic finance scholar."
    else:
        verdict       = "POTENTIALLY HALAL"
        verdict_color = C.GREEN
        verdict_icon  = "✓"
        verdict_reason = "Passes standard screening criteria. Always verify with a Shariah advisor."

    # ── Risk level ─────────────────────────────────────────
    risk = _assess_risk(data, issues, warnings)

    # ── Recommendation ────────────────────────────────────
    recommendation = _recommend(data, verdict, risk)

    return {
        "verdict":          verdict,
        "verdict_color":    verdict_color,
        "verdict_icon":     verdict_icon,
        "verdict_reason":   verdict_reason,
        "checks":           checks,
        "issues":           issues,
        "warnings":         warnings,
        "risk":             risk,
        "recommendation":   recommendation,
    }


def _assess_risk(data: dict, issues: list, warnings: list) -> str:
    score = 0

    # Haram → always high
    if issues:
        return "HIGH"

    # Debt level
    dr = data.get("debt_ratio") or 0
    if dr > 0.25:
        score += 2
    elif dr > 0.15:
        score += 1

    # Profitability
    pm = data.get("profit_margin") or 0
    if pm < 0:
        score += 2
    elif pm < 0.05:
        score += 1

    # Volatility proxy: 52-week spread
    high = data.get("week52_high") or 0
    low  = data.get("week52_low") or 1
    price = data.get("price") or 1
    spread = (high - low) / price if price else 0
    if spread > 0.6:
        score += 2
    elif spread > 0.3:
        score += 1

    # Warnings add risk
    score += len(warnings)

    if score >= 4:
        return "HIGH"
    elif score >= 2:
        return "MEDIUM"
    return "LOW"


def _recommend(data: dict, verdict: str, risk: str) -> str:
    if verdict == "NOT HALAL":
        return "AVOID — This stock does not meet halal criteria."
    if verdict == "DOUBTFUL":
        return "CAUTION — Seek scholarly opinion before investing."

    # Halal — assess investment attractiveness
    pe  = data.get("pe_ratio")
    pb  = data.get("pb_ratio")
    pm  = data.get("profit_margin") or 0
    roe = data.get("return_on_equity") or 0

    positives = 0
    if pe and 5 < pe < 20:   positives += 1
    if pb and pb < 1.5:       positives += 1
    if pm > 0.10:             positives += 1
    if roe > 0.12:            positives += 1

    if risk == "LOW" and positives >= 3:
        return "BUY — Halal, low risk, solid fundamentals. Suitable for long-term holding."
    elif risk == "LOW" and positives >= 1:
        return "HOLD / MONITOR — Halal and stable. Watch for better entry points."
    elif risk == "MEDIUM":
        return "HOLD — Halal but moderate risk. Diversify; don't over-concentrate."
    else:
        return "CAUTION — Halal but high volatility. Only if you can tolerate the risk."


# ═══════════════════════════════════════════════════════════
#  WATCHLIST (persistent JSON)
# ═══════════════════════════════════════════════════════════

def load_watchlist() -> list:
    if WATCHLIST_FILE.exists():
        try:
            return json.loads(WATCHLIST_FILE.read_text())
        except Exception:
            return []
    return []


def save_watchlist(wl: list):
    WATCHLIST_FILE.write_text(json.dumps(wl, indent=2))


def add_to_watchlist(symbol: str, data: dict, screening: dict):
    wl = load_watchlist()
    entry = {
        "symbol":     symbol.upper(),
        "ticker":     data.get("ticker"),
        "name":       data.get("name"),
        "added_at":   datetime.now().isoformat(),
        "verdict":    screening.get("verdict"),
        "risk":       screening.get("risk"),
        "recommendation": screening.get("recommendation"),
    }
    # Update if already in watchlist
    wl = [e for e in wl if e.get("symbol") != symbol.upper()]
    wl.append(entry)
    save_watchlist(wl)
    print(clr(f"\n  ✓ Added {symbol.upper()} to watchlist.", C.GREEN))


def remove_from_watchlist(symbol: str):
    wl = load_watchlist()
    before = len(wl)
    wl = [e for e in wl if e.get("symbol") != symbol.upper().replace(".KL","")]
    if len(wl) < before:
        save_watchlist(wl)
        print(clr(f"\n  ✓ Removed {symbol.upper()} from watchlist.", C.GREEN))
    else:
        print(clr(f"\n  ✗ {symbol.upper()} not found in watchlist.", C.YELLOW))


# ═══════════════════════════════════════════════════════════
#  DISPLAY FUNCTIONS
# ═══════════════════════════════════════════════════════════

SEP  = clr("─" * 60, C.DIM)
SEP2 = clr("═" * 60, C.GOLD)

def fmt_myr(val) -> str:
    if val is None or val == 0:
        return "N/A"
    if val >= 1_000_000_000:
        return f"MYR {val/1e9:.2f}B"
    if val >= 1_000_000:
        return f"MYR {val/1e6:.2f}M"
    return f"MYR {val:,.0f}"

def fmt_pct(val) -> str:
    if val is None:
        return "N/A"
    return f"{val*100:.1f}%"

def fmt_price(val) -> str:
    if val is None:
        return "N/A"
    return f"MYR {val:.4f}"

def print_header():
    print()
    print(clr("╔══════════════════════════════════════════════════════════╗", C.GOLD))
    print(clr("║  ", C.GOLD) + clr("  MIZAN  —  Halal Investment Screener  ", C.BOLD) + clr("         ║", C.GOLD))
    print(clr("║  ", C.GOLD) + clr("  ميزان  —  Bursa Malaysia Edition     ", C.DIM)  + clr("         ║", C.GOLD))
    print(clr("╚══════════════════════════════════════════════════════════╝", C.GOLD))
    print()

def print_stock_report(data: dict, screening: dict):
    v = screening
    vc = v["verdict_color"]

    print()
    print(SEP2)
    print(clr(f"  {data['name']}", C.BOLD) + clr(f"  [{data['ticker']}]", C.DIM))
    print(clr(f"  {data['sector']} › {data['industry']}", C.DIM))
    print(SEP2)

    # ── Price block ───────────────────────────────────────
    change_sym = "▲" if data["change_pct"] >= 0 else "▼"
    change_clr = C.GREEN if data["change_pct"] >= 0 else C.RED
    print()
    print(clr("  PRICE", C.GOLD))
    chg_str = f"{change_sym} {abs(data['change_pct']):.2f}%"
    print(f"  Current:  {clr(fmt_price(data['price']), C.BOLD)}  {clr(chg_str, change_clr)}")
    print(f"  52w High: {fmt_price(data['week52_high'])}    "
          f"52w Low: {fmt_price(data['week52_low'])}")
    print(f"  Volume:   {data['volume']:,}" if data['volume'] else "  Volume:  N/A")
    print(f"  Mkt Cap:  {fmt_myr(data['market_cap'])}")

    # ── Financial metrics ─────────────────────────────────
    print()
    print(SEP)
    print(clr("  FINANCIALS", C.GOLD))
    pe_str = f"{data['pe_ratio']:.1f}x" if data['pe_ratio'] else 'N/A'
    pb_str = f"{data['pb_ratio']:.2f}x" if data['pb_ratio'] else 'N/A'
    print(f"  P/E Ratio:         {pe_str}")
    print(f"  P/B Ratio:         {pb_str}")
    print(f"  Profit Margin:     {fmt_pct(data['profit_margin'])}")
    print(f"  Return on Equity:  {fmt_pct(data['return_on_equity'])}")
    print(f"  Return on Assets:  {fmt_pct(data['return_on_assets'])}")
    print(f"  Dividend Yield:    {fmt_pct(data['dividend_yield'])}")
    print(f"  Total Assets:      {fmt_myr(data['total_assets'])}")
    print(f"  Total Debt:        {fmt_myr(data['total_debt'])}")
    print(f"  Total Revenue:     {fmt_myr(data['total_revenue'])}")

    # ── 6-month price chart (sparkline) ───────────────────
    if data.get("history"):
        print()
        print(SEP)
        print(clr("  6-MONTH PRICE HISTORY", C.GOLD))
        _print_sparkline(data["history"])

    # ── Halal screening results ───────────────────────────
    print()
    print(SEP)
    print(clr("  SHARIAH SCREENING", C.GOLD))
    print()

    check_icons = {"PASS": clr("✓", C.GREEN), "WARN": clr("◐", C.YELLOW), "FAIL": clr("✗", C.RED)}
    for chk in screening["checks"]:
        icon = check_icons.get(chk["status"], "?")
        print(f"  {icon}  {clr(chk['name'], C.BOLD)}")
        print(clr(f"     {chk['detail']}", C.DIM))
        print()

    # ── Verdict ───────────────────────────────────────────
    print(SEP)
    risk_clr = {
        "LOW": C.GREEN, "MEDIUM": C.YELLOW, "HIGH": C.RED
    }.get(v["risk"], C.YELLOW)

    print()
    print(f"  {clr('VERDICT:', C.BOLD)}  "
          f"{clr(v['verdict_icon'] + '  ' + v['verdict'], vc + C.BOLD)}")
    print(clr(f"  {v['verdict_reason']}", C.DIM))
    print()
    print(f"  {clr('RISK LEVEL:', C.BOLD)}     {clr(v['risk'], risk_clr + C.BOLD)}")
    print()
    print(f"  {clr('RECOMMENDATION:', C.BOLD)}")
    print(f"  {clr(v['recommendation'], vc)}")
    print()
    print(clr("  ⚠  DISCLAIMER: This is NOT financial advice. Consult a", C.DIM))
    print(clr("     licensed advisor and Islamic finance scholar.", C.DIM))
    print()
    print(SEP2)
    print()


def _print_sparkline(history: list):
    """Print a terminal bar chart of monthly prices."""
    if not history:
        return
    prices  = [p for _, p in history]
    labels  = [m[-7:] for m, _ in history]  # YYYY-MM
    min_p   = min(prices)
    max_p   = max(prices)
    rng     = max_p - min_p or 1
    bars    = "▁▂▃▄▅▆▇█"
    line    = ""
    for p in prices:
        idx  = int((p - min_p) / rng * (len(bars) - 1))
        line += clr(bars[idx], C.CYAN)
    print(f"  {line}")
    # Price range labels
    print(clr(f"  Low: {fmt_price(min_p)}  →  High: {fmt_price(max_p)}", C.DIM))
    # Month labels (every other)
    lbl_row = "  "
    for i, lbl in enumerate(labels):
        lbl_row += (lbl if i % 2 == 0 else " " * len(lbl)) + " "
    print(clr(lbl_row, C.DIM))


def print_watchlist():
    wl = load_watchlist()
    print()
    print(SEP2)
    print(clr("  MY WATCHLIST", C.GOLD + C.BOLD))
    print(SEP2)
    if not wl:
        print(clr("  No stocks in watchlist yet. Screen a stock and add it.", C.DIM))
    else:
        for e in wl:
            vc = {
                "POTENTIALLY HALAL": C.GREEN,
                "DOUBTFUL":          C.YELLOW,
                "NOT HALAL":         C.RED,
            }.get(e.get("verdict",""), C.DIM)
            rc = {"LOW": C.GREEN, "MEDIUM": C.YELLOW, "HIGH": C.RED}.get(e.get("risk",""), C.DIM)
            sym_str  = clr(f"{e.get('symbol','?'):<12}", C.BOLD)
            name_str = clr(f"{e.get('name','?')[:28]:<30}", C.DIM)
            verd_str = clr(e.get('verdict','?'), vc)
            print(f"  {sym_str}  {name_str}  {verd_str}")
            print(f"  {'':12}  Risk: {clr(e.get('risk','?'), rc)}"
                  f"  |  {clr(e.get('recommendation',''), C.DIM)}")
            print(f"  {'':12}  Added: {e.get('added_at','?')[:10]}")
            print()
    print(SEP2)
    print()


def print_menu():
    print(clr("  MAIN MENU", C.GOLD + C.BOLD))
    print()
    print(clr("  [1]", C.CYAN) + "  Screen a Bursa Malaysia stock")
    print(clr("  [2]", C.CYAN) + "  View my watchlist")
    print(clr("  [3]", C.CYAN) + "  Refresh watchlist with live data")
    print(clr("  [4]", C.CYAN) + "  Remove stock from watchlist")
    print(clr("  [0]", C.DIM)  + "  Exit")
    print()


# ═══════════════════════════════════════════════════════════
#  MAIN APPLICATION LOOP
# ═══════════════════════════════════════════════════════════

def screen_flow():
    print()
    symbol = input(clr("  Enter stock code (e.g. 1295, MAYBANK, 5347.KL): ", C.CYAN)).strip()
    if not symbol:
        return

    data = fetch_stock_data(symbol)

    if data.get("error"):
        print(clr(f"\n  ✗ Error: {data['error']}", C.RED))
        print(clr("  Tip: Use the Bursa stock code (e.g. 1295 for PBBANK) "
                  "or ticker (e.g. MAYBANK).", C.DIM))
        return

    screening = screen_halal(data)
    print_stock_report(data, screening)

    # Offer to add to watchlist
    ans = input(clr("  Add to watchlist? (y/n): ", C.CYAN)).strip().lower()
    if ans == "y":
        add_to_watchlist(symbol, data, screening)


def refresh_watchlist():
    wl = load_watchlist()
    if not wl:
        print(clr("\n  Watchlist is empty.", C.YELLOW))
        return
    print(clr("\n  Refreshing watchlist with live data…\n", C.DIM))
    updated = []
    for entry in wl:
        sym = entry.get("symbol","")
        data = fetch_stock_data(sym)
        if data.get("error"):
            print(clr(f"  ✗ {sym}: {data['error']}", C.RED))
            updated.append(entry)
        else:
            screening = screen_halal(data)
            entry["verdict"]        = screening["verdict"]
            entry["risk"]           = screening["risk"]
            entry["recommendation"] = screening["recommendation"]
            entry["price"]          = data.get("price")
            entry["change_pct"]     = data.get("change_pct")
            updated.append(entry)
            vc = {
                "POTENTIALLY HALAL": C.GREEN,
                "DOUBTFUL": C.YELLOW,
                "NOT HALAL": C.RED,
            }.get(screening["verdict"], C.DIM)
            change_sym = "▲" if (data.get("change_pct") or 0) >= 0 else "▼"
            change_clr = C.GREEN if (data.get("change_pct") or 0) >= 0 else C.RED
            chg_pct = data.get("change_pct", 0)
            chg_str = clr(f"{change_sym} {abs(chg_pct):.2f}%", change_clr)
            print(f"  {clr(sym, C.BOLD):<15}  {fmt_price(data.get('price')):<12}  {chg_str:<10}  {clr(screening['verdict'], vc)}")
    save_watchlist(updated)
    print(clr("\n  ✓ Watchlist updated.", C.GREEN))


def remove_flow():
    wl = load_watchlist()
    if not wl:
        print(clr("\n  Watchlist is empty.", C.YELLOW))
        return
    print()
    for i, e in enumerate(wl, 1):
        print(f"  [{i}]  {clr(e.get('symbol','?'), C.BOLD)}  {e.get('name','?')}")
    print()
    sym = input(clr("  Enter stock code to remove: ", C.CYAN)).strip()
    if sym:
        remove_from_watchlist(sym)


def main():
    print_header()
    print(clr("  Live Shariah-compliant stock screener for Bursa Malaysia.", C.DIM))
    print(clr("  Data sourced in real-time from Yahoo Finance.", C.DIM))
    print()

    while True:
        print_menu()
        choice = input(clr("  Your choice: ", C.CYAN)).strip()
        print()

        if choice == "1":
            screen_flow()
        elif choice == "2":
            print_watchlist()
        elif choice == "3":
            refresh_watchlist()
        elif choice == "4":
            remove_flow()
        elif choice == "0":
            print(clr("  بارك الله فيك — May Allah bless you.\n", C.GOLD))
            break
        else:
            print(clr("  Invalid choice. Please enter 1–4 or 0.", C.YELLOW))


if __name__ == "__main__":
    main()
