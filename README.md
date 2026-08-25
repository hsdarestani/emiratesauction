# Emirates Auction Intelligence

Production-oriented first milestone for collecting real Emirates Auction vehicle data.

## What is real

- Live inventory comes from Emirates Auction's public web API (`POST /api/Vehicles`).
- Detail/specification/image/report data is extracted from the server-rendered `__NEXT_DATA__` payload on each vehicle page.
- The worker selects and tracks 10 active vehicle auctions, polls every five minutes, and only appends a snapshot when price or bid count changes.
- Closed lots are persisted to `auction_results`.

The site also exposes a SignalR auction-detail hub. Polling is deliberately used for milestone one because it is restart-safe and adequate for five-minute history. SignalR support is the next realtime enhancement.

## Run

```bash
cp .env.example .env
docker compose up --build -d
curl http://localhost/api/health
```

Dashboard: `http://localhost`. API docs: `http://localhost/api/docs`.

## Admin API

Send `X-Admin-Token`:

```bash
curl -X POST http://localhost/api/tracked-auctions \
  -H 'Content-Type: application/json' -H 'X-Admin-Token: change-me' \
  -d '{"lot_url":"https://www.emiratesauction.com/auctions/vehicles/655351/4","target_price":30000,"notes":"Inspect chassis"}'
```

Market prices and repair/import estimates can be entered through `POST /api/vehicles/{id}/valuation`.

## Discovered data sources

| Purpose | Source | Authentication |
|---|---|---|
| Active vehicle list, price, bids, end time | `https://apiv8.emiratesauction.net/api/Vehicles` | Public |
| Vehicle specifications, condition tags, media, inspection report | Server-rendered vehicle page `__NEXT_DATA__` | Public |
| Live push updates | SignalR `https://bpapi.emiratesauction.net/auctiondetail` | Public connection; channel protocol required |

