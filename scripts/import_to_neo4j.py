"""
将知识图谱 JSON 数据导入 Neo4j，用于可视化展示。

前置条件:
  1. 安装 Neo4j Python 驱动:  pip install neo4j
  2. 启动 Neo4j 数据库（本地或 Docker）
     Docker 一键启动:
       docker run -d --name neo4j -p 7474:7474 -p 7687:7687 \
         -e NEO4J_AUTH=neo4j/password neo4j
  3. 打开浏览器访问 http://localhost:7474 查看图谱

用法:
  python scripts/import_to_neo4j.py [character_id]
  python scripts/import_to_neo4j.py 6
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ========== 配置 ==========
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j123")

CHARACTER_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 6


def main():
    """主函数：读取知识图谱 JSON 文件，连接 Neo4j 数据库，创建实体节点和关系边"""
    from neo4j import GraphDatabase

    # 读取图谱 JSON
    graph_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "graphs", f"graph_{CHARACTER_ID}.json"
    )
    if not os.path.exists(graph_path):
        print(f"[ERROR] 图谱文件不存在: {graph_path}")
        sys.exit(1)

    with open(graph_path, "r", encoding="utf-8") as f:
        graph_data = json.load(f)

    entities = graph_data.get("entities", {})
    relations = graph_data.get("relations", [])
    print(f"[INFO] 加载图谱: {len(entities)} 个实体, {len(relations)} 条关系")

    # 连接 Neo4j
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    print(f"[INFO] 连接 Neo4j: {NEO4J_URI}")

    with driver.session() as session:
        # session.run("MATCH (n) DETACH DELETE n")
        # print("所有节点和关系已删除")
        # 清空旧数据（仅清该 character_id 的）
        session.run(
            "MATCH (n {character_id: $cid}) DETACH DELETE n",
            cid=CHARACTER_ID
        )
        print(f"[INFO] 已清空 character_id={CHARACTER_ID} 的旧数据")

        # 创建实体节点
        created_nodes = 0
        for name, info in entities.items():
            etype = info.get("type", "其他")
            count = info.get("count", 0)
            # 用实体类型作为 Neo4j Label
            label = etype.replace(" ", "_")
            session.run(
                f"CREATE (n:`{label}` {{name: $name, type: $etype, count: $count, character_id: $cid}})",
                name=name, etype=etype, count=count, cid=CHARACTER_ID
            )
            created_nodes += 1

        print(f"[INFO] 创建了 {created_nodes} 个节点")

        # 创建唯一索引加速匹配
        try:
            session.run(
                "CREATE INDEX entity_name IF NOT EXISTS FOR (n:Entity) ON (n.name)"
            )
        except Exception:
            pass

        # 创建关系边
        created_edges = 0
        for rel in relations:
            src = rel["source"]
            tgt = rel["target"]
            relation = rel["relation"]
            # 关系类型做 Neo4j 关系标签
            rel_type = relation.replace(" ", "_").replace("、", "_")
            session.run(
                f"""
                MATCH (a {{name: $src, character_id: $cid}})
                MATCH (b {{name: $tgt, character_id: $cid}})
                CREATE (a)-[r:`{rel_type}` {{relation: $relation}}]->(b)
                """,
                src=src, tgt=tgt, relation=relation, cid=CHARACTER_ID
            )
            created_edges += 1

        print(f"[INFO] 创建了 {created_edges} 条关系")

    driver.close()
    print(f"\n[DONE] 导入完成！")
    print(f"  打开浏览器访问 http://localhost:7474")
    print(f"  在查询框输入: MATCH (n {{character_id: {CHARACTER_ID}}})-[r]->(m) RETURN n, r, m LIMIT 200")
    print(f"  即可看到知识图谱的可视化！")


if __name__ == "__main__":
    main()
