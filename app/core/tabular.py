"""Spreadsheet reading with openpyxl + csv only (no pandas / numpy needed), shared by Cases 2 and 3."""
import csv
from pathlib import Path

from .jobs import UserInputError


def _read_csv(p: Path) -> list[list]:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = p.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UserInputError("The CSV file is not in a readable text encoding.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [list(r) for r in csv.reader(text.splitlines(), dialect)]


def read_sheets(path) -> list[tuple[str, list[list]]]:
    """Returns [(sheet name, rows)], rows = list of cell-value lists ('' for empty cells)."""
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext in (".csv", ".tsv", ".txt"):
            return [("csv", _read_csv(p))]
        if ext == ".xls":
            raise UserInputError("Old .xls files are not supported. Open it in Excel, choose 'Save As' .xlsx (or CSV) and upload that.")
        from openpyxl import load_workbook
        wb = load_workbook(p, read_only=True, data_only=True)
        try:
            out = []
            for ws in wb.worksheets:
                try:
                    ws.reset_dimensions()  # some exporters write a wrong sheet size
                except Exception:
                    pass
                out.append((ws.title, [["" if v is None else v for v in row] for row in ws.iter_rows(values_only=True)]))
            return out
        finally:
            wb.close()
    except UserInputError:
        raise
    except Exception as e:
        raise UserInputError(f"The file could not be opened as a spreadsheet ({type(e).__name__}). "
                             "It may be corrupt, empty or password-protected.") from e