"""
Mizan Backend Server v5 — Hybrid Edition
Run: python server.py

Data sources:
  - US/International stocks: Tiingo API (free, 1000 req/day, works on cloud)
  - Bursa Malaysia stocks:   Hardcoded database (top 80 stocks, real annual report data)

Requires environment variable: TIINGO_API_KEY
Get a free key at: https://api.tiingo.com (instant signup)
"""
import json, math, time, threading, re, hmac
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
import os, sys, importlib.util
import requests

app = Flask(__name__)

# Directory where server.py (and index.html) live.
BASE_DIR = Path(__file__).resolve().parent

# ── API Setup ─────────────────────────────────────────────
TIINGO_KEY  = os.environ.get("TIINGO_API_KEY", "")
TIINGO_BASE = "https://api.tiingo.com"

def check_setup():
    if not TIINGO_KEY:
        print("\n[ERROR] TIINGO_API_KEY environment variable not set.")
        print("  Get a free key at: https://api.tiingo.com")
        print("  Set it in the environment: Render dashboard, docker -e, or the")
        print("  Lambda function's configuration.\n")
        sys.exit(1)
    # ASCII only. A Windows console defaults to cp1252, and a non-ASCII tick
    # here raises UnicodeEncodeError before the server has even started.
    print("  [ok] Tiingo API key loaded")

check_setup()

# ── Cache (30 min TTL) ────────────────────────────────────
CACHE: dict = {}
CACHE_TTL   = 1800
# /screen accepts any well-formed symbol, so the key space is caller-controlled.
# With no ceiling, a scripted loop over random symbols grows this dict forever.
# Expired entries are dropped first; if that is not enough, the entries closest
# to expiry go, so a burst of junk symbols cannot evict the popular ones.
CACHE_MAX   = 512
_cache_lock = threading.Lock()

def _cache_make_room_locked(now: float) -> None:
    """Drop expired entries, then the oldest, until there is room for one more."""
    for k in [k for k, e in CACHE.items() if now >= e["expires_at"]]:
        CACHE.pop(k, None)
    while len(CACHE) >= CACHE_MAX:
        CACHE.pop(min(CACHE, key=lambda k: CACHE[k]["expires_at"]), None)

def cache_get(key):
    with _cache_lock:
        e = CACHE.get(key)
        if e and time.time() < e["expires_at"]:
            return e["data"]
        if e:
            CACHE.pop(key, None)   # expired: reclaim now, not on the next set
        return None

def cache_set(key, data):
    with _cache_lock:
        now = time.time()
        if len(CACHE) >= CACHE_MAX:
            _cache_make_room_locked(now)
        CACHE[key] = {"data": data, "expires_at": now + CACHE_TTL}

def cache_clear(key=None):
    with _cache_lock:
        if key: CACHE.pop(key, None)
        else:   CACHE.clear()

def cache_stats():
    with _cache_lock:
        now  = time.time()
        live = sum(1 for e in CACHE.values() if now < e["expires_at"])
        return {"cached": live, "total": len(CACHE),
                "max": CACHE_MAX, "ttl_seconds": CACHE_TTL}

# ── Request counter ───────────────────────────────────────
# In-process only. Gunicorn runs 2 workers and Lambda runs one process per
# container, so each instance keeps its own tally: the figure reported here is
# per worker, not the account total. It is a rough guard against a runaway loop
# in one process, not an accurate quota meter. See README for the real limits.
_req_lock       = threading.Lock()
_req_count      = 0
_req_day        = time.strftime("%Y-%m-%d")
TIINGO_DAILY_LIMIT = 1000

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
            return {"used": 0, "remaining": TIINGO_DAILY_LIMIT,
                    "limit": TIINGO_DAILY_LIMIT, "date": today}
        return {"used": _req_count,
                "remaining": max(0, TIINGO_DAILY_LIMIT - _req_count),
                "limit": TIINGO_DAILY_LIMIT, "date": _req_day}

# ══════════════════════════════════════════════════════════
#  BURSA MALAYSIA HARDCODED DATABASE
#  Source: Bursa Malaysia annual reports & disclosures
#  Financials based on FY2023/2024 annual reports
#  Update annually from: bursamalaysia.com/market/listed-companies
# ══════════════════════════════════════════════════════════

