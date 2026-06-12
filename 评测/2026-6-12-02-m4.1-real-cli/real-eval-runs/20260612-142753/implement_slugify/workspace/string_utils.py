import re


def slugify(text: str) -> str:
    """Convert *text* into a URL-friendly slug.

    - Lowercase the string.
    - Treat any non-alphanumeric character(s) as separator(s).
    - Collapse consecutive separators into a single hyphen.
    - Strip leading/trailing hyphens.
    """
    text = text.lower()
    # Replace one or more non-alphanumeric characters with a single hyphen
    text = re.sub(r'[^a-z0-9]+', '-', text)
    # Strip leading/trailing hyphens
    text = text.strip('-')
    return text
