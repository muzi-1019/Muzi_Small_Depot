"""
本文件的作用：LightRAG 服务 — 轻量级图增强检索。

与传统 Graph RAG 的区别：
1. 增量更新：新文档可增量更新知识图谱，无需全量重建
2. 双层检索：Local（实体邻域）+ Global（社区摘要）两层检索策略
3. 更高效的图构建：使用关键词触发而非全文扫描

检索流程：
  query → 实体识别 → Local检索(邻域子图) + Global检索(社区摘要) → 融合排序 → 上下文
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_LIGHTRAG_DIR = Path(settings.data_dir) / "lightrag"
_LIGHTRAG_DIR.mkdir(parents=True, exist_ok=True)


class LightRAGService:
    """LightRAG 服务：增量图谱 + 双层检索"""

    def __init__(self) -> None:
        self._data: dict[int, dict] = {}

    # ==================== 增量图谱构建 ====================

    def ingest_chunks(self, character_id: int, chunks: list[str], batch_size: int = 5) -> dict:
        """增量更新知识图谱：新片段只添加新实体和关系，不影响已有数据。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        existing = self._load(character_id) or {}
        entities = existing.get("entities", {})
        relations = existing.get("relations", [])
        communities = existing.get("communities", [])
        ingested_hashes = set(existing.get("ingested_hashes", []))

        # 过滤已处理的片段（增量）
        new_chunks = []
        for chunk in chunks:
            h = hash(chunk[:200])
            if h not in ingested_hashes:
                new_chunks.append(chunk)
                ingested_hashes.add(h)

        if not new_chunks:
            print(f"  [LightRAG] 没有新片段需要处理")
            return existing

        print(f"  [LightRAG] 增量处理 {len(new_chunks)} 个新片段（已有 {len(entities)} 实体）")

        # 如果片段过多，采样
        if len(new_chunks) > 150:
            new_chunks = sorted(new_chunks, key=len, reverse=True)[:150]

        # 并发抽取
        batches = []
        for i in range(0, len(new_chunks), batch_size):
            batch = new_chunks[i:i + batch_size]
            combined = "\n\n".join(f"[片段] {c[:500]}" for c in batch)
            batches.append(combined)

        completed_count = [0]
        total = len(batches)

        def _extract(text):
            triples = self._extract_triples(text)
            completed_count[0] += 1
            print(f"  [LightRAG] batch {completed_count[0]}/{total}, +{len(triples)} triples", flush=True)
            return triples

        all_new_triples = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_extract, b) for b in batches]
            for f in as_completed(futures):
                try:
                    all_new_triples.extend(f.result())
                except Exception as e:
                    logger.debug("LightRAG extract error: %s", e)

        # 增量合并实体和关系
        for t in all_new_triples:
            src = t.get("source", "").strip()
            tgt = t.get("target", "").strip()
            rel = t.get("relation", "").strip()
            if not src or not tgt or not rel:
                continue
            for name, etype in [(src, t.get("source_type", "实体")), (tgt, t.get("target_type", "实体"))]:
                if name not in entities:
                    entities[name] = {"type": etype, "count": 0, "neighbors": []}
                entities[name]["count"] += 1
            # 记录邻居关系
            if tgt not in entities[src].get("neighbors", []):
                entities[src].setdefault("neighbors", []).append(tgt)
            if src not in entities[tgt].get("neighbors", []):
                entities[tgt].setdefault("neighbors", []).append(src)
            relations.append({"source": src, "target": tgt, "relation": rel})

        # 重建社区（简单连通分量聚类）
        communities = self._build_communities(entities, relations)

        data = {
            "character_id": character_id,
            "entities": entities,
            "relations": relations,
            "communities": communities,
            "ingested_hashes": list(ingested_hashes),
            "entity_count": len(entities),
            "relation_count": len(relations),
            "community_count": len(communities),
        }
        self._save(character_id, data)
        return data

    def _build_communities(self, entities: dict, relations: list) -> list[dict]:
        """基于连通分量的简单社区发现"""
        # 构建邻接表
        adj: dict[str, set] = defaultdict(set)
        for rel in relations:
            adj[rel["source"]].add(rel["target"])
            adj[rel["target"]].add(rel["source"])

        visited = set()
        communities = []

        for node in entities:
            if node in visited:
                continue
            # BFS 找连通分量
            component = set()
            queue = [node]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                for neighbor in adj.get(current, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)

            if len(component) >= 2:
                # 生成社区摘要
                member_types = [entities.get(m, {}).get("type", "实体") for m in component]
                community_rels = [r for r in relations if r["source"] in component and r["target"] in component]
                summary_parts = []
                for r in community_rels[:10]:
                    summary_parts.append(f"{r['source']}--[{r['relation']}]-->{r['target']}")
                communities.append({
                    "members": list(component),
                    "size": len(component),
                    "summary": "; ".join(summary_parts) if summary_parts else "",
                    "types": list(set(member_types)),
                })

        communities.sort(key=lambda c: c["size"], reverse=True)
        return communities

    # ==================== 双层检索 ====================

    def search_local(self, character_id: int, query: str, max_hops: int = 2, top_k: int = 5) -> list[dict]:
        """Local 检索：从匹配实体出发，在邻域子图中搜索相关三元组"""
        data = self._load(character_id)
        if not data:
            return []

        entities = data.get("entities", {})
        relations = data.get("relations", [])

        # 匹配查询中的实体
        matched = set()
        q_lower = query.lower()
        for name in entities:
            if name.lower() in q_lower or q_lower in name.lower():
                matched.add(name)

        if not matched:
            try:
                import jieba
                q_tokens = set(jieba.lcut(query))
                for name in entities:
                    e_tokens = set(jieba.lcut(name))
                    if q_tokens & e_tokens and any(len(t) > 1 for t in q_tokens & e_tokens):
                        matched.add(name)
            except Exception:
                pass

        if not matched:
            return []

        # BFS 扩展
        visited = set(matched)
        frontier = set(matched)
        result_triples = []

        for _ in range(max_hops):
            next_frontier = set()
            for rel in relations:
                if rel["source"] in frontier or rel["target"] in frontier:
                    result_triples.append(rel)
                    for n in [rel["source"], rel["target"]]:
                        if n not in visited:
                            next_frontier.add(n)
                            visited.add(n)
            frontier = next_frontier
            if not frontier:
                break

        # 去重
        seen = set()
        unique = []
        for t in result_triples:
            key = (t["source"], t["relation"], t["target"])
            if key not in seen:
                seen.add(key)
                unique.append(t)
        return unique[:top_k]

    def search_global(self, character_id: int, query: str, top_k: int = 3) -> list[dict]:
        """Global 检索：在社区摘要中搜索语义相关的社区"""
        data = self._load(character_id)
        if not data:
            return []

        communities = data.get("communities", [])
        q_lower = query.lower()

        scored = []
        for comm in communities:
            summary = comm.get("summary", "").lower()
            members = " ".join(comm.get("members", [])).lower()
            # 简单词匹配评分
            score = sum(1 for word in q_lower if word in summary or word in members)
            if score > 0:
                scored.append({**comm, "score": score})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def search_dual(self, character_id: int, query: str) -> str:
        """双层检索融合：Local + Global 结果合并为上下文"""
        local_results = self.search_local(character_id, query)
        global_results = self.search_global(character_id, query)

        parts = []
        if local_results:
            parts.append("[LightRAG Local 检索 — 实体邻域]")
            for i, t in enumerate(local_results, 1):
                parts.append(f"  ({i}) {t['source']} --[{t['relation']}]--> {t['target']}")

        if global_results:
            parts.append("[LightRAG Global 检索 — 社区摘要]")
            for i, comm in enumerate(global_results, 1):
                members_str = ", ".join(comm["members"][:5])
                parts.append(f"  社区{i} ({comm['size']}个实体): {members_str}")
                if comm.get("summary"):
                    parts.append(f"    摘要: {comm['summary'][:200]}")

        return "\n".join(parts) if parts else ""

    def has_data(self, character_id: int) -> bool:
        return self._load(character_id) is not None

    # ==================== 持久化 ====================

    def _save(self, character_id: int, data: dict):
        path = _LIGHTRAG_DIR / f"lightrag_{character_id}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._data[character_id] = data

    def _load(self, character_id: int) -> dict | None:
        if character_id in self._data:
            return self._data[character_id]
        path = _LIGHTRAG_DIR / f"lightrag_{character_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._data[character_id] = data
            return data
        except Exception:
            return None

    def _extract_triples(self, text: str) -> list[dict]:
        """调用 LLM 抽取三元组（复用 graph_service 相同逻辑）"""
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
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return json.loads(content)
        except Exception as e:
            logger.debug("LightRAG 三元组抽取失败: %s", e)
            return []

    # ==================== 统计 ====================

    def get_stats(self, character_id: int) -> dict:
        data = self._load(character_id)
        if not data:
            return {}
        return {
            "entity_count": data.get("entity_count", 0),
            "relation_count": data.get("relation_count", 0),
            "community_count": data.get("community_count", 0),
            "top_communities": [
                {"size": c["size"], "types": c["types"], "members_preview": c["members"][:5]}
                for c in data.get("communities", [])[:5]
            ],
        }
