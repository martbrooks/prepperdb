# Third-party notices and asset provenance

Project code is licensed under GPL-3.0-only (`LICENSE`). Owner-supplied/generated project artwork in `static/branding/`, `static/icons/` and `static/placeholder.svg` is licensed under CC BY 4.0 (`ARTWORK-LICENSE.txt`); attribute Martin A. Brooks / PrepperDB, link the licence and indicate changes. It does not replace the licences of bundled libraries, installed dependencies or imported product data.

## Bundled browser barcode decoder

- **ZXing Browser 0.2.1**, MIT: `static/vendor/zxing-browser-LICENSE.txt`.
- **ZXing Library 0.23.0**, Apache-2.0: `static/vendor/zxing-library-LICENSE.txt`.

The local browser bundle contains both components. `static/vendor/manifest.json` records upstream package URLs, integrity values and bundle SHA-256. Keep these notices/licences when redistributing it. The bundle needs no runtime CDN.

## Project graphics and screenshots

The pantry banner, doodle background texture and helmeted tin favicon are included byte-for-byte in `static/branding/`. The original action-icon designs and their supplied sizes are included in `static/icons/`; `static/icons/SOURCES.md` records provenance and the AI-generated Delete item variant. The Photo missing SVG is project-authored. These graphics do not require an image-generation service to run. The project artwork terms above apply to these graphics; third-party product imagery is excluded.

Screenshots in `docs/images/` were captured from the owner's current household installation with permission for publication, omitting browser chrome and hostname. They are illustrations, not a bundled starter database. Visible third-party product imagery/logos retain their respective rights; screenshot publication does not turn those into project-owned artwork.

## Optional product lookup and imported photographs

Open Food Facts data is separate from this source release:

- Database: [ODbL](https://opendatacommons.org/licenses/odbl/1-0/).
- Individual database contents: [Database Contents Licence](https://opendatacommons.org/licenses/dbcl/1-0/).
- Imported photographs: Open Food Facts contributors, [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/), with source attribution retained in the app.

Imported photographs are resized/re-encoded; contributors and product source links are shown in the app's Sources and licences/product pages. Review upstream terms for redistributing your own collection. No private inventory, downloaded product-photo database, user accounts or credentials are shipped with v0.1. Product trademarks remain with their owners.

## Python dependencies

Exact installed packages and hashes are in `requirements.lock`. Django, asgiref, sqlparse, Waitress and Pillow retain their upstream licences, distributed with their wheels. Development/test dependencies are separately locked. A dependency's appearance here does not imply endorsement.
