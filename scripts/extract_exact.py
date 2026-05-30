with open(r'd:\桌面\Senior High School Grade 6\Role-playing system based on RAG_new\frontend\src\App.jsx', 'r', encoding='utf-8') as f:
    content = f.read()

start = content.find('<div className="field-grid"><label>API \u5730\u5740')
end = content.find('</div></div>', start) + len('</div></div>')
exact = content[start:end]
with open(r'd:\桌面\Senior High School Grade 6\Role-playing system based on RAG_new\exact_old.txt', 'w', encoding='utf-8') as f2:
    f2.write(exact)
print('Wrote', len(exact), 'chars')
