# Action icon sources

The original nine designs and their 24/32/48/256px variants were supplied by the owner in `prepperdb-icons.zip`; they are unchanged. The wiki's technical/branding page records their integration.

## Delete item (#32)

Generated with the built-in imagegen tool on 6 November 2026, using the existing `256/add-stock.png` and `256/delete-category.png` as visual references. New transparent PNG variants are saved as `24/delete-item.png`, `32/delete-item.png`, `48/delete-item.png` and `256/delete-item.png`, downscaled with Pillow's Lanczos filter. The interface uses the 24px asset and 48px high-density source through the existing icon template. No runtime image-generation service is involved.

Final prompt:

> Use case: precise-object-edit. Asset type: local web UI delete-item action icon. Edit the first reference (yellow food tin with green plus badge) into a delete-item icon. Preserve the tin silhouette, bean graphic, cream/black thick outlines, warm yellow/olive retro pantry illustration style, compact square composition and transparent background. Replace only the green plus badge with the red-orange circular badge and cream X from the second reference (delete-category icon), matching its colours and thick outline. The result must show ONE yellow food tin with ONE small red circular cream-X delete badge overlapping its lower right corner. It will be used at 24 and 48 pixels beside the existing Add stock tin icon; use bold simple shapes readable at that scale. No text, lettering, radiation symbol, explosion, additional objects, background, checkerboard or watermark. Actual alpha transparency. Return a single square icon.
