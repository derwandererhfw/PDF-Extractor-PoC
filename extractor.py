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

    def extract_all(self) -> dict:
        return {
            "text": self.extract_text(),
            "images": self.extract_images(),
            "tables": self.extract_tables(),
        }
