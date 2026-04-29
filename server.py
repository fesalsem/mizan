"""
Mizan Backend Server v4 — Financial Modeling Prep Edition
Run: python server.py

Data source: Financial Modeling Prep (FMP)
- 250 free requests/day
- Real fundamentals on free tier
- Works reliably on cloud servers

Requires environment variable: FMP_API_KEY
Get a free key at: https://site.financialmodelingprep.com/developer/docs
"""
import json, math, time, threading, re
from flask import Flask, request, jsonify
from pathlib import Path
import os, sys, importlib.util
import requests

app = Flask(__name__)

# ── API Setup ─────────────────────────────────────────────
FMP_KEY  = os.environ.get("FMP_API_KEY", "")
FMP_BASE = "https://financialmodelingprep.com/api/v3"

def check_setup():
    if not FMP_KEY:
        print("\n[ERROR] FMP_API_KEY environment variable not set.")
        print("  Get a free key at: https://site.financialmodelingprep.com")
        print("  Then in Render: Environment → FMP_API_KEY = your_key\n")
        sys.exit(1)
    print("  ✓  FMP API key loaded")

check_setup()

# ── Cache (30 min TTL to conserve daily quota) ────────────
CACHE: dict = {}
CACHE_TTL   = 1800
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
_req_lock      = threading.Lock()
_req_count     = 0
_req_day       = time.strftime("%Y-%m-%d")
FMP_DAILY_LIMIT = 250

def req_increment():
    global _req_count, _req_day
    with _req_lock:
        today = time.strftime("%Y-%m-%d")
        if today != _req_day:
            _req_count = 0
            _req_day   = today
        _req_count += 1
        return _req_count

def req_stats():
    with _req_lock:
        today = time.strftime("%Y-%m-%d")
        if today != _req_day:
            return {"used": 0, "remaining": FMP_DAILY_LIMIT,
                    "limit": FMP_DAILY_LIMIT, "date": today}
        return {
            "used":      _req_count,
            "remaining": max(0, FMP_DAILY_LIMIT - _req_count),
            "limit":     FMP_DAILY_LIMIT,
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
    "1015","1023","1082","1171","1198",
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
                    print(f"  ✓  SC list loaded ({len(_sc_list)} stocks)")
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
            }, indent=2))
        except Exception:
            pass
        print(f"  ✓  SC list initialised ({len(stocks)} stocks)")
        return _sc_list

def check_sc_list(ticker):
    code = None
    clean = ticker.replace(".KL", "").replace(".KLS", "")
    if clean.isdigit():
        code = clean.zfill(4)
    if not code:
        return {"found": False, "status": "not_applicable",
                "note": "SC Malaysia list covers Bursa Malaysia stocks only."}
    sc    = load_sc_list()
    entry = sc.get(code)
    if not entry:
        return {"found": False, "status": "not_found",
                "note": "Not in built-in SC list. Verify manually at sc.com.my"}
    s = entry["status"]
    return {
        "found":  True, "status": s,
        "source": entry.get("source", "builtin"),
        "note": (
            f"Listed as Shariah-{'compliant' if s=='compliant' else 'non-compliant'} "
            f"by SC Malaysia (built-in data). Always verify the latest list at sc.com.my"
        )
    }

# ── Screening Constants ───────────────────────────────────
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

US_KNOWN = {
    "AAPL","MSFT","GOOGL","GOOG","AMZN","TSLA","NVDA","META","NFLX","AMD",
    "INTC","QCOM","AVGO","TXN","MU","AMAT","JPM","BAC","GS","MS","WFC",
    "JNJ","PFE","MRK","ABBV","LLY","XOM","CVX","WMT","COST","HD","MCD",
    "SBUX","NKE","DIS","V","MA","PYPL","BABA","NIO","COIN","SQ",
}

# ── Helpers ───────────────────────────────────────────────
def safe_float(v, d=None):
    try:    return float(v) if v not in (None, "", "None", "N/A", "-", 0, "0") else d
    except: return d

def safe_int(v, d=None):
    try:    return int(float(v)) if v not in (None, "", "None", "N/A", "-") else d
    except: return d

