"""Small, explicit household corrections for incomplete provider records."""

# Owner-confirmed in #14. Suggestions are editable and applied only on save.
KNOWN_SIZES = {"00071106061776": ("400", "g", "tin")}


def known_size(codes, packaging=""):
    for code in codes:
        if value := KNOWN_SIZES.get(code.zfill(14)):
            if packaging and packaging != value[2]:
                continue  # A local packaging correction takes precedence.
            return dict(size_value=value[0], size_unit=value[1], packaging=value[2])
    return {}
