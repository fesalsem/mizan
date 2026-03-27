"""
╔══════════════════════════════════════════════════════════╗
║   MIZAN — Backend Server                                 ║
║   Serves real financial data to the frontend             ║
║   Run: python server.py                                  ║
╚══════════════════════════════════════════════════════════╝

Fetches data from Yahoo Finance using yfinance (no CORS issues),
applies Shariah screening logic, and serves it to the HTML frontend
via a local API on http://localhost:5000
"""

import json
import math
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import sys
import os

# ── Dependency check ──────────────────────────────────────
def check_deps():
    missing = []
    for pkg in ["yfinance", "pandas"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"\n[ERROR] Missing packages: {', '.join(missing)}")
        print("\nRun this first:\n")
        print(f"  pip install {' '.join(missing)}\n")
        sys.exit(1)

check_deps()

import yfinance as yf

# ══════════════════════════════════════════════════════════
#  SHARIAH SCREENING CONSTANTS
# ══════════════════════════════════════════════════════════

DEBT_THRESHOLD   = 0.33  # AAOIFI SS-21: interest-bearing debt < 33% of total assets
INCOME_THRESHOLD = 0.05  # DJIM: non-permissible income < 5% of total revenue
LIQUID_THRESHOLD = 0.33  # Cash + receivables < 33% of market cap (for trading)

HARAM_KEYWORDS = [
    "alcohol", "beer", "wine", "spirit", "spirits", "brew", "brewery",
    "distill", "distillery", "liquor", "whisky", "whiskey", "vodka",
    "tobacco", "cigarette", "cigarettes", "cigar", "cigars",
    "casino", "gambling", "lottery", "betting", "gaming resort",
    "pork", "swine", "pig farming",
    "adult entertainment", "pornograph",
    "arms manufacture", "ammunition", "weapon manufacturer",
    "conventional bank", "money lending", "pawnbroker",
    "insurance underwriting",
]

HARAM_SECTORS = [
    "alcohol", "tobacco", "gambling", "adult entertainment",
    "conventional banking",
]

DOUBTFUL_SECTORS = [
    "financial services", "diversified financial",
    "media", "entertainment",
    "food & beverage", "beverages",
    "hospitality", "hotel", "hotels",
    "restaurants",
]

# ══════════════════════════════════════════════════════════
#  DATA FETCHING
# ══════════════════════════════════════════════════════════

def normalise_ticker(symbol: str) -> str:
    """Convert user input to Yahoo Finance Bursa .KL ticker."""
    s = symbol.upper().strip()
    if s.endswith(".KL"):
        return s
    # Pure digits → pad to 4 chars
    if s.isdigit():
        return s.zfill(4) + ".KL"
    # Letters → append .KL
    return s + ".KL"


def safe(val, default=None):
    """Return None for NaN/Inf floats so JSON serialises cleanly."""
    if val is None:
        return default
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return default
    return val