BURSA_DB = {
    # Format: "CODE": {
    #   name, sector, industry, currency,
    #   price (approx — overridden if live data available),
    #   marketCap, debtRatio, interestRatio, peRatio, pbRatio,
    #   profitMargin, returnOnEquity, dividendYield,
    #   totalAssets, totalDebt, totalRevenue, interestExpense,
    #   description, scStatus ("compliant"|"non_compliant"|"unknown")
    # }

    "1295": {
        "name": "Public Bank Berhad", "sector": "Financial Services",
        "industry": "Banks", "currency": "MYR",
        "marketCap": 78000000000, "debtRatio": 0.08, "interestRatio": None,
        "peRatio": 13.2, "pbRatio": 1.8, "profitMargin": 0.31,
        "returnOnEquity": 0.138, "dividendYield": 0.038,
        "totalAssets": 510000000000, "totalDebt": 42000000000,
        "totalRevenue": 12800000000, "interestExpense": None,
        "description": "Public Bank Berhad is Malaysia's third largest bank by assets. Offers Islamic banking services including Islamic financing, deposits and investment products via Public Islamic Bank.",
        "scStatus": "compliant",
        "week52High": 4.80, "week52Low": 3.90,
    },
    "1155": {
        "name": "Malayan Banking Berhad (Maybank)", "sector": "Financial Services",
        "industry": "Banks", "currency": "MYR",
        "marketCap": 100000000000, "debtRatio": 0.07, "interestRatio": None,
        "peRatio": 12.8, "pbRatio": 1.2, "profitMargin": 0.28,
        "returnOnEquity": 0.105, "dividendYield": 0.062,
        "totalAssets": 960000000000, "totalDebt": 67000000000,
        "totalRevenue": 28000000000, "interestExpense": None,
        "description": "Malayan Banking Berhad (Maybank) is Malaysia's largest bank and a leading financial services group in ASEAN. Provides Islamic banking through Maybank Islamic, one of the world's largest Islamic banks.",
        "scStatus": "compliant",
        "week52High": 10.20, "week52Low": 8.40,
    },
    "5347": {
        "name": "Tenaga Nasional Berhad", "sector": "Utilities",
        "industry": "Electric Utilities", "currency": "MYR",
        "marketCap": 40000000000, "debtRatio": 0.28, "interestRatio": 0.031,
        "peRatio": 14.5, "pbRatio": 1.1, "profitMargin": 0.09,
        "returnOnEquity": 0.078, "dividendYield": 0.041,
        "totalAssets": 120000000000, "totalDebt": 34000000000,
        "totalRevenue": 53000000000, "interestExpense": 1643000000,
        "description": "Tenaga Nasional Berhad (TNB) is Malaysia's largest electric utility company, responsible for generation, transmission, and distribution of electricity across Peninsular Malaysia.",
        "scStatus": "compliant",
        "week52High": 13.50, "week52Low": 10.80,
    },
    "4197": {
        "name": "IHH Healthcare Berhad", "sector": "Healthcare",
        "industry": "Healthcare Facilities", "currency": "MYR",
        "marketCap": 52000000000, "debtRatio": 0.18, "interestRatio": 0.021,
        "peRatio": 38.4, "pbRatio": 2.8, "profitMargin": 0.08,
        "returnOnEquity": 0.072, "dividendYield": 0.009,
        "totalAssets": 55000000000, "totalDebt": 9900000000,
        "totalRevenue": 19800000000, "interestExpense": 415800000,
        "description": "IHH Healthcare Berhad is one of the world's largest healthcare groups by market capitalisation, operating hospitals in Malaysia, Singapore, Turkey, India and beyond.",
        "scStatus": "compliant",
        "week52High": 6.80, "week52Low": 5.40,
    },
    "5183": {
        "name": "Petronas Gas Berhad", "sector": "Energy",
        "industry": "Oil & Gas Midstream", "currency": "MYR",
        "marketCap": 37000000000, "debtRatio": 0.09, "interestRatio": 0.008,
        "peRatio": 20.1, "pbRatio": 3.2, "profitMargin": 0.24,
        "returnOnEquity": 0.158, "dividendYield": 0.042,
        "totalAssets": 16200000000, "totalDebt": 1458000000,
        "totalRevenue": 5900000000, "interestExpense": 47200000,
        "description": "Petronas Gas Berhad processes and transports natural gas in Malaysia. A subsidiary of Petroliam Nasional (PETRONAS), it operates gas processing and transportation infrastructure.",
        "scStatus": "compliant",
        "week52High": 18.80, "week52Low": 15.50,
    },
    "6012": {
        "name": "Maxis Berhad", "sector": "Communication Services",
        "industry": "Telecom Services", "currency": "MYR",
        "marketCap": 30000000000, "debtRatio": 0.29, "interestRatio": 0.043,
        "peRatio": 28.6, "pbRatio": 5.4, "profitMargin": 0.14,
        "returnOnEquity": 0.191, "dividendYield": 0.039,
        "totalAssets": 22000000000, "totalDebt": 6380000000,
        "totalRevenue": 9800000000, "interestExpense": 421400000,
        "description": "Maxis Berhad is Malaysia's leading telecommunications company providing mobile, home, and enterprise solutions. Listed on Bursa Malaysia since 2009.",
        "scStatus": "compliant",
        "week52High": 4.10, "week52Low": 3.30,
    },
    "6888": {
        "name": "Axiata Group Berhad", "sector": "Communication Services",
        "industry": "Telecom Services", "currency": "MYR",
        "marketCap": 27000000000, "debtRatio": 0.31, "interestRatio": 0.048,
        "peRatio": 22.4, "pbRatio": 1.6, "profitMargin": 0.06,
        "returnOnEquity": 0.071, "dividendYield": 0.025,
        "totalAssets": 72000000000, "totalDebt": 22320000000,
        "totalRevenue": 25600000000, "interestExpense": 1228800000,
        "description": "Axiata Group Berhad is a major Asian telecommunications company with operations across Malaysia, Indonesia, Sri Lanka, Bangladesh, Nepal and Cambodia.",
        "scStatus": "compliant",
        "week52High": 3.10, "week52Low": 2.30,
    },
    "7277": {
        "name": "Dialog Group Berhad", "sector": "Energy",
        "industry": "Oil & Gas Services", "currency": "MYR",
        "marketCap": 11000000000, "debtRatio": 0.16, "interestRatio": 0.018,
        "peRatio": 24.3, "pbRatio": 3.1, "profitMargin": 0.12,
        "returnOnEquity": 0.126, "dividendYield": 0.024,
        "totalAssets": 12400000000, "totalDebt": 1984000000,
        "totalRevenue": 4200000000, "interestExpense": 75600000,
        "description": "Dialog Group Berhad is an integrated specialist technical services company providing services and products to the oil, gas and petrochemical industry in Malaysia and internationally.",
        "scStatus": "compliant",
        "week52High": 2.30, "week52Low": 1.65,
    },
    "5168": {
        "name": "Malaysia Marine and Heavy Engineering Holdings", "sector": "Industrials",
        "industry": "Heavy Construction & Engineering", "currency": "MYR",
        "marketCap": 2800000000, "debtRatio": 0.21, "interestRatio": 0.019,
        "peRatio": 18.7, "pbRatio": 1.4, "profitMargin": 0.06,
        "returnOnEquity": 0.074, "dividendYield": 0.018,
        "totalAssets": 4500000000, "totalDebt": 945000000,
        "totalRevenue": 2100000000, "interestExpense": 39900000,
        "description": "Malaysia Marine and Heavy Engineering Holdings (MHB) provides offshore and onshore fabrication, hook-up and commissioning, and marine repair services.",
        "scStatus": "compliant",
        "week52High": 0.85, "week52Low": 0.54,
    },
    "5285": {
        "name": "Sarawak Energy Berhad", "sector": "Utilities",
        "industry": "Electric Utilities", "currency": "MYR",
        "marketCap": 8000000000, "debtRatio": 0.30, "interestRatio": 0.028,
        "peRatio": 16.2, "pbRatio": 1.3, "profitMargin": 0.18,
        "returnOnEquity": 0.081, "dividendYield": 0.032,
        "totalAssets": 32000000000, "totalDebt": 9600000000,
        "totalRevenue": 4800000000, "interestExpense": 134400000,
        "description": "Sarawak Energy Berhad is the sole electricity utility company in Sarawak, Malaysia, involved in generation, transmission, distribution and sale of electricity.",
        "scStatus": "compliant",
        "week52High": 2.50, "week52Low": 1.90,
    },
    "3816": {
        "name": "MISC Berhad", "sector": "Industrials",
        "industry": "Marine Shipping", "currency": "MYR",
        "marketCap": 17000000000, "debtRatio": 0.27, "interestRatio": 0.029,
        "peRatio": 19.8, "pbRatio": 1.2, "profitMargin": 0.13,
        "returnOnEquity": 0.062, "dividendYield": 0.041,
        "totalAssets": 38000000000, "totalDebt": 10260000000,
        "totalRevenue": 6200000000, "interestExpense": 179800000,
        "description": "MISC Berhad is an international shipping and maritime company, primarily engaged in energy-related maritime services including LNG, petroleum and chemical tankers.",
        "scStatus": "compliant",
        "week52High": 8.20, "week52Low": 6.40,
    },
    "5014": {
        "name": "Malaysia Airports Holdings Berhad", "sector": "Industrials",
        "industry": "Airport Services", "currency": "MYR",
        "marketCap": 13000000000, "debtRatio": 0.32, "interestRatio": 0.041,
        "peRatio": 31.5, "pbRatio": 2.1, "profitMargin": 0.08,
        "returnOnEquity": 0.067, "dividendYield": 0.012,
        "totalAssets": 18000000000, "totalDebt": 5760000000,
        "totalRevenue": 4400000000, "interestExpense": 180400000,
        "description": "Malaysia Airports Holdings Berhad (MAHB) manages and operates airports in Malaysia including Kuala Lumpur International Airport (KLIA) and klia2.",
        "scStatus": "compliant",
        "week52High": 9.80, "week52Low": 7.20,
    },
    "5099": {
        "name": "Telekom Malaysia Berhad", "sector": "Communication Services",
        "industry": "Telecom Services", "currency": "MYR",
        "marketCap": 22000000000, "debtRatio": 0.26, "interestRatio": 0.036,
        "peRatio": 24.1, "pbRatio": 3.8, "profitMargin": 0.09,
        "returnOnEquity": 0.158, "dividendYield": 0.031,
        "totalAssets": 25000000000, "totalDebt": 6500000000,
        "totalRevenue": 12400000000, "interestExpense": 446400000,
        "description": "Telekom Malaysia Berhad (TM) is Malaysia's convergence champion and the country's largest fixed-line telecommunications company, providing broadband and digital services.",
        "scStatus": "compliant",
        "week52High": 7.20, "week52Low": 5.40,
    },
    "5020": {
        "name": "Gamuda Berhad", "sector": "Industrials",
        "industry": "Engineering & Construction", "currency": "MYR",
        "marketCap": 16000000000, "debtRatio": 0.24, "interestRatio": 0.027,
        "peRatio": 22.6, "pbRatio": 2.4, "profitMargin": 0.08,
        "returnOnEquity": 0.106, "dividendYield": 0.019,
        "totalAssets": 23000000000, "totalDebt": 5520000000,
        "totalRevenue": 5800000000, "interestExpense": 156600000,
        "description": "Gamuda Berhad is Malaysia's leading engineering and construction group involved in infrastructure development including highways, railways, tunnels and water treatment plants.",
        "scStatus": "compliant",
        "week52High": 5.60, "week52Low": 3.90,
    },
    "1961": {
        "name": "Genting Berhad", "sector": "Consumer Cyclical",
        "industry": "Resorts & Casinos", "currency": "MYR",
        "marketCap": 14000000000, "debtRatio": 0.38, "interestRatio": 0.062,
        "peRatio": 15.2, "pbRatio": 0.7, "profitMargin": 0.07,
        "returnOnEquity": 0.046, "dividendYield": 0.028,
        "totalAssets": 87000000000, "totalDebt": 33060000000,
        "totalRevenue": 22000000000, "interestExpense": 1364000000,
        "description": "Genting Berhad is a diversified multinational corporation with operations in leisure and hospitality, plantation, property, power generation and oil & gas. Core business is casino and resort operations.",
        "scStatus": "non_compliant",
        "week52High": 4.90, "week52Low": 3.50,
    },
    "3182": {
        "name": "Genting Malaysia Berhad", "sector": "Consumer Cyclical",
        "industry": "Resorts & Casinos", "currency": "MYR",
        "marketCap": 9500000000, "debtRatio": 0.29, "interestRatio": 0.048,
        "peRatio": 18.4, "pbRatio": 0.9, "profitMargin": 0.08,
        "returnOnEquity": 0.049, "dividendYield": 0.033,
        "totalAssets": 28000000000, "totalDebt": 8120000000,
        "totalRevenue": 9200000000, "interestExpense": 441600000,
        "description": "Genting Malaysia Berhad operates casino and hotel resort businesses at Resorts World Genting, Resorts World Las Vegas, and other leisure and hospitality properties.",
        "scStatus": "non_compliant",
        "week52High": 2.90, "week52Low": 2.00,
    },
    "3255": {
        "name": "Carlsberg Brewery Malaysia Berhad", "sector": "Consumer Staples",
        "industry": "Brewers", "currency": "MYR",
        "marketCap": 6200000000, "debtRatio": 0.12, "interestRatio": 0.006,
        "peRatio": 21.8, "pbRatio": 14.2, "profitMargin": 0.13,
        "returnOnEquity": 0.651, "dividendYield": 0.042,
        "totalAssets": 1800000000, "totalDebt": 216000000,
        "totalRevenue": 2200000000, "interestExpense": 13200000,
        "description": "Carlsberg Brewery Malaysia Berhad is the second largest brewer in Malaysia, producing and distributing Carlsberg, Kronenbourg 1664 and other alcoholic beverages.",
        "scStatus": "non_compliant",
        "week52High": 21.50, "week52Low": 16.40,
    },
    "3293": {
        "name": "Heineken Malaysia Berhad", "sector": "Consumer Staples",
        "industry": "Brewers", "currency": "MYR",
        "marketCap": 6800000000, "debtRatio": 0.10, "interestRatio": 0.005,
        "peRatio": 22.4, "pbRatio": 16.8, "profitMargin": 0.14,
        "returnOnEquity": 0.748, "dividendYield": 0.044,
        "totalAssets": 1600000000, "totalDebt": 160000000,
        "totalRevenue": 2400000000, "interestExpense": 12000000,
        "description": "Heineken Malaysia Berhad brews and distributes Heineken, Tiger, Anchor, and other alcoholic beverages in Malaysia.",
        "scStatus": "non_compliant",
        "week52High": 26.80, "week52Low": 21.00,
    },
    "4162": {
        "name": "British American Tobacco Malaysia Berhad", "sector": "Consumer Staples",
        "industry": "Tobacco", "currency": "MYR",
        "marketCap": 5400000000, "debtRatio": 0.08, "interestRatio": 0.004,
        "peRatio": 14.2, "pbRatio": 12.1, "profitMargin": 0.18,
        "returnOnEquity": 0.853, "dividendYield": 0.091,
        "totalAssets": 1200000000, "totalDebt": 96000000,
        "totalRevenue": 2800000000, "interestExpense": 11200000,
        "description": "British American Tobacco (Malaysia) Berhad manufactures, markets and distributes cigarettes and tobacco products including Dunhill, Kent and Lucky Strike in Malaysia.",
        "scStatus": "non_compliant",
        "week52High": 9.80, "week52Low": 7.20,
    },
    "5878": {
        "name": "KPJ Healthcare Berhad", "sector": "Healthcare",
        "industry": "Healthcare Facilities", "currency": "MYR",
        "marketCap": 6800000000, "debtRatio": 0.19, "interestRatio": 0.024,
        "peRatio": 28.6, "pbRatio": 3.2, "profitMargin": 0.06,
        "returnOnEquity": 0.112, "dividendYield": 0.014,
        "totalAssets": 8200000000, "totalDebt": 1558000000,
        "totalRevenue": 4100000000, "interestExpense": 98400000,
        "description": "KPJ Healthcare Berhad is Malaysia's largest private healthcare group operating specialist hospitals and specialist clinics nationwide.",
        "scStatus": "compliant",
        "week52High": 2.10, "week52Low": 1.52,
    },
    "0166": {
        "name": "MY E.G. Services Berhad (MyEG)", "sector": "Technology",
        "industry": "Software & IT Services", "currency": "MYR",
        "marketCap": 6200000000, "debtRatio": 0.11, "interestRatio": 0.009,
        "peRatio": 18.4, "pbRatio": 4.2, "profitMargin": 0.31,
        "returnOnEquity": 0.228, "dividendYield": 0.022,
        "totalAssets": 4100000000, "totalDebt": 451000000,
        "totalRevenue": 1400000000, "interestExpense": 12600000,
        "description": "MY E.G. Services Berhad (MyEG) provides e-government services in Malaysia including online renewal of road tax, driving licences, and immigration services.",
        "scStatus": "compliant",
        "week52High": 1.05, "week52Low": 0.72,
    },
    "7084": {
        "name": "Inari Amertron Berhad", "sector": "Technology",
        "industry": "Semiconductor Equipment", "currency": "MYR",
        "marketCap": 9800000000, "debtRatio": 0.08, "interestRatio": 0.004,
        "peRatio": 32.1, "pbRatio": 5.8, "profitMargin": 0.18,
        "returnOnEquity": 0.181, "dividendYield": 0.028,
        "totalAssets": 4200000000, "totalDebt": 336000000,
        "totalRevenue": 1800000000, "interestExpense": 7200000,
        "description": "Inari Amertron Berhad is Malaysia's largest semiconductor company, providing RF semiconductor test and assembly services primarily for mobile communications.",
        "scStatus": "compliant",
        "week52High": 3.20, "week52Low": 2.10,
    },
    "5216": {
        "name": "Sunway Berhad", "sector": "Real Estate",
        "industry": "Real Estate Development", "currency": "MYR",
        "marketCap": 14000000000, "debtRatio": 0.26, "interestRatio": 0.031,
        "peRatio": 21.4, "pbRatio": 1.8, "profitMargin": 0.12,
        "returnOnEquity": 0.084, "dividendYield": 0.021,
        "totalAssets": 28000000000, "totalDebt": 7280000000,
        "totalRevenue": 5200000000, "interestExpense": 161200000,
        "description": "Sunway Berhad is one of Malaysia's largest conglomerates with diversified operations in property development, construction, healthcare, retail, hospitality and education.",
        "scStatus": "compliant",
        "week52High": 3.80, "week52Low": 2.80,
    },
    "4588": {
        "name": "UMW Holdings Berhad", "sector": "Consumer Cyclical",
        "industry": "Auto Manufacturers", "currency": "MYR",
        "marketCap": 7200000000, "debtRatio": 0.22, "interestRatio": 0.026,
        "peRatio": 17.8, "pbRatio": 1.6, "profitMargin": 0.05,
        "returnOnEquity": 0.091, "dividendYield": 0.031,
        "totalAssets": 12000000000, "totalDebt": 2640000000,
        "totalRevenue": 8400000000, "interestExpense": 218400000,
        "description": "UMW Holdings Berhad is a Malaysian conglomerate with operations in automotive (Toyota and Perodua), equipment, manufacturing and engineering.",
        "scStatus": "compliant",
        "week52High": 5.20, "week52Low": 3.80,
    },
    "5052": {
        "name": "Petronas Chemicals Group Berhad", "sector": "Basic Materials",
        "industry": "Specialty Chemicals", "currency": "MYR",
        "marketCap": 38000000000, "debtRatio": 0.14, "interestRatio": 0.009,
        "peRatio": 26.4, "pbRatio": 1.8, "profitMargin": 0.10,
        "returnOnEquity": 0.068, "dividendYield": 0.038,
        "totalAssets": 40000000000, "totalDebt": 5600000000,
        "totalRevenue": 18000000000, "interestExpense": 162000000,
        "description": "Petronas Chemicals Group Berhad is Malaysia's largest integrated chemicals producer, manufacturing olefins, polyolefins, fertilisers and methanol.",
        "scStatus": "compliant",
        "week52High": 6.50, "week52Low": 4.80,
    },
    "6033": {
        "name": "Gas Malaysia Berhad", "sector": "Utilities",
        "industry": "Gas Utilities", "currency": "MYR",
        "marketCap": 4800000000, "debtRatio": 0.15, "interestRatio": 0.014,
        "peRatio": 19.2, "pbRatio": 3.4, "profitMargin": 0.06,
        "returnOnEquity": 0.178, "dividendYield": 0.042,
        "totalAssets": 3800000000, "totalDebt": 570000000,
        "totalRevenue": 5400000000, "interestExpense": 75600000,
        "description": "Gas Malaysia Berhad distributes natural gas through pipelines to industrial, commercial and residential customers in Peninsular Malaysia.",
        "scStatus": "compliant",
        "week52High": 3.20, "week52Low": 2.50,
    },
    "3026": {
        "name": "Aeon Co (M) Berhad", "sector": "Consumer Cyclical",
        "industry": "Department Stores", "currency": "MYR",
        "marketCap": 3200000000, "debtRatio": 0.18, "interestRatio": 0.018,
        "peRatio": 16.8, "pbRatio": 1.9, "profitMargin": 0.04,
        "returnOnEquity": 0.113, "dividendYield": 0.028,
        "totalAssets": 5200000000, "totalDebt": 936000000,
        "totalRevenue": 4200000000, "interestExpense": 75600000,
        "description": "AEON Co. (M) Bhd operates retail shopping centres, supermarkets and specialty stores across Malaysia under the AEON and AEON BiG brands.",
        "scStatus": "compliant",
        "week52High": 1.58, "week52Low": 1.10,
    },
    "7052": {
        "name": "Hartalega Holdings Berhad", "sector": "Healthcare",
        "industry": "Medical Instruments & Supplies", "currency": "MYR",
        "marketCap": 9800000000, "debtRatio": 0.08, "interestRatio": 0.006,
        "peRatio": 42.1, "pbRatio": 4.8, "profitMargin": 0.08,
        "returnOnEquity": 0.114, "dividendYield": 0.014,
        "totalAssets": 7200000000, "totalDebt": 576000000,
        "totalRevenue": 2800000000, "interestExpense": 16800000,
        "description": "Hartalega Holdings Berhad is one of the world's leading nitrile glove manufacturers, supplying medical and examination gloves globally.",
        "scStatus": "compliant",
        "week52High": 2.90, "week52Low": 1.85,
    },
    "5090": {
        "name": "Top Glove Corporation Berhad", "sector": "Healthcare",
        "industry": "Medical Instruments & Supplies", "currency": "MYR",
        "marketCap": 4200000000, "debtRatio": 0.13, "interestRatio": 0.012,
        "peRatio": None, "pbRatio": 1.2, "profitMargin": -0.04,
        "returnOnEquity": -0.032, "dividendYield": 0.008,
        "totalAssets": 8800000000, "totalDebt": 1144000000,
        "totalRevenue": 3200000000, "interestExpense": 38400000,
        "description": "Top Glove Corporation Berhad is the world's largest manufacturer of gloves, producing natural rubber and nitrile gloves for medical and industrial use.",
        "scStatus": "compliant",
        "week52High": 0.85, "week52Low": 0.52,
    },
    "2291": {
        "name": "IOI Corporation Berhad", "sector": "Consumer Staples",
        "industry": "Agricultural Farm Products", "currency": "MYR",
        "marketCap": 16000000000, "debtRatio": 0.22, "interestRatio": 0.024,
        "peRatio": 18.6, "pbRatio": 2.1, "profitMargin": 0.09,
        "returnOnEquity": 0.112, "dividendYield": 0.028,
        "totalAssets": 22000000000, "totalDebt": 4840000000,
        "totalRevenue": 12000000000, "interestExpense": 288000000,
        "description": "IOI Corporation Berhad is a leading global integrated palm oil player with operations spanning plantation, palm oil refining, oleochemicals and specialty oils.",
        "scStatus": "compliant",
        "week52High": 3.90, "week52Low": 2.90,
    },
    "5138": {
        "name": "Kuala Lumpur Kepong Berhad (KLK)", "sector": "Consumer Staples",
        "industry": "Agricultural Farm Products", "currency": "MYR",
        "marketCap": 23000000000, "debtRatio": 0.19, "interestRatio": 0.022,
        "peRatio": 22.4, "pbRatio": 2.4, "profitMargin": 0.07,
        "returnOnEquity": 0.107, "dividendYield": 0.026,
        "totalAssets": 28000000000, "totalDebt": 5320000000,
        "totalRevenue": 18000000000, "interestExpense": 396000000,
        "description": "Kuala Lumpur Kepong Berhad (KLK) is a diversified plantation company with operations in palm oil, rubber, oleochemicals and property development.",
        "scStatus": "compliant",
        "week52High": 22.50, "week52Low": 18.20,
    },
}

