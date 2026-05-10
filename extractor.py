import json
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber


class PDFExtractor:
    def __init__(self, pdf_path: str, session_dir: str):
        self.pdf_path = pdf_path
        self.session_dir = Path(session_dir)
        self.images_dir = self.session_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def extract_text(self) -> dict:
        result = {"pages": []}

        with pdfplumber.open(self.pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                words = page.extract_words(extra_attrs=["fontname", "size"])
                if not words:
                    continue

                blocks = self._group_into_blocks(words)
                if blocks:
                    result["pages"].append({"page": page_num, "blocks": blocks})

        return result

    def _group_into_blocks(self, words: list) -> list:
        if not words:
            return []

        sizes = [w.get("size", 12) or 12 for w in words]
        sizes_sorted = sorted(sizes)
        median_size = sizes_sorted[len(sizes_sorted) // 2]

        lines: list[list] = []
        current_line: list = []
        current_y = None

        for word in words:
            y = round(word.get("top", 0), 1)
            if current_y is None or abs(y - current_y) > 3:
                if current_line:
                    lines.append(current_line)
                current_line = [word]
                current_y = y
            else:
                current_line.append(word)

        if current_line:
            lines.append(current_line)

        blocks = []
        for line in lines:
            text = " ".join(w["text"] for w in line).strip()
            if not text:
                continue

            avg_size = sum(w.get("size", 12) or 12 for w in line) / len(line)
            fontnames = [w.get("fontname", "") or "" for w in line]
            is_bold = any("Bold" in fn or "bold" in fn for fn in fontnames)

            if avg_size >= median_size * 1.4 or (avg_size >= median_size * 1.15 and is_bold):
                block_type = "heading"
            elif avg_size >= median_size * 1.1 or is_bold:
                block_type = "subheading"
            else:
                block_type = "paragraph"

            blocks.append({
                "type": block_type,
                "text": text,
                "font_size": round(avg_size, 1),
            })

        return self._merge_paragraphs(blocks)

    def _merge_paragraphs(self, blocks: list) -> list:
        merged = []
        for block in blocks:
            if (
                merged
                and block["type"] == "paragraph"
                and merged[-1]["type"] == "paragraph"
            ):
                merged[-1]["text"] += " " + block["text"]
            else:
                merged.append(block)
        return merged

    def extract_images(self) -> dict:
        result = {"images": []}
        seen_xrefs: set = set()

        doc = fitz.open(self.pdf_path)
        try:
            for page_num in range(len(doc)):
                page = doc[page_num]
                for img in page.get_images(full=True):
                    xref = img[0]
                    if xref in seen_xrefs:
                        continue
                    seen_xrefs.add(xref)

                    try:
                        base_image = doc.extract_image(xref)
                        ext = base_image["ext"]
                        filename = f"page{page_num + 1}_img{xref}.{ext}"
                        img_path = self.images_dir / filename

                        with open(img_path, "wb") as f:
                            f.write(base_image["image"])

                        result["images"].append({
                            "page": page_num + 1,
                            "filename": filename,
                            "url": f"/api/images/{self.session_dir.name}/{filename}",
                            "width": base_image.get("width"),
                            "height": base_image.get("height"),
                        })
                    except Exception:
                        continue
        finally:
            doc.close()

        return result

    def extract_tables(self) -> dict:
        result = {"tables": []}

        with pdfplumber.open(self.pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                for table_idx, table in enumerate(page.extract_tables(), 1):
                    if not table:
                        continue
                    result["tables"].append({
                        "page": page_num,
                        "index": table_idx,
                        "rows": len(table),
                        "cols": max((len(r) for r in table), default=0),
                        "html": self._table_to_html(table),
                    })

        return result

    def _table_to_html(self, table: list) -> str:
        html = '<table class="extracted-table">'
        for row_idx, row in enumerate(table):
            html += "<tr>"
            tag = "th" if row_idx == 0 else "td"
            for cell in row:
                cell_text = (cell or "").replace("\n", "<br>")
                html += f"<{tag}>{cell_text}</{tag}>"
            html += "</tr>"
        html += "</table>"
        return html

    def generate_composed_pdf(self, elements: list, page_count: int, output_path: str) -> str:
        """
        Build a PDF from layout-positioned elements.

        Each element carries:
          pageIndex : int   — 0-based page index
          xPct, yPct       — position as fraction of A4 (0.0–1.0)
          wPct, hPct       — size   as fraction of A4 (0.0–1.0)
          type             — 'text' | 'image' | 'table'
        """
        A4_W = 595.28   # points
        A4_H = 841.89   # points

        doc = fitz.open()
        pages = [doc.new_page(width=A4_W, height=A4_H) for _ in range(page_count)]

        for el in elements:
            pi = int(el.get("pageIndex", 0))
            if pi < 0 or pi >= len(pages):
                continue

            page = pages[pi]
            x0 = float(el.get("xPct", 0.0)) * A4_W
            y0 = float(el.get("yPct", 0.0)) * A4_H
            w  = float(el.get("wPct", 0.85)) * A4_W
            h  = float(el.get("hPct", 0.10)) * A4_H
            rect = fitz.Rect(x0, y0, x0 + w, y0 + h)

            el_type = el.get("type", "")

            if el_type == "text":
                block_type = el.get("block_type", "paragraph")
                if block_type == "heading":
                    fontsize, fontname = 18, "hebo"
                elif block_type == "subheading":
                    fontsize, fontname = 13, "hebo"
                else:
                    fontsize, fontname = 11, "helv"

                page.insert_textbox(
                    rect, el.get("text", ""),
                    fontsize=fontsize, fontname=fontname,
                    color=(0.07, 0.07, 0.07),
                    align=0,
                )

            elif el_type == "image":
                img_path = self.images_dir / el.get("filename", "")
                if img_path.exists():
                    try:
                        page.insert_image(rect, filename=str(img_path), keep_proportion=True)
                    except Exception:
                        pass

            elif el_type == "table":
                self._draw_table_on_page(page, rect, el)

        doc.save(str(output_path), garbage=4, deflate=True)
        doc.close()
        return output_path

    def _draw_table_on_page(self, page, rect: "fitz.Rect", el: dict) -> None:
        """Render an HTML table as a grid of cells on a PDF page."""
        from html.parser import HTMLParser

        class _TableParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.rows: list = []
                self._row = None
                self._cell = None

            def handle_starttag(self, tag, attrs):
                if tag == "tr":
                    self._row = []
                elif tag in ("td", "th") and self._row is not None:
                    self._cell = []

            def handle_endtag(self, tag):
                if tag == "tr" and self._row is not None:
                    self.rows.append(self._row)
                    self._row = None
                elif tag in ("td", "th") and self._cell is not None and self._row is not None:
                    self._row.append("".join(self._cell).strip())
                    self._cell = None

            def handle_data(self, data):
                if self._cell is not None:
                    self._cell.append(data)

        parser = _TableParser()
        parser.feed(el.get("html", ""))
        rows = parser.rows
        if not rows:
            return

        ncols   = max((len(r) for r in rows), default=1)
        nrows   = len(rows)
        col_w   = rect.width / ncols
        row_h   = min(rect.height / nrows, 18.0)

        H_BG   = (0.91, 0.92, 0.96)   # header row background
        ALT_BG = (0.97, 0.97, 0.97)   # alternating row background
        BORDER = (0.60, 0.60, 0.60)
        TEXT   = (0.07, 0.07, 0.07)

        for ri, row in enumerate(rows):
            y_top = rect.y0 + ri * row_h
            y_bot = y_top + row_h
            if y_top >= rect.y1:
                break
            y_bot = min(y_bot, rect.y1)

            for ci in range(ncols):
                x_l = rect.x0 + ci * col_w
                x_r = min(x_l + col_w, rect.x1)
                cell_rect = fitz.Rect(x_l, y_top, x_r, y_bot)

                fill = H_BG if ri == 0 else (ALT_BG if ri % 2 == 0 else None)
                if fill:
                    page.draw_rect(cell_rect, color=None, fill=fill)
                page.draw_rect(cell_rect, color=BORDER, width=0.4)

                cell_text = row[ci] if ci < len(row) else ""
                fn        = "hebo" if ri == 0 else "helv"
                text_rect = fitz.Rect(x_l + 2.5, y_top + 1.5, x_r - 1.5, y_bot - 1.0)
                page.insert_textbox(
                    text_rect, cell_text,
                    fontsize=8, fontname=fn, color=TEXT, align=0,
                )

    def generate_mobile_pdf(self, output_path: str, target_width_pt: float = 360.0) -> str:
        """
        Re-scale every page so it fits exactly target_width_pt wide.
        Height is kept proportional → no horizontal scrolling, layout intact.
        Fully vector-based via show_pdf_page() → no quality loss.
        """
        src = fitz.open(self.pdf_path)
        dst = fitz.open()

        for page_num in range(len(src)):
            src_page = src[page_num]
            src_rect = src_page.rect

            if src_rect.width == 0:
                continue

            scale = target_width_pt / src_rect.width
            new_w = target_width_pt
            new_h = round(src_rect.height * scale, 2)

            dst_page = dst.new_page(width=new_w, height=new_h)
            dst_page.show_pdf_page(
                fitz.Rect(0, 0, new_w, new_h),
                src,
                page_num,
            )

        dst.save(output_path, garbage=4, deflate=True)
        dst.close()
        src.close()
        return output_path

    def extract_all(self) -> dict:
        return {
            "text": self.extract_text(),
            "images": self.extract_images(),
            "tables": self.extract_tables(),
        }
