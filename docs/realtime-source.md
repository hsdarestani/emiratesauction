# Technische Quelle für Live- und Endpreise

Geprüft am 26. August 2026 anhand der produktiven Emirates-Auction-Webseite und ihrer ausgelieferten Next.js-Bundles.

- Listen-JSON: `POST https://apiv8.emiratesauction.net/api/Vehicles` mit den Web-Headern `Lang` und `Source`. Enthält `CurrentPriceStr`, `Bids`, `EndDate` und `IsExpired`.
- Offizielle Detailquelle: `https://www.emiratesauction.com/auctions/vehicles/{lot}/4`, serverseitiges `__NEXT_DATA__.props.pageProps.fallback.detailsData.Data` mit denselben Preis-, Gebots- und Ablaufwerten.
- Realtime: Das Original-Frontend enthält Microsoft SignalR 8.0 und verbindet mit WebSocket-Transport (`skipNegotiation: true`) zu `https://bpapi.emiratesauction.net/bids/public`, `.../bids/private` und `.../auctiondetail`. Zusätzlich sind Heartbeat/Pong, automatischer Reconnect (0/2/5/10/30 Sekunden) und Channel-Beitritte implementiert.

Der öffentliche Hub ist damit als echte Push-Quelle nachgewiesen. Die produktive Anwendung verwendet vorerst bewusst den offiziellen Detail-JSON mit adaptivem Polling, weil die ausgelieferten Bundles keine stabile, dokumentierte öffentliche Ereignis-Spezifikation für anonyme Preisnachrichten garantieren. So werden keine Ereignisse aufgrund erratener Methodennamen als Endpreise fehlinterpretiert.

Ein Endpreis wird ausschließlich gesetzt, wenn die nach Ende erneut geladene offizielle Detailquelle `IsExpired=true` liefert. Das Verschwinden aus der Live-Liste reicht nicht aus.