def normalise_ticker(symbol: str) -> str:
    s = symbol.upper().strip().replace(" ", "")
    # Strip .KL suffix — FMP uses plain codes for Bursa
    if s.endswith(".KL"):
        code = s[:-3]
        return code.zfill(4) if code.isdigit() else code
    if s.isdigit():
        return s.zfill(4)
    if s in US_KNOWN or (s.isalpha() and len(s) <= 5):
        return s
    return s

def is_bursa(symbol: str) -> bool:
    return symbol.replace(".KL", "").isdigit()

# ══════════════════════════════════════════════════════════
#  FMP API CALLS
# ══════════════════════════════════════════════════════════

def fmp_get(endpoint: str, params: dict = None, timeout: int = 15) -> any:
    """
    Make a single request to FMP API.
    Handles errors, rate limits, and invalid keys cleanly.
    """
    p = params or {}
    p["apikey"] = FMP_KEY
    used = req_increment()
    url  = f"{FMP_BASE}/{endpoint}"
    print(f"  FMP #{used}: /{endpoint.split('?')[0]}")

    try:
        resp = requests.get(url, params=p, timeout=timeout,
                            headers={"User-Agent": "Mizan/4.0"})

        # Rate limit
        if resp.status_code == 429:
            raise ValueError(
                "Too many requests. Please wait a moment and try again."
            )

        # Unauthorised — wrong or missing key
        if resp.status_code == 401:
            raise ValueError(
                "Invalid FMP API key. Check FMP_API_KEY in Render environment settings."
            )

        if not resp.ok:
            raise ValueError(f"FMP API error {resp.status_code}")

        data = resp.json()

        # FMP returns error messages inside the response body
        if isinstance(data, dict):
            if "Error Message" in data:
                msg = data["Error Message"]
                if "Limit Reach" in msg or "limit" in msg.lower():
                    raise ValueError(
                        f"FMP daily limit reached ({FMP_DAILY_LIMIT} requests/day on free tier). "
                        f"Used today: {used}. Resets at midnight UTC."
                    )
                if "Invalid API KEY" in msg or "API key" in msg.lower():
                    raise ValueError(
                        "Invalid FMP API key. Please check your key at "
                        "financialmodelingprep.com and update FMP_API_KEY in Render."
                    )
                raise ValueError(f"FMP error: {msg}")

            if "message" in data and "not available" in str(data.get("message","")).lower():
                return None  # endpoint not available on free tier — handled by caller

        return data

    except requests.Timeout:
        raise ValueError("Request timed out. FMP may be slow — please try again.")
    except requests.RequestException as e:
        raise ValueError(f"Network error: {e}")


def fmp_get_list(endpoint: str, params: dict = None) -> list:
    """FMP GET that expects a list response."""
    data = fmp_get(endpoint, params)
    if isinstance(data, list):
        return data
    return []


def fmp_get_first(endpoint: str, params: dict = None) -> dict:
    """FMP GET that returns the first item of a list response."""
    items = fmp_get_list(endpoint, params)
    return items[0] if items else {}

# ══════════════════════════════════════════════════════════
#  MAIN FETCH FUNCTION
# ══════════════════════════════════════════════════════════