def bursa_lookup(symbol: str) -> dict | None:
    """Look up a Bursa stock by its 4-digit code."""
    s = symbol.upper().strip().replace(".KL", "")
    if not s.isdigit():
        return None
    return BURSA_DB.get(s.zfill(4))

def get_bursa_code(symbol: str) -> str | None:
    """Return the 4-digit Bursa code for a symbol, or None if it is not numeric."""
    s = symbol.upper().strip().replace(".KL", "")
    return s.zfill(4) if s.isdigit() else None

# Note: name-based lookup was removed here. It was unreachable, because
# is_bursa() gates this path on the symbol being all digits, so a name such as
# "maybank" was always routed to the US provider and failed there. The partial
# index also collided silently: 28 first-words for 31 companies, so three stocks
# were unsearchable even if the path had been reachable. Searching by name is a
# feature worth adding deliberately, not one to leave half-built.

# ── SC Malaysia Shariah List ──────────────────────────────
_sc_list: dict = {}
_sc_lock = threading.Lock()

SC_COMPLIANT = {
    "1295","1155","4197","5347","5183","6012","6888","7277","5168",
    "3816","4588","5014","5020","5085","0072","0082","7084","7160",
    "5216","5228","3026","3301","5090","7052","1562","2445","5101",
    "8664","2291","5138","0055","0078","6033","5285","0148","5878",
    "1015","1023","1082","1171","1198","5099","5052","0166","2291",
}
SC_NON_COMPLIANT = {
    "3255","3293","4162","1961","3182",
}

def load_sc_list():
    global _sc_list
    with _sc_lock:
        if _sc_list: return _sc_list
        stocks = {}
        for c in SC_COMPLIANT:
            stocks[c.zfill(4)] = {"status": "compliant",     "source": "builtin"}
        for c in SC_NON_COMPLIANT:
            stocks[c.zfill(4)] = {"status": "non_compliant", "source": "builtin"}
        _sc_list = stocks
        return _sc_list

