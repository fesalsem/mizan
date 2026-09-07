# Mizan — ميزان — Halal Investment Screener

> Search any Bursa Malaysia or US stock and get an instant Shariah-compliance verdict — halal, doubtful, or not halal — with the financial reasoning behind it.

## 🚀 Try it now

**https://mizan-eft5.onrender.com**

No install, no setup — just open the link and search. Enter a 4-digit Bursa Malaysia code (e.g. `1295`, `1155`) or a US ticker (e.g. `TSLA`, `AAPL`).

---

## How to use

1. **Open** the app: https://mizan-eft5.onrender.com
2. **Type** a stock code or ticker into the search box
3. **Read** the verdict and the numbers behind it

That's it.

---

## What it gives you

- **Shariah verdict** — ✅ Potentially Halal · ◐ Doubtful · ✗ Not Halal
- **Live stock data** — price, daily change, 52-week high/low, volume, market cap
- **Financial screening** — Debt-to-Assets ratio and non-permissible income %, checked against AAOIFI and DJIM standards
- **6-month price chart** — price history at a glance
- **Buy / Hold / Avoid recommendation** — based on fundamentals and risk
- **Watchlist** — save and track stocks you care about
- **Broker guide** — a comparison of licensed brokers for placing actual trades

---

## Shariah Screening Criteria

| Criterion | Standard | Threshold |
|-----------|----------|-----------|
| Business activity | AAOIFI / DJIM | Categorical ban: alcohol, gambling, riba banking, tobacco, weapons, pork |
| Debt-to-Assets ratio | AAOIFI SS-21 | < 33% of total assets |
| Non-permissible income | DJIM | < 5% of total revenue |
| Gharar check | Fiqh principle | Loss-making companies flagged |

---

## Supported Markets

| Market | Format | Example |
|--------|--------|---------|
| Bursa Malaysia | 4-digit code | `1295`, `1155`, `5347` |
| US (NYSE / NASDAQ) | Ticker | `TSLA`, `NVDA`, `AAPL` |
| London Stock Exchange | Ticker + `.L` | `HSBA.L` |
| Hong Kong | Code + `.HK` | `9988.HK` |
| Japan | Ticker + `.T` | `7203.T` (Toyota) |

> **Data sources:** US/global prices come from the Tiingo API. Bursa Malaysia prices, volume and charts come live from Yahoo Finance (~15 min delayed — Bursa has no free real-time feed). Bursa financials (debt ratio, P/E, ROE, revenue) come from FY2023/2024 annual reports and drive the Shariah screening.

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

## 🛠️ For developers

Want to run or modify it locally?

### 1. Set your Tiingo API key
The backend uses a free [Tiingo](https://api.tiingo.com) API key for US/global stock data.

```bash
# Linux / macOS
export TIINGO_API_KEY="your_key_here"

# Windows (PowerShell)
$env:TIINGO_API_KEY="your_key_here"
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Start the backend
```bash
python server.py
```

### 4. Open the app
Visit **http://localhost:5000** — the backend serves the frontend directly, so there's no separate build step.

### Architecture

```
index.html  ←→  server.py  ←→  Tiingo API (US) + Yahoo Finance (Bursa) + Bursa DB
(Browser UI)    (Python backend)  (Live prices + annual-report financials)
```

The Python backend handles all data fetching and Shariah screening logic. The frontend is a single HTML file that calls the backend over same-origin REST endpoints (`/screen`, `/purify`, `/health`) — no frameworks, no build step.

**Tech Stack:** Python · JavaScript · REST API · Tiingo API (US/global) · Yahoo Finance (Bursa live prices) · Bursa Malaysia database (financials)

---

MIT License · *بارك الله فيك — May Allah bless you.*
