"""Price tracker: controlla i prezzi dei prodotti in products.yaml e avvisa su Telegram.

Uso:
    python tracker.py            controlla tutti i prodotti e invia gli avvisi
    python tracker.py --dry      mostra solo i prezzi trovati, nessun avviso, nessun salvataggio
    python tracker.py --test     invia un messaggio di prova su Telegram
"""
import html
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


# ---------------------------------------------------------------- comandi del bot

PRODUCTS_HEADER = """\
# Prodotti da tracciare.
#   name:     nome che compare nella notifica
#   url:      link della pagina prodotto
#   target:   avvisami quando il prezzo è <= di questo valore (in euro)
#   selector: (opzionale) selettore CSS dell'elemento col prezzo, solo se il sito
#             non viene letto in automatico (vedi README)
#
# AMAZON: non metterlo qui (Amazon blocca i server di GitHub), usa Keepa.
# I prodotti si possono aggiungere anche dal bot Telegram con /aggiungi.
"""

HELP = (
    "🤖 <b>Comandi disponibili</b>\n\n"
    "/aggiungi <i>link prezzo [nome]</i>\n"
    "   es. <code>/aggiungi https://www.mediaworld.it/... 299,90</code>\n"
    "/lista – prodotti seguiti\n"
    "/soglia <i>numero prezzo</i> – cambia la soglia\n"
    "   es. <code>/soglia 2 250</code>\n"
    "/rimuovi <i>numero</i> – smetti di seguire un prodotto\n\n"
    "ℹ️ I comandi vengono letti a ogni controllo (circa ogni 30 minuti), "
    "quindi la risposta può arrivare con un po' di ritardo.\n"
    "Per Amazon usa Keepa."
)


def save_products(products):
    out = [PRODUCTS_HEADER]
    for p in products:
        out.append(f"- name: {json.dumps(p['name'], ensure_ascii=False)}")
        out.append(f"  url: {json.dumps(p['url'], ensure_ascii=False)}")
        out.append(f"  target: {float(p['target']):.2f}")
        if p.get("selector"):
            out.append(f"  selector: {json.dumps(p['selector'], ensure_ascii=False)}")
        out.append("")
    PRODUCTS_FILE.write_text("\n".join(out), encoding="utf-8")


def page_title(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    el = soup.find("meta", property="og:title")
    title = el.get("content") if el else (soup.title.get_text() if soup.title else "")
    title = re.sub(r"\s+", " ", title or "").strip()
    return (title[:70].rsplit(" ", 1)[0] + "…") if len(title) > 70 else (title or "Prodotto")


def _cmd_aggiungi(args, products, state):
    url = next((a for a in args if a.startswith("http")), None)
    rest = [a for a in args if a is not url]
    price_arg = next((a for a in rest if parse_price(a)), None)
    if not url or not price_arg:
        return "Formato: <code>/aggiungi link prezzo [nome]</code>\nes. <code>/aggiungi https://www.mediaworld.it/... 299,90</code>", False
    target = parse_price(price_arg)
    custom_name = " ".join(a for a in rest if a is not price_arg).strip()

    if "amazon." in url:
        asin = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
        link = f"https://keepa.com/#!product/8-{asin.group(1)}" if asin else "https://keepa.com"
        return ("🛒 I prodotti Amazon si seguono su <b>Keepa</b> (Amazon blocca il mio controllo).\n"
                f"Apri qui e imposta la soglia nella scheda “Traccia questo prodotto”:\n{link}"), False

    if any(p["url"] == url for p in products):
        return "Questo prodotto è già nella lista: usa /lista per vederlo.", False

    try:
        page = fetch(url)
        price = extract_price(page, url)
    except Unavailable:
        price = None
    except Exception as e:
        return (f"❌ Non riesco a leggere il prezzo da questa pagina ({html.escape(str(e))}).\n"
                "Questo sito va configurato a mano: chiedi a Claude di aggiungerlo."), False

    name = custom_name or page_title(page)
    products.append({"name": name, "url": url, "target": target})
    st = state.setdefault(url, {"name": name})
    if price is not None:
        st["last_price"] = st["min_price"] = price
    msg = f"✅ Aggiunto: <b>{html.escape(name)}</b>\nSoglia: {eur(target)}\n"
    msg += f"Prezzo attuale: {eur(price)}" if price is not None else "Al momento non è disponibile."
    if price is not None and price <= target:
        msg += "\n🔥 È già sotto la soglia!"
    return msg, True


def _pick(args, products):
    try:
        i = int(args[0]) - 1
        return i if 0 <= i < len(products) else None
    except (IndexError, ValueError):
        return None


def run_command(text, products, state):
    """Esegue un comando ricevuto su Telegram. Ritorna (risposta, prodotti_modificati)."""
    parts = text.split()
    if not parts:
        return None, False
    cmd, args = parts[0].split("@")[0].lower(), parts[1:]

    if cmd == "/aggiungi":
        return _cmd_aggiungi(args, products, state)

    if cmd == "/lista":
        if not products:
            return "La lista è vuota. Aggiungi un prodotto con /aggiungi.", False
        rows = []
        for n, p in enumerate(products, 1):
            last = state.get(p["url"], {}).get("last_price")
            now = eur(last) if last else "n.d."
            rows.append(f"{n}. <b>{html.escape(p['name'])}</b>\n   ora {now} · soglia {eur(float(p['target']))}")
        return "📋 <b>Prodotti seguiti</b>\n\n" + "\n".join(rows), False

    if cmd == "/rimuovi":
        i = _pick(args, products)
        if i is None:
            return "Formato: <code>/rimuovi numero</code> (il numero lo vedi con /lista)", False
        p = products.pop(i)
        state.pop(p["url"], None)
        return f"🗑 Rimosso: <b>{html.escape(p['name'])}</b>", True

    if cmd == "/soglia":
        i = _pick(args, products)
        target = parse_price(args[1]) if len(args) > 1 else None
        if i is None or not target:
            return "Formato: <code>/soglia numero prezzo</code>, es. <code>/soglia 2 250</code>", False
        products[i]["target"] = target
        state.get(products[i]["url"], {}).pop("notified_price", None)
        return f"✏️ <b>{html.escape(products[i]['name'])}</b>: nuova soglia {eur(target)}", True

    return HELP, False


def handle_commands(products, state):
    """Legge i messaggi arrivati al bot dall'ultimo controllo. Ritorna True se la lista prodotti è cambiata."""
    token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                         params={"offset": state.get("_telegram_offset", 0)}, timeout=20).json()
    except Exception as e:
        print(f"  [!] impossibile leggere i comandi Telegram: {e}")
        return False

    changed = False
    for upd in r.get("result", []):
        state["_telegram_offset"] = upd["update_id"] + 1
        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(chat_id):
            continue  # accetta comandi solo dalla tua chat
        text = (msg.get("text") or "").strip()
        print(f"[CMD] {text}")
        reply, modified = run_command(text, products, state)
        changed |= modified
        if reply:
            send_telegram(reply)
    return changed


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

    if not dry and handle_commands(products, state):
        save_products(products)

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
                send_telegram(f"⚠️ Non riesco a leggere il prezzo di <b>{html.escape(name)}</b> da {ERROR_ALERT_AFTER} controlli.\n"
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
                msg = f"🔥 <b>{html.escape(name)}</b>\nPrezzo: <b>{eur(price)}</b> (soglia {eur(target)})"
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
        if not u.startswith("_") and u not in urls:
            del state[u]

    if not dry:
        save_state(state)


if __name__ == "__main__":
    main()
