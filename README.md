# Mizan — ميزان — Halal Investment Screener
### Bursa Malaysia Edition · Real-Time Data · Python Backend

Real-time Shariah-compliant stock screener. Uses a Python backend (yfinance) to fetch genuine financial data — real balance sheets, real debt ratios, real income statements.

## Architecture

```
index.html  ←→  server.py  ←→  Yahoo Finance
(Browser UI)    (Python backend)  (Live data)
```

## Quick Start

### 1. Install dependencies (one time only)
```bash
pip install -r requirements.txt
```

### 2. Start the backend
```bash
python server.py
```

### 3. Open the app
Double-click `index.html`. Keep the terminal running.

## What's Real

- Live price + daily change
- Balance sheet: total assets, total debt → **real Debt-to-Assets ratio**
- Income statement: revenue, interest expense → **real non-permissible income %**
- P/E, P/B, profit margin, ROE, ROA, dividend yield
- 6-month price chart

## Shariah Screening (4 Criteria)

| Criterion | Standard | Threshold |
|-----------|----------|-----------|
| Business activity | AAOIFI / DJIM | Categorical ban on haram industries |
| Debt-to-Assets | AAOIFI SS-21 | < 33% (real balance sheet) |
| Non-permissible income | DJIM | < 5% of revenue (real income statement) |
| Gharar check | Fiqh principle | Loss-making flagged |

## Brokers (to place actual trades)

| Broker | Min Deposit |
|--------|-------------|
| [Rakuten Trade](https://www.rakutentrade.my) | MYR 0 |
| [Mplus Online](https://www.mplusonline.com.my) | MYR 1,000 |
| [Kenanga iTrade](https://www.kenanga.com.my) | MYR 1,000 |
| [myETF](https://www.myetf.com.my) | MYR 100 |

## Disclaimer

Educational purposes only. NOT financial advice. Always verify against [SC Malaysia's official Shariah list](https://www.sc.com.my/development/islamic-capital-market/shariah-compliant-securities) and consult a qualified Islamic finance scholar.

MIT License · *بارك الله فيك*
