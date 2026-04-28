"""
Mizan Backend Server v3 — Alpha Vantage Edition
Run: python server.py

Data source switched from Yahoo Finance (blocked on cloud) to
Alpha Vantage (official API, works reliably on Render/cloud servers).

Requires environment variable: AV_API_KEY
Get a free key at: https://www.alphavantage.co/support/#api-key
"""
import json, math, time, threading, re
from flask import Flask, request, jsonify
from pathlib import Path
import os, sys, importlib.util
import requests

app = Flask(__name__)

# ── API Key ───────────────────────────────────────────────
AV_KEY = os.environ.get("AV_API_KEY", "")
AV_BASE = "https://www.alphavantage.co/query"

def check_setup():
    missing_pkgs = [p for p in ["requests"] if not importlib.util.find_spec(p)]
    if missing_pkgs:
        print(f"\n[ERROR] Missing packages: {', '.join(missing_pkgs)}")
        sys.exit(1)
    if not AV_KEY:
        print("\n[ERROR] AV_API_KEY environment variable not set.")
        print("  Get a free key at: https://www.alphavantage.co/support/#api-key")
        print("  Then set it in Render: Environment → AV_API_KEY = your_key\n")
        sys.exit(1)
    print(f"  ✓  Alpha Vantage API key loaded")

check_setup()

# ── Cache ─────────────────────────────────────────────────
# TTL is longer (30 min) since we have 25 req/day limit
CACHE: dict = {}
CACHE_TTL   = 1800  # 30 minutes
_cache_lock = threading.Lock()

def cache_get(key):
    with _cache_lock:
        e = CACHE.get(key)
        return e["data"] if e and time.time() < e["expires_at"] else None

def cache_set(key, data):
    with _cache_lock:
        CACHE[key] = {"data": data, "expires_at": time.time() + CACHE_TTL}

def cache_clear(key=None):
    with _cache_lock:
        if key: CACHE.pop(key, None)
        else:   CACHE.clear()

def cache_stats():
    with _cache_lock:
        now  = time.time()
        live = sum(1 for e in CACHE.values() if now < e["expires_at"])
        return {"cached": live, "total": len(CACHE), "ttl_seconds": CACHE_TTL}

# ── Daily request counter ─────────────────────────────────
# Tracks how many Alpha Vantage API calls used today
_req_lock      = threading.Lock()
_req_count     = 0
_req_day       = time.strftime("%Y-%m-%d")
AV_DAILY_LIMIT = 25

def req_increment():
    global _req_count, _req_day
    with _req_lock:
        today = time.strftime("%Y-%m-%d")
        if today != _req_day:          # new day — reset counter
            _req_count = 0
            _req_day   = today
        _req_count += 1
        return _req_count

def req_stats():
    with _req_lock:
        today = time.strftime("%Y-%m-%d")
        if today != _req_day:
            return {"used": 0, "remaining": AV_DAILY_LIMIT, "limit": AV_DAILY_LIMIT, "date": today}
        return {
            "used":      _req_count,
            "remaining": max(0, AV_DAILY_LIMIT - _req_count),
            "limit":     AV_DAILY_LIMIT,
            "date":      _req_day,
        }

# ── SC Malaysia Shariah List ──────────────────────────────
SC_LIST_FILE = Path(__file__).parent / "sc_shariah_list.json"
_sc_list: dict = {}
_sc_lock = threading.Lock()

SC_COMPLIANT = {
    "1295","1155","4197","5347","5183","6012","6888","7277","5168",
    "3816","4588","5014","5020","5085","0072","0082","7084","7160",
    "5216","5228","3026","3301","5090","7052","1562","2445","5101",
    "8664","2291","5138","0055","0078","6033","5285","0148","5878",
    "1015","1023","1066","1082","1171","1198",
}
SC_NON_COMPLIANT = {
    "3255",  # Carlsberg
    "3293",  # Heineken Malaysia
    "4162",  # BAT Malaysia
    "1961",  # Genting Berhad
    "3182",  # Genting Malaysia
}

