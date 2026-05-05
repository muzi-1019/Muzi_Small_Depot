"""
PDF 知识库初始化脚本。
根据预定义的角色-PDF 映射关系，将 PDF 文件解析并写入 Milvus 向量数据库。
支持通过 --character-id 参数指定只导入某个角色的 PDF，不指定则导入所有。

用法：
  python scripts/init_pdf_knowledge.py
  python scripts/init_pdf_knowledge.py --character-id 2 --character-id 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.pdf_ingest_service import PDFIngestService


def main() -> int:
    """主函数：解析命令行参数，遍历角色-PDF 映射，将每个 PDF 解析并写入 Milvus"""
    parser = argparse.ArgumentParser(description="Initialize PDF knowledge into Milvus")
    parser.add_argument(
        "--character-id",
        type=int,
        action="append",
        help="Only ingest the given character id(s). Can be provided multiple times.",
    )
    args = parser.parse_args()

    service = PDFIngestService()
    mapping = service._role_pdf_mapping()

    if args.character_id:
        mapping = {cid: path for cid, path in mapping.items() if cid in set(args.character_id)}

    total = 0
    for character_id, pdf_path in mapping.items():
        inserted = service.ingest_file(character_id, Path(pdf_path))
        print(f"character_id={character_id} file={pdf_path.name} inserted={inserted}")
        total += inserted

    print(f"total_inserted={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
