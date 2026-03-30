"""
Mizan Backend Server v2
Run: python server.py
"""
import json, math, time, threading, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import sys

def check_deps():
    missing = [p for p in ["yfinance","pandas"] if not __import__("importlib").util.find_spec(p)]
    if missing:
        print(f"\n[ERROR] Missing: {', '.join(missing)}\nRun: pip install {' '.join(missing)}\n")
        sys.exit(1)
check_deps()
import yfinance as yf

# ── Cache ─────────────────────────────────────────────────
CACHE: dict = {}
CACHE_TTL   = 900   # 15 minutes
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

# ── SC Malaysia Shariah List ──────────────────────────────
SC_LIST_FILE = Path(__file__).parent / "sc_shariah_list.json"
_sc_list: dict = {}
_sc_lock = threading.Lock()

# Built-in subset of known SC Malaysia compliant/non-compliant stocks
# Always verify against the official list at sc.com.my
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
    if not ticker.endswith(".KL"):
        return {"found": False, "status": "not_applicable",
                "note": "SC Malaysia list covers Bursa Malaysia stocks only."}
    code = ticker.replace(".KL", "").zfill(4)
    sc   = load_sc_list()
    entry = sc.get(code)
    if not entry:
        return {"found": False, "status": "not_found",
                "note": "Not in built-in SC list. Verify manually at sc.com.my"}
    s = entry["status"]
    return {
        "found":  True,
        "status": s,
        "source": entry.get("source","builtin"),
        "note": (
            f"Listed as Shariah-{'compliant' if s=='compliant' else 'non-compliant'} "
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
EXCHANGE_SUFFIXES = {
    ".KL":"Bursa Malaysia",".L":"LSE",".PA":"Euronext",".DE":"Frankfurt",
    ".HK":"Hong Kong",".T":"Tokyo",".AX":"ASX",".SI":"Singapore",
    ".SS":"Shanghai",".SZ":"Shenzhen",
}
US_KNOWN = {
    "AAPL","MSFT","GOOGL","GOOG","AMZN","TSLA","NVDA","META","NFLX","AMD",
    "INTC","QCOM","AVGO","TXN","MU","AMAT","LRCX","KLAC","JPM","BAC","GS",
    "MS","WFC","C","BRK-B","BRK-A","JNJ","PFE","MRK","ABBV","LLY","BMY",
    "AMGN","GILD","XOM","CVX","COP","SLB","EOG","WMT","COST","TGT","HD",
    "MCD","SBUX","NKE","DIS","CMCSA","T","VZ","TMUS","V","MA","PYPL","AXP",
    "SQ","COIN","BABA","JD","PDD","BIDU","NIO","XPEV","LI",
}

def normalise_ticker(symbol):
    s = symbol.upper().strip().replace(" ","")
    for sfx in EXCHANGE_SUFFIXES:
        if s.endswith(sfx): return s
    if s.isdigit():           return s.zfill(4) + ".KL"
    if s in US_KNOWN:         return s
    if s.isalpha() and len(s) <= 5: return s
    return s

def safe(v, d=None):
    if v is None: return d
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return d
    return v

# ── Data Fetching ─────────────────────────────────────────
def fetch_stock(symbol):
    ticker_str = normalise_ticker(symbol)
    cached = cache_get(ticker_str)
    if cached:
        r = dict(cached); r["_cached"] = True
        return r

    tk   = yf.Ticker(ticker_str)
    info = tk.info or {}

    if not info or (info.get("regularMarketPrice") is None and
                    info.get("currentPrice")       is None and
                    info.get("previousClose")      is None):
        if not any(ticker_str.endswith(s) for s in EXCHANGE_SUFFIXES) and not ticker_str[-1].isdigit():
            fb    = ticker_str + ".KL"
            tk2   = yf.Ticker(fb)
            info2 = tk2.info or {}
            if info2.get("regularMarketPrice") or info2.get("currentPrice") or info2.get("previousClose"):
                ticker_str = fb; tk = tk2; info = info2
            else:
                raise ValueError(f"No data found for '{ticker_str}'. Check the stock code.")
        else:
            raise ValueError(f"No data found for '{ticker_str}'. Check the stock code.")

    price      = safe(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
    prev_close = safe(info.get("previousClose") or price)
    change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0

    total_assets = safe(info.get("totalAssets"))
    total_debt   = safe(info.get("totalDebt"))

    if total_assets is None or total_debt is None:
        try:
            bs = tk.balance_sheet
            if bs is not None and not bs.empty:
                def get_bs(ns):
                    for n in ns:
                        m = [c for c in bs.index if n.lower() in c.lower()]
                        if m:
                            v = bs.loc[m[0]].iloc[0]
                            if v is not None and not (isinstance(v,float) and math.isnan(v)):
                                return float(v)
                    return None
                if total_assets is None: total_assets = get_bs(["Total Assets","TotalAssets"])
                if total_debt   is None: total_debt   = get_bs(["Total Debt","Long Term Debt","LongTermDebt","Total Long Term Debt","Short Long Term Debt"])
        except Exception: pass

    total_revenue    = safe(info.get("totalRevenue"))
    interest_expense = safe(info.get("interestExpense"))
    gross_profit     = safe(info.get("grossProfits"))

    if total_revenue is None or interest_expense is None:
        try:
            inc = tk.income_stmt
            if inc is not None and not inc.empty:
                def get_inc(ns):
                    for n in ns:
                        m = [c for c in inc.index if n.lower() in c.lower()]
                        if m:
                            v = inc.loc[m[0]].iloc[0]
                            if v is not None and not (isinstance(v,float) and math.isnan(v)):
                                return float(v)
                    return None
                if total_revenue    is None: total_revenue    = get_inc(["Total Revenue","TotalRevenue","Revenue"])
                if interest_expense is None:
                    ie = get_inc(["Interest Expense","InterestExpense"])
                    interest_expense = abs(ie) if ie is not None else None
        except Exception: pass

    debt_ratio     = (total_debt / total_assets)       if (total_assets and total_debt   is not None and total_assets > 0)  else None
    interest_ratio = (abs(interest_expense)/total_revenue) if (total_revenue and interest_expense is not None and total_revenue > 0) else None

    history = []
    try:
        hist = tk.history(period="6mo", interval="1mo")
        if not hist.empty:
            for ts, row in hist.iterrows():
                cl = row.get("Close")
                if cl is not None and not (isinstance(cl,float) and math.isnan(cl)):
                    history.append({
                        "date":   ts.strftime("%b %y"),
                        "close":  round(float(cl),4),
                        "open":   round(float(row.get("Open",cl)),4),
                        "high":   round(float(row.get("High",cl)),4),
                        "low":    round(float(row.get("Low", cl)),4),
                        "volume": int(row.get("Volume",0) or 0),
                    })
    except Exception: pass

    sc_check  = check_sc_list(ticker_str)
    screening = screen_halal(
        name=info.get("longName") or info.get("shortName") or ticker_str,
        sector=info.get("sector") or "",
        industry=info.get("industry") or "",
        description=(info.get("longBusinessSummary") or "")[:500],
        debt_ratio=debt_ratio, interest_ratio=interest_ratio,
        pe_ratio=safe(info.get("trailingPE") or info.get("forwardPE")),
        profit_margin=safe(info.get("profitMargins")),
        sc_check=sc_check,
    )

    result = {
        "ticker":        ticker_str,
        "name":          info.get("longName") or info.get("shortName") or ticker_str,
        "sector":        info.get("sector") or "N/A",
        "industry":      info.get("industry") or "N/A",
        "description":   (info.get("longBusinessSummary") or "")[:400],
        "exchange":      info.get("exchange") or "N/A",
        "currency":      info.get("currency") or "MYR",
        "price":         round(price,4),
        "prevClose":     round(prev_close,4),
        "changePct":     round(change_pct,3),
        "week52High":    round(safe(info.get("fiftyTwoWeekHigh"),0),4),
        "week52Low":     round(safe(info.get("fiftyTwoWeekLow"),0),4),
        "volume":        int(safe(info.get("volume") or info.get("regularMarketVolume"),0)),
        "avgVolume":     int(v) if (v:=safe(info.get("averageVolume"))) else None,
        "marketCap":     int(v) if (v:=safe(info.get("marketCap")))    else None,
        "beta":          round(v,3) if (v:=safe(info.get("beta")))     else None,
        "totalAssets":   int(total_assets)   if total_assets   else None,
        "totalDebt":     int(total_debt)     if total_debt     else None,
        "totalRevenue":  int(total_revenue)  if total_revenue  else None,
        "interestExpense": int(abs(interest_expense)) if interest_expense else None,
        "grossProfit":   int(gross_profit)   if gross_profit   else None,
        "debtRatio":     round(debt_ratio,4)     if debt_ratio     is not None else None,
        "interestRatio": round(interest_ratio,4) if interest_ratio is not None else None,
        "peRatio":        round(v,2)  if (v:=safe(info.get("trailingPE") or info.get("forwardPE"))) else None,
        "pbRatio":        round(v,3)  if (v:=safe(info.get("priceToBook")))                         else None,
        "profitMargin":   round(v,4)  if (v:=safe(info.get("profitMargins")))    is not None else None,
        "returnOnEquity": round(v,4)  if (v:=safe(info.get("returnOnEquity")))   is not None else None,
        "returnOnAssets": round(v,4)  if (v:=safe(info.get("returnOnAssets")))   is not None else None,
        "dividendYield":  round(v,4)  if (v:=safe(info.get("dividendYield") or info.get("trailingAnnualDividendYield"))) else None,
        "earningsGrowth": round(v,4)  if (v:=safe(info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")))   is not None else None,
        "revenueGrowth":  round(v,4)  if (v:=safe(info.get("revenueGrowth")))    is not None else None,
        "currentRatio":   round(v,3)  if (v:=safe(info.get("currentRatio")))     is not None else None,
        "quickRatio":     round(v,3)  if (v:=safe(info.get("quickRatio")))       is not None else None,
        "history":       history,
        "scCheck":       sc_check,
        "screening":     screening,
        "fetchedAt":     time.strftime("%H:%M:%S"),
        "_cached":       False,
    }
    cache_set(ticker_str, result)
    return result

# ── Screening Engine ──────────────────────────────────────
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
        pass  # international stock — skip
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
                "detail":f'Sector "{sector or "N/A"}" may have mixed income sources. Requires verification.'})
            warnings.append("doubtful_sector")
        else:
            checks.append({"status":"pass","name":"Business Activity / Industry",
                "detail":f'Sector ({sector or "N/A"}) / Industry ({industry or "N/A"}) — no prohibited activity detected.'})

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
            "detail":"Company generates positive economic value. " + (f"P/E: {pe_ratio:.1f}x." if pe_ratio else "P/E data unavailable.")})

    if issues:    verdict,v_class,v_icon,v_reason = "Not Halal","haram","✗","Fails one or more categorical Shariah screening criteria."
    elif warnings: verdict,v_class,v_icon,v_reason = "Doubtful","doubtful","◐","Borderline on some criteria. Consult a qualified Islamic finance scholar."
    else:          verdict,v_class,v_icon,v_reason = "Potentially Halal","halal","✓","Passes all standard Shariah screening criteria. Always verify with a scholar."

    if issues: risk = "HIGH"
    else:
        s = 0
        dr = debt_ratio or 0
        pm = profit_margin or 0
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

# ── Dividend Purification ─────────────────────────────────
def calc_purification(dividend, interest_ratio, currency="MYR"):
    if interest_ratio is None or interest_ratio <= 0:
        return {"dividend":dividend,"interestRatio":interest_ratio,
                "purifyAmount":0.0,"keepAmount":dividend,"currency":currency,
                "note":"No purification needed — interest ratio is zero or not applicable.","isRequired":False}
    purify = round(dividend * interest_ratio, 4)
    keep   = round(dividend - purify, 4)
    intensity = "Small" if interest_ratio <= 0.05 else "Significant"
    note = (f"{intensity} purification required. Donate {currency} {purify:.2f} to charity "
            f"({interest_ratio*100:.1f}% of dividend). You keep {currency} {keep:.2f}.")
    if interest_ratio > 0.05:
        note += " Consider whether this stock is suitable for your portfolio."
    return {"dividend":dividend,"interestRatio":interest_ratio,"purifyAmount":purify,
            "keepAmount":keep,"currency":currency,"note":note,
            "isRequired":purify>0,"percentage":round(interest_ratio*100,2)}

# ── HTTP Handler ──────────────────────────────────────────
class MizanHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"  [{self.command}] {self.path.split('?')[0]}  →  {args[1]}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self): self.send_json({}, 200)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path   = parsed.path

        if path == "/screen":
            symbol = params.get("symbol",[None])[0]
            if not symbol:
                return self.send_json({"ok":False,"error":"Missing ?symbol= parameter"},400)
            if not re.match(r'^[A-Za-z0-9.\-]{1,12}$', symbol.strip()):
                return self.send_json({"ok":False,"error":"Invalid ticker format."},400)
            try:
                data   = fetch_stock(symbol.strip())
                cached = data.get("_cached",False)
                if cached: print("       ↳ served from cache")
                self.send_json({"ok":True,"data":data,"cached":cached})
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},400)

        elif path == "/purify":
            try:
                dividend       = float(params.get("dividend",      [0])[0])
                interest_ratio = float(params.get("interest_ratio",[0])[0])
                currency       = params.get("currency",["MYR"])[0].upper()[:3]
                if dividend < 0 or not (0 <= interest_ratio <= 1):
                    raise ValueError("Invalid parameters.")
                self.send_json({"ok":True,"data":calc_purification(dividend,interest_ratio,currency)})
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)},400)

        elif path == "/cache/stats":
            self.send_json({"ok":True,"data":cache_stats()})

        elif path == "/cache/clear":
            sym = params.get("symbol",[None])[0]
            if sym: cache_clear(normalise_ticker(sym.strip())); msg = f"Cleared {sym}"
            else:   cache_clear(); msg = "Full cache cleared"
            self.send_json({"ok":True,"message":msg})

        elif path == "/health":
            self.send_json({"ok":True,"status":"Mizan backend running","cache":cache_stats()})

        else:
            self.send_json({"error":"Not found"},404)

# ── Entry Point ───────────────────────────────────────────
PORT = 5000

def main():
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║  MIZAN Backend Server v2                        ║")
    print("╚══════════════════════════════════════════════════╝")
    print()
    load_sc_list()
    print(f"  ✓  http://localhost:{PORT}")
    print(f"  ✓  Cache TTL: {CACHE_TTL//60} min  |  SC list: built-in")
    print(f"  ✓  Open index.html in your browser")
    print(f"  ✓  Ctrl+C to stop\n")
    server = HTTPServer(("localhost", PORT), MizanHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped. بارك الله فيك\n")
        server.server_close()

if __name__ == "__main__":
    main()
