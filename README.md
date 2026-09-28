# Mizan — ميزان — Halal Investment Screener

> Search any Bursa Malaysia or US stock and get an instant Shariah-compliance verdict — halal, doubtful, or not halal — with the financial reasoning behind it.

## 🚀 Try it now

**AWS:** https://quai45qkwxlb5n6bodajvt3snm0tmgfe.lambda-url.ap-southeast-1.on.aws

**Render:** https://mizan-eft5.onrender.com

No install, no setup — just open either link and search. Enter a 4-digit Bursa Malaysia code (e.g. `1295`, `1155`) or a US ticker (e.g. `TSLA`, `AAPL`).

Both run the same container image. The AWS link is a Lambda function behind a public Function URL, so the first request after an idle period may take a second or two while the container starts.

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
| SC Malaysia official list | Securities Commission Malaysia | Company must appear on the published Shariah-compliant securities list |
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

Screening figures are held for 31 Bursa Malaysia companies and 19 US companies. Any other US ticker the Tiingo free tier carries is fetched live, though without the annual-report figures that drive the verdict.

Other exchanges are **not** supported. London (`HSBA.L`), Hong Kong (`9988.HK`) and Japan (`7203.T`) each return a "ticker not found" error, because the only upstream source queried for non-Bursa symbols is Tiingo, which does not carry them.

> **Data sources:** US prices come from the Tiingo API. Bursa Malaysia prices, volume and charts come live from Yahoo Finance (~15 min delayed — Bursa has no free real-time feed). Bursa financials (debt ratio, P/E, ROE, revenue) come from FY2023/2024 annual reports and drive the Shariah screening.

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

### Or run with Docker

No Python setup needed, and it pins the exact interpreter version.

```bash
# put TIINGO_API_KEY in a .env file beside docker-compose.yml
docker compose up --build
```

Visit **http://localhost:8000** (8000, not 5000, so it does not clash with a `python server.py` already running).

Stop with `Ctrl+C`. `docker compose down` removes the container.

Notes on the image:

- Multi-stage is not needed here. The dependency set is three packages, so the image stays small.
- The container runs as a non-root user and the app writes nothing to disk, so there is no volume to mount.
- The build fails fast on a missing key by design: `server.py` calls `check_setup()` at import time and exits if `TIINGO_API_KEY` is unset. Compose also checks for it before starting.
- The health check polls `/health`, which returns 200 unconditionally. It deliberately does not use `/screen`, since that endpoint requires a `symbol` parameter and answers 400 without one.

### Architecture

```
index.html  ←→  Flask + Gunicorn  ←→  Tiingo API (US prices + fundamentals)
(Browser UI)    (screening engine)     Yahoo Finance (Bursa live prices)
                                       Annual report figures (31 Bursa, 19 US)
```

The Python backend handles all data fetching and Shariah screening logic. The frontend is a single HTML file that calls the backend over same-origin REST endpoints (`/screen`, `/purify`, `/health`) — no frameworks, no build step.

**Tech Stack:** Python · JavaScript · REST API · Tiingo API (US) · Yahoo Finance (Bursa live prices) · annual report database (financials) · Docker · AWS Lambda · Amazon ECR · CloudWatch

### Deployment

The same application runs on two hosts.

| Host | How it is built | Notes |
|------|-----------------|-------|
| AWS Lambda | container image pushed to Amazon ECR, behind a public Function URL | no server to manage; scales to zero when idle |
| Render | Docker deploy from the repository | always warm, so no cold start |

`Dockerfile.lambda` is the AWS build. Three differences from the local `Dockerfile`, each commented in the file: it starts from the AWS managed Python base image, it copies the Lambda Web Adapter in as an extension, and it overrides the base image `ENTRYPOINT` so Gunicorn starts directly.

Measured on the deployed function: warm requests return in 55 to 94 ms, cold starts take 1.75 to 1.79 seconds.

To redeploy after a code change:

```bash
cd deploy/aws
./deploy.sh          # builds, pushes to ECR, updates the function
```

Full runbook, including the two IAM permissions a public Function URL needs and the image media-type pitfall that blocks a first deploy: **[docs/aws-deployment.md](docs/aws-deployment.md)**.

---

MIT License · *بارك الله فيك — May Allah bless you.*