def fetch_stock(symbol: str) -> dict:
    ticker = normalise_ticker(symbol)

    # ── Cache check ───────────────────────────────────────
    cached = cache_get(ticker)
    if cached:
        r = dict(cached); r["_cached"] = True
        return r

    print(f"\n  === Fetching {ticker} ===")

    # ── For Bursa stocks: FMP uses format like "1295.KL" ──
    fmp_symbol = ticker + ".KL" if is_bursa(ticker) else ticker

    # ── 1. Quote + Profile (combined — 1 API call) ────────
    # FMP /profile includes price, sector, industry, description, mktCap
    profile = fmp_get_first(f"profile/{fmp_symbol}")

    if not profile or not profile.get("price"):
        # Try without exchange suffix for Bursa
        if is_bursa(ticker):
            profile = fmp_get_first(f"profile/{ticker}")
        if not profile or not profile.get("price"):
            raise ValueError(
                f"No data found for '{ticker}'. "
                "Check the stock code. US examples: AAPL, TSLA, NVDA. "
                "Bursa examples: 1295, 1155, 5347."
            )

    name        = profile.get("companyName")    or ticker
    sector      = profile.get("sector")         or "N/A"
    industry    = profile.get("industry")        or "N/A"
    description = (profile.get("description")   or "")[:400]
    exchange    = profile.get("exchangeShortName") or "N/A"
    currency    = profile.get("currency")        or ("MYR" if is_bursa(ticker) else "USD")
    market_cap  = safe_int(profile.get("mktCap"))
    beta        = safe_float(profile.get("beta"))
    price       = safe_float(profile.get("price"), 0)
    change_pct  = safe_float(profile.get("changes"), 0)
    # Compute change_pct as percentage if not already
    prev_close  = price / (1 + change_pct/100) if (price and change_pct) else price
    week52_high = safe_float(profile.get("range", "0-0").split("-")[-1]) if profile.get("range") else None
    week52_low  = safe_float(profile.get("range", "0-0").split("-")[0])  if profile.get("range") else None
    volume      = safe_int(profile.get("volAvg"))
    image       = profile.get("image")

    # ── 2. Key metrics TTM (1 API call) ───────────────────
    # Gives P/E, P/B, dividend yield, ROE, ROA, debt/equity etc.
    metrics = fmp_get_first(f"key-metrics-ttm/{fmp_symbol}")
    if not metrics and is_bursa(ticker):
        metrics = fmp_get_first(f"key-metrics-ttm/{ticker}")
    metrics = metrics or {}

    pe_ratio         = safe_float(metrics.get("peRatioTTM"))
    pb_ratio         = safe_float(metrics.get("pbRatioTTM"))
    dividend_yield   = safe_float(metrics.get("dividendYieldTTM") or
                                   metrics.get("dividendYieldPercentageTTM"))
    return_on_equity = safe_float(metrics.get("roeTTM"))
    return_on_assets = safe_float(metrics.get("returnOnTangibleAssetsTTM"))
    debt_to_equity   = safe_float(metrics.get("debtToEquityTTM"))
    current_ratio    = safe_float(metrics.get("currentRatioTTM"))
    revenue_per_share= safe_float(metrics.get("revenuePerShareTTM"))

    # ── 3. Income statement (1 API call) ──────────────────
    inc = fmp_get_first(f"income-statement/{fmp_symbol}", {"limit": "1"})
    if not inc and is_bursa(ticker):
        inc = fmp_get_first(f"income-statement/{ticker}", {"limit": "1"})
    inc = inc or {}

    total_revenue  = safe_float(inc.get("revenue"))
    int_expense    = safe_float(inc.get("interestExpense"))
    gross_profit   = safe_float(inc.get("grossProfit"))
    profit_margin  = safe_float(inc.get("netIncomeRatio"))
    earnings_growth= safe_float(inc.get("epsgrowth"))

    # Fallback profit margin from metrics
    if profit_margin is None:
        profit_margin = safe_float(metrics.get("netProfitMarginTTM"))

    # ── 4. Balance sheet (1 API call) ─────────────────────
    bs = fmp_get_first(f"balance-sheet-statement/{fmp_symbol}", {"limit": "1"})
    if not bs and is_bursa(ticker):
        bs = fmp_get_first(f"balance-sheet-statement/{ticker}", {"limit": "1"})
    bs = bs or {}

    total_assets = safe_float(bs.get("totalAssets"))
    total_debt   = safe_float(bs.get("totalDebt") or bs.get("longTermDebt"))

    # ── 5. Price history (1 API call) ─────────────────────
    history = []
    try:
        hist_data = fmp_get(
            f"historical-price-full/{fmp_symbol}",
            {"serietype": "line", "timeseries": "180"}  # 6 months
        )
        if isinstance(hist_data, dict):
            raw_hist = hist_data.get("historical", [])
            # Sample monthly — take every ~22 trading days
            sampled = raw_hist[::22][:6]
            for row in reversed(sampled):
                history.append({
                    "date":   row.get("date", "")[:7],
                    "close":  safe_float(row.get("close"), 0),
                    "open":   safe_float(row.get("open"),  0),
                    "high":   safe_float(row.get("high"),  0),
                    "low":    safe_float(row.get("low"),   0),
                    "volume": safe_int(row.get("volume"),  0),
                })
    except Exception as e:
        print(f"  History fetch failed (non-critical): {e}")

    # ── 6. Compute Shariah ratios ─────────────────────────
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

    # ── 7. SC Malaysia + Shariah screening ────────────────
    sc_check  = check_sc_list(ticker)
    screening = screen_halal(
        name=name, sector=sector, industry=industry, description=description,
        debt_ratio=debt_ratio, interest_ratio=interest_ratio,
        pe_ratio=pe_ratio, profit_margin=profit_margin, sc_check=sc_check,
    )

    result = {
        # Identity
        "ticker":      ticker,
        "name":        name,
        "sector":      sector,
        "industry":    industry,
        "description": description,
        "exchange":    exchange,
        "currency":    currency,
        "image":       image,
        # Price
        "price":       round(price, 4),
        "prevClose":   round(prev_close, 4) if prev_close else None,
        "changePct":   round(change_pct, 3),
        "week52High":  round(week52_high, 4) if week52_high else None,
        "week52Low":   round(week52_low,  4) if week52_low  else None,
        "volume":      volume,
        "avgVolume":   volume,
        "marketCap":   market_cap,
        "beta":        round(beta, 3) if beta else None,
        # Financials
        "totalAssets":    safe_int(total_assets),
        "totalDebt":      safe_int(total_debt),
        "totalRevenue":   safe_int(total_revenue),
        "interestExpense":safe_int(abs(int_expense)) if int_expense else None,
        "grossProfit":    safe_int(gross_profit),
        # Screening ratios
        "debtRatio":     round(debt_ratio,     4) if debt_ratio     is not None else None,
        "interestRatio": round(interest_ratio, 4) if interest_ratio is not None else None,
        # Valuation
        "peRatio":        round(pe_ratio,         2) if pe_ratio         else None,
        "pbRatio":        round(pb_ratio,          3) if pb_ratio         else None,
        "profitMargin":   round(profit_margin,     4) if profit_margin    is not None else None,
        "returnOnEquity": round(return_on_equity,  4) if return_on_equity is not None else None,
        "returnOnAssets": round(return_on_assets,  4) if return_on_assets is not None else None,
        "dividendYield":  round(dividend_yield,    4) if dividend_yield   is not None else None,
        "earningsGrowth": round(earnings_growth,   4) if earnings_growth  is not None else None,
        "revenueGrowth":  None,
        "currentRatio":   round(current_ratio,     3) if current_ratio    is not None else None,
        "quickRatio":     None,
        # History & screening
        "history":   history,
        "scCheck":   sc_check,
        "screening": screening,
        "fetchedAt": time.strftime("%H:%M:%S"),
        "_cached":   False,
        "_source":   "Financial Modeling Prep",
    }

    cache_set(ticker, result)
    return result


