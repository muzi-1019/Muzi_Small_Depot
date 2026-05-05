# RAG 性能瓶颈分析报告（Task 13）

- **测试时间**: 2026-05-05 08:28:44
- **LLM 模型**: deepseek-ai/DeepSeek-V3
- **Embedding 模型**: BAAI/bge-large-zh-v1.5
- **Milvus**: http://47.95.237.114:19530
- **测试问题数**: 5

## 分阶段耗时

| 阶段 | 平均耗时 | 最小 | 最大 | 占比 |
| --- | --- | --- | --- | --- |
| Query Rewrite (指代消解) | 0.000s | 0.000s | 0.000s | 0.0% |
| Embedding (向量化) | 0.676s | 0.564s | 0.720s | 5.2% |
| Vector Search (向量检索) | 0.751s | 0.285s | 2.546s | 5.7% |
| Keyword Search (BM25) | 1.515s | 0.012s | 7.515s | 11.6% |
| Hybrid Merge (混合排序) | 0.304s | 0.289s | 0.317s | 2.3% |
| Graph Retrieval (图检索) | 0.011s | 0.000s | 0.021s | 0.1% |
| LightRAG Retrieval | 0.001s | 0.000s | 0.004s | 0.0% |
| Context Assembly (上下文组装) | 0.000s | 0.000s | 0.000s | 0.0% |
| LLM Generation (大模型生成) | 9.812s | 3.969s | 16.268s | 75.1% |
| Total (总计) | 13.071s | 5.304s | 17.573s | 0.0% |

## 瓶颈识别

**主要瓶颈**: LLM Generation (大模型生成)
- 占总耗时: **75%**
- 平均耗时: **9.81s**

## 优化建议

### LLM Generation (大模型生成) (9.81s)
- 部署本地 vLLM/SGLang 推理服务减少网络延迟
- 使用更小的模型（如 DeepSeek-V2-Lite）降低生成时间
- 启用 KV Cache 复用加速多轮对话
- 减少 max_tokens 限制不必要的长回复

### Keyword Search (BM25) (1.52s)
- 对文档预建倒排索引避免全量扫描
- 使用 Elasticsearch 替代内存 BM25
- 缓存 BM25 分词结果

### Vector Search (向量检索) (0.75s)
- 为 Milvus 创建 IVF_FLAT 索引替代暴力搜索
- 减少 retrieval_top_k 数量
- 使用 Milvus 分区键减少扫描范围

## 已实施优化 — 前后对比

### 优化措施：BM25 文档缓存

- **问题**：每次 BM25 检索都从 Milvus 拉取 2000 行文档 + 重新分词
- **方案**：添加 `_bm25_cache`，缓存文档列表和预分词结果，300 秒 TTL
- **效果**：Hybrid Merge 包含了 BM25 + Vector 并行，首次调用后 BM25 走缓存

| 阶段 | 优化前 | 优化后 | 提升幅度 |
| --- | --- | --- | --- |
| **总平均耗时** | 25.81s | **13.07s** | **↓49%** |
| Keyword Search (BM25) | 7.24s | **1.52s** | **↓79%** |
| Hybrid Merge (混合排序) | 7.28s | **0.30s** | **↓96%** |
| Embedding (向量化) | 0.77s | 0.68s | ↓12% |
| Vector Search (向量检索) | 1.46s | 0.75s | ↓49% |
| LLM Generation | 9.06s | 9.81s | — (波动范围内) |

### 结论

- 检索链路耗时从 **16.75s** 降至 **3.25s**（↓81%）
- 当前主要瓶颈已转移至 **LLM 生成**（占 75%），需要部署本地推理服务进一步优化
- Embedding LRU 缓存在重复 query 场景下有效（命中后 <1ms）
