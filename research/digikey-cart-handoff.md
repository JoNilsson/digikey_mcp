# DigiKey cart handoff mechanisms

Date: 2026-11-11

Question: which mechanism gets a resolved list of (DigiKey part number,
quantity, optional customer reference) into a DigiKey cart for a US business
customer, without the tool ever submitting the order?

## Summary

- No Cart API exists. The only official ordering endpoint, `POST
  /Ordering/v3/Orders`, places an order (3-legged OAuth, credit account), so
  it violates the never-submit rule. Do not use it.
- Best fit: the FastAdd URL
  `https://www.digikey.com/classic/ordering/fastadd.aspx` with `partN`,
  `qtyN`, `crefN`. It was documented by DigiKey staff in Oct 2025, needs no
  credentials, and lands the user on a cart. Limit: about 1700 characters per
  GET URL, so chunk the BOM (about 20-25 lines per URL), or use POST.
- Second: the undocumented no-auth MyLists endpoint (single-use link, list
  can be added to cart). Not in the official developer portal.
- Product Information v4: 120/min, 1000/day, no batch endpoint, so one call
  per line.
- Browser automation is prohibited by the website terms and bot-blocked.

## 1. Official API: ordering

| Item | Finding | Confidence |
| --- | --- | --- |
| Cart endpoint | None. FastAdd post states "no direct Cart API" [1] | High |
| Create order | `POST https://api.digikey.com/Ordering/v3/Orders` [2] | High |
| Behaviour | "Queues an order for processing"; email later [2][3] | High |
| OAuth | 3-legged only (authorization code); token URL `/v1/oauth2/token` [2][3] | High |
| Scopes | None defined in the spec [2] | Medium |
| Headers | `X-DIGIKEY-Client-Id` required; `Authorization: Bearer` [2] | High |
| Required body | `Currency`, `LineItems`, `PurchaseOrderNumber`, `ShippingContact` [2] | High |
| Line item | `RequestedQuantity`, `UnitPrice`, and `DigiKeyPartNumber` or `ManufacturerPartNumber`; optional `CustomerLineItemId`, `CustomerPartNumber` [2] | High |
| Account | Active DigiKey Credit Account required [3][4] | High |
| Rate limit | Ordering 10/min, no daily cap listed [5] | High |
| Sandbox | `sandbox-api.digikey.com`; sandbox apps visible only to their creator [2][5] | High |
| Special approval | No formal process documented; credit account is the gate [4]. Enhanced feeds need an email to api.contact@digikey.com [6] | Medium |
| Account-ID header | `X-DIGIKEY-Customer-Id` replaced by `X-DIGIKEY-Account-ID` on 2025-11-24 for Product Information, MyLists; Ordering asks callers to populate AccountID in `CustomerID` [7] | High |

Other 3-legged APIs: Quote v4 `CreateQuote`, `AddProductsToQuote` [8]
(Quoting 10/min [5]); MyLists v1 (create list, add parts) [9]. Request bodies
for these are in Swagger downloads that were not read (unconfirmed). A quote
is not a cart, so it does not meet the goal. MyLists `CreateList` and `Lists`
take `X-DIGIKEY-Account-ID` [7].

Conclusion: ordering API is a submit mechanism, not a handoff. A quote or
MyList via API is possible but ends at a list, not a cart. Unconfirmed
whether the official MyLists API can add a list to the cart.

## 2. Cart URL (FastAdd)

Source: DigiKey TechForum guide by DigiKey staff, 2025-10-17 [1].

```text
https://www.digikey.com/classic/ordering/fastadd.aspx
  ?part1=1050-ABX00052-ND&qty1=1&cref1=cpn123
  &part2=1050-ABX00162-ND&qty2=1&cref2=bobs-board
  &utm_source=<tool>&utm_content=<id>
```

- `partN` (DigiKey PN) and `qtyN` required; `crefN` optional (appears on
  sales documents and pick labels); `newcart=true` empties the cart first
  (default false, URL only); `utm_source` for referral attribution.
- GET: max safe length about 1700 chars; for more, issue several URLs
  without `newcart=true`. POST via an HTML form is supported; advice is not
  to exceed 400 parts per request, no hard limit stated [1].
- The cart is the user's own web session; user logs in, reviews, pays. The
  tool never submits.
- Third-party MCP implementation builds the same URL and warns above 1700
  chars [10]. Still supported in 2026: the guide is 13 months old and
  attributed to DigiKey staff; no deprecation seen. Confirm with one live
  test (unconfirmed).
- The `addpart` and `ordering/shoppingcart` schemes from the brief were not
  found in any source (unconfirmed, likely legacy).
- Quantity below a full reel may prompt the cut tape or Digi-Reel choice in
  the UI [11].

## 3. MyLists, BOM Manager, quick order

- Undocumented no-auth endpoint: `POST
  https://www.digikey.com/mylists/api/thirdparty?listName=<name>&tags=<t>`
  with a JSON array of objects `requestedPartNumber`, `manufacturerName`,
  `referenceDesignator`, `customerReference`, `notes`,
  `quantities:[{quantity:n}]`. Returns a single-use URL where the user adds
  to cart or saves the list [10][12]. The DigiKey forum post is the only
  primary source; the page 404ed on fetch, so field names rely on [10]
  (medium). A reply reports Cloudflare WAF blocking default python-requests
  agents [12 snippet].
- Lists hold up to 1,000 line items (2021 press release, stale) [13].
- The BOM Manager and Price and Availability uploader: map at least a part
  number column and one quantity column; customer reference can be mapped
  [14]. Exact column order, paste delimiter and size limits for Quick Order
  and BOM upload: unconfirmed, no official page found. BOM Manager decks say
  an MPN alone suffices [15]; ordering from a BOM requires a valid DigiKey PN
  [16].
