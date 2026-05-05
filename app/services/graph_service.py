"""
本文件的作用：知识图谱服务（Graph + RAG）。
从 PDF 文本中抽取实体与关系，构建知识图谱，并支持基于图的检索和问答。

工作流程：
1. 对 PDF 文本的每个 chunk 调用 LLM 抽取 (实体, 关系, 实体) 三元组
2. 使用 NetworkX 构建内存图
3. 持久化为 JSON 文件
4. 查询时先匹配实体节点，再沿边扩展获取上下文
5. 提供可视化数据接口（nodes + edges JSON）
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# ========== 图数据持久化路径 ==========
_GRAPH_DIR = Path(settings.data_dir) / "graphs"
_GRAPH_DIR.mkdir(parents=True, exist_ok=True)


class KnowledgeGraphService:
    """知识图谱服务：实体抽取 → 图构建 → 图检索 → 可视化"""

    def __init__(self) -> None:
        self._graphs: dict[int, dict] = {}   # character_id -> graph data

    # ==================== 图构建 ====================

    def build_graph(self, character_id: int, chunks: list[str], batch_size: int = 5, max_workers: int = 4) -> dict:
        """从文本片段中抽取实体关系并构建知识图谱。
        每 batch_size 个 chunk 合并为一次 LLM 调用，max_workers 个并发线程加速。
        如果 chunk 数量 > 200，则采样 200 个最具信息量的片段以控制耗时。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # 如果片段过多，采样减少 API 调用
        if len(chunks) > 200:
            # 优先选择较长的、信息量较大的片段
            chunks = sorted(chunks, key=len, reverse=True)[:200]
            print(f"  [采样] 片段过多，已选取最长的 200 个片段")

        all_triples: list[dict] = []
        entities: dict[str, dict] = {}   # name -> {type, count, desc}
        relations: list[dict] = []       # {source, target, relation, context}

        # 准备所有批次
        batches = []
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            combined = "\n\n".join(f"[片段{i+j+1}] {c[:500]}" for j, c in enumerate(batch))
            batches.append((i // batch_size + 1, combined))

        total_batches = len(batches)
        completed = [0]  # mutable counter for threads

        def _process_batch(batch_info):
            batch_num, combined = batch_info
            triples = self._extract_triples(combined)
            completed[0] += 1
            print(f"  [GraphRAG] batch {completed[0]}/{total_batches}, extracted {len(triples)} triples", flush=True)
            return triples

        # 并发执行
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_process_batch, b) for b in batches]
            for future in as_completed(futures):
                try:
                    triples = future.result()
                    all_triples.extend(triples)
                except Exception as e:
                    print(f"  [GraphRAG] batch error: {e}", flush=True)

        # 构建图数据
        for t in all_triples:
            src = t.get("source", "").strip()
            tgt = t.get("target", "").strip()
            rel = t.get("relation", "").strip()
            if not src or not tgt or not rel:
                continue
            src_type = t.get("source_type", "实体")
            tgt_type = t.get("target_type", "实体")
            # 更新实体
            if src not in entities:
                entities[src] = {"type": src_type, "count": 0}
            entities[src]["count"] += 1
            if tgt not in entities:
                entities[tgt] = {"type": tgt_type, "count": 0}
            entities[tgt]["count"] += 1
            relations.append({
                "source": src,
                "target": tgt,
                "relation": rel,
            })

        graph_data = {
            "character_id": character_id,
            "entities": entities,
            "relations": relations,
            "triple_count": len(relations),
            "entity_count": len(entities),
        }

        # 持久化
        path = _GRAPH_DIR / f"graph_{character_id}.json"
        path.write_text(json.dumps(graph_data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._graphs[character_id] = graph_data
        logger.info("[GraphRAG] built graph for character %d: %d entities, %d relations",
                     character_id, len(entities), len(relations))
        return graph_data

    def _extract_triples(self, text: str) -> list[dict]:
        """调用 LLM 从文本中抽取实体-关系-实体三元组。"""
        base_url = (settings.openai_api_base or "").rstrip("/")
        api_key = settings.openai_api_key or ""
        if not base_url or not api_key:
            return []
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {
            "model": settings.llm_model_name,
            "messages": [
                {"role": "system", "content": (
                    "你是一个信息抽取助手。从给定文本中抽取实体和关系三元组。\n"
                    "输出 JSON 数组，每个元素格式：\n"
                    '{"source": "实体1", "source_type": "类型", "relation": "关系", "target": "实体2", "target_type": "类型"}\n'
                    "实体类型可选：公司、人物、产品、金额、比例、日期、地点、机构、风险、技术、其他\n"
                    "关系可选：控制、持股、发行、募集、用于、位于、从事、包含、金额为、占比、风险来源、合作、任职、子公司、供应商、客户、授予、申请、等\n"
                    "只输出 JSON 数组，不要输出其他内容。最多抽取 20 个三元组。"
                )},
                {"role": "user", "content": text[:3000]},
            ],
            "temperature": 0.1,
            "max_tokens": 1500,
        }
        try:
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            # 尝试从 markdown code block 中提取 JSON
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return json.loads(content)
        except Exception as e:
            logger.debug("三元组抽取失败: %s", e)
            return []

    # ==================== 图加载 ====================

    def load_graph(self, character_id: int) -> dict | None:
        """从磁盘加载已构建的知识图谱"""
        if character_id in self._graphs:
            return self._graphs[character_id]
        path = _GRAPH_DIR / f"graph_{character_id}.json"
        if not path.exists():
            return None
        try:
            graph_data = json.loads(path.read_text(encoding="utf-8"))
            self._graphs[character_id] = graph_data
            return graph_data
        except Exception:
            return None

    def has_graph(self, character_id: int) -> bool:
        """检查指定角色是否已有知识图谱"""
        return self.load_graph(character_id) is not None

    # ==================== 图检索 ====================

    def search_graph(self, character_id: int, query: str, max_hops: int = 2, top_k: int = 8) -> list[dict]:
        """基于知识图谱的检索：匹配查询中的实体 → 沿关系边扩展 → 返回相关三元组。
        1. 对查询分词，匹配图中实体节点
        2. 从匹配的节点出发，沿边扩展 max_hops 跳
        3. 收集沿途的三元组作为上下文
        """
        graph = self.load_graph(character_id)
        if not graph:
            return []

        entities = graph.get("entities", {})
        relations = graph.get("relations", [])

        # 步骤1：匹配查询中的实体
        matched_entities = set()
        query_lower = query.lower()
        for entity_name in entities:
            if entity_name.lower() in query_lower or query_lower in entity_name.lower():
                matched_entities.add(entity_name)

        # 如果精确匹配太少，做模糊匹配
        if len(matched_entities) < 2:
            try:
                import jieba
                query_tokens = set(jieba.lcut(query))
                for entity_name in entities:
                    entity_tokens = set(jieba.lcut(entity_name))
                    overlap = query_tokens & entity_tokens
                    if overlap and any(len(t) > 1 for t in overlap):
                        matched_entities.add(entity_name)
            except Exception:
                pass

        if not matched_entities:
            return []

        # 步骤2：从匹配的实体出发，BFS扩展
        visited = set(matched_entities)
        frontier = set(matched_entities)
        relevant_triples = []

        for _hop in range(max_hops):
            next_frontier = set()
            for rel in relations:
                src, tgt = rel["source"], rel["target"]
                if src in frontier or tgt in frontier:
                    relevant_triples.append(rel)
                    if src not in visited:
                        next_frontier.add(src)
                        visited.add(src)
                    if tgt not in visited:
                        next_frontier.add(tgt)
                        visited.add(tgt)
            frontier = next_frontier
            if not frontier:
                break

        # 去重
        seen = set()
        unique_triples = []
        for t in relevant_triples:
            key = (t["source"], t["relation"], t["target"])
            if key not in seen:
                seen.add(key)
                unique_triples.append(t)

        return unique_triples[:top_k]

    def graph_context(self, character_id: int, query: str, max_hops: int = 2, top_k: int = 8) -> str:
        """将图检索结果格式化为 LLM 可用的上下文字符串"""
        triples = self.search_graph(character_id, query, max_hops, top_k)
        if not triples:
            return ""
        lines = ["[知识图谱检索结果]"]
        for i, t in enumerate(triples, 1):
            lines.append(f"  ({i}) {t['source']} --[{t['relation']}]--> {t['target']}")
        return "\n".join(lines)

    # ==================== 可视化数据 ====================

    def get_vis_data(self, character_id: int) -> dict:
        """返回知识图谱的可视化数据（nodes + edges），供前端 D3/vis.js 渲染。"""
        graph = self.load_graph(character_id)
        if not graph:
            return {"nodes": [], "edges": [], "stats": {}}

        entities = graph.get("entities", {})
        relations = graph.get("relations", [])

        # 构建节点列表
        type_colors = {
            "公司": "#4CAF50", "人物": "#2196F3", "产品": "#FF9800",
            "金额": "#F44336", "比例": "#9C27B0", "日期": "#795548",
            "地点": "#00BCD4", "机构": "#3F51B5", "风险": "#E91E63",
            "技术": "#009688", "其他": "#607D8B",
        }
        nodes = []
        node_ids = {}
        for idx, (name, info) in enumerate(entities.items()):
            etype = info.get("type", "其他")
            nodes.append({
                "id": idx,
                "label": name,
                "type": etype,
                "color": type_colors.get(etype, "#607D8B"),
                "size": min(5 + info.get("count", 1) * 2, 30),
            })
            node_ids[name] = idx

        edges = []
        for rel in relations:
            src_id = node_ids.get(rel["source"])
            tgt_id = node_ids.get(rel["target"])
            if src_id is not None and tgt_id is not None:
                edges.append({
                    "source": src_id,
                    "target": tgt_id,
                    "label": rel["relation"],
                })

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "entity_count": len(entities),
                "relation_count": len(relations),
            },
        }
