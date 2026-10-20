"""Price tracker: controlla i prezzi dei prodotti in products.yaml e avvisa su Telegram.

Uso:
    python tracker.py            controlla tutti i prodotti e invia gli avvisi
    python tracker.py --dry      mostra solo i prezzi trovati, nessun avviso, nessun salvataggio
    python tracker.py --test     invia un messaggio di prova su Telegram
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE = Path(__file__).parent
PRODUCTS_FILE = BASE / "products.yaml"
STATE_FILE = BASE / "state.json"

# Dopo quanti errori consecutivi avvisare che un prodotto non viene più letto
ERROR_ALERT_AFTER = 6

AMAZON_SELECTORS = [
    "#corePrice_feature_div .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
    "#apex_desktop .a-price .a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    "#tp_price_block_total_price_ww .a-offscreen",
]


class Unavailable(Exception):
    """Prodotto esaurito / non disponibile: non è un errore di lettura."""


# ---------------------------------------------------------------- parsing prezzi

def parse_price(text):
    """Converte '1.299,99 €', '€1,299.99', '1299.99' ecc. in float."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = re.sub(r"[^\d.,]", "", str(text))
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        # il separatore che compare per ultimo è quello dei decimali
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if s.count(",") > 1:  # "1,299,000" -> migliaia
            s = s.replace(",", "")
        else:  # formato italiano: virgola = decimali
            s = s.replace(",", ".")
    elif "." in s:
        head, _, tail = s.rpartition(".")
        if len(tail) == 3:  # "1.299" -> migliaia
            s = s.replace(".", "")
        else:
            s = head.replace(".", "") + "." + tail
    try:
        return float(s)
    except ValueError:
        return None


def _jsonld_prices(soup):
    """Cerca il prezzo nei dati strutturati schema.org (usati dalla maggior parte degli e-commerce)."""
    prices = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            if "offers" in node:
                walk_offers(node["offers"])
            for key in ("@graph", "mainEntity", "object", "itemOffered"):
                if key in node:
                    walk(node[key])

    def walk_offers(offers):
        if isinstance(offers, list):
            for o in offers:
                walk_offers(o)
        elif isinstance(offers, dict):
            for key in ("price", "lowPrice"):
                p = parse_price(offers.get(key))
                if p:
                    prices.append(p)
                    return
            if "priceSpecification" in offers:
                walk_offers(offers["priceSpecification"])
            if "offers" in offers:
                walk_offers(offers["offers"])

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(tag.string or ""))
        except (json.JSONDecodeError, TypeError):
            continue
    return prices


def extract_price(html, url, selector=None):
    soup = BeautifulSoup(html, "html.parser")

    if "amazon." in url and ("captcha" in html.lower()[:20000] and "validateCaptcha" in html):
        raise RuntimeError("Amazon ha mostrato un CAPTCHA (riprovo al prossimo giro)")

    # 1. selettore CSS indicato a mano nel products.yaml
    if selector:
        el = soup.select_one(selector)
        if el is None:
            raise RuntimeError(f"selettore '{selector}' non trovato nella pagina")
        p = parse_price(el.get("content") or el.get_text())
        if p:
            return p

    # 2. selettori specifici Amazon
    if "amazon." in url:
        if soup.select_one("#outOfStock"):
            raise Unavailable()
        for sel in AMAZON_SELECTORS:
            el = soup.select_one(sel)
            if el:
                p = parse_price(el.get_text())
                if p:
                    return p

    # 3. dati strutturati JSON-LD
    prices = _jsonld_prices(soup)
    if prices:
        return prices[0]

    # 4. meta tag / microdata
    for attrs in (
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
        {"itemprop": "price"},
    ):
        el = soup.find(attrs=attrs)
        if el:
            p = parse_price(el.get("content") or el.get_text())
            if p:
                return p

    raise RuntimeError("prezzo non trovato: aggiungi un 'selector' CSS per questo prodotto")


def fetch(url):
    r = requests.get(
        url,
        impersonate="chrome",  # imita un browser Chrome reale (riduce i blocchi anti-bot)
        headers={"Accept-Language": "it-IT,it;q=0.9"},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.text


# ---------------------------------------------------------------- telegram

def send_telegram(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("  [!] TELEGRAM_TOKEN / TELEGRAM_CHAT_ID mancanti: messaggio non inviato")
        print("     " + text.replace("\n", "\n     "))
        return
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=20,
    )
    if r.status_code != 200:
        print(f"  [!] errore Telegram {r.status_code}: {r.text}")


def eur(p):
    return f"{p:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------- main

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    args = set(sys.argv[1:])
    if "--test" in args:
        send_telegram("✅ Price tracker collegato correttamente!")
        return

    dry = "--dry" in args
    products = yaml.safe_load(PRODUCTS_FILE.read_text(encoding="utf-8")) or []
    state = load_state()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for prod in products:
        name, url, target = prod["name"], prod["url"], float(prod["target"])
        st = state.setdefault(url, {"name": name})
        st["name"] = name
        try:
            price = extract_price(fetch(url), url, prod.get("selector"))
        except Unavailable:
            st["errors"] = 0
            print(f"[ -- ] {name}: non disponibile")
            time.sleep(2)
            continue
        except Exception as e:
            st["errors"] = st.get("errors", 0) + 1
            print(f"[ERR] {name}: {e} (errori consecutivi: {st['errors']})")
            if not dry and st["errors"] == ERROR_ALERT_AFTER:
                send_telegram(f"⚠️ Non riesco a leggere il prezzo di <b>{name}</b> da {ERROR_ALERT_AFTER} controlli.\n"
                              f"Errore: {e}\n{url}")
            time.sleep(2)
            continue

        st["errors"] = 0
        prev = st.get("last_price")
        st["last_price"] = price
        if price != prev:
            st["last_change"] = now
        if price < st.get("min_price", float("inf")):
            st["min_price"] = price
        flag = "<= TARGET" if price <= target else ""
        print(f"[OK ] {name}: {eur(price)} (target {eur(target)}) {flag}")

        if price <= target:
            # avvisa la prima volta che scende sotto la soglia, poi solo se scende ancora
            notified = st.get("notified_price")
            if not dry and (notified is None or price < notified):
                msg = f"🔥 <b>{name}</b>\nPrezzo: <b>{eur(price)}</b> (soglia {eur(target)})"
                if prev and prev != price:
                    msg += f"\nPrima: {eur(prev)}"
                msg += f"\n{url}"
                send_telegram(msg)
                st["notified_price"] = price
        else:
            st.pop("notified_price", None)  # tornato sopra soglia: riarma l'avviso

        time.sleep(2)

    # rimuove dallo stato i prodotti tolti dal yaml
    urls = {p["url"] for p in products}
    for u in list(state):
        if u not in urls:
            del state[u]

    if not dry:
        save_state(state)


if __name__ == "__main__":
    main()