def load_sc_list():
    global _sc_list
    with _sc_lock:
        if _sc_list:
            return _sc_list
        if SC_LIST_FILE.exists():
            try:
                raw = json.loads(SC_LIST_FILE.read_text())
                if time.time() - raw.get("fetched_at", 0) < 86400:
                    _sc_list = raw.get("stocks", {})
                    print(f"  ✓  SC list loaded from cache ({len(_sc_list)} stocks)")
                    return _sc_list
            except Exception:
                pass
        stocks = {}
        for c in SC_COMPLIANT:
            stocks[c.zfill(4)] = {"status": "compliant",     "source": "builtin"}
        for c in SC_NON_COMPLIANT:
            stocks[c.zfill(4)] = {"status": "non_compliant", "source": "builtin"}
        _sc_list = stocks
        try:
            SC_LIST_FILE.write_text(json.dumps({
                "fetched_at": time.time(), "stocks": stocks,
                "note": "Built-in list. Verify at sc.com.my for full accuracy."
            }, indent=2))
        except Exception:
            pass
        print(f"  ✓  SC list initialised ({len(stocks)} stocks)")
        return _sc_list

def check_sc_list(ticker):
    # Normalise: strip exchange suffixes for Bursa check
    bursa_code = None
    if ticker.endswith(".KL"):
        bursa_code = ticker.replace(".KL", "").zfill(4)
    elif ticker.isdigit():
        bursa_code = ticker.zfill(4)

    if not bursa_code:
        return {"found": False, "status": "not_applicable",
                "note": "SC Malaysia list covers Bursa Malaysia stocks only."}

    sc    = load_sc_list()
    entry = sc.get(bursa_code)
    if not entry:
        return {"found": False, "status": "not_found",
                "note": "Not in built-in SC list. Verify manually at sc.com.my"}
    s = entry["status"]
    return {
        "found":  True,
        "status": s,
        "source": entry.get("source", "builtin"),
        "note": (
            f"Listed as Shariah-{'compliant' if s == 'compliant' else 'non-compliant'} "
            f"by SC Malaysia (built-in data). Always verify the latest list at sc.com.my"
        )
    }

# ── Constants ─────────────────────────────────────────────
DEBT_THRESHOLD   = 0.33
INCOME_THRESHOLD = 0.05

HARAM_KEYWORDS = [
    "alcohol","beer","wine","spirit","spirits","brew","brewery","distill",
    "distillery","liquor","whisky","whiskey","vodka","tobacco","cigarette",
    "cigarettes","cigar","cigars","casino","gambling","lottery","betting",
    "gaming resort","pork","swine","pig farming","adult entertainment",
    "pornograph","arms manufacture","ammunition","weapon manufacturer",
    "conventional bank","money lending","pawnbroker","insurance underwriting",
]
DOUBTFUL_SECTORS = [
    "financial services","diversified financial","media","entertainment",
    "food & beverage","beverages","hospitality","hotel","hotels","restaurants",
]

# Bursa Malaysia 4-digit codes and their AV-compatible symbol
# Alpha Vantage uses format like "1295.KLS" for Bursa stocks
EXCHANGE_SUFFIXES = {
    ".KL":  "Bursa Malaysia",
    ".L":   "London Stock Exchange",
    ".PA":  "Euronext Paris",
    ".DE":  "Frankfurt / XETRA",
    ".HK":  "Hong Kong",
    ".T":   "Tokyo",
    ".AX":  "ASX Australia",
    ".SI":  "Singapore",
    ".SS":  "Shanghai",
    ".SZ":  "Shenzhen",
}

US_KNOWN = {
    "AAPL","MSFT","GOOGL","GOOG","AMZN","TSLA","NVDA","META","NFLX","AMD",
    "INTC","QCOM","AVGO","TXN","MU","AMAT","LRCX","KLAC","JPM","BAC","GS",
    "MS","WFC","C","BRK-B","BRK-A","JNJ","PFE","MRK","ABBV","LLY","BMY",
    "AMGN","GILD","XOM","CVX","COP","SLB","EOG","WMT","COST","TGT","HD",
    "MCD","SBUX","NKE","DIS","CMCSA","T","VZ","TMUS","V","MA","PYPL","AXP",
    "SQ","COIN","BABA","JD","PDD","BIDU","NIO","XPEV","LI",
}

