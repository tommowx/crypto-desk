# Crypto Desk

App Windows locale per ricerca su dati storici e simulazioni virtuali. Non usa agenti AI, non contiene chiavi di exchange e non invia ordini finanziari reali.

## Installazione

Il file `Crypto_Desk_Installa.cmd` installa l'app in `%LOCALAPPDATA%\CryptoDesk` e crea l'icona **Crypto Desk** sul desktop. Richiede Python 3.10 o successivo con `pythonw.exe` ed Edge o Chrome. I dati restano in una cartella `data` separata dal codice. L'installer può copiare i dati di una vecchia installazione senza rimuovere l'originale.

Dopo la prima installazione apri sempre l'app dalla stessa icona. A ogni avvio il launcher controlla il manifest pubblico HTTPS, verifica SHA-256 e installa una nuova versione in una cartella separata. Se il controllo o l'aggiornamento fallisce, usa l'ultima versione locale. SHA-256 rileva errori nel pacchetto, ma non è una firma digitale dell'autore.

## Uso e limiti

Le ricerche storiche Binance e la simulazione live Kraken sono separate. Tutte le operazioni sono di ricerca o paper trading: i risultati non dimostrano redditività e non garantiscono profitti. L'app non effettua trading reale.

## Sviluppo e test

I test dell'updater si eseguono con `python -m unittest discover -s tests`. I test dell'app vanno eseguiti dalla cartella del progetto. L'installer PowerShell e l'aspetto in browser reale richiedono verifica su Windows; la validazione cloud non equivale a tale verifica.

## Distribuzione

Il repository contiene solo codice, documentazione e pacchetto pubblico. Non aggiungere `data/`, report, log, cache, chiavi o credenziali.


## Versione 0.9.3

Ricerca con indicatori persistenti nei processi e segnali riutilizzati; numero
processi scelto in base a CPU e RAM disponibili. Portafoglio storico continuo,
segnali causali, esecuzione alla successiva apertura, costi base/stress,
verifica finale congelata sull'ultimo 20% e promozione condizionata delle regole.
Esportazione asincrona anche durante la ricerca, copia coerente SQLite,
salvataggio automatico in Documenti/Crypto Desk/Esportazioni e ultimo.json.
Il messaggio FATTO richiede ricerca terminata e nuova esportazione completata.
Il grafico distingue la riproduzione di un test dal lavoro del motore.

Verifiche: python -m unittest discover -s tests -q; node tests/test_ui.cjs.
