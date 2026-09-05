# Mizan — ميزان — Halal Investment Screener

> A full-stack web application that screens Bursa Malaysia and US stocks for Shariah compliance in real time, using a Python backend and a browser-based frontend.

**Tech Stack:** Python · JavaScript · REST API · Tiingo API (US/global data) · Bursa Malaysia database

**Live:** https://mizan-eft5.onrender.com

---

## Project Description

Developed a full-stack web application that screens Bursa Malaysia and US stocks for Shariah compliance in real time, using a Python backend and a browser-based frontend. Implemented automated financial data retrieval via the Tiingo API (US/global) and a hardcoded Bursa Malaysia database to compute key Islamic finance metrics including Debt-to-Assets ratio and non-permissible income percentage, following AAOIFI and DJIM standards. Designed an interactive dashboard with live stock screening, watchlist tracking, 6-month price chart, and buy/hold/avoid recommendations to support data-driven halal investment decisions.

---

## Architecture

```
index.html  ←→  server.py  ←→  Tiingo API + Bursa DB
(Browser UI)    (Python backend)  (Live data)
```

The Python backend handles all data fetching and Shariah screening logic. The frontend is a single HTML file that calls the backend and renders results — no frameworks, no build step. The backend serves the frontend directly at `/`, so a single URL hosts the whole app.

---

## Quick Start

### 1. Set your Tiingo API key
The backend needs a free [Tiingo](https://api.tiingo.com) API key for US/global stock data.

```bash
# Linux / macOS
export TIINGO_API_KEY="your_key_here"

# Windows (PowerShell)
$env:TIINGO_API_KEY="your_key_here"
```

### 2. Install dependencies (one time only)
```bash
pip install -r requirements.txt
```

### 3. Start the backend
```bash
python server.py
```

### 4. Open the app
Visit **http://localhost:5000** in your browser. The backend serves the frontend directly, so there's no need to open `index.html` separately. Keep the terminal running in the background.

---

## Features

- **Live stock data** — Real-time price, daily change, 52-week high/low, volume, market cap
- **Real financial statements** — Balance sheet and income statement pulled directly from Yahoo Finance
- **Shariah screening** — 4-criteria compliance check with clear verdicts
- **Multi-market support** — Bursa Malaysia (4-digit codes), US stocks (TSLA, NVDA, AAPL), and global exchanges
- **Watchlist** — Save and track multiple stocks with persistent storage
- **6-month price chart** — Canvas-based price history visualisation
- **Buy / Hold / Avoid recommendations** — Based on fundamentals and risk level
- **Broker guide** — Comparison of licensed brokers for placing actual trades

---

## Shariah Screening Criteria

| Criterion | Standard | Threshold |
|-----------|----------|-----------|
| Business activity | AAOIFI / DJIM | Categorical ban: alcohol, gambling, riba banking, tobacco, weapons, pork |
| Debt-to-Assets ratio | AAOIFI SS-21 | < 33% of total assets |
| Non-permissible income | DJIM | < 5% of total revenue |
| Gharar check | Fiqh principle | Loss-making companies flagged |

**Verdicts:** ✅ Potentially Halal · ◐ Doubtful · ✗ Not Halal

---

## Supported Markets

| Market | Format | Example |
|--------|--------|---------|
| Bursa Malaysia | 4-digit code | `1295`, `1155`, `5347` |
| US (NYSE / NASDAQ) | Ticker | `TSLA`, `NVDA`, `AAPL` |
| London Stock Exchange | Ticker + `.L` | `HSBA.L` |
| Hong Kong | Code + `.HK` | `9988.HK` |
| Other Yahoo Finance markets | Ticker + suffix | `7203.T` (Toyota) |

---

## Brokers (to place actual trades)

This app is a research tool. To buy stocks, use a licensed broker:

| Broker | Market | Min Deposit |
|--------|--------|-------------|
| [Rakuten Trade](https://www.rakutentrade.my) | Bursa Malaysia | MYR 0 |
| [Mplus Online](https://www.mplusonline.com.my) | Bursa Malaysia | MYR 1,000 |
| [Kenanga iTrade](https://www.kenanga.com.my) | Bursa Malaysia | MYR 1,000 |
| [myETF](https://www.myetf.com.my) | Bursa (ETFs only) | MYR 100 |
| [Interactive Brokers](https://www.interactivebrokers.com) | US + Global | USD 0 |
| [Webull](https://www.webull.com) | US Stocks | USD 0 |

---

## Disclaimer

This software is for **educational and informational purposes only**. It is **not financial advice**. Shariah compliance is a scholarly matter — always verify against the [SC Malaysia official Shariah-compliant securities list](https://www.sc.com.my/development/islamic-capital-market/shariah-compliant-securities) and consult a qualified Islamic finance scholar before investing.

---

MIT License · *بارك الله فيك — May Allah bless you.*
