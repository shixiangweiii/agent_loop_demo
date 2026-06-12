import re

def slugify(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text.lower())
    return s.strip("-")
