"""
本文件的作用：知识图谱（Graph RAG）相关 API 接口。
提供以下端点：
- POST /graph/build          —— 构建指定角色的知识图谱
- GET  /graph/search         —— 基于知识图谱检索
- GET  /graph/vis            —— 获取知识图谱可视化数据
- GET  /graph/stats          —— 获取知识图谱统计信息
- GET  /graph/vis-page       —— 知识图谱可视化 HTML 页面
"""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from app.core.deps import get_current_user_id, require_admin
from app.services.graph_service import KnowledgeGraphService
from app.services.pdf_ingest_service import PDFIngestService

router = APIRouter()

_graph_svc = KnowledgeGraphService()
_pdf_svc = PDFIngestService()


@router.post("/build")
def build_graph(
    character_id: int = Query(..., description="角色 id"),
    current_user_id: int = Depends(require_admin),
):
    """构建知识图谱：从该角色的 PDF 知识库中抽取实体关系并构建图"""
    # 从 Milvus 获取已有的文本片段
    from pymilvus import Collection, connections, utility
    from app.core.config import settings

    try:
        connections.connect(alias="default", uri=settings.milvus_url, db_name=settings.milvus_db)
    except Exception as e:
        return {"code": 500, "msg": f"无法连接 Milvus: {e}"}

    collection_name = f"{settings.milvus_collection}_{character_id}"
    if not utility.has_collection(collection_name):
        return {"code": 404, "msg": f"角色 {character_id} 没有知识库数据（集合 {collection_name} 不存在）"}

    collection = Collection(collection_name)
    collection.load()
    rows = collection.query(
        expr="",
        output_fields=["text"],
        limit=2000,
    )
    if not rows:
        return {"code": 404, "msg": f"角色 {character_id} 没有知识库数据"}

    chunks = [str(r.get("text", "")) for r in rows if r.get("text")]
    graph_data = _graph_svc.build_graph(character_id, chunks)
    return {
        "code": 200,
        "msg": f"知识图谱构建完成：{graph_data['entity_count']} 个实体，{graph_data['triple_count']} 条关系",
        "data": {
            "entity_count": graph_data["entity_count"],
            "triple_count": graph_data["triple_count"],
        },
    }


@router.get("/search")
def search_graph(
    character_id: int = Query(..., description="角色 id"),
    query: str = Query(..., description="查询问题"),
    max_hops: int = Query(2, ge=1, le=4, description="图遍历最大跳数"),
    current_user_id: int = Depends(get_current_user_id),
):
    """基于知识图谱检索：从匹配的实体节点出发，沿关系边扩展获取上下文"""
    if not _graph_svc.has_graph(character_id):
        return {"code": 404, "msg": "该角色尚未构建知识图谱，请先调用 /graph/build"}
    triples = _graph_svc.search_graph(character_id, query, max_hops=max_hops)
    context = _graph_svc.graph_context(character_id, query, max_hops=max_hops)
    return {
        "code": 200,
        "data": {
            "triples": triples,
            "context": context,
            "count": len(triples),
        },
    }


@router.get("/vis")
def get_vis_data(
    character_id: int = Query(..., description="角色 id"),
    current_user_id: int = Depends(get_current_user_id),
):
    """获取知识图谱可视化数据（nodes + edges JSON）"""
    vis = _graph_svc.get_vis_data(character_id)
    return {"code": 200, "data": vis}


@router.get("/stats")
def get_graph_stats(
    character_id: int = Query(..., description="角色 id"),
    current_user_id: int = Depends(get_current_user_id),
):
    """获取知识图谱统计信息"""
    graph = _graph_svc.load_graph(character_id)
    if not graph:
        return {"code": 404, "msg": "该角色尚未构建知识图谱"}
    entities = graph.get("entities", {})
    relations = graph.get("relations", [])
    # 按类型统计
    type_stats: dict[str, int] = {}
    for info in entities.values():
        t = info.get("type", "其他")
        type_stats[t] = type_stats.get(t, 0) + 1
    return {
        "code": 200,
        "data": {
            "entity_count": len(entities),
            "relation_count": len(relations),
            "entity_types": type_stats,
            "top_entities": sorted(
                [{"name": k, "type": v.get("type", ""), "count": v.get("count", 0)}
                 for k, v in entities.items()],
                key=lambda x: x["count"], reverse=True
            )[:20],
        },
    }