- Official MyLists API (3-legged): create, update, delete lists, add parts
  [9]; request body unconfirmed.

## 4. Browser automation (fallback only)

- Website Terms: "You may not use any robot, scraper, spider, or other
  automated means to access or gather the Content from the Site" [17].
  Automated purchasing clause is in the unread portion (unconfirmed).
- API Agreement permits using the API to "automate and enhance your
  purchasing" [6]; it also bans bulk download and building a database from
  DigiKey Data (5.1 d, e) [6].
- Obstacles: Cloudflare-style bot challenge ("big fans of Robots") reported
  on the forum [18]; login required for the user's cart. Not recommended;
  FastAdd makes it unnecessary.

## 5. Product Information v4 limits

- Standard limits: Product Information 120/min, 1000/day [5]. Header
  `X-RateLimit-Limit` and `X-RateLimit-Remaining` report the daily budget
  [19]. Higher volume needs a sales manager's approval [4].
- Exceeding the threshold gives a 10-minute suspension with 403; three
  suspensions can terminate the agreement [6].
- No batch endpoint in v4: `GET /search/{productNumber}/productdetails` is one
  part per call; `POST /search/keyword` returns up to 50 hits [20]. The older
  v3 library has a `batch_product_details` (max 50) that must be explicitly
  enabled [21]; not confirmed for v4. Other useful v4 calls:
  `/pricingbyquantity/{qty}`, `/packagetypebyquantity/{pn}` [20].
- 2-legged client credentials works for Product Information [22]; the
  user-specific `X-DIGIKEY-Account-ID` gives MyPricing [7].
- A 200-line BOM costs 200 to 400 calls (resolve plus pricing), under the
  daily cap once; cache within the session only, given 5.1(e) [6].

## Recommendation (ranked)

1. FastAdd URL for the cart, chunked to 1700 chars, `cref` carries the
   customer reference, `newcart=true` only on the first chunk. Zero
   credentials. Prototype first and test with a live account.
2. MyLists no-auth endpoint as a one-link alternative for large BOMs (add
   to cart from a list); send a non-default User-Agent. Undocumented, so
   keep as a fallback behind a feature flag.
3. Official MyLists or Quote API (3-legged) only if a persistent list or
   quote is wanted; verify Swagger first.
4. Never: `POST /Ordering/v3/Orders`; browser automation.

User setup: create a developer.digikey.com account and organization; create a
Production App (Product Information v4, client ID and secret, OAuth callback
if 3-legged is used later); no credit account or approval needed for 1-3.
Sign in to a business myDigiKey account in the browser for the cart.

## Unconfirmed

- Quick Order paste and CSV column format and limits: settle by reading the
  logged-in Quick Order help page.
- FastAdd in 2026 and cart line cap: settle with one live test of 30 lines.
- Whether MyLists API v1 can create a cart: download the Swagger at
  `/node/2690/oas-download`.
- Whether the thirdparty MyLists endpoint is stable: ask DigiKey via the
  forum thread.

## Sources (accessed 2026-11-11)

1. DigiKey FastAdd guide: <https://forum.digikey.com/t/digikey-fastadd-bulk-add-parts-into-a-digikey-cart-via-third-party-tooling-and-urls/61356/1>
2. Ordering v3 OpenAPI: <https://developer.digikey.com/node/4546/oas-download>
3. Ordering product pages: <https://developer.digikey.com/products/ordering/ordering> and <https://developer.digikey.com/products/ordering>
4. API solutions FAQ: <https://www.digikey.at/en/resources/api-solutions-faqs>
5. Documentation, rate limits and OAuth: <https://developer.digikey.com/documentation>
6. API User Agreement: <https://developer.digikey.com/api-user-agreement>
7. Changelog: <https://developer.digikey.com/changelog>
8. Quote v4 CreateQuote: <https://developer.digikey.com/products/quote/quote-v4/createquote>
9. MyLists: <https://developer.digikey.com/products/mylists>
10. digikey-mcp source: <https://raw.githubusercontent.com/simon-77/digikey-mcp/main/digikey_noauth_tools.py>
11. Tape and reel thread: <https://forum.digikey.com/t/quantity-not-available-in-tape-reel-errors-when-uploading-a-list-of-components-in-xlsx-format/30167>
12. MyLists Third Party API post (search snippet only): <https://forum.digikey.com/t/mylists-third-party-api-easily-get-parts-into-mylists-from-third-party-applications/61445>
13. myLists press release 2021: <https://www.digikey.at/en/news/press-releases/2021/june/digi-key-electronics-launches-mylists-consolidated-list-management-system>
14. Price and Availability Tool (search snippet): <https://forum.digikey.com/t/digikey-price-and-availability-tool/2347>
15. BOM Manager deck (student copy): <https://eceseniordesign2020fall.ece.gatech.edu/software/hardware/Digi-KeyBOM%20Manager.pdf>
16. Scheme-it BOM Manager help: <https://www.digikey.be/en/schemeit/help-template/sch_data/en/bom_manager.html>
17. Website terms: <https://www.digikey.com/en/terms-and-conditions>
18. Forum, robot challenge: <https://forum.digikey.com/t/digikey-website-says-im-a-robot/32995>
19. Products, plans, APIs FAQ: <https://developer.digikey.com/faq/products-plans-and-apis>
20. Product Information v4 (unofficial mirror): <https://dkapidemo.pages.dev/product-information>
21. peeter123/digikey-api: <https://github.com/peeter123/digikey-api>
22. Jentic catalog (unofficial): <https://jentic.com/apis/digikey.com/digikey.md>
