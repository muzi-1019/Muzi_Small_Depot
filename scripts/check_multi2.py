with open(r'd:\桌面\Senior High School Grade 6\Role-playing system based on RAG_new\frontend\src\App.jsx', 'r', encoding='utf-8') as f:
    content = f.read()

# 从文件中提取精确子串，确保匹配
start = content.find('<div className="field-grid"><label>API 地址')
if start >= 0:
    end = content.find('</div></div>', start) + len('</div></div>')
    exact = content[start:end]
    print('EXACT:', exact[:200])
    print('EXACT end:', exact[-100:])
    print('count:', content.count(exact))
else:
    print('not found')
