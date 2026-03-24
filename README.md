# Mizan — ميزان — Halal Investment Screener
### Bursa Malaysia Edition

A real-time, Shariah-compliant stock screener for Bursa Malaysia.
Live data is fetched directly from Yahoo Finance — no fake/mocked data.

---

## ⚡ Quick Setup (one time only)

### Step 1 — Make sure Python is installed
Open your terminal / command prompt and run:
```
python --version
```
You need Python 3.8 or newer. Download from https://python.org if needed.

### Step 2 — Install the required libraries
In the same folder as this README, run:
```
pip install -r requirements.txt
```

### Step 3 — Run the app
```
python mizan.py
```

That's it. The app will start immediately.

---

## 📱 How to use it

### Screening a stock
- Press **[1]** from the main menu
- Enter the Bursa Malaysia stock code, e.g.:
  - `1295` → Public Bank
  - `1155` → Maybank
  - `4197` → IHH Healthcare
  - `5347` → Tenaga Nasional
  - `MAYBANK` → also works (name-style)
  - `5347.KL` → also works (full Yahoo Finance format)

### What you'll see
- **Live price** with daily change
- **52-week high/low**
- **Key financials**: P/E, P/B, Profit Margin, ROE, Dividend Yield
- **6-month price sparkline chart**
- **Shariah screening** across 4 criteria
- **Verdict**: Potentially Halal / Doubtful / Not Halal
- **Risk level**: Low / Medium / High
- **Recommendation**: Buy / Hold / Caution / Avoid

### Watchlist
- After screening, you can **save stocks** to your watchlist
- Press **[2]** to view your saved stocks
- Press **[3]** to refresh all watchlist stocks with live data at once
- Press **[4]** to remove a stock

Your watchlist is saved in `watchlist.json` in the same folder — it persists between sessions.

---

## 📐 Shariah Screening Criteria

| Criterion | Standard | Threshold |
|-----------|----------|-----------|
| Business activity (industry) | AAOIFI / DJIM | Categorical ban on alcohol, gambling, riba banking, tobacco, weapons, pork, adult content |
| Debt-to-assets ratio | AAOIFI SS-21 | < 33% |
| Non-permissible income ratio | DJIM | < 5% of total revenue |
| Profitability / gharar check | General fiqh principle | Loss-making flagged as speculative |

**Verdict levels:**
- ✅ **Potentially Halal** — passes all criteria
- ◐ **Doubtful** — borderline; seek scholarly opinion
- ✗ **Not Halal** — fails one or more categorical criteria

---

## 🔗 Where to place your actual trades

This tool helps you *research and decide*. To actually buy stocks, use:

| Platform | Market | Notes |
|----------|--------|-------|
| [Rakuten Trade](https://www.rakutentrade.my) | Bursa Malaysia | Online, low fees |
| [Mplus Online](https://www.mplusonline.com.my) | Bursa Malaysia | Local broker |
| [Kenanga iTrade](https://www.kenangainvestors.com.my) | Bursa Malaysia | Established broker |
| [i-VCAP (myETF)](https://www.myetf.com.my) | Bursa | Shariah-compliant ETFs |

---

## ⚠️ Disclaimer

This software is for **educational and informational purposes only**.

- It is **NOT financial advice**.
- Shariah compliance determinations are a scholarly matter — **always consult a qualified Islamic finance scholar** (e.g., from ISRA, SAC-SC, or your bank's Shariah committee) before investing.
- Financial data is sourced from Yahoo Finance and may have delays or inaccuracies.
- Past financial ratios do not guarantee future halal compliance.
- The authors bear no responsibility for investment decisions made using this tool.

---

## 📚 Further Learning Resources

- **AAOIFI Standards**: https://aaoifi.com
- **Securities Commission Malaysia Shariah Advisory Council**: https://www.sc.com.my
- **Bursa Malaysia Shariah-Compliant Securities List**: https://www.bursamalaysia.com
- **ISRA International Islamic Finance Research Academy**: https://ifikr.isra.my
- **Zoya App** (cross-check): https://zoya.finance
- **Islamicly App** (cross-check): https://islamicly.com

---

*بارك الله فيك — May Allah bless you in your endeavours.*