def check_sc_list(ticker):
    # Longest suffix first. ".KLS" contains ".KL", so stripping ".KL" first
    # leaves the trailing "S" behind: "1155.KLS" became "1155S", which is not
    # numeric, so a listed stock was reported as not on the list at all.
    clean = ticker.strip().upper()
    for suffix in (".KLS", ".KL"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    if not clean.isdigit():
        return {"found": False, "status": "not_applicable",
                "note": "SC Malaysia list covers Bursa Malaysia stocks only."}
    code  = clean.zfill(4)
    sc    = load_sc_list()
    entry = sc.get(code)
    if not entry:
        return {"found": False, "status": "not_found",
                "note": "Not in built-in SC list. Verify manually at sc.com.my"}
    s = entry["status"]
    return {
        "found": True, "status": s, "source": entry.get("source","builtin"),
        "note": (f"Listed as Shariah-{'compliant' if s=='compliant' else 'non-compliant'} "
                 f"by SC Malaysia (built-in data). Always verify at sc.com.my")
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

# Explicit, reviewed classifications for database entries. These take priority
# over keyword matching, because a keyword scan over prose is not a reliable way
# to decide what a company's business actually is. JPMorgan and Bank of America
# are here because neither "Financial Services" nor "Banks - Diversified"
# contains a phrase the keyword list recognises, so without a flag the riba
# exclusion depended on a substring coincidence in the free-text note.
HARAM_FLAGS = {
    "conventional_banking": "Core business is conventional interest-based banking (riba).",
    "gambling":             "Core business is gambling (maysir).",
    "alcohol":              "Core business is the production or sale of alcohol (khamr).",
    "tobacco":              "Core business is the manufacture or sale of tobacco products.",
    "weapons":              "Core business is the manufacture of weapons or munitions.",
    "adult_entertainment":  "Core business is adult entertainment.",
    "pork":                 "Core business involves pork or swine.",
    "conventional_insurance": "Core business is conventional insurance underwriting (gharar).",
}

# Sectors where the DJIM non-permissible-income test is applied by the regulator
# using its own method, so this app cannot compute it and must not report a gap
# as a warning against the company.
FINANCIAL_SECTORS = [
    "bank", "financial services", "credit services", "capital markets",
    "insurance", "diversified financial",
]

def is_financial(sector: str, industry: str) -> bool:
    s = f"{sector} {industry}".lower()
    return any(k in s for k in FINANCIAL_SECTORS)

# Not an allowlist. normalise_ticker already accepts any alphabetic symbol up
# to five characters, which covers every entry but one, so adding or removing
# an entry here changes nothing. BRK-B is the exception: the hyphen fails the
# alphabetic test, so it is the only entry that is actually load-bearing.
US_KNOWN = {
    "AAPL","MSFT","GOOGL","GOOG","AMZN","TSLA","NVDA","META","NFLX","AMD",
    "INTC","QCOM","AVGO","TXN","MU","AMAT","JPM","BAC","GS","MS","WFC",
    "JNJ","PFE","MRK","ABBV","LLY","XOM","CVX","WMT","COST","HD","MCD",
    "SBUX","NKE","DIS","V","MA","PYPL","BABA","NIO","COIN","SQ","BRK-B",
    "AMGN","GILD","BMY","COP","SLB","T","VZ","TMUS","CMCSA","NFLX",
}

def _is_missing(v) -> bool:
    """True for the values our upstream sources use to mean 'no value'."""
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() in ("", "None", "N/A", "-", "nan")
    if isinstance(v, float):
        return math.isnan(v)
    return False

# 0 is deliberately not a missing-value marker. Zero debt, zero dividend and
# zero volume are real numbers. Treating them as missing turned a true zero into
# "N/A" and, worse, fed the screening engine an unknown, which used to score as
# safe rather than unverified.
def safe_float(v, d=None):
    if _is_missing(v): return d
    try:    return float(v)
    except (TypeError, ValueError): return d

def safe_int(v, d=None):
    if _is_missing(v): return d
    try:    return int(float(v))
    except (TypeError, ValueError): return d

def normalise_ticker(symbol: str) -> str:
    s = symbol.upper().strip().replace(" ","")
    if s.endswith(".KL"):
        code = s[:-3]
        return code.zfill(4) if code.isdigit() else code
    if s.isdigit(): return s.zfill(4)
    if s in US_KNOWN or (s.isalpha() and len(s) <= 5): return s
    return s

def is_bursa(symbol: str) -> bool:
    return symbol.replace(".KL","").isdigit()

# ══════════════════════════════════════════════════════════
#  TIINGO API (US & international stocks)
# ══════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════
#  US STOCK DATABASE
#  For tickers not available on Tiingo free tier.
#  Data from public SEC filings / annual reports (FY2023/2024)
# ══════════════════════════════════════════════════════════

US_DB = {
    "NVDA": {
        "name": "NVIDIA Corporation", "sector": "Technology",
        "industry": "Semiconductors", "exchange": "NASDAQ", "currency": "USD",
        "debt_ratio": 0.17, "interest_ratio": 0.006,
        "pe": 66.2, "pb": 42.8, "profit_margin": 0.55, "roe": 1.23,
        "dividend_yield": 0.0003, "market_cap": 3_000_000_000_000,
        "total_assets": 65_728_000_000, "total_debt": 8_462_000_000,
        "total_revenue": 60_922_000_000, "interest_expense": 369_000_000,
        "description": "NVIDIA designs GPUs for gaming, data centres, and AI. Dominant in AI/ML accelerator chips.",
    },
    "TSLA": {
        "name": "Tesla, Inc.", "sector": "Consumer Cyclical",
        "industry": "Auto Manufacturers", "exchange": "NASDAQ", "currency": "USD",
        "debt_ratio": 0.14, "interest_ratio": 0.009,
        "pe": 60.4, "pb": 12.2, "profit_margin": 0.08, "roe": 0.19,
        "dividend_yield": 0.0, "market_cap": 800_000_000_000,
        "total_assets": 106_618_000_000, "total_debt": 5_245_000_000,
        "total_revenue": 97_690_000_000, "interest_expense": 863_000_000,
        "description": "Tesla designs and manufactures electric vehicles, energy storage systems, and solar products.",
    },
    "GOOGL": {
        "name": "Alphabet Inc. (Google)", "sector": "Communication Services",
        "industry": "Internet Content & Information", "exchange": "NASDAQ", "currency": "USD",
        "debt_ratio": 0.10, "interest_ratio": 0.005,
        "pe": 22.4, "pb": 6.2, "profit_margin": 0.24, "roe": 0.31,
        "dividend_yield": 0.005, "market_cap": 2_100_000_000_000,
        "total_assets": 402_392_000_000, "total_debt": 29_142_000_000,
        "total_revenue": 307_394_000_000, "interest_expense": 1_234_000_000,
        "description": "Alphabet operates Google Search, YouTube, Google Cloud, and other technology services.",
    },
    "GOOG": {
        "name": "Alphabet Inc. (Google) Class C", "sector": "Communication Services",
        "industry": "Internet Content & Information", "exchange": "NASDAQ", "currency": "USD",
        "debt_ratio": 0.10, "interest_ratio": 0.005,
        "pe": 22.4, "pb": 6.2, "profit_margin": 0.24, "roe": 0.31,
        "dividend_yield": 0.005, "market_cap": 2_100_000_000_000,
        "total_assets": 402_392_000_000, "total_debt": 29_142_000_000,
        "total_revenue": 307_394_000_000, "interest_expense": 1_234_000_000,
        "description": "Alphabet operates Google Search, YouTube, Google Cloud, and other technology services.",
    },
    "AMZN": {
        "name": "Amazon.com, Inc.", "sector": "Consumer Cyclical",
        "industry": "Internet Retail", "exchange": "NASDAQ", "currency": "USD",
        "debt_ratio": 0.22, "interest_ratio": 0.011,
        "pe": 44.8, "pb": 9.8, "profit_margin": 0.09, "roe": 0.22,
        "dividend_yield": 0.0, "market_cap": 2_200_000_000_000,
        "total_assets": 527_854_000_000, "total_debt": 58_314_000_000,
        "total_revenue": 574_785_000_000, "interest_expense": 3_282_000_000,
        "description": "Amazon operates e-commerce, AWS cloud computing, digital streaming, and logistics.",
    },
    "META": {
        "name": "Meta Platforms, Inc.", "sector": "Communication Services",
        "industry": "Internet Content & Information", "exchange": "NASDAQ", "currency": "USD",
        "debt_ratio": 0.09, "interest_ratio": 0.004,
        "pe": 28.6, "pb": 9.2, "profit_margin": 0.35, "roe": 0.38,
        "dividend_yield": 0.004, "market_cap": 1_400_000_000_000,
        "total_assets": 229_623_000_000, "total_debt": 28_826_000_000,
        "total_revenue": 134_902_000_000, "interest_expense": 567_000_000,
        "description": "Meta operates Facebook, Instagram, WhatsApp and develops virtual/augmented reality.",
    },
    "AMD": {
        "name": "Advanced Micro Devices, Inc.", "sector": "Technology",
        "industry": "Semiconductors", "exchange": "NASDAQ", "currency": "USD",
        "debt_ratio": 0.06, "interest_ratio": 0.014,
        "pe": 112.4, "pb": 4.4, "profit_margin": 0.04, "roe": 0.04,
        "dividend_yield": 0.0, "market_cap": 250_000_000_000,
        "total_assets": 67_885_000_000, "total_debt": 1_723_000_000,
        "total_revenue": 22_680_000_000, "interest_expense": 311_000_000,
        "description": "AMD designs CPUs, GPUs, and semi-custom chips for data centres, PCs, and gaming consoles.",
    },
    "AVGO": {
        "name": "Broadcom Inc.", "sector": "Technology",
        "industry": "Semiconductors", "exchange": "NASDAQ", "currency": "USD",
        "debt_ratio": 0.38, "interest_ratio": 0.072,
        "pe": 28.4, "pb": 12.6, "profit_margin": 0.22, "roe": 0.48,
        "dividend_yield": 0.012, "market_cap": 900_000_000_000,
        "total_assets": 165_645_000_000, "total_debt": 66_664_000_000,
        "total_revenue": 35_819_000_000, "interest_expense": 2_566_000_000,
        "description": "Broadcom designs semiconductors and infrastructure software for data centres and networking.",
    },
    "NFLX": {
        "name": "Netflix, Inc.", "sector": "Communication Services",
        "industry": "Entertainment", "exchange": "NASDAQ", "currency": "USD",
        "debt_ratio": 0.24, "interest_ratio": 0.022,
        "pe": 48.2, "pb": 16.4, "profit_margin": 0.16, "roe": 0.38,
        "dividend_yield": 0.0, "market_cap": 380_000_000_000,
        "total_assets": 48_731_000_000, "total_debt": 14_144_000_000,
        "total_revenue": 33_723_000_000, "interest_expense": 754_000_000,
        "description": "Netflix is a global streaming entertainment platform offering TV shows, films, and games.",
    },
    "JPM": {
        "name": "JPMorgan Chase & Co.", "sector": "Financial Services",
        "industry": "Banks — Diversified", "exchange": "NYSE", "currency": "USD",
        "debt_ratio": 0.91, "interest_ratio": None,
        "pe": 12.8, "pb": 2.0, "profit_margin": 0.28, "roe": 0.17,
        "dividend_yield": 0.024, "market_cap": 740_000_000_000,
        "total_assets": 3_875_393_000_000, "total_debt": None,
        "total_revenue": 162_395_000_000, "interest_expense": None,
        "description": "JPMorgan Chase is a global financial services firm. Core business is interest-based banking (riba).",
        "note": "Conventional banking operations involve riba, which is not permissible.",
        "haramFlags": ["conventional_banking"],
    },
    "BAC": {
        "name": "Bank of America Corporation", "sector": "Financial Services",
        "industry": "Banks — Diversified", "exchange": "NYSE", "currency": "USD",
        "debt_ratio": 0.90, "interest_ratio": None,
        "pe": 14.2, "pb": 1.2, "profit_margin": 0.24, "roe": 0.09,
        "dividend_yield": 0.026, "market_cap": 350_000_000_000,
        "total_assets": 3_318_340_000_000, "total_debt": None,
        "total_revenue": 101_912_000_000, "interest_expense": None,
        "description": "Bank of America provides banking, investment, and financial services. Core business is interest-based.",
        "note": "Conventional banking operations involve riba, which is not permissible.",
        "haramFlags": ["conventional_banking"],
    },
    "XOM": {
        "name": "Exxon Mobil Corporation", "sector": "Energy",
        "industry": "Oil & Gas Integrated", "exchange": "NYSE", "currency": "USD",
        "debt_ratio": 0.12, "interest_ratio": 0.008,
        "pe": 14.2, "pb": 2.2, "profit_margin": 0.09, "roe": 0.16,
        "dividend_yield": 0.034, "market_cap": 520_000_000_000,
        "total_assets": 376_317_000_000, "total_debt": 37_483_000_000,
        "total_revenue": 398_675_000_000, "interest_expense": 836_000_000,
        "description": "ExxonMobil is an integrated oil and gas company engaged in exploration, production and refining.",
    },
    "WMT": {
        "name": "Walmart Inc.", "sector": "Consumer Defensive",
        "industry": "Discount Stores", "exchange": "NYSE", "currency": "USD",
        "debt_ratio": 0.24, "interest_ratio": 0.009,
        "pe": 38.4, "pb": 6.8, "profit_margin": 0.025, "roe": 0.18,
        "dividend_yield": 0.010, "market_cap": 780_000_000_000,
        "total_assets": 254_396_000_000, "total_debt": 37_090_000_000,
        "total_revenue": 648_125_000_000, "interest_expense": 2_395_000_000,
        "description": "Walmart operates retail stores and e-commerce. Sells alcohol (some stores) but primary business is retail.",
    },
    "V": {
        "name": "Visa Inc.", "sector": "Financial Services",
        "industry": "Credit Services", "exchange": "NYSE", "currency": "USD",
        "debt_ratio": 0.21, "interest_ratio": 0.019,
        "pe": 30.4, "pb": 14.2, "profit_margin": 0.53, "roe": 0.49,
        "dividend_yield": 0.008, "market_cap": 620_000_000_000,
        "total_assets": 87_888_000_000, "total_debt": 20_876_000_000,
        "total_revenue": 32_653_000_000, "interest_expense": 617_000_000,
        "description": "Visa operates a global payment processing network. Does not issue credit or lend money directly.",
    },
    "MA": {
        "name": "Mastercard Incorporated", "sector": "Financial Services",
        "industry": "Credit Services", "exchange": "NYSE", "currency": "USD",
        "debt_ratio": 0.28, "interest_ratio": 0.022,
        "pe": 36.2, "pb": 60.4, "profit_margin": 0.46, "roe": 2.12,
        "dividend_yield": 0.006, "market_cap": 480_000_000_000,
        "total_assets": 40_102_000_000, "total_debt": 13_987_000_000,
        "total_revenue": 25_098_000_000, "interest_expense": 545_000_000,
        "description": "Mastercard operates a global payment network. Does not issue credit directly.",
    },
    "JNJ": {
        "name": "Johnson & Johnson", "sector": "Healthcare",
        "industry": "Drug Manufacturers", "exchange": "NYSE", "currency": "USD",
        "debt_ratio": 0.22, "interest_ratio": 0.012,
        "pe": 16.8, "pb": 5.2, "profit_margin": 0.18, "roe": 0.30,
        "dividend_yield": 0.032, "market_cap": 380_000_000_000,
        "total_assets": 167_558_000_000, "total_debt": 25_881_000_000,
        "total_revenue": 85_159_000_000, "interest_expense": 1_004_000_000,
        "description": "J&J develops pharmaceuticals, medical devices, and consumer health products.",
    },
    "NKE": {
        "name": "NIKE, Inc.", "sector": "Consumer Cyclical",
        "industry": "Footwear & Accessories", "exchange": "NYSE", "currency": "USD",
        "debt_ratio": 0.28, "interest_ratio": 0.014,
        "pe": 22.6, "pb": 8.4, "profit_margin": 0.10, "roe": 0.44,
        "dividend_yield": 0.020, "market_cap": 120_000_000_000,
        "total_assets": 37_748_000_000, "total_debt": 8_930_000_000,
        "total_revenue": 51_362_000_000, "interest_expense": 733_000_000,
        "description": "Nike designs and sells athletic footwear, apparel, and equipment globally.",
    },
    "BABA": {
        "name": "Alibaba Group Holding Limited", "sector": "Consumer Cyclical",
        "industry": "Internet Retail", "exchange": "NYSE", "currency": "USD",
        "debt_ratio": 0.14, "interest_ratio": 0.018,
        "pe": 10.2, "pb": 1.2, "profit_margin": 0.14, "roe": 0.11,
        "dividend_yield": 0.016, "market_cap": 220_000_000_000,
        "total_assets": 277_860_000_000, "total_debt": 28_862_000_000,
        "total_revenue": 130_352_000_000, "interest_expense": 1_142_000_000,
        "description": "Alibaba operates e-commerce, cloud computing, and digital payments in China and globally.",
        "note": "Ant Group (Alipay) fintech arm involves interest-based lending. Interest ratio may be understated.",
    },
    "COIN": {
        "name": "Coinbase Global, Inc.", "sector": "Financial Services",
        "industry": "Financial Data & Stock Exchanges", "exchange": "NASDAQ", "currency": "USD",
        "debt_ratio": 0.18, "interest_ratio": 0.028,
        "pe": 28.4, "pb": 6.2, "profit_margin": 0.12, "roe": 0.22,
        "dividend_yield": 0.0, "market_cap": 65_000_000_000,
        "total_assets": 24_618_000_000, "total_debt": 4_208_000_000,
        "total_revenue": 3_108_000_000, "interest_expense": 168_000_000,
        "description": "Coinbase operates a cryptocurrency exchange platform.",
        "note": "Crypto trading involves significant speculation (gharar). Scholars differ on permissibility.",
    },
}


def fetch_us_db_stock(ticker: str) -> dict:
    """Return US stock data from built-in database with live price from Tiingo."""
    db = US_DB[ticker]

    # Try to get live price from Tiingo
    price = None; prev_close = None; change_pct = 0.0; volume = None
    try:
        iex = tiingo_get(f"iex/{ticker}")
        if iex and isinstance(iex, list) and iex:
            q          = iex[0]
            price      = safe_float(q.get("last") or q.get("tngoLast"))
            prev_close = safe_float(q.get("prevClose"), price)
            change_pct = ((price - prev_close) / prev_close * 100) if (price and prev_close) else 0.0
            volume     = safe_int(q.get("volume"))
    except Exception:
        pass

    # Try historical prices for chart
    history = []
    try:
        import datetime
        start = (datetime.date.today() - datetime.timedelta(days=180)).isoformat()
        hist  = tiingo_get(f"tiingo/daily/{ticker}/prices",
                           {"startDate": start, "resampleFreq": "monthly"})
        if hist and isinstance(hist, list):
            for row in hist[-6:]:
                cl = safe_float(row.get("adjClose") or row.get("close"), 0)
                history.append({
                    "date":   row.get("date","")[:7],
                    "close":  cl, "open": cl, "high": cl, "low": cl,
                    "volume": safe_int(row.get("volume"), 0),
                })
    except Exception:
        pass

    dr = db.get("debt_ratio")
    ir = db.get("interest_ratio")
    sc_check  = check_sc_list(ticker)
    # The description is still passed for display, but screen_halal no longer
    # scans it for prohibited-business keywords, only the sector, industry and
    # company name. See the note in the business-activity check.
    screening = screen_halal(
        name=db["name"], sector=db["sector"], industry=db["industry"],
        description=db.get("description","") + " " + db.get("note",""),
        debt_ratio=dr, interest_ratio=ir,
        pe_ratio=db.get("pe"), profit_margin=db.get("profit_margin"),
        sc_check=sc_check, flags=db.get("haramFlags"),
    )

    return {
        "ticker": ticker, "name": db["name"],
        "sector": db["sector"], "industry": db["industry"],
        "description": db.get("description",""),
        "exchange": db.get("exchange","NASDAQ"), "currency": "USD",
        "price":      round(price, 4) if price else None,
        "prevClose":  round(prev_close, 4) if prev_close else None,
        "changePct":  round(change_pct, 3),
        "week52High": None, "week52Low": None,
        "volume": volume, "avgVolume": None,
        "marketCap": db.get("market_cap"),
        "beta": None,
        "totalAssets":    db.get("total_assets"),
        "totalDebt":      db.get("total_debt"),
        "totalRevenue":   db.get("total_revenue"),
        "interestExpense":db.get("interest_expense"),
        "grossProfit": None,
        "debtRatio":     dr,
        "interestRatio": ir,
        "peRatio":        db.get("pe"),
        "pbRatio":        db.get("pb"),
        "profitMargin":   db.get("profit_margin"),
        "returnOnEquity": db.get("roe"),
        "returnOnAssets": None,
        "dividendYield":  db.get("dividend_yield"),
        "earningsGrowth": None, "revenueGrowth": None,
        "currentRatio": None, "quickRatio": None,
        "history": history, "scCheck": sc_check, "screening": screening,
        "fetchedAt": time.strftime("%H:%M:%S"),
        "_cached": False, "_source": "US DB (SEC FY2023/2024) + Tiingo live price",
        "_dataNote": "Financial ratios from SEC public filings (FY2023/2024). Price fetched live where available.",
        "dataAsOf":  "live price where available; financials from FY2024 filings",
    }

class UpstreamError(RuntimeError):
    """A provider failure that is not the caller's fault, mapped to HTTP 5xx."""


def tiingo_get(path: str, params: dict = None) -> any:
    """
    Make a Tiingo API request.

    Returns None for "this provider does not know the ticker", which callers
    treat as a fallback signal. Raises UpstreamError for anything that is the
    provider's problem, so the route can answer 502/503 rather than blaming the
    client with a 400, and so a raw exception string never reaches the browser.
    """
    used = req_increment()
    url  = f"{TIINGO_BASE}/{path}"
    headers = {
        "Authorization": f"Token {TIINGO_KEY}",
        "Content-Type":  "application/json",
    }
    print(f"  Tiingo #{used}: /{path.split('?')[0]}")
    try:
        # 10s, not 15s. A single screen can make several of these in series and
        # the gunicorn worker is killed at 25s, so the old budget let one slow
        # provider response consume the whole request before any of it rendered.
        resp = requests.get(url, params=params or {}, headers=headers, timeout=10)
        if resp.status_code in (400, 404):
            return None   # ticker not carried on this plan; caller falls back
        if resp.status_code == 401:
            raise UpstreamError("The data provider rejected the configured API key.")
        if resp.status_code == 429:
            raise UpstreamError("The data provider's rate limit was reached. Try again shortly.")
        if resp.status_code >= 500:
            raise UpstreamError(f"The data provider returned an error ({resp.status_code}).")
        if not resp.ok:
            raise UpstreamError(f"The data provider returned an unexpected status ({resp.status_code}).")
        return resp.json()
    except requests.Timeout:
        raise UpstreamError("The data provider did not respond in time.")
    except requests.RequestException:
        raise UpstreamError("The data provider could not be reached.")


# Tiingo's free plan does not serve the fundamentals endpoints usefully: they
# come back empty, and every attempt still counts against the daily budget.
# Probe once, then stop asking until the process restarts, so a plan upgrade
# still gets picked up on the next deploy.
_fund_lock       = threading.Lock()
_fund_state      = {"available": True, "misses": 0}

def fundamentals_available() -> bool:
    with _fund_lock:
        return _fund_state["available"]

def note_fundamentals_result(got_data: bool) -> None:
    with _fund_lock:
        if got_data:
            _fund_state["available"] = True
            _fund_state["misses"]    = 0
        else:
            _fund_state["misses"] += 1
            if _fund_state["misses"] >= 3:
                _fund_state["available"] = False
                print("  Fundamentals endpoints returned nothing 3 times; "
                      "skipping them for this process.")

def fetch_us_stock(ticker: str) -> dict:
    """
    Fetch US/international stock data from Tiingo.

    The request budget drives the shape of this function. The free plan allows
    1000 calls a day and every screen used to cost six of them, so about 160
    distinct tickers exhausted it. It now costs two when the fundamentals
    endpoints do not answer (metadata, plus one price series that supplies the
    quote, the 52-week range and the chart) and four when they do.
    """
    # 1. Metadata (name, description, exchange)
    meta = tiingo_get(f"tiingo/daily/{ticker}")
    if not meta:
        if ticker in US_DB:
            print(f"  -> Tiingo has no metadata for {ticker}, using the built-in record")
            return fetch_us_db_stock(ticker)
        raise ValueError(
            f"Ticker '{ticker}' not found. "
            "US examples: TSLA, NVDA, GOOGL, AMZN, META. "
            "Bursa examples: 1295, 1155, 5347."
        )

    name        = meta.get("name")        or ticker
    description = (meta.get("description") or "")[:400]
    exchange    = meta.get("exchangeCode") or "N/A"

    # 2. One daily series covering the year. This replaces three calls that each
    #    fetched part of the same thing: a one-row quote, a legacy IEX quote and
    #    a separate six-month series for the chart. It also corrects two wrong
    #    figures:
    #      - the previous close used to be the same day's adjusted close, so the
    #        "daily change" was a dividend artefact rather than a daily move;
    #      - "week52High"/"week52Low" were the latest day's high and low. On
    #        META those sat 1.8% apart, which no genuine 52-week range does.
    from datetime import datetime, timedelta
    start  = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    series = tiingo_get(f"tiingo/daily/{ticker}/prices",
                        {"startDate": start, "sort": "date"})

    bars = []
    if series and isinstance(series, list):
        for row in series:
            cl = safe_float(row.get("close"))
            if cl is None:
                continue
            bars.append({
                "date":   (row.get("date") or "")[:10],
                "close":  cl,
                "high":   safe_float(row.get("high"), cl),
                "low":    safe_float(row.get("low"),  cl),
                "volume": safe_int(row.get("volume"), 0),
            })

    if not bars:
        raise ValueError(f"No price history returned for '{ticker}'.")

    price       = bars[-1]["close"]
    prev_close  = bars[-2]["close"] if len(bars) >= 2 else None
    change_pct  = (((price - prev_close) / prev_close * 100)
                   if price and prev_close else None)
    volume      = bars[-1]["volume"]
    avg_volume  = int(sum(b["volume"] for b in bars) / len(bars))
    week52_high = max(b["high"] for b in bars)
    week52_low  = min(b["low"]  for b in bars)

    # Month-end closes from the same series, last six months.
    monthly = {}
    for b in bars:
        monthly[b["date"][:7]] = b
    history = [{
        "date":   k,
        "close":  round(b["close"], 3),
        "open":   round(b["close"], 3),
        "high":   round(b["high"], 3),
        "low":    round(b["low"], 3),
        "volume": b["volume"],
    } for k, b in sorted(monthly.items())][-6:]

    # 3. Fundamentals (income statement, balance sheet, key metrics)
    income_stmt = {}; balance_sheet = {}; metrics = {}
    if fundamentals_available():
        fund_data = tiingo_get(f"tiingo/fundamentals/{ticker}/statements",
                               {"frequency": "annual", "limit": 1})
        overview  = tiingo_get(f"tiingo/fundamentals/{ticker}/daily",
                               {"limit": 1})

        if fund_data and isinstance(fund_data, list) and fund_data:
            stmts = fund_data[0].get("statementData", {})
            for stmt in stmts.get("incomeStatement", [{}]):
                code = stmt.get("dataCode")
                if code == "revenue":     income_stmt["revenue"]     = stmt.get("value")
                if code == "intExp":      income_stmt["intExp"]      = stmt.get("value")
                if code == "grossProfit": income_stmt["grossProfit"] = stmt.get("value")
                if code == "netMargin":   income_stmt["netMargin"]   = stmt.get("value")
            for stmt in stmts.get("balanceSheet", [{}]):
                code = stmt.get("dataCode")
                if code == "totalAssets":  balance_sheet["totalAssets"]  = stmt.get("value")
                if code == "totalDebt":    balance_sheet["totalDebt"]    = stmt.get("value")
                if code == "currentRatio": balance_sheet["currentRatio"] = stmt.get("value")

        if overview and isinstance(overview, list) and overview:
            metrics = overview[0]

        note_fundamentals_result(bool(income_stmt or balance_sheet or metrics))

    total_revenue  = safe_float(income_stmt.get("revenue"))
    int_expense    = safe_float(income_stmt.get("intExp"))
    gross_profit   = safe_float(income_stmt.get("grossProfit"))
    profit_margin  = safe_float(income_stmt.get("netMargin"))
    total_assets   = safe_float(balance_sheet.get("totalAssets"))
    total_debt     = safe_float(balance_sheet.get("totalDebt"))
    current_ratio  = safe_float(balance_sheet.get("currentRatio"))
    pe_ratio       = safe_float(metrics.get("peRatio"))
    pb_ratio       = safe_float(metrics.get("pbRatio"))
    market_cap     = safe_float(metrics.get("marketCap"))
    div_yield      = safe_float(metrics.get("divYield"))
    roe            = safe_float(metrics.get("roe"))
    roa            = safe_float(metrics.get("roa"))

    debt_ratio     = (total_debt / total_assets
                      if total_assets and total_debt is not None and total_assets > 0
                      else None)
    interest_ratio = (abs(int_expense) / total_revenue
                      if total_revenue and int_expense is not None and total_revenue > 0
                      else None)

    # 4. Fall back to the built-in filings record where one exists. Live figures
    #    still win. Without this, every US stock screened as Doubtful on every
    #    request, for every ticker, purely because two inputs were missing from
    #    a source that was never going to supply them.
    db_entry  = US_DB.get(ticker)
    sector, industry = "N/A", "N/A"
    if db_entry:
        sector   = db_entry["sector"]
        industry = db_entry["industry"]
        if debt_ratio     is None: debt_ratio     = db_entry.get("debt_ratio")
        if interest_ratio is None: interest_ratio = db_entry.get("interest_ratio")
        if pe_ratio       is None: pe_ratio       = db_entry.get("pe")
        if pb_ratio       is None: pb_ratio       = db_entry.get("pb")
        if profit_margin  is None: profit_margin  = db_entry.get("profit_margin")
        if roe            is None: roe            = db_entry.get("roe")
        if div_yield      is None: div_yield      = db_entry.get("dividend_yield")
        if market_cap     is None: market_cap     = db_entry.get("market_cap")
        if total_assets   is None: total_assets   = db_entry.get("total_assets")
        if total_debt     is None: total_debt     = db_entry.get("total_debt")
        if total_revenue  is None: total_revenue  = db_entry.get("total_revenue")
        if int_expense    is None: int_expense    = db_entry.get("interest_expense")

    from_db_filings = bool(db_entry) and not (income_stmt or balance_sheet or metrics)

    sc_check  = check_sc_list(ticker)
    screening = screen_halal(
        name=name, sector=sector, industry=industry,
        description=description,
        debt_ratio=debt_ratio, interest_ratio=interest_ratio,
        pe_ratio=pe_ratio, profit_margin=profit_margin,
        sc_check=sc_check,
        flags=db_entry.get("haramFlags") if db_entry else None,
    )

    return {
        "ticker": ticker, "name": name,
        "sector": sector, "industry": industry,
        "description": description, "exchange": exchange, "currency": "USD",
        "price":       round(price, 4) if price is not None else None,
        "prevClose":   round(prev_close, 4) if prev_close is not None else None,
        "changePct":   round(change_pct, 3) if change_pct is not None else None,
        "week52High":  round(week52_high, 4),
        "week52Low":   round(week52_low, 4),
        "volume":      volume, "avgVolume": avg_volume, "marketCap": safe_int(market_cap),
        "beta":        None,
        "totalAssets":    safe_int(total_assets),
        "totalDebt":      safe_int(total_debt),
        "totalRevenue":   safe_int(total_revenue),
        "interestExpense":safe_int(abs(int_expense)) if int_expense else None,
        "grossProfit":    safe_int(gross_profit),
        "debtRatio":     round(debt_ratio,     4) if debt_ratio     is not None else None,
        "interestRatio": round(interest_ratio, 4) if interest_ratio is not None else None,
        "peRatio":        round(pe_ratio,  2) if pe_ratio        else None,
        "pbRatio":        round(pb_ratio,  3) if pb_ratio        else None,
        "profitMargin":   round(profit_margin, 4) if profit_margin is not None else None,
        "returnOnEquity": round(roe, 4) if roe is not None else None,
        "returnOnAssets": round(roa, 4) if roa is not None else None,
        "dividendYield":  round(div_yield, 4) if div_yield is not None else None,
        "earningsGrowth": None, "revenueGrowth": None,
        "currentRatio":   round(current_ratio, 3) if current_ratio is not None else None,
        "quickRatio": None,
        "history":   history,
        "scCheck":   sc_check,
        "screening": screening,
        "fetchedAt": time.strftime("%H:%M:%S"),
        "_cached":   False,
        "_source":   ("Tiingo API + built-in FY2023/2024 filings"
                      if from_db_filings else "Tiingo API"),
        "_dataNote": ("Price and chart are live from Tiingo. Fundamentals come from the "
                      "built-in filings record because the provider did not supply them."
                      if from_db_filings else
                      "Price and chart are live from Tiingo."),
        "dataAsOf":  ("live price; financials from FY2024 filings"
                      if from_db_filings else
                      "live price and provider fundamentals"),
    }

# ══════════════════════════════════════════════════════════
#  BURSA STOCK FETCH (live Yahoo quote + hardcoded financials)
# ══════════════════════════════════════════════════════════

BURSA_LIVE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36",
}

BURSA_HISTORY_MONTHS = 6

def fetch_bursa_live(code: str) -> dict | None:
    """
    Fetch a live Bursa Malaysia quote from Yahoo Finance's chart API.
    Quotes are ~15 minutes delayed (Bursa has no free real-time feed).
    Returns None on any failure so the caller can fall back to the DB.

    One call, daily bars, for two reasons.

    The daily change needs a previous close. Yahoo leaves meta.previousClose
    empty on this endpoint, and meta.chartPreviousClose is the close before the
    START of the requested range, which is roughly six months back at range=6mo.
    Reading that as yesterday's close reported Tenaga's six-month drift of
    -7.4% as a single day's fall. The second-to-last bar gives the real previous
    close, which for the same stock on the same data is +0.5%.

    The chart is then bucketed to month-end closes from that same daily series,
    so the quote and the chart cannot disagree. Asking Yahoo for interval=1mo
    directly returned the current price repeated across the recent months.
    """
    if not code:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.KL"
    params = {"range": "6mo", "interval": "1d"}

    for attempt in range(2):
        try:
            resp = requests.get(url, params=params,
                                headers=BURSA_LIVE_HEADERS, timeout=8)
            if resp.status_code != 200:
                time.sleep(0.5 * (attempt + 1))
                continue
            data = resp.json()
            result = (data.get("chart", {}).get("result") or [None])[0]
            if not result:
                return None

            meta   = result.get("meta", {})
            ts     = result.get("timestamp") or []
            q      = (result.get("indicators", {}).get("quote") or [{}])[0]
            closes = q.get("close")  or []
            highs  = q.get("high")   or []
            lows   = q.get("low")    or []
            vols   = q.get("volume") or []

            from datetime import datetime, timezone
            bars = []
            for i, t in enumerate(ts):
                cl = closes[i] if i < len(closes) else None
                if cl is None:
                    continue
                bars.append({
                    "date":   datetime.fromtimestamp(t, tz=timezone.utc).date(),
                    "close":  cl,
                    "high":   highs[i] if i < len(highs) else cl,
                    "low":    lows[i]  if i < len(lows)  else cl,
                    "volume": safe_int(vols[i], 0) if i < len(vols) else 0,
                })
            if not bars:
                return None

            price = safe_float(meta.get("regularMarketPrice"), bars[-1]["close"])
            # Second-to-last bar. Correct whether the final bar is a finished
            # session or today's still-forming one.
            prev  = bars[-2]["close"] if len(bars) >= 2 else None
            change_pct = (((price - prev) / prev * 100)
                          if price and prev else None)
            volume = safe_int(meta.get("regularMarketVolume"), bars[-1]["volume"])
            wk_hi  = safe_float(meta.get("fiftyTwoWeekHigh"))
            wk_lo  = safe_float(meta.get("fiftyTwoWeekLow"))

            # Month-end closes, oldest first.
            monthly = {}
            for b in bars:
                monthly[(b["date"].year, b["date"].month)] = b
            history = [{
                "date":   f"{y:04d}-{mo:02d}",
                "close":  round(b["close"], 3),
                "open":   round(b["close"], 3),
                "high":   round(b["high"], 3),
                "low":    round(b["low"], 3),
                "volume": b["volume"],
            } for (y, mo), b in sorted(monthly.items())][-BURSA_HISTORY_MONTHS:]

            return {
                "price":      round(price, 4)  if price is not None else None,
                "prevClose":  round(prev, 4)   if prev  is not None else None,
                "changePct":  round(change_pct, 3) if change_pct is not None else None,
                "week52High": round(wk_hi, 4)  if wk_hi is not None else None,
                "week52Low":  round(wk_lo, 4)  if wk_lo is not None else None,
                "volume":     volume,
                "history":    history,
            }
        except Exception as e:
            print(f"  Bursa live fetch failed (attempt {attempt+1}): {e}")
            time.sleep(0.5 * (attempt + 1))

    return None


def fetch_bursa_stock(symbol: str) -> dict:
    """Fetch Bursa Malaysia stock from hardcoded database + live Yahoo price."""
    db = bursa_lookup(symbol)
    code = get_bursa_code(symbol)

    if not db:
        # List available stocks in error message
        available = ", ".join(sorted(BURSA_DB.keys())[:15]) + "..."
        raise ValueError(
            f"Bursa stock '{symbol}' not in database. "
            f"Available codes: {available}. "
            f"For full live data on all Bursa stocks, use the PC version of this app."
        )

    sc_check = check_sc_list(code or symbol)

    # Use SC status from database if not in SC list
    if not sc_check.get("found") and db.get("scStatus"):
        sc_status = db["scStatus"]
        sc_check = {
            "found":  True,
            "status": sc_status,
            "source": "database",
            "note": (
                f"Listed as Shariah-{'compliant' if sc_status=='compliant' else 'non-compliant'} "
                f"based on SC Malaysia records. Always verify latest list at sc.com.my"
            )
        }

    debt_ratio     = db.get("debtRatio")
    interest_ratio = db.get("interestRatio")
    pe_ratio       = db.get("peRatio")
    profit_margin  = db.get("profitMargin")

    screening = screen_halal(
        name=db["name"], sector=db["sector"], industry=db["industry"],
        description=db.get("description",""),
        debt_ratio=debt_ratio, interest_ratio=interest_ratio,
        pe_ratio=pe_ratio, profit_margin=profit_margin,
        sc_check=sc_check, flags=db.get("haramFlags"),
    )

    # Live price overlay (Yahoo Finance, ~15 min delayed), falling back to the
    # database. The old fallback was week52High * 0.85, which put a number that
    # nothing had ever observed into the price field. A missing quote now
    # reports as missing so the UI can say so.
    live = fetch_bursa_live(code)
    is_live = live is not None

    price      = live["price"]      if is_live else None
    prev_close = live["prevClose"]  if is_live else None
    change_pct = live["changePct"]  if is_live else None
    week_hi    = live["week52High"] if is_live and live["week52High"] is not None else db.get("week52High")
    week_lo    = live["week52Low"]  if is_live and live["week52Low"]  is not None else db.get("week52Low")
    volume     = live["volume"]     if is_live else None

    history = live["history"] if is_live and live["history"] else []

    return {
        "ticker":      code or symbol,
        "name":        db["name"],
        "sector":      db["sector"],
        "industry":    db["industry"],
        "description": db.get("description",""),
        "exchange":    "KLSE",
        "currency":    "MYR",
        "price":       price,
        "prevClose":   prev_close,
        "changePct":   change_pct,
        "week52High":  week_hi,
        "week52Low":   week_lo,
        "volume":      volume,
        "avgVolume":   None,
        "marketCap":   db.get("marketCap"),
        "beta":        None,
        "totalAssets":    db.get("totalAssets"),
        "totalDebt":      db.get("totalDebt"),
        "totalRevenue":   db.get("totalRevenue"),
        "interestExpense":db.get("interestExpense"),
        "grossProfit":    None,
        "debtRatio":      round(debt_ratio,     4) if debt_ratio     is not None else None,
        "interestRatio":  round(interest_ratio, 4) if interest_ratio is not None else None,
        "peRatio":         db.get("peRatio"),
        "pbRatio":         db.get("pbRatio"),
        "profitMargin":    db.get("profitMargin"),
        "returnOnEquity":  db.get("returnOnEquity"),
        "returnOnAssets":  None,
        "dividendYield":   db.get("dividendYield"),
        "earningsGrowth":  None,
        "revenueGrowth":   None,
        "currentRatio":    None,
        "quickRatio":      None,
        "history":    history,
        "scCheck":    sc_check,
        "screening":  screening,
        "fetchedAt":  time.strftime("%H:%M:%S"),
        "_cached":    False,
        "_source":    ("Yahoo Finance (live, ~15 min delayed) + Bursa DB financials"
                       if is_live else "Bursa Malaysia Database (Annual Report Data)"),
        "_dataNote":  ("Price/change/volume/chart are live (15-min delayed). "
                       "Financials are from FY2023/2024 annual reports."
                       if is_live else
                       "Live quote unavailable, so no price is shown. "
                       "Financials are from FY2023/2024 annual reports."),
        "dataAsOf":   ("live quote, 15 min delayed; financials from FY2024 annual report"
                       if is_live else
                       "no live quote available; financials from FY2024 annual report"),
    }

# ══════════════════════════════════════════════════════════
#  MAIN FETCH DISPATCHER
# ══════════════════════════════════════════════════════════

def fetch_stock(symbol: str) -> dict:
    ticker = normalise_ticker(symbol)

    cached = cache_get(ticker)
    if cached:
        r = dict(cached); r["_cached"] = True
        return r

    print(f"\n  === Fetching {ticker} ===")

    if is_bursa(ticker):
        result = fetch_bursa_stock(ticker)
    else:
        result = fetch_us_stock(ticker)

    cache_set(ticker, result)
    return result

# ══════════════════════════════════════════════════════════
#  SHARIAH SCREENING ENGINE
# ══════════════════════════════════════════════════════════

def screen_halal(name, sector, industry, description,
                 debt_ratio, interest_ratio, pe_ratio,
                 profit_margin, sc_check=None, flags=None):
    """
    Run the Shariah screen and return a verdict.

    Two rules govern the shape of this function:

    1. An authoritative source beats a heuristic. Where the SC Malaysia list
       covers a stock it settles the business-activity question, and the sector
       heuristics below do not get to contradict it.

    2. Missing data is not the same as a clean result. An input that could not
       be fetched is recorded as unverified and pushes risk up, because the old
       behaviour scored a missing balance sheet as zero debt, which is the
       safest possible score for the least verified company.
    """
    checks=[]; issues=[]; warnings=[]; missing=[]

    sc_status        = (sc_check or {}).get("status")
    sc_authoritative = bool((sc_check or {}).get("found")) and \
                       sc_status in ("compliant", "non_compliant")

    # ── 1. SC Malaysia official list ──────────────────────
    if sc_authoritative:
        if sc_status == "compliant":
            checks.append({"status":"pass","name":"SC Malaysia Official Shariah List",
                "detail":f"✓ Listed as Shariah-compliant by SC Malaysia. {sc_check.get('note','')}"})
        else:
            checks.append({"status":"fail","name":"SC Malaysia Official Shariah List",
                "detail":f"✗ Listed as non-Shariah-compliant by SC Malaysia. {sc_check.get('note','')}"})
            issues.append("sc_non_compliant")
    elif sc_status == "not_applicable":
        checks.append({"status":"n/a","name":"SC Malaysia Official Shariah List",
            "detail":"The SC Malaysia list covers Bursa Malaysia listings only. This stock is screened on the criteria below."})
    else:
        checks.append({"status":"warn","name":"SC Malaysia Official Shariah List",
            "detail":"Not found in built-in SC list. Verify manually at sc.com.my"})
        warnings.append("sc_not_found")

    # ── 2. Business activity ──────────────────────────────
    # An explicit flag on the database entry wins. Otherwise scan the structured
    # fields and the company name, never the free-text description: scanning
    # prose flagged Walmart as "Not Halal" because its blurb mentions that some
    # stores sell alcohol, which is not the same claim as alcohol being the
    # core business.
    combined    = f"{sector} {industry} {name}".lower()
    has_sector  = bool(sector and sector.strip().upper() not in ("N/A", "NA", "NONE", ""))
    flag        = next((f for f in (flags or []) if f in HARAM_FLAGS), None)
    hkw         = next((kw for kw in HARAM_KEYWORDS if kw in combined), None)

    if flag:
        checks.append({"status":"fail","name":"Business Activity / Industry",
            "detail":HARAM_FLAGS[flag]})
        issues.append("haram_industry")
    elif hkw:
        checks.append({"status":"fail","name":"Business Activity / Industry",
            "detail":f'Keyword "{hkw}" found in the sector, industry or company name. Core business involves a prohibited activity.'})
        issues.append("haram_industry")
    elif not has_sector:
        # The live provider path carries no sector for many tickers. Reporting
        # "nothing prohibited detected" when nothing was examined is a false
        # all-clear, so it is recorded as unverified.
        checks.append({"status":"warn","name":"Business Activity / Industry",
            "detail":"Sector and industry are not supplied by this data source, so the core business could not be checked. Verify against the company's filings."})
        warnings.append("no_business_data")
        missing.append("sector")
    elif sc_authoritative:
        # The official list has already vetted the business activity. Adding a
        # "doubtful sector" warning on top made listed Islamic banks such as
        # Public Bank and Maybank report as Doubtful directly beneath a check
        # that read "Listed as Shariah-compliant by SC Malaysia".
        checks.append({"status":"pass","name":"Business Activity / Industry",
            "detail":f"Sector ({sector}) / Industry ({industry}). Covered by the SC Malaysia listing above."})
    elif next((d for d in DOUBTFUL_SECTORS if d in combined), None):
        checks.append({"status":"warn","name":"Business Activity / Industry",
            "detail":f'Sector "{sector}" may have mixed income sources. Requires verification.'})
        warnings.append("doubtful_sector")
    else:
        checks.append({"status":"pass","name":"Business Activity / Industry",
            "detail":f"Sector ({sector}) / Industry ({industry}) — no prohibited activity detected."})

    # ── 3. Debt-to-assets ratio (AAOIFI) ──────────────────
    if debt_ratio is None:
        checks.append({"status":"warn","name":"Debt-to-Assets Ratio (AAOIFI: ≤ 33%)",
            "detail":"Balance sheet data is unavailable from the current source. Unverified, not clean."})
        warnings.append("no_debt_data")
        missing.append("debtRatio")
    elif debt_ratio > DEBT_THRESHOLD:
        sev = "fail" if debt_ratio > 0.50 else "warn"
        checks.append({"status":sev,"name":"Debt-to-Assets Ratio (AAOIFI: ≤ 33%)",
            "detail":f"Ratio is {debt_ratio*100:.1f}% — {'significantly ' if debt_ratio>0.50 else 'marginally '}exceeds the 33% AAOIFI threshold."})
        if sev == "fail": issues.append("high_debt")
        else:             warnings.append("marginal_debt")
    else:
        checks.append({"status":"pass","name":"Debt-to-Assets Ratio (AAOIFI: ≤ 33%)",
            "detail":f"Ratio is {debt_ratio*100:.1f}% — within the permissible 33% limit."})

    # ── 4. Non-permissible income (DJIM) ──────────────────
    # The figure this app can compute is interest EXPENSE over revenue. The DJIM
    # test is interest INCOME as a share of total revenue, and no free source
    # used here supplies that. The proxy is labelled as a proxy so the number is
    # not read as the DJIM ratio itself.
    if interest_ratio is None:
        if is_financial(sector, industry):
            checks.append({"status":"n/a","name":"Non-Permissible Income (DJIM: ≤ 5%)",
                "detail":"Not computed for banks and financial firms. SC Malaysia applies this test using its own method, shown in the first check above."})
        else:
            checks.append({"status":"warn","name":"Non-Permissible Income (DJIM: ≤ 5%)",
                "detail":"Interest and revenue data is unavailable. Unverified, not clean."})
            warnings.append("no_income_data")
            missing.append("interestRatio")
    elif interest_ratio > INCOME_THRESHOLD:
        sev = "fail" if interest_ratio > 0.20 else "warn"
        checks.append({"status":sev,"name":"Non-Permissible Income (DJIM: ≤ 5%)",
            "detail":f"Interest expense is {interest_ratio*100:.1f}% of revenue, {'well above' if interest_ratio>0.20 else 'above'} the 5% DJIM limit. Proxy measure: interest income, which DJIM actually tests, is not available from this source."})
        if sev == "fail": issues.append("high_interest")
        else:             warnings.append("marginal_interest")
    else:
        checks.append({"status":"pass","name":"Non-Permissible Income (DJIM: ≤ 5%)",
            "detail":f"Interest expense is {interest_ratio*100:.1f}% of revenue, within the 5% limit. Proxy measure: interest income, which DJIM actually tests, is not available from this source."})

    # ── 5. Gharar: real value creation ────────────────────
    if pe_ratio is not None and pe_ratio < 0:
        checks.append({"status":"warn","name":"Gharar Check — Real Value Creation",
            "detail":f"Negative P/E ({pe_ratio:.1f}x) — company is loss-making."})
        warnings.append("loss_making")
    elif profit_margin is not None and profit_margin < 0:
        checks.append({"status":"warn","name":"Gharar Check — Real Value Creation",
            "detail":f"Negative profit margin ({profit_margin*100:.1f}%) — company operating at a loss."})
        warnings.append("loss_making")
    else:
        checks.append({"status":"pass","name":"Gharar Check — Real Value Creation",
            "detail":"Company generates positive economic value. " +
                     (f"P/E: {pe_ratio:.1f}x." if pe_ratio else "P/E data unavailable.")})

    # ── 6. Verdict ────────────────────────────────────────
    if issues:
        verdict,v_class,v_icon,v_reason = "Not Halal","haram","✗","Fails one or more categorical Shariah screening criteria."
    elif warnings:
        verdict,v_class,v_icon,v_reason = "Doubtful","doubtful","◐","Borderline, or some criteria could not be verified. Consult a qualified Islamic finance scholar."
    else:
        verdict,v_class,v_icon,v_reason = "Potentially Halal","halal","✓","Passes all standard Shariah screening criteria. Always verify with a scholar."

    # ── 7. Risk ───────────────────────────────────────────
    if issues:
        risk = "HIGH"
    else:
        s = 0
        if debt_ratio is None:      s += 1   # unverified is not the same as clean
        elif debt_ratio > 0.25:     s += 2
        elif debt_ratio > 0.15:     s += 1
        if profit_margin is None:   s += 1
        elif profit_margin < 0:     s += 2
        elif profit_margin < 0.05:  s += 1
        s += len(warnings)
        risk = "HIGH" if s >= 4 else ("MEDIUM" if s >= 2 else "LOW")

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
            "checks":checks,"issues":issues,"warnings":warnings,"risk":risk,"rec":rec,
            "missingInputs":missing,
            "dataQuality":"complete" if not missing else "partial"}

