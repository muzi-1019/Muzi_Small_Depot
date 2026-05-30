with open(r'd:\桌面\Senior High School Grade 6\Role-playing system based on RAG_new\frontend\src\App.jsx', 'r', encoding='utf-8') as f:
    content = f.read()

subs = [
    '<div className="field-grid"><label>API \u5730\u5740</label><div style={{display:"flex",gap:6,alignItems:"center"}}><input value={apiBase} onChange={(e) => setApiBase(e.target.value)} style={{flex:1}} /><button type="button" className="secondary" onClick={checkHealth} style={{marginTop:4,minWidth:72}}>\u68c0\u6d4b</button><span className={`badge \${status === "\u5728\u7ebf" ? "ok" : "bad"}`} style={{marginTop:4}}>{status}</span></div></div>',
    'function AuthPage({ apiBase, setApiBase, status, checkHealth, onLogin, onRegister }) {',
    '<AuthPage apiBase={apiBase} setApiBase={setApiBase} status={status} checkHealth={checkHealth} onLogin={onLogin} onRegister={onRegister} />',
    '      setStatus("\u79bb\u7ebf");\n      if (apiBase.trim()) setToast(e.message);',
]
for i, s in enumerate(subs, 1):
    count = content.count(s)
    print(f'{i}: count={count}')