# ══════════════════════════════════════════════════════════
#  SHARIAH SCREENING ENGINE
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
            "detail":f"Ratio is {debt_ratio*100:.1f}% — {'significantly ' if debt_ratio>0.50 else 'marginally '}exceeds the 33% AAOIFI threshold."})
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
            "detail":f"Negative P/E ({pe_ratio:.1f}x) — company is currently loss-making."})
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
#  DIVIDEND PURIFICATION CALCULATOR
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
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/")
def root():
    return jsonify({
        "ok": True, "message": "Mizan Backend v4 — FMP Edition",
        "cache": cache_stats(), "requests": req_stats(),
    })

@app.route("/screen")
def screen():
    symbol = request.args.get("symbol", "").strip()
    if not symbol or not re.match(r'^[A-Za-z0-9.\-]{1,12}$', symbol):
        return jsonify({"ok": False, "error": "Invalid or missing symbol"}), 400
    try:
        data = fetch_stock(symbol)
        return jsonify({"ok": True, "data": data, "cached": data.get("_cached", False)})
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

@app.route("/usage")
def usage():
    s = req_stats()
    return jsonify({"ok": True, "data": s,
        "message": f"Used {s['used']} of {s['limit']} FMP requests today. {s['remaining']} remaining."})

@app.route("/health")
def health():
    return jsonify({
        "ok": True, "status": "Mizan backend running — FMP Edition",
        "cache": cache_stats(), "requests": req_stats(),
        "fmp_key": "set" if FMP_KEY else "MISSING",
    })

if __name__ == "__main__":
    load_sc_list()
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  MIZAN Backend v4 — FMP Edition                 ║")
    print("╚══════════════════════════════════════════════════╝\n")
    print(f"  ✓  250 free FMP requests/day")
    print(f"  ✓  Cache TTL: {CACHE_TTL//60} minutes")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
