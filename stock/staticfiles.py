"""Development serving of existing generated files; cache misses never access SQLite."""

from pathlib import Path
import re
from django.conf import settings
from django.contrib.staticfiles.finders import BaseFinder


class GeneratedImageFinder(BaseFinder):
    def find(self, path, find_all=False, **kwargs):
        if not re.fullmatch(r"products/[a-f0-9]{64}\.jpg", path):
            return [] if find_all else None
        root = Path(settings.STATIC_ROOT).resolve()
        candidate = root / path
        if (
            candidate.is_file()
            and not candidate.is_symlink()
            and candidate.resolve().is_relative_to(root)
        ):
            return [str(candidate)] if find_all else str(candidate)
        return [] if find_all else None

    def list(self, ignore_patterns):
        return iter(())  # Generated cache is not a source for collectstatic.