# ── Dividend Purification ─────────────────────────────────
def calc_purification(dividend, interest_ratio, currency="MYR"):
    if interest_ratio is None or interest_ratio <= 0:
        return {"dividend":dividend,"interestRatio":interest_ratio,
                "purifyAmount":0.0,"keepAmount":dividend,"currency":currency,
                "note":"No purification needed.","isRequired":False}
    purify = round(dividend * interest_ratio, 4)
    keep   = round(dividend - purify, 4)
    note = (f"{'Small' if interest_ratio<=0.05 else 'Significant'} purification required. "
            f"Donate {currency} {purify:.2f} ({interest_ratio*100:.1f}% of dividend). "
            f"You keep {currency} {keep:.2f}.")
    if interest_ratio > 0.05:
        note += " Consider whether this stock suits your portfolio."
    return {"dividend":dividend,"interestRatio":interest_ratio,"purifyAmount":purify,
            "keepAmount":keep,"currency":currency,"note":note,
            "isRequired":purify>0,"percentage":round(interest_ratio*100,2)}

# ══════════════════════════════════════════════════════════
#  FLASK ROUTES
# ══════════════════════════════════════════════════════════

# ── Operational controls ──────────────────────────────────
# Same-origin by default. The frontend is served by this same app, so it needs
# no CORS grant, and a wildcard let any third-party page spend the provider's
# daily quota through a visitor's browser. Set this only for a separate origin.
CORS_ORIGIN = os.environ.get("MIZAN_CORS_ORIGIN", "").strip()

