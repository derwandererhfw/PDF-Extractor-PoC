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

    def generate_composed_pdf(self, elements: list, output_path: str) -> str:
        """
        Build a new PDF from a list of composition elements.
        Each element: {type: 'text'|'image'|'table', ...}
        """
        import base64
        import html as html_lib

        css = """
            body { font-family: Arial, Helvetica, sans-serif; font-size: 11pt;
                   line-height: 1.55; color: #1a1a1a; margin: 0; padding: 0; }
            h1   { font-size: 18pt; font-weight: bold; margin: 0 0 8pt; }
            h2   { font-size: 13pt; font-weight: bold; margin: 0 0 6pt; }
            p    { margin: 0 0 8pt; }
            img  { max-width: 100%; height: auto; display: block; margin: 6pt 0; }
            table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 10pt; }
            th   { background: #e8eaf6; font-weight: bold;
                   padding: 5pt 7pt; border: 0.5pt solid #999; }
            td   { padding: 4pt 7pt; border: 0.5pt solid #999; }
            tr:nth-child(even) td { background: #f5f5f5; }
        """
        parts = [f"<html><head><style>{css}</style></head><body>"]

        for el in elements:
            t = el.get("type")
            if t == "text":
                tag = {"heading": "h1", "subheading": "h2"}.get(el.get("block_type"), "p")
                text = html_lib.escape(el.get("text", ""))
                parts.append(f"<{tag}>{text}</{tag}>")
            elif t == "image":
                img_path = self.images_dir / el.get("filename", "")
                if img_path.exists():
                    ext = img_path.suffix[1:].lower()
                    mime = "jpeg" if ext in ("jpg", "jpeg") else ext
                    data = base64.b64encode(img_path.read_bytes()).decode()
                    parts.append(f'<img src="data:image/{mime};base64,{data}"/>')
            elif t == "table":
                parts.append(el.get("html", ""))

        parts.append("</body></html>")
        full_html = "\n".join(parts)

        story = fitz.Story(html=full_html)
        writer = fitz.DocumentWriter(str(output_path))
        pagerect = fitz.paper_rect("a4")
        margin = 50
        where = fitz.Rect(
            pagerect.x0 + margin, pagerect.y0 + margin,
            pagerect.x1 - margin, pagerect.y1 - margin,
        )
        more = True
        while more:
            device = writer.begin_page(pagerect)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
        writer.close()
        return output_path

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