def fetch_stock(symbol: str) -> dict:
    """
    Fetch comprehensive data for a Bursa Malaysia stock.
    Returns a dict ready to JSON-serialise.
    """
    ticker_str = normalise_ticker(symbol)
    tk = yf.Ticker(ticker_str)

    # ── Basic info ────────────────────────────────────────
    info = tk.info or {}
    if not info or (
        info.get("regularMarketPrice") is None and
        info.get("currentPrice") is None and
        info.get("previousClose") is None
    ):
        raise ValueError(f"No data found for '{ticker_str}'. Check the stock code.")

    # ── Price ─────────────────────────────────────────────
    price      = safe(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
    prev_close = safe(info.get("previousClose") or price)
    change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0

    # ── Balance sheet (most important for halal screening) ─
    # Strategy: try fast_info first, then full balance sheet download
    total_assets = safe(info.get("totalAssets"))
    total_debt   = safe(info.get("totalDebt"))

    # Fallback: download actual balance sheet
    if total_assets is None or total_debt is None:
        try:
            bs = tk.balance_sheet
            if bs is not None and not bs.empty:
                def get_bs(row_names):
                    for name in row_names:
                        matches = [c for c in bs.index if name.lower() in c.lower()]
                        if matches:
                            val = bs.loc[matches[0]].iloc[0]
                            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                                return float(val)
                    return None

                if total_assets is None:
                    total_assets = get_bs(["Total Assets", "TotalAssets"])
                if total_debt is None:
                    total_debt = get_bs([
                        "Total Debt", "Long Term Debt", "LongTermDebt",
                        "Total Long Term Debt", "Short Long Term Debt"
                    ])
        except Exception:
            pass

    # ── Income statement ──────────────────────────────────
    total_revenue    = safe(info.get("totalRevenue"))
    interest_expense = safe(info.get("interestExpense"))
    gross_profit     = safe(info.get("grossProfits"))

    if total_revenue is None or interest_expense is None:
        try:
            inc = tk.income_stmt
            if inc is not None and not inc.empty:
                def get_inc(row_names):
                    for name in row_names:
                        matches = [c for c in inc.index if name.lower() in c.lower()]
                        if matches:
                            val = inc.loc[matches[0]].iloc[0]
                            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                                return float(val)
                    return None

                if total_revenue is None:
                    total_revenue = get_inc(["Total Revenue", "TotalRevenue", "Revenue"])
                if interest_expense is None:
                    ie = get_inc(["Interest Expense", "InterestExpense"])
                    interest_expense = abs(ie) if ie is not None else None
        except Exception:
            pass

    # ── Compute screening ratios ──────────────────────────
    debt_ratio     = (total_debt / total_assets) if (total_assets and total_debt is not None and total_assets > 0) else None
    interest_ratio = (abs(interest_expense) / total_revenue) if (total_revenue and interest_expense is not None and total_revenue > 0) else None

    # ── Valuation & profitability ─────────────────────────
    pe_ratio         = safe(info.get("trailingPE") or info.get("forwardPE"))
    pb_ratio         = safe(info.get("priceToBook"))
    profit_margin    = safe(info.get("profitMargins"))
    return_on_equity = safe(info.get("returnOnEquity"))
    return_on_assets = safe(info.get("returnOnAssets"))
    dividend_yield   = safe(info.get("dividendYield") or info.get("trailingAnnualDividendYield"))
    earnings_growth  = safe(info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth"))
    revenue_growth   = safe(info.get("revenueGrowth"))
    current_ratio    = safe(info.get("currentRatio"))
    quick_ratio      = safe(info.get("quickRatio"))

    # ── Market data ───────────────────────────────────────
    market_cap    = safe(info.get("marketCap"))
    week52_high   = safe(info.get("fiftyTwoWeekHigh"))
    week52_low    = safe(info.get("fiftyTwoWeekLow"))
    volume        = safe(info.get("volume") or info.get("regularMarketVolume"))
    avg_volume    = safe(info.get("averageVolume"))
    beta          = safe(info.get("beta"))

    # ── 6-month price history ─────────────────────────────
    history = []
    try:
        hist = tk.history(period="6mo", interval="1mo")
        if not hist.empty:
            for ts, row in hist.iterrows():
                cl = row.get("Close")
                if cl is not None and not (isinstance(cl, float) and math.isnan(cl)):
                    history.append({
                        "date":  ts.strftime("%b %y"),
                        "close": round(float(cl), 4),
                        "open":  round(float(row.get("Open", cl)), 4),
                        "high":  round(float(row.get("High", cl)), 4),
                        "low":   round(float(row.get("Low",  cl)), 4),
                        "volume": int(row.get("Volume", 0) or 0),
                    })
    except Exception:
        pass

    # ── Shariah screening ─────────────────────────────────
    screening = screen_halal(
        name        = info.get("longName") or info.get("shortName") or ticker_str,
        sector      = info.get("sector") or "",
        industry    = info.get("industry") or "",
        description = (info.get("longBusinessSummary") or "")[:500],
        debt_ratio  = debt_ratio,
        interest_ratio = interest_ratio,
        pe_ratio    = pe_ratio,
        profit_margin  = profit_margin,
    )

    return {
        # Identity
        "ticker":       ticker_str,
        "name":         info.get("longName") or info.get("shortName") or ticker_str,
        "sector":       info.get("sector") or "N/A",
        "industry":     info.get("industry") or "N/A",
        "description":  (info.get("longBusinessSummary") or "")[:400],
        "exchange":     info.get("exchange") or "KLSE",
        "currency":     info.get("currency") or "MYR",

        # Price
        "price":        round(price, 4),
        "prevClose":    round(prev_close, 4),
        "changePct":    round(change_pct, 3),
        "week52High":   round(week52_high, 4) if week52_high else None,
        "week52Low":    round(week52_low, 4)  if week52_low  else None,
        "volume":       int(volume) if volume else None,
        "avgVolume":    int(avg_volume) if avg_volume else None,
        "marketCap":    int(market_cap) if market_cap else None,
        "beta":         round(beta, 3) if beta else None,

        # Financial ratios (raw values for display)
        "totalAssets":     int(total_assets)  if total_assets  else None,
        "totalDebt":       int(total_debt)    if total_debt    else None,
        "totalRevenue":    int(total_revenue) if total_revenue else None,
        "interestExpense": int(abs(interest_expense)) if interest_expense else None,
        "grossProfit":     int(gross_profit)  if gross_profit  else None,

        # Computed ratios
        "debtRatio":     round(debt_ratio, 4)     if debt_ratio     is not None else None,
        "interestRatio": round(interest_ratio, 4) if interest_ratio is not None else None,

        # Valuation
        "peRatio":        round(pe_ratio, 2)         if pe_ratio         else None,
        "pbRatio":        round(pb_ratio, 3)          if pb_ratio         else None,
        "profitMargin":   round(profit_margin, 4)     if profit_margin    is not None else None,
        "returnOnEquity": round(return_on_equity, 4)  if return_on_equity is not None else None,
        "returnOnAssets": round(return_on_assets, 4)  if return_on_assets is not None else None,
        "dividendYield":  round(dividend_yield, 4)    if dividend_yield   is not None else None,
        "earningsGrowth": round(earnings_growth, 4)   if earnings_growth  is not None else None,
        "revenueGrowth":  round(revenue_growth, 4)    if revenue_growth   is not None else None,
        "currentRatio":   round(current_ratio, 3)     if current_ratio    is not None else None,
        "quickRatio":     round(quick_ratio, 3)        if quick_ratio      is not None else None,

        # History
        "history": history,

        # Shariah screening result
        "screening": screening,
    }


# ══════════════════════════════════════════════════════════
#  SHARIAH SCREENING ENGINE
# ══════════════════════════════════════════════════════════

def screen_halal(name, sector, industry, description,
                 debt_ratio, interest_ratio, pe_ratio, profit_margin) -> dict:

    checks   = []
    issues   = []
    warnings = []

    combined = f"{sector} {industry} {name} {description}".lower()

    # ── Check 1: Business activity ────────────────────────
    haram_kw = next((kw for kw in HARAM_KEYWORDS if kw in combined), None)

    if haram_kw:
        checks.append({
            "status": "fail",
            "name": "Business Activity / Industry",
            "detail": (
                f'Keyword "{haram_kw}" found in sector/industry/description. '
                f'The primary business involves a categorically prohibited (haram) activity.'
            )
        })
        issues.append("haram_industry")
    else:
        doubtful = next((ds for ds in DOUBTFUL_SECTORS if ds in combined), None)
        if doubtful:
            checks.append({
                "status": "warn",
                "name": "Business Activity / Industry",
                "detail": (
                    f'Sector "{sector or "N/A"}" may involve mixed income sources. '
                    f'Not categorically haram, but requires additional verification. '
                    f'Cross-check against the SC Malaysia Shariah-compliant securities list.'
                )
            })
            warnings.append("doubtful_sector")
        else:
            checks.append({
                "status": "pass",
                "name": "Business Activity / Industry",
                "detail": (
                    f'Sector ({sector or "N/A"}) and industry ({industry or "N/A"}) '
                    f'do not match any categorically prohibited activity.'
                )
            })

    # ── Check 2: Debt-to-Assets ratio (AAOIFI ≤ 33%) ─────
    if debt_ratio is None:
        checks.append({
            "status": "warn",
            "name": "Debt-to-Assets Ratio (AAOIFI: ≤ 33%)",
            "detail": (
                "Balance sheet data unavailable from Yahoo Finance for this stock. "
                "Verify manually via Bursa Malaysia company disclosures or the annual report."
            )
        })
        warnings.append("no_debt_data")
    elif debt_ratio > DEBT_THRESHOLD:
        sev = "fail" if debt_ratio > 0.50 else "warn"
        checks.append({
            "status": sev,
            "name": "Debt-to-Assets Ratio (AAOIFI: ≤ 33%)",
            "detail": (
                f"Ratio is {debt_ratio*100:.1f}% — "
                f"{'significantly ' if debt_ratio > 0.50 else 'marginally '}"
                f"exceeds the AAOIFI 33% threshold. "
                f"High reliance on interest-bearing debt (riba)."
            )
        })
        if sev == "fail":
            issues.append("high_debt")
        else:
            warnings.append("marginal_debt")
    else:
        checks.append({
            "status": "pass",
            "name": "Debt-to-Assets Ratio (AAOIFI: ≤ 33%)",
            "detail": f"Ratio is {debt_ratio*100:.1f}% — within the permissible 33% limit."
        })

    # ── Check 3: Non-permissible income (DJIM ≤ 5%) ──────
    if interest_ratio is None:
        checks.append({
            "status": "warn",
            "name": "Non-Permissible Revenue (DJIM: ≤ 5%)",
            "detail": (
                "Interest expense / revenue data unavailable from Yahoo Finance. "
                "Verify manually via the company's annual report (look for 'interest income' in the income statement)."
            )
        })
        warnings.append("no_income_data")
    elif interest_ratio > INCOME_THRESHOLD:
        sev = "fail" if interest_ratio > 0.20 else "warn"
        checks.append({
            "status": sev,
            "name": "Non-Permissible Revenue (DJIM: ≤ 5%)",
            "detail": (
                f"Interest expense is {interest_ratio*100:.1f}% of revenue — "
                f"{'well above' if interest_ratio > 0.20 else 'above'} the 5% DJIM limit."
            )
        })
        if sev == "fail":
            issues.append("high_interest")
        else:
            warnings.append("marginal_interest")
    else:
        checks.append({
            "status": "pass",
            "name": "Non-Permissible Revenue (DJIM: ≤ 5%)",
            "detail": f"Interest expense is {interest_ratio*100:.1f}% of revenue — within the permissible 5% limit."
        })

    # ── Check 4: Gharar — real value creation ─────────────
    if pe_ratio is not None and pe_ratio < 0:
        checks.append({
            "status": "warn",
            "name": "Gharar Check — Real Value Creation",
            "detail": (
                f"Negative P/E ratio ({pe_ratio:.1f}x) — company is currently loss-making. "
                "Investing in a persistently loss-making company increases speculative risk (gharar). "
                "Proceed with caution."
            )
        })
        warnings.append("loss_making")
    elif profit_margin is not None and profit_margin < 0:
        checks.append({
            "status": "warn",
            "name": "Gharar Check — Real Value Creation",
            "detail": (
                f"Negative profit margin ({profit_margin*100:.1f}%) — company is operating at a loss. "
                "Review recent financial reports before investing."
            )
        })
        warnings.append("loss_making")
    else:
        checks.append({
            "status": "pass",
            "name": "Gharar Check — Real Value Creation",
            "detail": (
                "Company generates positive economic value. "
                + (f"P/E ratio: {pe_ratio:.1f}x." if pe_ratio else "P/E data unavailable.")
            )
        })

    # ── Overall verdict ───────────────────────────────────
    if issues:
        verdict = "Not Halal"
        v_class = "haram"
        v_icon  = "✗"
        v_reason = "Fails one or more categorical Shariah screening criteria."
    elif warnings:
        verdict = "Doubtful"
        v_class = "doubtful"
        v_icon  = "◐"
        v_reason = "Borderline on some criteria. Consult a qualified Islamic finance scholar."
    else:
        verdict = "Potentially Halal"
        v_class = "halal"
        v_icon  = "✓"
        v_reason = "Passes all standard Shariah screening criteria. Always verify with a scholar."

    # ── Risk level ────────────────────────────────────────
    if issues:
        risk = "HIGH"
    else:
        score = 0
        dr = debt_ratio or 0
        if dr > 0.25: score += 2
        elif dr > 0.15: score += 1
        pm = profit_margin or 0
        if pm < 0: score += 2
        elif pm < 0.05: score += 1
        score += len(warnings)
        risk = "HIGH" if score >= 4 else ("MEDIUM" if score >= 2 else "LOW")

    # ── Recommendation ────────────────────────────────────
    if verdict == "Not Halal":
        rec = "AVOID — Does not meet Shariah criteria."
    elif verdict == "Doubtful":
        rec = "CAUTION — Seek scholar's opinion before investing."
    else:
        pos = 0
        pe  = pe_ratio or 0
        pm2 = profit_margin or 0
        if 5 < pe < 20:  pos += 1
        if pm2 > 0.10:   pos += 1
        if risk == "LOW" and pos >= 2:
            rec = "BUY — Halal, low risk, solid fundamentals."
        elif risk == "LOW":
            rec = "HOLD / MONITOR — Halal and stable."
        elif risk == "MEDIUM":
            rec = "HOLD — Halal but moderate risk. Diversify."
        else:
            rec = "CAUTION — Halal but high volatility."

    return {
        "verdict":  verdict,
        "vClass":   v_class,
        "vIcon":    v_icon,
        "vReason":  v_reason,
        "checks":   checks,
        "issues":   issues,
        "warnings": warnings,
        "risk":     risk,
        "rec":      rec,
    }


# ══════════════════════════════════════════════════════════
#  HTTP SERVER
# ══════════════════════════════════════════════════════════

class MizanHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # Cleaner console output
        print(f"  [{self.command}] {self.path.split('?')[0]}  →  {args[1]}")

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        # Allow requests from any local file / GitHub Pages
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_json({}, 200)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # ── GET /screen?symbol=1295 ───────────────────────
        if parsed.path == "/screen":
            symbol = params.get("symbol", [None])[0]
            if not symbol:
                self.send_json({"error": "Missing ?symbol= parameter"}, 400)
                return
            try:
                data = fetch_stock(symbol.strip())
                self.send_json({"ok": True, "data": data})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 400)

        # ── GET /health ───────────────────────────────────
        elif parsed.path == "/health":
            self.send_json({"ok": True, "status": "Mizan backend running"})

        else:
            self.send_json({"error": "Not found"}, 404)


# ══════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════

PORT = 5000

def main():
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║  MIZAN Backend Server — Starting up             ║")
    print("╚══════════════════════════════════════════════════╝")
    print()
    print(f"  ✓  Server running at  http://localhost:{PORT}")
    print(f"  ✓  Open index.html in your browser to use the app")
    print(f"  ✓  Press Ctrl+C to stop")
    print()

    server = HTTPServer(("localhost", PORT), MizanHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n  Server stopped. بارك الله فيك\n")
        server.server_close()


if __name__ == "__main__":
    main()
