# -*- coding: utf-8 -*-
from pathlib import Path

folder = Path(r"D:\桌面\工单\RAG 工单")
try:
    import fitz
except Exception as exc:
    print("NO_FITZ", exc)
    raise SystemExit(1)

for pdf in sorted(folder.glob("*.pdf")):
    doc = fitz.open(str(pdf))
    text_parts = []
    for page in list(doc)[:2]:
        text_parts.append(page.get_text("text"))
    text = "\n".join(text_parts).replace("\x00", "")[:1800]
    print("\n===== " + pdf.name + " =====")
    print(text)