@router.get("/vis-page", response_class=HTMLResponse)
def graph_vis_page(
    character_id: int = Query(..., description="角色 id"),
):
    """知识图谱可视化 HTML 页面（内嵌 vis-network.js）"""
    return HTMLResponse(content=_VIS_HTML.replace("__CHARACTER_ID__", str(character_id)))


# ========== 内嵌可视化 HTML ==========
_VIS_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>知识图谱可视化</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Microsoft YaHei', sans-serif; background: #0a0a1a; color: #e0e0e0; }
  #header { padding: 16px 24px; background: #12122a; border-bottom: 1px solid #2a2a4a;
            display: flex; align-items: center; gap: 16px; }
  #header h2 { font-size: 18px; color: #8ab4f8; }
  #stats { font-size: 13px; color: #888; }
  #search-box { padding: 8px 16px; border-radius: 6px; background: #1a1a3a;
                border: 1px solid #3a3a5a; color: #fff; font-size: 14px; width: 300px; }
  #graph { width: 100%; height: calc(100vh - 64px); }
  .legend { position: fixed; bottom: 16px; right: 16px; background: #12122ae0;
           padding: 12px; border-radius: 8px; font-size: 12px; }
  .legend-item { display: flex; align-items: center; gap: 6px; margin: 4px 0; }
  .legend-dot { width: 12px; height: 12px; border-radius: 50%; }
</style>
</head>
<body>
<div id="header">
  <h2>📊 知识图谱可视化</h2>
  <input id="search-box" placeholder="搜索实体..." oninput="filterNodes(this.value)">
  <span id="stats"></span>
</div>
<div id="graph"></div>
<div class="legend" id="legend"></div>
<script>
const CID = __CHARACTER_ID__;
let allNodes = [], allEdges = [], network = null;
const typeColors = {
  '公司':'#4CAF50','人物':'#2196F3','产品':'#FF9800','金额':'#F44336',
  '比例':'#9C27B0','日期':'#795548','地点':'#00BCD4','机构':'#3F51B5',
  '风险':'#E91E63','技术':'#009688','其他':'#607D8B'
};

async function loadGraph() {
  // Try to get token from localStorage, fallback to prompt
  let token = localStorage.getItem('rag_token');
  if (!token) {
    token = prompt('请输入 JWT Token:');
    if (token) localStorage.setItem('rag_token', token);
  }
  const resp = await fetch(`/api/v1/graph/vis?character_id=${CID}`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  const json = await resp.json();
  const data = json.data || {};
  allNodes = (data.nodes || []).map(n => ({
    id: n.id, label: n.label, color: { background: n.color, border: n.color, highlight: { background: '#fff', border: n.color } },
    font: { color: '#e0e0e0', size: 12 }, size: n.size, title: `${n.label} (${n.type})`, _type: n.type
  }));
  allEdges = (data.edges || []).map((e, i) => ({
    id: i, from: e.source, to: e.target, label: e.label,
    color: { color: '#555', highlight: '#8ab4f8' }, font: { color: '#999', size: 10 }, arrows: 'to'
  }));
  const container = document.getElementById('graph');
  const visData = { nodes: new vis.DataSet(allNodes), edges: new vis.DataSet(allEdges) };
  const options = {
    physics: { stabilization: { iterations: 150 }, barnesHut: { gravitationalConstant: -3000, springLength: 120 } },
    interaction: { hover: true, tooltipDelay: 100 },
    nodes: { shape: 'dot', borderWidth: 2 },
    edges: { smooth: { type: 'continuous' } }
  };
  network = new vis.Network(container, visData, options);
  document.getElementById('stats').textContent = `${allNodes.length} 个实体，${allEdges.length} 条关系`;
  // Build legend
  const types = [...new Set(allNodes.map(n => n._type))];
  const legend = document.getElementById('legend');
  legend.innerHTML = types.map(t => `<div class="legend-item"><div class="legend-dot" style="background:${typeColors[t]||'#607D8B'}"></div>${t}</div>`).join('');
}

function filterNodes(keyword) {
  if (!network) return;
  if (!keyword) { network.unselectAll(); return; }
  const kw = keyword.toLowerCase();
  const matched = allNodes.filter(n => n.label.toLowerCase().includes(kw)).map(n => n.id);
  if (matched.length > 0) {
    network.selectNodes(matched);
    network.focus(matched[0], { scale: 1.5, animation: true });
  }
}
loadGraph();
</script>
</body>
</html>"""
