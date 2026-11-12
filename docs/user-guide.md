# Using PrepperDB

All accounts use one shared household inventory. Anyone signed in can add, change or delete its contents. Accounts do not provide separate personal stock or administrator roles.

## Add supplies

Choose **Add stock**. Type a name, scan a barcode with the camera, or select a barcode image. Previously saved products are reused locally; optional Open Food Facts lookup can suggest details and photos when internet access is available. Review suggestions before saving. Unknown barcodes can be associated with a manual product. No lookup/API key is needed for manual entry.

A name and quantity are sufficient. Quantity defaults to one. For multipacks, specify the number of packs and items per pack: two six-packs add twelve individual items. Use the size of one item, rather than the combined pack weight. Missing details are highlighted in Needs attention and can be corrected later.

## Dates and rotation

Best-before dates may use month/year; the app sorts these at the end of that month without pretending a day was printed. Use-by dates require a full date when supplied. Leave an unknown date blank rather than guessing.

Endangered stock lists dated batches in date order, including past dates, and shows undated stock separately. A past best-before date does not automatically mean food is unsafe. Use-by dates remain distinct. PrepperDB does not make food-safety decisions or remove dated stock automatically.

## Change quantities

Use **+**, **Use one** or other quantity controls for quick changes. Consuming the final item asks for confirmation. Use several and Discard cannot remove more than the available batch count; Set quantity supports upward stocktake corrections. Discard is distinct from consumption. Matching additions join an existing batch; different dates/properties keep separate batches.

**History** retains the last 32 days of quantity changes. Use Undo when offered; stale/conflicting changes may prevent undoing an older operation. History is not a backup. Changes older than 32 days are pruned. Out-of-stock products remain in the catalogue for replenishment. Delete item permanently removes a product and its batches after confirmation; it is not a quantity adjustment.

## Categories, photos and appearance

Use Categories to create, rename and move folders. Deleting a category branch moves its products to Uncategorised without deleting their stock. Expand category folders to navigate the inventory.

Use an item's photo controls to upload/replace, crop, zoom, rotate or remove its picture. Custom images take precedence over lookup images. Uploads are limited to 10 MiB and 12.5 million decoded pixels and become square JPEGs up to 800×800. Imported photos retain source attribution on the product. All saved photos are local; the database is their authoritative storage.

Choose dark or light appearance with the theme button. Manual stock entry, viewing and corrections work without internet after installation. Initial installation and optional online lookup still need connectivity. Browser camera access normally needs trusted HTTPS; a phone connecting over LAN HTTP is not localhost.
