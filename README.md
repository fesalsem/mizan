# Mizan — ميزان — Halal Investment Screener

**A real-time, Shariah-compliant stock screener for Bursa Malaysia.**

[![Live Demo](https://img.shields.io/badge/Live%20Demo-GitHub%20Pages-gold?style=flat-square)](https://YOUR-USERNAME.github.io/mizan)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Halal](https://img.shields.io/badge/Approach-Halal%20Investing-brightgreen?style=flat-square)]()

> *ميزان* means "The Balance" in Arabic — reflecting the balance between financial returns and Shariah compliance.

---

## ✨ Features

- 🔍 **Real-time stock screening** — Live data from Yahoo Finance for all Bursa Malaysia stocks
- ☪️ **4-layer Shariah screening** — Industry check, Debt-to-Assets (AAOIFI ≤33%), Non-permissible income (DJIM ≤5%), Gharar check
- 📊 **Full financial dashboard** — Price, P/E, P/B, profit margin, ROE, dividend yield, 6-month chart
- 📋 **Persistent watchlist** — Save and track multiple stocks, refresh all with live prices
- 🏦 **Broker guide** — Full comparison of SC Malaysia licensed brokers (Rakuten Trade, Mplus, Kenanga, myETF)
- 📖 **Investing guide** — 8-step halal investing guide for beginners
- 💻 **Zero installation** — Single HTML file, works offline after first load

---

## 🚀 Quick Start

### Option A — Use it instantly (no setup)
1. Download [`index.html`](index.html)
2. Double-click it — opens in your browser
3. Type a stock code and screen it

### Option B — Run it from GitHub Pages (free hosting)
1. Fork this repo
2. Go to **Settings → Pages → Source: main branch / root**
3. Your app is live at `https://YOUR-USERNAME.github.io/mizan`


## 📐 Shariah Screening Standards

| Criterion | Standard | Threshold |
|-----------|----------|-----------|
| Business activity | AAOIFI / DJIM | Categorical ban: alcohol, gambling, riba banking, tobacco, weapons, pork, adult content |
| Debt-to-Assets ratio | AAOIFI SS-21 | < 33% |
| Non-permissible income | DJIM | < 5% of total revenue |
| Gharar (speculation) | General fiqh | Loss-making companies flagged |

### Verdict System
| Verdict | Meaning |
|---------|---------|
| ✅ Potentially Halal | Passes all 4 criteria |
| ◐ Doubtful | Borderline on 1+ criteria — seek scholar |
| ✗ Not Halal | Fails categorical criteria |

---

## 🇲🇾 Supported Market

**Bursa Malaysia** — Enter stocks by:
- 4-digit code: `1295` (Public Bank), `1155` (Maybank), `5347` (Tenaga)
- Name: `MAYBANK`, `TENAGA`, `IHH`
- Full ticker: `1295.KL`

---

## 🏦 Supported Brokers (for placing trades)

This tool helps you **research**. To actually buy stocks, use one of these SC Malaysia licensed brokers:

| Broker | Min Deposit | Shariah Support | Best For |
|--------|-------------|-----------------|----------|
| [Rakuten Trade](https://www.rakutentrade.my) | MYR 0 | Shariah list available | Beginners |
| [Mplus Online](https://www.mplusonline.com.my) | MYR 1,000 | Built-in Shariah filter | Research-focused |
| [Kenanga iTrade](https://www.kenanga.com.my) | MYR 1,000 | Islamic account option | Full-service |
| [myETF](https://www.myetf.com.my) | MYR 100 | 100% Shariah ETFs | Passive investors |

---

## ⚠️ Disclaimer

This software is for **educational and informational purposes only**.

- ❌ This is **NOT financial advice**
- ❌ This does **NOT guarantee halal compliance** — Shariah determinations are scholarly matters
- ✅ Always verify against the [SC Malaysia official Shariah-compliant securities list](https://www.sc.com.my/development/islamic-capital-market/shariah-compliant-securities)
- ✅ Consult a qualified Islamic finance scholar before investing
- ✅ Data is sourced from Yahoo Finance and may have delays

---

## 📚 References & Standards

- [AAOIFI Shariah Standards](https://aaoifi.com)
- [SC Malaysia Shariah Advisory Council](https://www.sc.com.my)
- [Dow Jones Islamic Market Index Methodology](https://www.spglobal.com/spdji/en/indices/equity/dow-jones-islamic-market-world-index/)
- [ISRA — International Shariah Research Academy](https://ifikr.isra.my)
- [Bursa Malaysia Shariah-Compliant Securities](https://www.bursamalaysia.com)

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

*بارك الله فيك — May Allah bless you in your endeavours.*
