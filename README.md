# Price Tracker → Telegram

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
1. Su github.com → **New repository** → nome es. `price-tracker`.
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
**Actions** → "Price tracker" → **Run workflow**. Dopo un minuto apri il run e controlla
il log: vedi il prezzo letto per ogni prodotto. Da lì in poi parte da solo ogni 15 minuti.

## Aggiungere / togliere prodotti
Modifica `products.yaml` direttamente da GitHub (icona matita):

```yaml
- name: Nome che vuoi nella notifica
  url: https://www.amazon.it/dp/XXXXXXXXXX
  target: 49.99
```

Per Amazon basta il link corto `https://www.amazon.it/dp/<codice ASIN>`.

### Se un sito non viene letto
La maggior parte degli e-commerce viene letta in automatico (Amazon, MediaWorld, Euronics…).
Se nel log vedi `prezzo non trovato`, aggiungi un `selector`:
1. Apri la pagina in Chrome, tasto destro sul prezzo → **Ispeziona**.
2. Guarda l'elemento evidenziato, es. `<span class="product-price">29,90 €</span>`.
3. Aggiungi al prodotto: `selector: .product-price` (classe → punto davanti, id → `#` davanti).

## Come funzionano gli avvisi
- Ricevi un messaggio appena il prezzo è **≤ soglia**.
- Non ti ripete lo stesso avviso a ogni controllo: ti riscrive solo se il prezzo **scende ancora**.
- Se il prezzo risale sopra la soglia e poi riscende, ti riavvisa.
- Se un prodotto non è leggibile per 6 controlli di fila (link rotto, sito cambiato) ti arriva un ⚠️.
- I prodotti esauriti su Amazon vengono saltati senza errori.

## Prova in locale (facoltativo)
```
pip install -r requirements.txt
python tracker.py --dry     # mostra i prezzi letti, senza avvisi
set TELEGRAM_TOKEN=...  &  set TELEGRAM_CHAT_ID=...
python tracker.py --test    # manda un messaggio di prova
```

## Limiti da sapere
- GitHub non garantisce il minuto esatto: i controlli "ogni 15 minuti" a volte slittano di qualche minuto.
- Amazon ogni tanto risponde con un CAPTCHA ai server di GitHub: quel giro viene saltato e si riprova al successivo.
- Su un repo senza attività per 60 giorni GitHub sospende i workflow pianificati (ti avvisa via email,
  basta riattivarlo dalla scheda Actions).
