"""Refresh prices.json from public Yahoo Finance quotes (no key). Run by .github/workflows/prices.yml.
Reads symbols.json ({ticker: yahoo_symbol}); keeps the last good price when a quote fails."""
import json, time, urllib.request, datetime

symbols = json.load(open("symbols.json"))
import os
old = {}
base = os.environ.get("SITE_URL", "").rstrip("/")
if base:  # keep the last published prices for any quote that fails this time
    try:
        with urllib.request.urlopen(base + "/prices.json", timeout=15) as r:
            old = json.load(r).get("px", {})
    except Exception as e:
        print("no previous prices:", e)

def quote(sym):
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                meta = json.load(r)["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            if price is None:
                continue
            pct = round((price / prev - 1) * 100, 2) if prev else None
            return {"price": round(price, 4), "pct": pct, "time": meta.get("regularMarketTime")}
        except Exception as e:
            print("fail", sym, host, e)
    return None

px, ok = {}, 0
for t, sym in sorted(symbols.items()):
    q = quote(sym)
    if q:
        px[t] = q; ok += 1
    elif t in old:
        px[t] = old[t]
    time.sleep(0.3)

# market caps: Yahoo's quote endpoint needs a session cookie and a "crumb" (public, no account)
def market_caps(syms):
    import http.cookiejar, urllib.parse
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")]
    try:
        op.open("https://fc.yahoo.com", timeout=15)
    except Exception:
        pass  # this page answers 404 but still sets the cookie
    crumb = op.open("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=15).read().decode().strip()
    caps = {}
    for i in range(0, len(syms), 40):
        part = syms[i:i+40]
        url = ("https://query2.finance.yahoo.com/v7/finance/quote?symbols=" + urllib.parse.quote(",".join(part))
               + "&fields=marketCap,currency&crumb=" + urllib.parse.quote(crumb))
        res = json.load(op.open(url, timeout=20))["quoteResponse"]["result"]
        for q in res:
            if q.get("marketCap"):
                caps[q["symbol"]] = (q["marketCap"], q.get("currency"))
        time.sleep(0.5)
    return caps

try:
    caps = market_caps(sorted(set(symbols.values())))
    print(len(caps), "market caps from Yahoo")
except Exception as e:
    caps = {}
    print("Yahoo market caps failed:", e)

# fallback 1: Nasdaq's public quote summary (US listings)
def nasdaq_cap(t):
    req = urllib.request.Request(f"https://api.nasdaq.com/api/quote/{t}/summary?assetclass=stocks",
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                 "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    v = (((d.get("data") or {}).get("summaryData") or {}).get("MarketCap") or {}).get("value", "")
    v = "".join(ch for ch in str(v) if ch.isdigit())
    return int(v) if v else None

# fallback 2: Taiwan Stock Exchange open data (paid-in capital / NT$10 par = shares)
def twse_shares():
    req = urllib.request.Request("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = json.load(r)
    out = {}
    for row in rows:
        code = str(row.get("公司代號", "")).strip()
        cap = "".join(ch for ch in str(row.get("實收資本額", "")) if ch.isdigit())
        if code and cap:
            out[code] = int(cap) / 10
    return out

n_nas = 0
for t, sym in symbols.items():
    if sym in caps or t not in px:
        continue
    if "." not in sym:
        try:
            v = nasdaq_cap(t)
            if v:
                caps[sym] = (v, "USD"); n_nas += 1
        except Exception as e:
            print("nasdaq fail", t, e)
        time.sleep(0.3)
print(n_nas, "market caps from Nasdaq")
tw = [t for t, sym in symbols.items() if sym.endswith(".TW") and sym not in caps and t in px]
if tw:
    try:
        sh = twse_shares()
        for t in tw:
            if t in sh:
                caps[symbols[t]] = (round(px[t]["price"] * sh[t]), "TWD")
        print(len([t for t in tw if t in sh]), "market caps from TWSE")
    except Exception as e:
        print("TWSE shares failed:", e)
for t, sym in symbols.items():
    if t not in px:
        continue
    if sym in caps:
        px[t]["mcap"], px[t]["mcur"] = caps[sym]
    elif t in old and old[t].get("mcap"):
        px[t]["mcap"], px[t]["mcur"] = old[t]["mcap"], old[t].get("mcur")

out = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "source": "Yahoo Finance", "px": px}
json.dump(out, open("prices.json", "w"), ensure_ascii=False, indent=0, sort_keys=True)
print(f"{ok}/{len(symbols)} quotes updated")
if ok == 0:
    raise SystemExit("no quotes at all")
