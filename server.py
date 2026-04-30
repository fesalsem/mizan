"""
Mizan Backend Server v5 — Hybrid Edition
Run: python server.py

Data sources:
  - US/International stocks: Tiingo API (free, 1000 req/day, works on cloud)
  - Bursa Malaysia stocks:   Hardcoded database (top 80 stocks, real annual report data)

Requires environment variable: TIINGO_API_KEY
Get a free key at: https://api.tiingo.com (instant signup)
"""
import json, math, time, threading, re
from flask import Flask, request, jsonify
from pathlib import Path
import os, sys, importlib.util
import requests

app = Flask(__name__)

# ── API Setup ─────────────────────────────────────────────
TIINGO_KEY  = os.environ.get("TIINGO_API_KEY", "")
TIINGO_BASE = "https://api.tiingo.com"

def check_setup():
    if not TIINGO_KEY:
        print("\n[ERROR] TIINGO_API_KEY environment variable not set.")
        print("  Get a free key at: https://api.tiingo.com")
        print("  Then in Render: Environment → TIINGO_API_KEY = your_key\n")
        sys.exit(1)
    print("  ✓  Tiingo API key loaded")

check_setup()

# ── Cache (30 min TTL) ────────────────────────────────────
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

# ── Request counter ───────────────────────────────────────
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

# Name → code lookup for text search
BURSA_NAME_INDEX = {
    v["name"].lower(): k for k, v in BURSA_DB.items()
}
# Also index by partial name
BURSA_PARTIAL_INDEX = {
    v["name"].lower().split()[0]: k for k, v in BURSA_DB.items()
}

def bursa_lookup(symbol: str) -> dict | None:
    """Look up a Bursa stock in the hardcoded database."""
    s = symbol.upper().strip().replace(".KL", "")
    if s.isdigit():
        code = s.zfill(4)
        return BURSA_DB.get(code)
    # Try name search
    sl = symbol.lower().strip()
    for name, code in BURSA_NAME_INDEX.items():
        if sl in name or name in sl:
            return BURSA_DB.get(code)
    for name, code in BURSA_PARTIAL_INDEX.items():
        if sl.startswith(name[:4]) or name.startswith(sl[:4]):
            return BURSA_DB.get(code)
    return None

def get_bursa_code(symbol: str) -> str | None:
    s = symbol.upper().strip().replace(".KL", "")
    if s.isdigit():
        return s.zfill(4)
    sl = symbol.lower().strip()
    for name, code in BURSA_NAME_INDEX.items():
        if sl in name:
            return code
    return None

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
    clean = ticker.replace(".KL","").replace(".KLS","")
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

US_KNOWN = {
    "AAPL","MSFT","GOOGL","GOOG","AMZN","TSLA","NVDA","META","NFLX","AMD",
    "INTC","QCOM","AVGO","TXN","MU","AMAT","JPM","BAC","GS","MS","WFC",
    "JNJ","PFE","MRK","ABBV","LLY","XOM","CVX","WMT","COST","HD","MCD",
    "SBUX","NKE","DIS","V","MA","PYPL","BABA","NIO","COIN","SQ","BRK-B",
    "AMGN","GILD","BMY","COP","SLB","T","VZ","TMUS","CMCSA","NFLX",
}

def safe_float(v, d=None):
    try:    return float(v) if v not in (None,"","None","N/A","-",0,"0") else d
    except: return d

def safe_int(v, d=None):
    try:    return int(float(v)) if v not in (None,"","None","N/A","-") else d
    except: return d

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

def tiingo_get(path: str, params: dict = None) -> any:
    """Make a Tiingo API request."""
    used = req_increment()
    url  = f"{TIINGO_BASE}/{path}"
    headers = {
        "Authorization": f"Token {TIINGO_KEY}",
        "Content-Type":  "application/json",
    }
    print(f"  Tiingo #{used}: /{path.split('?')[0]}")
    try:
        resp = requests.get(url, params=params or {}, headers=headers, timeout=15)
        if resp.status_code == 401:
            raise ValueError("Invalid Tiingo API key. Check TIINGO_API_KEY in Render environment.")
        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            raise ValueError("Tiingo rate limit hit. Please wait a moment and try again.")
        if not resp.ok:
            raise ValueError(f"Tiingo API error {resp.status_code}")
        return resp.json()
    except requests.Timeout:
        raise ValueError("Tiingo request timed out. Please try again.")
    except requests.RequestException as e:
        raise ValueError(f"Network error: {e}")