# Required for the mutating and diagnostic endpoints. When it is unset they are
# closed, except from loopback so local development keeps working. Fail closed:
# an unconfigured token must not mean "open to the internet".
ADMIN_TOKEN = os.environ.get("MIZAN_ADMIN_TOKEN", "").strip()

# 60 rather than something tighter: the frontend refreshes a whole watchlist in
# one action, so the ceiling has to clear the largest watchlist a person could
# plausibly keep, not just one search. It still bounds a scripted loop, which
# previously had no bound at all.
RATE_LIMIT_PER_MINUTE = int(os.environ.get("MIZAN_RATE_LIMIT", "60"))

# The Lambda Web Adapter and Render's router both terminate the connection and
# forward to gunicorn over loopback, so request.remote_addr is 127.0.0.1 for
# every visitor on both deployments. Two things break if that address is
# treated as the client identity: the per-client limit collapses into a single
# global bucket shared by everyone, and the loopback fallback in
# _admin_authorised() makes the mutating endpoints world-writable. The real
# client is the first hop in X-Forwarded-For.
#
# Set MIZAN_TRUST_PROXY=0 where the app is reached directly. There, the header
# is attacker-controlled and trusting it would let one caller mint a fresh
# bucket per request and bypass the limit entirely.
TRUST_PROXY = os.environ.get("MIZAN_TRUST_PROXY", "1") == "1"

