import sys
from contextlib import contextmanager


@contextmanager
def pytesseract_module():
    """Import pytesseract without loading pandas, which this app does not use."""
    sentinel = object()
    previous = sys.modules.get("pandas", sentinel)
    sys.modules["pandas"] = None
    try:
        import pytesseract
        yield pytesseract
    finally:
        if previous is sentinel:
            sys.modules.pop("pandas", None)
        else:
            sys.modules["pandas"] = previous
