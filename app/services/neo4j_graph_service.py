from __future__ import annotations

import logging
import re

from app.core.config import settings

logger = logging.getLogger(__name__)
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("neo4j.pool").setLevel(logging.WARNING)
logging.getLogger("neo4j.io").setLevel(logging.WARNING)


class Neo4jGraphService:
    """Neo4j 图谱召回服务：从外部 Neo4j 中检索实体关系，失败时返回空结果。"""

    def graph_context(self, character_id: int, query: str, top_k: int | None = None) -> str:
        if not settings.neo4j_enabled:
            return ""
        triples = self.search_relations(character_id, query, top_k or settings.neo4j_top_k)
        if not triples:
            return ""
        lines = ["[Neo4j 图谱检索结果]"]
        for i, item in enumerate(triples, 1):
            lines.append(f"  ({i}) {item['source']} --[{item['relation']}]--> {item['target']}")
        return "\n".join(lines)

    def search_rows(self, character_id: int, query: str, top_k: int | None = None) -> list[dict[str, object]]:
        triples = self.search_relations(character_id, query, top_k or settings.neo4j_top_k)
        rows: list[dict[str, object]] = []
        for index, item in enumerate(triples):
            text = f"[Neo4j 图谱关系] {item['source']} --[{item['relation']}]--> {item['target']}"
            rows.append({
                "text": text,
                "score": 1.0,
                "source_file": "neo4j",
                "chunk_index": index,
                "keywords": f"{item['source']} {item['relation']} {item['target']}",
                "method": "neo4j_graph",
            })
        return rows

    def search_relations(self, character_id: int, query: str, top_k: int) -> list[dict[str, str]]:
        try:
            from neo4j import GraphDatabase
        except ImportError:
            logger.info("[Neo4j RAG] skipped: neo4j driver not installed")
            return []

        terms = self._query_terms(query)
        if not terms:
            return []
        logger.info("[Neo4j RAG] query terms=%s", terms)

        driver = None
        try:
            driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
                connection_timeout=3.0,
            )
            cypher = """
            MATCH (a {character_id: $cid})-[r]-(b {character_id: $cid})
            WHERE any(term IN $terms WHERE
                toLower(a.name) CONTAINS term OR
                toLower(b.name) CONTAINS term OR
                toLower(coalesce(r.relation, type(r))) CONTAINS term
            ) OR (
                any(term IN $terms WHERE toLower(a.name) CONTAINS term OR toLower(b.name) CONTAINS term)
                AND any(term IN $relation_terms WHERE toLower(coalesce(r.relation, type(r))) CONTAINS term)
            )
            RETURN a.name AS source,
                   coalesce(r.relation, type(r)) AS relation,
                   b.name AS target
            LIMIT $limit
            """
            with driver.session() as session:
                result = session.run(cypher, cid=character_id, terms=terms, relation_terms=["股东", "关联", "持股", "控制"], limit=top_k)
                rows = [
                    {
                        "source": str(record.get("source", "")),
                        "relation": str(record.get("relation", "")),
                        "target": str(record.get("target", "")),
                    }
                    for record in result
                ]
                if not rows and any(term in query for term in ("股东", "关联", "持股", "控制")):
                    fallback_cypher = """
                    MATCH (a {character_id: $cid})-[r]-(b {character_id: $cid})
                    WHERE any(term IN $relation_terms WHERE toLower(coalesce(r.relation, type(r))) CONTAINS term)
                    RETURN a.name AS source,
                           coalesce(r.relation, type(r)) AS relation,
                           b.name AS target
                    LIMIT $limit
                    """
                    fallback = session.run(fallback_cypher, cid=character_id, relation_terms=["股东", "关联", "持股", "控制"], limit=top_k)
                    rows = [
                        {
                            "source": str(record.get("source", "")),
                            "relation": str(record.get("relation", "")),
                            "target": str(record.get("target", "")),
                        }
                        for record in fallback
                    ]
            logger.info("[Neo4j RAG] character_id=%s hits=%d", character_id, len(rows))
            return rows
        except Exception as exc:
            logger.warning("[Neo4j RAG] skipped: %s", exc)
            return []
        finally:
            if driver is not None:
                try:
                    driver.close()
                except Exception:
                    pass

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        stopwords = {
            "什么", "哪些", "哪个", "如何", "是否", "之间", "以及", "有关", "关于", "请问", "帮我", "告诉我",
            "发行人", "公司", "有限", "股份", "电子", "关系", "情况", "有",
        }
        tokens: list[str] = []
        try:
            import jieba
            tokens.extend(t.strip().lower() for t in jieba.lcut(query) if t.strip())
        except Exception:
            pass
        tokens.extend(re.findall(r"[A-Za-z0-9_]{2,}", query.lower()))
        tokens.extend(re.findall(r"[\u4e00-\u9fff]{2,8}", query.lower()))
        for suffix in ("有限公司", "股份有限公司", "电子股份有限公司"):
            for match in re.findall(rf"[\u4e00-\u9fffA-Za-z0-9_]+{suffix}", query):
                tokens.append(match.lower())
        for keyword in ("股东", "关联", "持股", "控制", "赛克赛斯", "赛星", "济南赛明", "济南宝赛", "济南华赛"):
            if keyword in query:
                tokens.append(keyword.lower())
        terms: list[str] = []
        for token in tokens:
            if len(token) <= 1 or token in stopwords or len(token) > 18:
                continue
            if token not in terms:
                terms.append(token)
        return terms[:12]