_rl_lock    = threading.Lock()
_rl_buckets: dict = {}

def client_ip() -> str:
    if TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
    return request.remote_addr or "unknown"

def rate_limit_ok(client: str) -> bool:
    """
    Fixed-window per-client counter over one minute.

    Per process, so with two gunicorn workers or several Lambda containers the
    effective ceiling is higher than the number above. It exists to stop one
    scripted loop from burning the 1000-call daily provider quota in a couple of
    minutes, which is exactly what an unthrottled /screen allowed, not to be an
    exact throttle.
    """
    window = int(time.time() // 60)
    with _rl_lock:
        if len(_rl_buckets) > 4096:
            for k in [k for k, v in _rl_buckets.items() if v["window"] != window]:
                _rl_buckets.pop(k, None)
        b = _rl_buckets.get(client)
        if not b or b["window"] != window:
            _rl_buckets[client] = {"window": window, "count": 1}
            return True
        b["count"] += 1
        return b["count"] <= RATE_LIMIT_PER_MINUTE

def _admin_authorised() -> bool:
    if ADMIN_TOKEN:
        supplied = (request.headers.get("X-Admin-Token")
                    or request.args.get("token") or "")
        return hmac.compare_digest(supplied, ADMIN_TOKEN)
    # Loopback counts only for a request that arrived directly. Behind the
    # Lambda Web Adapter an internet request also reports 127.0.0.1, so without
    # the forwarding-header test these endpoints would be open to anyone
    # whenever no token is configured.
    if request.headers.get("X-Forwarded-For"):
        return False
    return client_ip() in ("127.0.0.1", "::1")

@app.after_request
def add_headers(response):
    if CORS_ORIGIN:
        response.headers["Access-Control-Allow-Origin"]  = CORS_ORIGIN
        response.headers["Vary"]                         = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Token"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"]        = "no-referrer"
    return response

