# Tracker prezzi → Telegram

Controlla ogni 15 minuti i prezzi dei prodotti in `products.yaml` e ti manda un messaggio
Telegram quando un prezzo scende sotto la soglia che hai scelto. Gira gratis su GitHub Actions,
quindi funziona 24/7 anche a PC spento.

## Setup (una volta sola, ~10 minuti)

### 1. Crea il bot Telegram
1. Su Telegram apri **@BotFather** → `/newbot` → scegli nome e username.
2. Ti dà un **token** tipo `7123456789:AAH...` → copialo.
3. Apri il tuo nuovo bot e premi **Avvia** (o scrivigli un messaggio qualsiasi).
4. Apri nel browser `https://api.telegram.org/bot<TOKEN>/getUpdates` (sostituisci `<TOKEN>`)
   e cerca `"chat":{"id":123456789` → quel numero è il tuo **chat id**.

### 2. Crea il repository su GitHub
1. Su github.com → **New repository** → nome es. `tracker-prezzi`.
   - **Public** = minuti di Actions illimitati e gratis (i token restano segreti, si vede solo la lista prodotti).
   - **Private** = 2000 minuti/mese gratis: in quel caso nel file
     `.github/workflows/track.yml` cambia `*/15` in `*/30` per restare nel limite.
2. Carica i file di questa cartella (anche con "Add file → Upload files" trascinando tutto,
   compresa la cartella `.github`).

### 3. Aggiungi i segreti
Nel repo: **Settings → Secrets and variables → Actions → New repository secret**
- `TELEGRAM_TOKEN` = il token del bot
- `TELEGRAM_CHAT_ID` = il tuo chat id

### 4. Avvia
**Actions** → "Tracker prezzi" → **Run workflow**. Dopo un minuto apri il run e controlla
il log: vedi il prezzo letto per ogni prodotto. Da lì in poi parte da solo ogni 15 minuti.

## Aggiungere / togliere prodotti dal bot Telegram
Scrivi al tuo bot:

| Comando | Cosa fa |
|---|---|
| `/aggiungi link prezzo` | segue un nuovo prodotto (es. `/aggiungi https://www.mediaworld.it/... 299,90`); puoi aggiungere un nome alla fine |
| `/lista` | mostra i prodotti seguiti, con prezzo attuale e soglia |
| `/soglia numero prezzo` | cambia la soglia (es. `/soglia 2 250`) |
| `/rimuovi numero` | smette di seguire un prodotto |

I comandi vengono letti a ogni controllo, quindi la risposta arriva entro circa 30 minuti.
Accetta comandi solo dalla tua chat. Se mandi un link Amazon ti risponde con il link Keepa.

## Aggiungere / togliere prodotti a mano
Oppure modifica `products.yaml` direttamente da GitHub (icona matita):

```yaml
- name: Nome che vuoi nella notifica
  url: https://www.mediaworld.it/it/product/...
  target: 49.99
```

## Prodotti Amazon → Keepa
Amazon blocca le richieste che arrivano dai server di GitHub (CAPTCHA o pagine "non disponibile"),
quindi i prodotti Amazon **non vanno messi in `products.yaml`**. Per Amazon usa **Keepa** (keepa.com, gratis):
1. Crea un account su keepa.com e collega Telegram dalle impostazioni delle notifiche.
2. Cerca il prodotto (o incolla il link Amazon), scheda **Segui prodotto**,
   imposta il prezzo desiderato e scegli Amazon.it.
3. Quando il prezzo scende sotto la soglia, Keepa ti scrive su Telegram.

### Se un sito non viene letto
La maggior parte degli e-commerce viene letta in automatico (MediaWorld, Euronics…).
Se nel log vedi `prezzo non trovato`, aggiungi un `selector`:
1. Apri la pagina in Chrome, tasto destro sul prezzo → **Ispeziona**.
2. Guarda l'elemento evidenziato, es. `<span class="product-price">29,90 €</span>`.
3. Aggiungi al prodotto: `selector: .product-price` (classe → punto davanti, id → `#` davanti).

## Come funzionano gli avvisi
- Ricevi un messaggio appena il prezzo è **≤ soglia**.
- Non ti ripete lo stesso avviso a ogni controllo: ti riscrive solo se il prezzo **scende ancora**.
- Se il prezzo risale sopra la soglia e poi riscende, ti riavvisa.
- Se un prodotto non è leggibile per 6 controlli di fila (link rotto, sito cambiato) ti arriva un ⚠️.

## Prova in locale (facoltativo)
```
pip install -r requirements.txt
python tracker.py --dry     # mostra i prezzi letti, senza avvisi
set TELEGRAM_TOKEN=...  &  set TELEGRAM_CHAT_ID=...
python tracker.py --test    # manda un messaggio di prova
```

## Limiti da sapere
- GitHub non garantisce il minuto esatto: i controlli "ogni 15 minuti" a volte slittano di qualche minuto.
- Amazon blocca i server di GitHub: per Amazon usa Keepa (vedi sopra).
- Su un repo senza attività per 60 giorni GitHub sospende i workflow pianificati (ti avvisa via email,
  basta riattivarlo dalla scheda Actions).