def safe(v, d=None):
    if v is None: return d
    try:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return d
    except Exception: return d
    return v

def safe_float(v, d=None):
    try:    return float(v) if v not in (None, "", "None", "N/A", "-") else d
    except: return d

def safe_int(v, d=None):
    try:    return int(float(v)) if v not in (None, "", "None", "N/A", "-") else d
    except: return d

# ══════════════════════════════════════════════════════════
#  TICKER NORMALISATION
# ══════════════════════════════════════════════════════════

def normalise_ticker(symbol: str) -> str:
    """
    Convert user input to a clean ticker string.
    For Alpha Vantage: US stocks use plain symbol (AAPL),
    Bursa stocks use 4-digit code (1295) — AV handles the exchange.
    """
    s = symbol.upper().strip().replace(" ", "")

    # Already has exchange suffix — strip .KL for AV (it uses plain code)
    if s.endswith(".KL"):
        code = s.replace(".KL", "")
        return code.zfill(4) if code.isdigit() else code

    # Pure digits = Bursa code
    if s.isdigit():
        return s.zfill(4)

    # Known US ticker
    if s in US_KNOWN:
        return s

    # Short alpha string — treat as US ticker
    if s.isalpha() and len(s) <= 5:
        return s

    return s


def is_bursa(symbol: str) -> bool:
    """Check if a symbol is a Bursa Malaysia stock."""
    s = symbol.replace(".KL", "")
    return s.isdigit()

# ══════════════════════════════════════════════════════════
#  ALPHA VANTAGE API CALLS
# ══════════════════════════════════════════════════════════