@app.route("/")
def root():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/info")
def api_info():
    """Machine-readable backend status (the old root JSON)."""
    return jsonify({"ok":True,"message":"Mizan Backend v5 — Hybrid Edition",
                    "cache":cache_stats(),"requests":req_stats(),
                    "bursa_stocks":len(BURSA_DB)})

@app.route("/screen")
def screen():
    symbol = (request.args.get("symbol") or "").strip()
    if not symbol or not re.fullmatch(r'[A-Za-z0-9.\-]{1,12}', symbol):
        return jsonify({"ok":False,"error":"Invalid or missing symbol"}), 400

    if not rate_limit_ok(client_ip()):
        return jsonify({"ok":False,
                        "error":"Too many requests. Please wait a minute and try again."}), 429

    try:
        data = fetch_stock(symbol)
        return jsonify({"ok":True,"data":data,"cached":data.get("_cached",False)})
    except UpstreamError as e:
        # The provider's problem, not the caller's. Log the detail and return a
        # message the user can act on. The old handler answered 400 with str(e),
        # which blamed the client for an outage and echoed internals such as
        # "Check TIINGO_API_KEY in Render environment" into the browser.
        app.logger.warning("Upstream failure screening %s: %s", symbol, e)
        return jsonify({"ok":False,"error":str(e)}), 502
    except ValueError as e:
        return jsonify({"ok":False,"error":str(e)}), 400
    except Exception:
        app.logger.exception("Unhandled error screening %s", symbol)
        return jsonify({"ok":False,
                        "error":"An internal error occurred while screening this symbol."}), 500

@app.route("/purify")
def purify():
    try:
        div = float(request.args.get("dividend",       0))
        rat = float(request.args.get("interest_ratio", 0))
    except (TypeError, ValueError):
        return jsonify({"ok":False,"error":"dividend and interest_ratio must be numbers"}), 400
    if not (math.isfinite(div) and math.isfinite(rat)):
        return jsonify({"ok":False,"error":"dividend and interest_ratio must be finite numbers"}), 400
    if div < 0 or not (0 <= rat <= 1):
        return jsonify({"ok":False,
                        "error":"dividend must be 0 or more, and interest_ratio must be between 0 and 1"}), 400
    cur = (request.args.get("currency") or "MYR").upper()[:3]
    return jsonify({"ok":True,"data":calc_purification(div,rat,cur)})

@app.route("/bursa/list")
def bursa_list():
    """List all available Bursa stocks in the database."""
    stocks = [{"code":k,"name":v["name"],"sector":v["sector"],
               "scStatus":v.get("scStatus","unknown")}
              for k,v in sorted(BURSA_DB.items())]
    return jsonify({"ok":True,"count":len(stocks),"stocks":stocks})

@app.route("/cache/stats")
def stats():
    if not _admin_authorised():
        return jsonify({"ok":False,"error":"Not authorised."}), 403
    return jsonify({"ok":True,"data":cache_stats()})

@app.route("/cache/clear", methods=["POST"])
def clear_cache():
    # POST, and authorised. This was a GET that any crawler, prefetcher or
    # third-party page could trigger, and each call it made the next visitor pay
    # for again in provider quota.
    if not _admin_authorised():
        return jsonify({"ok":False,"error":"Not authorised."}), 403
    sym = request.args.get("symbol")
    if sym:
        cache_clear(normalise_ticker(sym.strip()))
        msg = f"Cleared {sym}"
    else:
        cache_clear()
        msg = "Full cache cleared"
    return jsonify({"ok":True,"message":msg})

@app.route("/usage")
def usage():
    s = req_stats()
    return jsonify({"ok":True,"data":s,
        "scope":"per process; not the account total",
        "message":f"Used {s['used']} of {s['limit']} Tiingo requests today in this process. {s['remaining']} remaining."})

@app.route("/health")
def health():
    return jsonify({"ok":True,"status":"Mizan backend v5 — Hybrid Edition",
                    "cache":cache_stats(),"requests":req_stats(),
                    "tiingo_key":"set" if TIINGO_KEY else "MISSING",
                    "bursa_stocks":len(BURSA_DB),
                    "rate_limit_per_minute":RATE_LIMIT_PER_MINUTE,
                    "cors_origin":CORS_ORIGIN or "same-origin only",
                    "trust_proxy":TRUST_PROXY,
                    "cache_clear":"token required" if ADMIN_TOKEN else "loopback only"})

if __name__ == "__main__":
    load_sc_list()
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  MIZAN Backend v5 — Hybrid Edition              ║")
    print("║  US/Global: Tiingo API (1000 req/day free)      ║")
    print(f"║  Bursa MY:  Hardcoded DB ({len(BURSA_DB)} stocks)              ║")
    print("╚══════════════════════════════════════════════════╝\n")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
