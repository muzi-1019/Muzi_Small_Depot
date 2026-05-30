import re

with open(r'd:\桌面\Senior High School Grade 6\Role-playing system based on RAG_new\frontend\src\App.jsx', 'r', encoding='utf-8') as f:
    content = f.read()

# 搜索关键片段确认唯一性
frag1 = '<div className="field-grid"><label>API \u5730\u5740</label>'
frag2 = 'minWidth:72>\u68c0\u6d4b</button>'
print('frag1 count:', content.count(frag1))
print('frag2 count:', content.count(frag2))

matches = [m.start() for m in re.finditer(r'field-grid', content)]
print('field-grid count:', len(matches))

# 验证精确子串
sub = '<div className="field-grid"><label>API \u5730\u5740</label><div style={{display:"flex",gap:6,alignItems:"center"}}><input value={apiBase} onChange={(e) => setApiBase(e.target.value)} style={{flex:1}} /><button type="button" className="secondary" onClick={checkHealth} style={{marginTop:4,minWidth:72}}>\u68c0\u6d4b</button><span className={`badge ${status === "\u5728\u7ebf" ? "ok" : "bad"}`} style={{marginTop:4}}>{status}</span></div></div>'
print('exact sub count:', content.count(sub))