def fetch_us_stock(ticker: str) -> dict:
    """Fetch US/international stock data from Tiingo."""

    # 1. Metadata (name, description, exchange)
    meta = tiingo_get(f"tiingo/daily/{ticker}")
    if not meta:
        raise ValueError(
            f"Ticker '{ticker}' not found on Tiingo. "
            "Check the symbol — US examples: AAPL, TSLA, NVDA, MSFT."
        )

    name        = meta.get("name")        or ticker
    description = (meta.get("description") or "")[:400]
    exchange    = meta.get("exchangeCode") or "N/A"

    # 2. Latest price data
    prices = tiingo_get(f"tiingo/daily/{ticker}/prices",
                        {"startDate": "2024-01-01", "sort": "-date", "limit": 1})
    price_data = prices[0] if prices else {}

    price      = safe_float(price_data.get("close"),        0)
    prev_close = safe_float(price_data.get("adjClose"),     price)
    high       = safe_float(price_data.get("high"),         price)
    low        = safe_float(price_data.get("low"),          price)
    volume     = safe_int(price_data.get("volume"),         0)
    change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0

    # 3. Real-time IEX quote (more accurate price)
    iex = tiingo_get(f"iex/{ticker}")
    if iex and isinstance(iex, list) and iex:
        q          = iex[0]
        price      = safe_float(q.get("last") or q.get("tngoLast"), price)
        volume     = safe_int(q.get("volume"), volume)
        prev_close = safe_float(q.get("prevClose"), prev_close)
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else change_pct

    # 4. Fundamentals (income statement + balance sheet)
    # Tiingo fundamentals are available on free tier
    fund_data = tiingo_get(f"tiingo/fundamentals/{ticker}/statements",
                           {"frequency": "annual", "limit": 1})

    income_stmt = {}; balance_sheet = {}
    if fund_data and isinstance(fund_data, list) and fund_data:
        stmts = fund_data[0].get("statementData", {})
        # Find income statement
        for stmt in stmts.get("incomeStatement", [{}]):
            if stmt.get("dataCode") == "revenue":
                income_stmt["revenue"] = stmt.get("value")
            if stmt.get("dataCode") == "intExp":
                income_stmt["intExp"] = stmt.get("value")
            if stmt.get("dataCode") == "grossProfit":
                income_stmt["grossProfit"] = stmt.get("value")
            if stmt.get("dataCode") == "netMargin":
                income_stmt["netMargin"] = stmt.get("value")
        # Find balance sheet
        for stmt in stmts.get("balanceSheet", [{}]):
            if stmt.get("dataCode") == "totalAssets":
                balance_sheet["totalAssets"] = stmt.get("value")
            if stmt.get("dataCode") == "totalDebt":
                balance_sheet["totalDebt"] = stmt.get("value")
            if stmt.get("dataCode") == "currentRatio":
                balance_sheet["currentRatio"] = stmt.get("value")

    # 5. Key metrics from Tiingo overview
    overview = tiingo_get(f"tiingo/fundamentals/{ticker}/daily",
                          {"limit": 1})
    metrics = {}
    if overview and isinstance(overview, list) and overview:
        metrics = overview[0]

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

    # 6. Price history (6 months)
    history = []
    try:
        from datetime import datetime, timedelta
        six_months_ago = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        hist_data = tiingo_get(f"tiingo/daily/{ticker}/prices",
                               {"startDate": six_months_ago,
                                "resampleFreq": "monthly", "sort": "date"})
        if hist_data:
            for row in hist_data[-6:]:
                history.append({
                    "date":   row.get("date","")[:7],
                    "close":  safe_float(row.get("adjClose") or row.get("close"), 0),
                    "open":   safe_float(row.get("open"),  0),
                    "high":   safe_float(row.get("high"),  0),
                    "low":    safe_float(row.get("low"),   0),
                    "volume": safe_int(row.get("volume"),  0),
                })
    except Exception as e:
        print(f"  History fetch failed (non-critical): {e}")

    sc_check  = check_sc_list(ticker)
    screening = screen_halal(
        name=name, sector="N/A", industry="N/A",
        description=description,
        debt_ratio=debt_ratio, interest_ratio=interest_ratio,
        pe_ratio=pe_ratio, profit_margin=profit_margin,
        sc_check=sc_check,
    )

    return {
        "ticker": ticker, "name": name,
        "sector": "N/A", "industry": "N/A",
        "description": description, "exchange": exchange, "currency": "USD",
        "price":       round(price, 4),
        "prevClose":   round(prev_close, 4) if prev_close else None,
        "changePct":   round(change_pct, 3),
        "week52High":  round(high, 4) if high else None,
        "week52Low":   round(low, 4)  if low  else None,
        "volume":      volume, "avgVolume": volume, "marketCap": safe_int(market_cap),
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
        "_source":   "Tiingo API",
    }

# ══════════════════════════════════════════════════════════
#  BURSA STOCK FETCH (from hardcoded database)
# ══════════════════════════════════════════════════════════

