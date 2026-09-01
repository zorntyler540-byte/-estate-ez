# Shipping suite spike

Read-only exploration of post-sale CT Bids invoices → shipping invoice handoff (Shipping Saint).

## Why

After buyers pay the CTBids lot invoice, franchise staff still create/send a **separate shipping invoice**.
Estate-EZ already covers capture → AI catalog → CT post. Closing the loop needs:

1. Visibility into paid CT invoices that want shipping
2. Handoff into Shipping Saint (or CT addon invoices) for pack/ship billing

## Discovery (CT Seller SPA, Aug 2026)

Base: `https://seller.ctbids.com/services/api/v1` (same Bearer auth we already use).

### Invoice APIs (confirmed in Seller JS bundle)

| Endpoint | Role |
|----------|------|
| `POST /invoice/list` | Invoice grid (amount, status, sale, buyer, number) |
| `POST /invoice/detail/{id}` | Full invoice + child invoices, IRM, shipping fields |
| `POST /invoice/create` / `POST /invoice/add` | Create lot invoices |
| `POST /invoice/create/addon` | **Addon / shipping-style child invoices** |
| `POST /invoice/pdf/send/email` | Email invoice PDF to buyer |
| `POST /invoice/pdf/send/bulk/email/` | Bulk email |
| `PUT /invoice/shipping/update` | Shipping notes / tracking on invoice |
| `POST /invoice/item/receipt` | Receipt method updates |
| `POST /ctbids/sale/invoice/generate/` | Generate sale invoices |

### Fulfillment APIs

| Endpoint | Role |
|----------|------|
| `POST /fullfillment/pending/list` | Items on an invoice still needing fulfillment |
| `POST /fullfillment/list` / `add` / `update` | Manual fulfillment when not using Shipping Saint |

### Shipping Saint inside CT Seller

- Location settings include `shippingsaint` + `shippingsaintaccountnumber`
- When a sale has Shipping Saint enabled (`saleshippingsaint`), CT Seller **disables** manual fulfillment edit and deep-links to `https://manage.shippingsaint.com/`
- So for many CT shops, shipping invoices are owned by Shipping Saint after CT payment — not a second invent-from-scratch system

## Shipping Saint external APIs

- Quotes: `POST https://quotes.shippingsaint.com/api/quotes` (returns **401** without partner credentials)
- Order import / invoice send: partner onboarding (`info@shippingsaint.com` / demo)
- Pricing (public FAQ): ~$0.10/order imported, ~$1/shipping invoice, no monthly fee

## This spike in Estate-EZ

Implemented (read-only):

- `CTSellerClient.list_invoices` / `invoice_detail` / `list_fulfillment_pending`
- `GET /integrations/ctseller/invoices`
- `GET /integrations/ctseller/invoices/{id}`
- Website **Shipping** page: pull invoices, inspect detail, open Shipping Saint manage URL

Not in this spike (needs live credentials + partner approval):

- Creating addon shipping invoices
- Emailing invoices via CT
- Calling Shipping Saint quotes/import APIs
- Storing buyer PII locally

## Recommended next probes (with aunt’s CT login)

1. Open a **Paid** invoice that chose shipping → confirm `invoiceitemreceiptmethod` + `child_invoice` + `saleshippingsaint` fields on detail
2. Confirm whether her location uses Shipping Saint account number in CT Location settings
3. Schedule Shipping Saint demo and ask for **order-import API** access for a CT-oriented integrator
4. Only then: “Create shipping invoice” / “Send via Shipping Saint” buttons

## Product shape (target)

Sales dashboard / Shipping page after auction close:

**Needs shipping invoice** = CT lot invoice Paid + IRM shipping + no shipping child / not yet in Shipping Saint → one-click handoff.
