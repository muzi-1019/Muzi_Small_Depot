from __future__ import annotations  # 启用延迟求值类型注解，避免运行时解析前向引用导致兼容性问题

import logging  # 导入日志模块，用于记录 Neo4j 检索状态和异常
import re  # 导入正则模块，用于从用户问题中抽取检索关键词

from app.core.config import settings  # 导入全局配置，包括 Neo4j 开关、连接地址、账号密码和 top_k

logger = logging.getLogger(__name__)  # 创建当前模块日志器，便于定位日志来源
logging.getLogger("neo4j").setLevel(logging.WARNING)  # 降低 neo4j 驱动整体日志级别，避免连接细节刷屏
logging.getLogger("neo4j.pool").setLevel(logging.WARNING)  # 降低连接池日志级别，只保留 warning 及以上信息
logging.getLogger("neo4j.io").setLevel(logging.WARNING)  # 降低底层 IO 日志级别，减少无关网络读写日志


class Neo4jGraphService:
    """Neo4j 图谱召回服务：从外部 Neo4j 中检索实体关系，失败时返回空结果。"""

    def graph_context(self, character_id: int, query: str, top_k: int | None = None) -> str:
        if not settings.neo4j_enabled:  # 如果配置中关闭 Neo4j 检索
            return ""  # 直接返回空上下文，避免影响主 RAG 流程
        triples = self.search_relations(character_id, query, top_k or settings.neo4j_top_k)  # 检索当前角色相关的图谱三元组
        if not triples:  # 如果没有检索到任何关系
            return ""  # 返回空字符串，表示没有图谱上下文
        lines = ["[Neo4j 图谱检索结果]"]  # 初始化上下文文本标题，便于大模型识别来源
        for i, item in enumerate(triples, 1):  # 遍历三元组，并从 1 开始编号
            lines.append(f"  ({i}) {item['source']} --[{item['relation']}]--> {item['target']}")  # 将三元组格式化为可读关系文本
        return "\n".join(lines)  # 用换行拼接所有图谱关系，返回给上层作为上下文

    def search_rows(self, character_id: int, query: str, top_k: int | None = None) -> list[dict[str, object]]:
        triples = self.search_relations(character_id, query, top_k or settings.neo4j_top_k)  # 先复用 search_relations 获取图谱三元组
        rows: list[dict[str, object]] = []  # 初始化返回给混合检索模块的标准 row 列表
        for index, item in enumerate(triples):  # 遍历每条图谱关系，同时生成顺序编号
            text = f"[Neo4j 图谱关系] {item['source']} --[{item['relation']}]--> {item['target']}"  # 将结构化三元组转换为文本片段
            rows.append({  # 按 pdf_ingest_service.search_hybrid 需要的统一 row 格式追加结果
                "text": text,  # RAG 上下文文本，后续会参与 merged 和 rerank
                "score": 1.0,  # 图谱召回使用固定分数，表示命中结构化关系
                "source_file": "neo4j",  # 来源标记为 neo4j，区别于 PDF 文件名
                "chunk_index": index,  # 使用图谱结果的顺序编号作为 chunk_index
                "keywords": f"{item['source']} {item['relation']} {item['target']}",  # 将三元组拼成关键词，便于后续展示或检索
                "method": "neo4j_graph",  # 标记召回方式为 Neo4j 图谱
            })  # 完成一条 row 的构造
        return rows  # 返回标准化后的图谱召回结果

    def search_relations(self, character_id: int, query: str, top_k: int) -> list[dict[str, str]]:
        try:  # Neo4j 驱动是可选依赖，因此导入需要容错
            from neo4j import GraphDatabase  # 导入 Neo4j 官方 Python 驱动入口
        except ImportError:  # 如果当前环境没有安装 neo4j 包
            logger.info("[Neo4j RAG] skipped: neo4j driver not installed")  # 记录跳过原因
            return []  # 返回空结果，不影响 BM25/向量检索

        terms = self._query_terms(query)  # 从用户问题中抽取用于 Neo4j 匹配的关键词
        if not terms:  # 如果没有有效关键词
            return []  # 没有条件可查，直接返回空列表
        logger.info("[Neo4j RAG] query terms=%s", terms)  # 记录抽取到的查询词，便于调试图谱召回

        driver = None  # 先初始化 driver，方便 finally 中安全关闭连接
        try:  # Neo4j 连接和查询都可能失败，因此整体用 try 包裹
            driver = GraphDatabase.driver(  # 创建 Neo4j driver，用于打开 session 并执行 Cypher
                settings.neo4j_uri,  # Neo4j Bolt/URI 地址，例如 bolt://localhost:7687
                auth=(settings.neo4j_user, settings.neo4j_password),  # 使用配置中的用户名和密码认证
                connection_timeout=3.0,  # 设置较短连接超时，避免图谱服务异常时拖慢 RAG
            )  # 完成 driver 创建
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
            """  # 主查询：在同一 character_id 下匹配任意方向关系，并按实体名/关系名包含查询词过滤
            with driver.session() as session:  # 打开 Neo4j session，使用上下文管理器自动释放资源
                result = session.run(cypher, cid=character_id, terms=terms, relation_terms=["股东", "关联", "持股", "控制"], limit=top_k)  # 执行主 Cypher 查询
                rows = [  # 将 Neo4j Record 转成普通 dict，方便上层使用
                    {
                        "source": str(record.get("source", "")),  # 起点实体名称
                        "relation": str(record.get("relation", "")),  # 关系名称，优先使用 relation 属性，否则 Cypher 中已回退为关系类型
                        "target": str(record.get("target", "")),  # 终点实体名称
                    }  # 完成单条三元组转换
                    for record in result  # 遍历 Neo4j 返回的每条记录
                ]  # 完成主查询结果列表构造
                if not rows and any(term in query for term in ("股东", "关联", "持股", "控制")):  # 如果主查询无结果但问题明显在问关系类信息
                    fallback_cypher = """
                    MATCH (a {character_id: $cid})-[r]-(b {character_id: $cid})
                    WHERE any(term IN $relation_terms WHERE toLower(coalesce(r.relation, type(r))) CONTAINS term)
                    RETURN a.name AS source,
                           coalesce(r.relation, type(r)) AS relation,
                           b.name AS target
                    LIMIT $limit
                    """  # 兜底查询：只按关系名匹配股东/关联/持股/控制，避免实体词没抽好时完全无结果
                    fallback = session.run(fallback_cypher, cid=character_id, relation_terms=["股东", "关联", "持股", "控制"], limit=top_k)  # 执行兜底关系查询
                    rows = [  # 将兜底查询结果也转换为普通 dict
                        {
                            "source": str(record.get("source", "")),  # 兜底结果的起点实体
                            "relation": str(record.get("relation", "")),  # 兜底结果的关系名称
                            "target": str(record.get("target", "")),  # 兜底结果的终点实体
                        }  # 完成一条兜底三元组转换
                        for record in fallback  # 遍历兜底查询返回记录
                    ]  # 完成兜底结果列表构造
            logger.info("[Neo4j RAG] character_id=%s hits=%d", character_id, len(rows))  # 记录当前角色图谱召回命中数量
            return rows  # 返回图谱三元组列表
        except Exception as exc:  # 捕获 Neo4j 连接、认证、查询、字段转换等所有异常
            logger.warning("[Neo4j RAG] skipped: %s", exc)  # 记录异常但不中断主检索流程
            return []  # 图谱不可用时返回空列表
        finally:  # 无论成功还是失败，都尝试释放 Neo4j driver
            if driver is not None:  # 只有 driver 创建成功才需要关闭
                try:  # 关闭连接也可能异常，因此单独保护
                    driver.close()  # 关闭 Neo4j driver，释放连接池资源
                except Exception:  # 忽略关闭阶段异常，避免覆盖主异常或影响返回
                    pass  # 不做额外处理

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        stopwords = {  # 停用词集合：这些词过于常见，作为图谱检索词会造成大量噪声
            "什么", "哪些", "哪个", "如何", "是否", "之间", "以及", "有关", "关于", "请问", "帮我", "告诉我",
            "发行人", "公司", "有限", "股份", "电子", "关系", "情况", "有",
        }  # 停用词定义结束
        tokens: list[str] = []  # 保存从 query 中抽取出的原始候选词
        try:  # 优先使用 jieba 做中文分词，能更好识别公司名、人名和业务词
            import jieba  # 导入中文分词库
            tokens.extend(t.strip().lower() for t in jieba.lcut(query) if t.strip())  # 分词后去空白、转小写并加入候选词
        except Exception:  # 如果 jieba 未安装或分词失败
            pass  # 不抛错，继续使用正则兜底抽词
        tokens.extend(re.findall(r"[A-Za-z0-9_]{2,}", query.lower()))  # 抽取长度至少为 2 的英文/数字/下划线片段
        tokens.extend(re.findall(r"[\u4e00-\u9fff]{2,8}", query.lower()))  # 抽取 2 到 8 个汉字的连续片段，覆盖中文实体和关系词
        for suffix in ("有限公司", "股份有限公司", "电子股份有限公司"):  # 针对公司全称后缀做额外匹配
            for match in re.findall(rf"[\u4e00-\u9fffA-Za-z0-9_]+{suffix}", query):  # 查找带公司后缀的完整实体名
                tokens.append(match.lower())  # 将公司名转小写后加入候选词
        for keyword in ("股东", "关联", "持股", "控制", "赛克赛斯", "赛星", "济南赛明", "济南宝赛", "济南华赛"):  # 对项目中常见关系词和实体名做显式保留
            if keyword in query:  # 如果用户问题中包含该关键词
                tokens.append(keyword.lower())  # 加入候选词，避免被分词或正则遗漏
        terms: list[str] = []  # 保存过滤、去重后的最终查询词
        for token in tokens:  # 遍历所有候选词
            if len(token) <= 1 or token in stopwords or len(token) > 18:  # 过滤单字、停用词和过长噪声词
                continue  # 跳过无效 token
            if token not in terms:  # 如果该词还没有加入结果
                terms.append(token)  # 保留该查询词，并保持出现顺序
        return terms[:12]  # 最多返回 12 个词，避免 Cypher 条件过多影响查询性能