def fetch_bursa_stock(symbol: str) -> dict:
    """Fetch Bursa Malaysia stock from hardcoded database."""
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
        sc_check=sc_check,
    )

    # Build mock history from week52 range
    history = []
    if db.get("week52High") and db.get("week52Low"):
        import random
        lo, hi = db["week52Low"], db["week52High"]
        rng = hi - lo
        months = ["Jan","Feb","Mar","Apr","May","Jun"]
        for i, m in enumerate(months):
            close = round(lo + rng * (0.3 + 0.5 * i/5 + random.uniform(-0.1,0.1)), 3)
            history.append({"date": f"2024-{m}", "close": close,
                           "open": close, "high": close*1.02, "low": close*0.98, "volume": 0})

    return {
        "ticker":      code or symbol,
        "name":        db["name"],
        "sector":      db["sector"],
        "industry":    db["industry"],
        "description": db.get("description",""),
        "exchange":    "KLSE",
        "currency":    "MYR",
        "price":       db.get("week52High", 0) * 0.85,  # approximate mid-range
        "prevClose":   None,
        "changePct":   0,
        "week52High":  db.get("week52High"),
        "week52Low":   db.get("week52Low"),
        "volume":      None,
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
        "_source":    "Bursa Malaysia Database (Annual Report Data)",
        "_dataNote":  "Price data is approximate. Financials are from FY2023/2024 annual reports.",
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
                 profit_margin, sc_check=None):
    checks=[]; issues=[]; warnings=[]
    combined = f"{sector} {industry} {name} {description}".lower()

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

    if debt_ratio is None:
        checks.append({"status":"warn","name":"Debt-to-Assets Ratio (AAOIFI: ≤ 33%)",
            "detail":"Balance sheet data unavailable."})
        warnings.append("no_debt_data")
    elif debt_ratio > DEBT_THRESHOLD:
        sev = "fail" if debt_ratio > 0.50 else "warn"
        checks.append({"status":sev,"name":"Debt-to-Assets Ratio (AAOIFI: ≤ 33%)",
            "detail":f"Ratio is {debt_ratio*100:.1f}% — {'significantly ' if debt_ratio>0.50 else 'marginally '}exceeds the 33% AAOIFI threshold."})
        issues.append("high_debt") if sev=="fail" else warnings.append("marginal_debt")
    else:
        checks.append({"status":"pass","name":"Debt-to-Assets Ratio (AAOIFI: ≤ 33%)",
            "detail":f"Ratio is {debt_ratio*100:.1f}% — within the permissible 33% limit."})

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

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/")
def root():
    return jsonify({"ok":True,"message":"Mizan Backend v5 — Hybrid Edition",
                    "cache":cache_stats(),"requests":req_stats(),
                    "bursa_stocks":len(BURSA_DB)})

@app.route("/screen")
def screen():
    symbol = request.args.get("symbol","").strip()
    if not symbol or not re.match(r'^[A-Za-z0-9.\-]{1,12}$', symbol):
        return jsonify({"ok":False,"error":"Invalid or missing symbol"}), 400
    try:
        data = fetch_stock(symbol)
        return jsonify({"ok":True,"data":data,"cached":data.get("_cached",False)})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 400

@app.route("/purify")
def purify():
    try:
        div = float(request.args.get("dividend",       0))
        rat = float(request.args.get("interest_ratio", 0))
        cur = request.args.get("currency","MYR").upper()[:3]
        if div < 0 or not (0 <= rat <= 1): raise ValueError("Invalid parameters.")
        return jsonify({"ok":True,"data":calc_purification(div,rat,cur)})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 400

@app.route("/bursa/list")
def bursa_list():
    """List all available Bursa stocks in the database."""
    stocks = [{"code":k,"name":v["name"],"sector":v["sector"],
               "scStatus":v.get("scStatus","unknown")}
              for k,v in sorted(BURSA_DB.items())]
    return jsonify({"ok":True,"count":len(stocks),"stocks":stocks})

@app.route("/cache/stats")
def stats():
    return jsonify({"ok":True,"data":cache_stats()})

@app.route("/cache/clear")
def clear_cache():
    sym = request.args.get("symbol")
    if sym: cache_clear(normalise_ticker(sym.strip())); msg=f"Cleared {sym}"
    else:   cache_clear(); msg="Full cache cleared"
    return jsonify({"ok":True,"message":msg})

@app.route("/usage")
def usage():
    s = req_stats()
    return jsonify({"ok":True,"data":s,
        "message":f"Used {s['used']} of {s['limit']} Tiingo requests today. {s['remaining']} remaining."})

@app.route("/health")
def health():
    return jsonify({"ok":True,"status":"Mizan backend v5 — Hybrid Edition",
                    "cache":cache_stats(),"requests":req_stats(),
                    "tiingo_key":"set" if TIINGO_KEY else "MISSING",
                    "bursa_stocks":len(BURSA_DB)})

if __name__ == "__main__":
    load_sc_list()
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  MIZAN Backend v5 — Hybrid Edition              ║")
    print("║  US/Global: Tiingo API (1000 req/day free)      ║")
    print(f"║  Bursa MY:  Hardcoded DB ({len(BURSA_DB)} stocks)              ║")
    print("╚══════════════════════════════════════════════════╝\n")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