def av_get(params: dict, timeout: int = 15) -> dict:
    """Make a request to Alpha Vantage API and count usage."""
    params["apikey"] = AV_KEY
    used = req_increment()
    print(f"  AV call #{used}: {params.get('function','?')} {params.get('symbol','')}")
    try:
        resp = requests.get(AV_BASE, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        # ── AV error responses ────────────────────────────
        if "Error Message" in data:
            err = data["Error Message"]
            # Invalid ticker — friendly message
            if "Invalid API call" in err or "ticker" in err.lower():
                raise ValueError(
                    f"Ticker not found. Check the stock code and try again. "
                    f"US stocks: AAPL, TSLA, NVDA. Bursa: 1295, 1155, 5347."
                )
            raise ValueError(f"Alpha Vantage error: {err}")

        if "Note" in data:
            # Per-minute limit hit (5 req/min on free tier) — not daily limit
            note = data["Note"]
            if "minute" in note.lower():
                raise ValueError(
                    "Too many requests per minute. "
                    "Please wait 60 seconds and try again. "
                    "(Free tier limit: 5 requests/minute)"
                )
            raise ValueError(f"Alpha Vantage note: {note}")

        if "Information" in data:
            info_msg = data["Information"]
            # Could be: daily limit OR unactivated key OR premium feature
            if "premium" in info_msg.lower():
                raise ValueError(
                    "This data requires an Alpha Vantage premium plan. "
                    "Basic stock data should still work — try a different ticker."
                )
            if "thank you" in info_msg.lower() or "api call frequency" in info_msg.lower():
                raise ValueError(
                    f"Alpha Vantage daily limit reached ({AV_DAILY_LIMIT} requests/day on free tier). "
                    f"Used today: {used}. Resets at midnight UTC. "
                    f"Tip: previously screened stocks are cached for 30 minutes — check your watchlist."
                )
            # Unknown information message — log and continue if data present
            print(f"  AV Information: {info_msg[:100]}")
            if not any(data.values()):
                raise ValueError(f"Alpha Vantage: {info_msg[:150]}")

        return data
    except requests.RequestException as e:
        raise ValueError(f"Network error: {e}. Check your internet connection.")


def fetch_quote(symbol: str) -> dict:
    """
    Fetch real-time quote using GLOBAL_QUOTE endpoint.
    Works for US stocks and international symbols.
    """
    # For Bursa, Alpha Vantage uses format like "1295.KLS"
    av_symbol = symbol + ".KLS" if is_bursa(symbol) else symbol
    data = av_get({"function": "GLOBAL_QUOTE", "symbol": av_symbol})
    quote = data.get("Global Quote", {})
    if not quote or not quote.get("05. price"):
        # Retry without exchange suffix for Bursa
        if is_bursa(symbol):
            data  = av_get({"function": "GLOBAL_QUOTE", "symbol": symbol})
            quote = data.get("Global Quote", {})
    if not quote or not quote.get("05. price"):
        raise ValueError(
            f"No quote data found for '{symbol}'. "
            "Check the stock code or try the full ticker (e.g. AAPL, TSLA, 1295)."
        )
    return quote


def fetch_overview(symbol: str) -> dict:
    """
    Fetch company overview — name, sector, industry, financial ratios.
    This is the main source of fundamental data.
    """
    av_symbol = symbol + ".KLS" if is_bursa(symbol) else symbol
    data = av_get({"function": "OVERVIEW", "symbol": av_symbol})
    if not data or not data.get("Symbol"):
        if is_bursa(symbol):
            data = av_get({"function": "OVERVIEW", "symbol": symbol})
    return data if data and data.get("Symbol") else {}


def fetch_income_statement(symbol: str) -> dict:
    """Fetch annual income statement for interest/revenue ratio."""
    av_symbol = symbol + ".KLS" if is_bursa(symbol) else symbol
    try:
        data = av_get({"function": "INCOME_STATEMENT", "symbol": av_symbol})
        reports = data.get("annualReports", [])
        return reports[0] if reports else {}
    except Exception:
        return {}


def fetch_balance_sheet(symbol: str) -> dict:
    """Fetch annual balance sheet for debt/assets ratio."""
    av_symbol = symbol + ".KLS" if is_bursa(symbol) else symbol
    try:
        data = av_get({"function": "BALANCE_SHEET", "symbol": av_symbol})
        reports = data.get("annualReports", [])
        return reports[0] if reports else {}
    except Exception:
        return {}


def fetch_history(symbol: str) -> list:
    """
    Fetch 6-month monthly price history using TIME_SERIES_MONTHLY.
    Returns list of {date, close, open, high, low, volume} dicts.
    """
    av_symbol = symbol + ".KLS" if is_bursa(symbol) else symbol
    try:
        data = av_get({"function": "TIME_SERIES_MONTHLY", "symbol": av_symbol})
        series = data.get("Monthly Time Series", {})
        if not series and is_bursa(symbol):
            data   = av_get({"function": "TIME_SERIES_MONTHLY", "symbol": symbol})
            series = data.get("Monthly Time Series", {})

        history = []
        # Sort by date descending, take last 6 months
        for date_str in sorted(series.keys(), reverse=True)[:6]:
            row = series[date_str]
            history.insert(0, {
                "date":   date_str[:7],   # YYYY-MM
                "close":  safe_float(row.get("4. close"),  0),
                "open":   safe_float(row.get("1. open"),   0),
                "high":   safe_float(row.get("2. high"),   0),
                "low":    safe_float(row.get("3. low"),    0),
                "volume": safe_int(row.get("5. volume"),   0),
            })
        return history
    except Exception:
        return []

# ══════════════════════════════════════════════════════════
#  MAIN FETCH FUNCTION
#  Uses 2 AV API calls per stock (quote + overview)
#  Balance sheet + income stmt are best-effort (2 more calls)
#  History is best-effort (1 more call)
#  With caching, most stocks only cost 1-2 calls/day
# ══════════════════════════════════════════════════════════

def fetch_stock(symbol: str) -> dict:
    ticker = normalise_ticker(symbol)

    # ── Cache check ───────────────────────────────────────
    cached = cache_get(ticker)
    if cached:
        r = dict(cached); r["_cached"] = True
        return r

    print(f"  Fetching {ticker} from Alpha Vantage...")

    # ── 1. Real-time quote (1 API call) ───────────────────
    quote = fetch_quote(ticker)

    price      = safe_float(quote.get("05. price"),          0)
    prev_close = safe_float(quote.get("08. previous close"), price)
    change_pct = safe_float(quote.get("10. change percent", "0%").replace("%",""), 0)
    volume     = safe_int(quote.get("06. volume"),           0)
    week_high  = safe_float(quote.get("03. high"),           price)  # day high as proxy
    week_low   = safe_float(quote.get("04. low"),            price)  # day low as proxy

    # ── 2. Company overview + fundamentals (1 API call) ───
    overview = fetch_overview(ticker)

    name        = overview.get("Name")      or ticker
    sector      = overview.get("Sector")    or "N/A"
    industry    = overview.get("Industry")  or "N/A"
    description = (overview.get("Description") or "")[:400]
    exchange    = overview.get("Exchange")  or "N/A"
    currency    = overview.get("Currency")  or ("MYR" if is_bursa(ticker) else "USD")
    market_cap  = safe_int(overview.get("MarketCapitalization"))

    # Valuation ratios — all directly in overview
    pe_ratio    = safe_float(overview.get("TrailingPE") or overview.get("ForwardPE"))
    pb_ratio    = safe_float(overview.get("PriceToBookRatio"))
    profit_margin    = safe_float(overview.get("ProfitMargin"))
    return_on_equity = safe_float(overview.get("ReturnOnEquityTTM"))
    return_on_assets = safe_float(overview.get("ReturnOnAssetsTTM"))
    dividend_yield   = safe_float(overview.get("DividendYield"))
    beta             = safe_float(overview.get("Beta"))
    week52_high      = safe_float(overview.get("52WeekHigh")) or week_high
    week52_low       = safe_float(overview.get("52WeekLow"))  or week_low
    revenue_growth   = safe_float(overview.get("QuarterlyRevenueGrowthYOY"))
    earnings_growth  = safe_float(overview.get("QuarterlyEarningsGrowthYOY"))

    # ── 3. Balance sheet (1 API call) ─────────────────────
    bs           = fetch_balance_sheet(ticker)
    total_assets = safe_float(bs.get("totalAssets"))
    total_debt   = safe_float(
        bs.get("longTermDebt") or bs.get("shortLongTermDebtTotal") or bs.get("totalLiabilities")
    )
    current_ratio = safe_float(bs.get("currentRatio"))

    # ── 4. Income statement (1 API call) ──────────────────
    inc           = fetch_income_statement(ticker)
    total_revenue = safe_float(inc.get("totalRevenue"))
    int_expense   = safe_float(inc.get("interestExpense"))
    gross_profit  = safe_float(inc.get("grossProfit"))

    # Fallback: use overview values if income stmt empty
    if total_revenue is None:
        total_revenue = safe_float(overview.get("RevenueTTM"))
    if gross_profit is None:
        gross_profit  = safe_float(overview.get("GrossProfitTTM"))

    # ── 5. Compute Shariah ratios ─────────────────────────
    debt_ratio = (
        total_debt / total_assets
        if total_assets and total_debt is not None and total_assets > 0
        else None
    )
    interest_ratio = (
        abs(int_expense) / total_revenue
        if total_revenue and int_expense is not None and total_revenue > 0
        else None
    )

    # ── 6. Price history (1 API call) ─────────────────────
    history = fetch_history(ticker)

    # ── 7. SC Malaysia check ──────────────────────────────
    sc_check  = check_sc_list(ticker)

    # ── 8. Shariah screening ──────────────────────────────
    screening = screen_halal(
        name=name, sector=sector, industry=industry, description=description,
        debt_ratio=debt_ratio, interest_ratio=interest_ratio,
        pe_ratio=pe_ratio, profit_margin=profit_margin, sc_check=sc_check,
    )

    result = {
        # Identity
        "ticker":       ticker,
        "name":         name,
        "sector":       sector,
        "industry":     industry,
        "description":  description,
        "exchange":     exchange,
        "currency":     currency,
        # Price
        "price":        round(price, 4),
        "prevClose":    round(prev_close, 4),
        "changePct":    round(change_pct, 3),
        "week52High":   round(week52_high, 4) if week52_high else None,
        "week52Low":    round(week52_low,  4) if week52_low  else None,
        "volume":       volume,
        "avgVolume":    None,   # not in AV free tier
        "marketCap":    market_cap,
        "beta":         round(beta, 3) if beta else None,
        # Financials
        "totalAssets":    safe_int(total_assets),
        "totalDebt":      safe_int(total_debt),
        "totalRevenue":   safe_int(total_revenue),
        "interestExpense":safe_int(abs(int_expense)) if int_expense else None,
        "grossProfit":    safe_int(gross_profit),
        # Ratios
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
        "quickRatio":     None,  # not in AV free tier
        # History & screening
        "history":    history,
        "scCheck":    sc_check,
        "screening":  screening,
        "fetchedAt":  time.strftime("%H:%M:%S"),
        "_cached":    False,
        "_source":    "Alpha Vantage",
    }

    cache_set(ticker, result)
    return result


# ══════════════════════════════════════════════════════════
#  SHARIAH SCREENING ENGINE (unchanged)
# ══════════════════════════════════════════════════════════

def screen_halal(name, sector, industry, description,
                 debt_ratio, interest_ratio, pe_ratio,
                 profit_margin, sc_check=None):
    checks=[]; issues=[]; warnings=[]
    combined = f"{sector} {industry} {name} {description}".lower()

    # Check 0: SC Malaysia official list
    if sc_check and sc_check.get("found"):
        s = sc_check["status"]
        if s == "compliant":
            checks.append({"status":"pass","name":"SC Malaysia Official Shariah List",
                "detail":f"✓ Listed as Shariah-compliant by SC Malaysia. {sc_check.get('note','')}"})
        elif s == "non_compliant":
            checks.append({"status":"fail","name":"SC Malaysia Official Shariah List",
                "detail":f"✗ Listed as non-Shariah-compliant by SC Malaysia. {sc_check.get('note','')}"})
            issues.append("sc_non_compliant")
    elif sc_check and sc_check.get("status") == "not_applicable":
        pass
    else:
        checks.append({"status":"warn","name":"SC Malaysia Official Shariah List",
            "detail":"Not found in built-in SC list. Verify manually at sc.com.my"})
        warnings.append("sc_not_found")

    # Check 1: Haram industry
    hkw = next((kw for kw in HARAM_KEYWORDS if kw in combined), None)
    if hkw:
        checks.append({"status":"fail","name":"Business Activity / Industry",
            "detail":f'Keyword "{hkw}" detected. Core business involves a prohibited activity.'})
        issues.append("haram_industry")
    else:
        ds = next((d for d in DOUBTFUL_SECTORS if d in combined), None)
        if ds:
            checks.append({"status":"warn","name":"Business Activity / Industry",
                "detail":f'Sector "{sector}" may have mixed income sources. Requires verification.'})
            warnings.append("doubtful_sector")
        else:
            checks.append({"status":"pass","name":"Business Activity / Industry",
                "detail":f'Sector ({sector}) / Industry ({industry}) — no prohibited activity detected.'})

    # Check 2: Debt ratio
    if debt_ratio is None:
        checks.append({"status":"warn","name":"Debt-to-Assets Ratio (AAOIFI: ≤ 33%)",
            "detail":"Balance sheet data unavailable. Verify via annual report."})
        warnings.append("no_debt_data")
    elif debt_ratio > DEBT_THRESHOLD:
        sev = "fail" if debt_ratio > 0.50 else "warn"
        checks.append({"status":sev,"name":"Debt-to-Assets Ratio (AAOIFI: ≤ 33%)",
            "detail":f"Ratio is {debt_ratio*100:.1f}% — {'significantly ' if debt_ratio>0.50 else 'marginally '}exceeds 33% AAOIFI threshold."})
        issues.append("high_debt") if sev=="fail" else warnings.append("marginal_debt")
    else:
        checks.append({"status":"pass","name":"Debt-to-Assets Ratio (AAOIFI: ≤ 33%)",
            "detail":f"Ratio is {debt_ratio*100:.1f}% — within the permissible 33% limit."})

    # Check 3: Interest income
    if interest_ratio is None:
        checks.append({"status":"warn","name":"Non-Permissible Revenue (DJIM: ≤ 5%)",
            "detail":"Interest/revenue data unavailable. Verify via annual report."})
        warnings.append("no_income_data")
    elif interest_ratio > INCOME_THRESHOLD:
        sev = "fail" if interest_ratio > 0.20 else "warn"
        checks.append({"status":sev,"name":"Non-Permissible Revenue (DJIM: ≤ 5%)",
            "detail":f"Interest is {interest_ratio*100:.1f}% of revenue — {'well above' if interest_ratio>0.20 else 'above'} the 5% DJIM limit."})
        issues.append("high_interest") if sev=="fail" else warnings.append("marginal_interest")
    else:
        checks.append({"status":"pass","name":"Non-Permissible Revenue (DJIM: ≤ 5%)",
            "detail":f"Interest expense is {interest_ratio*100:.1f}% of revenue — within the 5% limit."})

    # Check 4: Gharar
    if pe_ratio is not None and pe_ratio < 0:
        checks.append({"status":"warn","name":"Gharar Check — Real Value Creation",
            "detail":f"Negative P/E ({pe_ratio:.1f}x) — company is loss-making. Increases speculative risk."})
        warnings.append("loss_making")
    elif profit_margin is not None and profit_margin < 0:
        checks.append({"status":"warn","name":"Gharar Check — Real Value Creation",
            "detail":f"Negative profit margin ({profit_margin*100:.1f}%) — company operating at a loss."})
        warnings.append("loss_making")
    else:
        checks.append({"status":"pass","name":"Gharar Check — Real Value Creation",
            "detail":"Company generates positive economic value. " +
                     (f"P/E: {pe_ratio:.1f}x." if pe_ratio else "P/E data unavailable.")})

    if issues:
        verdict,v_class,v_icon,v_reason = "Not Halal","haram","✗","Fails one or more categorical Shariah screening criteria."
    elif warnings:
        verdict,v_class,v_icon,v_reason = "Doubtful","doubtful","◐","Borderline on some criteria. Consult a qualified Islamic finance scholar."
    else:
        verdict,v_class,v_icon,v_reason = "Potentially Halal","halal","✓","Passes all standard Shariah screening criteria. Always verify with a scholar."

    if issues: risk = "HIGH"
    else:
        s = 0
        dr = debt_ratio or 0; pm = profit_margin or 0
        if dr > 0.25: s+=2
        elif dr > 0.15: s+=1
        if pm < 0: s+=2
        elif pm < 0.05: s+=1
        s += len(warnings)
        risk = "HIGH" if s>=4 else ("MEDIUM" if s>=2 else "LOW")

    if verdict=="Not Halal":  rec = "AVOID — Does not meet Shariah criteria."
    elif verdict=="Doubtful": rec = "CAUTION — Seek scholar's opinion before investing."
    else:
        pos = 0
        if pe_ratio and 5<pe_ratio<20: pos+=1
        if profit_margin and profit_margin>0.10: pos+=1
        if risk=="LOW" and pos>=2:  rec = "BUY — Halal, low risk, solid fundamentals."
        elif risk=="LOW":           rec = "HOLD / MONITOR — Halal and stable."
        elif risk=="MEDIUM":        rec = "HOLD — Halal but moderate risk. Diversify."
        else:                       rec = "CAUTION — Halal but high volatility."

    return {"verdict":verdict,"vClass":v_class,"vIcon":v_icon,"vReason":v_reason,
            "checks":checks,"issues":issues,"warnings":warnings,"risk":risk,"rec":rec}


# ══════════════════════════════════════════════════════════
#  DIVIDEND PURIFICATION CALCULATOR (unchanged)
# ══════════════════════════════════════════════════════════

def calc_purification(dividend, interest_ratio, currency="MYR"):
    if interest_ratio is None or interest_ratio <= 0:
        return {"dividend":dividend,"interestRatio":interest_ratio,
                "purifyAmount":0.0,"keepAmount":dividend,"currency":currency,
                "note":"No purification needed — interest ratio is zero or not applicable.",
                "isRequired":False}
    purify    = round(dividend * interest_ratio, 4)
    keep      = round(dividend - purify, 4)
    intensity = "Small" if interest_ratio <= 0.05 else "Significant"
    note = (f"{intensity} purification required. Donate {currency} {purify:.2f} to charity "
            f"({interest_ratio*100:.1f}% of dividend). You keep {currency} {keep:.2f}.")
    if interest_ratio > 0.05:
        note += " Consider whether this stock is suitable for your portfolio."
    return {"dividend":dividend,"interestRatio":interest_ratio,"purifyAmount":purify,
            "keepAmount":keep,"currency":currency,"note":note,
            "isRequired":purify>0,"percentage":round(interest_ratio*100,2)}


# ══════════════════════════════════════════════════════════
#  FLASK ROUTES
# ══════════════════════════════════════════════════════════

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/")
def root():
    return jsonify({
        "ok":      True,
        "message": "Mizan Backend Active — Alpha Vantage Edition",
        "cache":   cache_stats(),
        "requests": req_stats(),
    })

@app.route("/screen")
def screen():
    symbol = request.args.get("symbol","").strip()
    if not symbol or not re.match(r'^[A-Za-z0-9.\-]{1,12}$', symbol):
        return jsonify({"ok": False, "error": "Invalid or missing symbol"}), 400
    try:
        data   = fetch_stock(symbol)
        cached = data.get("_cached", False)
        return jsonify({"ok": True, "data": data, "cached": cached})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/purify")
def purify():
    try:
        div = float(request.args.get("dividend",       0))
        rat = float(request.args.get("interest_ratio", 0))
        cur = request.args.get("currency", "MYR").upper()[:3]
        if div < 0 or not (0 <= rat <= 1):
            raise ValueError("Invalid parameters.")
        return jsonify({"ok": True, "data": calc_purification(div, rat, cur)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/cache/stats")
def stats():
    return jsonify({"ok": True, "data": cache_stats()})

@app.route("/cache/clear")
def clear_cache():
    sym = request.args.get("symbol")
    if sym:
        cache_clear(normalise_ticker(sym.strip()))
        return jsonify({"ok": True, "message": f"Cleared cache for {sym}"})
    cache_clear()
    return jsonify({"ok": True, "message": "Full cache cleared"})

@app.route("/health")
def health():
    return jsonify({
        "ok":      True,
        "status":  "Mizan backend running — Alpha Vantage",
        "cache":   cache_stats(),
        "av_key":  "set" if AV_KEY else "MISSING",
        "requests": req_stats(),
    })

@app.route("/usage")
def usage():
    """Check how many API requests have been used today."""
    stats = req_stats()
    return jsonify({"ok": True, "data": stats,
        "message": f"Used {stats['used']} of {stats['limit']} requests today. {stats['remaining']} remaining."
    })

if __name__ == "__main__":
    load_sc_list()
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  MIZAN Backend v3 — Alpha Vantage Edition       ║")
    print("╚══════════════════════════════════════════════════╝\n")
    print(f"  ✓  Cache TTL: {CACHE_TTL//60} minutes")
    print(f"  ✓  Data source: Alpha Vantage (25 req/day free)")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
