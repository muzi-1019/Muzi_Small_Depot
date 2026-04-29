/**
 * 本文件的作用：前端应用的主组件文件（React 单页应用的核心）。
 *
 * 整个前端界面都在这个文件中实现，包括：
 * 1. 登录/注册页面（AuthPage）—— 带粒子动画背景和图形验证码
 * 2. 聊天主页面（ChatPage）—— 包含左侧边栏（设置/角色列表）、中间聊天区、右侧会话列表
 * 3. 管理员仪表盘（嵌入在 ChatPage 中）—— 查看系统统计数据
 *
 * 主要功能：
 * - 用户认证（登录/注册/退出）
 * - 角色选择与管理（创建/编辑/删除角色，仅管理员）
 * - 实时聊天（支持流式输出，打字机效果）
 * - 会话管理（创建/删除/重命名/导出会话）
 * - 历史消息查看和搜索
 * - 知识库文件上传（仅管理员）
 * - 暗色/亮色主题切换
 *
 * 技术栈：React + Vite，无路由库（单页切换通过状态控制）
 */

import { useEffect, useMemo, useRef, useState } from "react"; // React 核心 Hooks

const defaultApiBase = ""; // 默认 API 地址（空字符串表示使用当前域名）

/** 去除 API 地址末尾的斜杠 */
function normalizeApiBase(base) {
  return base.trim().replace(/\/$/, "");
}

/** 验证并规范化 API 地址（必须是合法的 http/https URL） */
function resolveApiBase(base) {
  const value = base.trim();
  if (!value) return "";
  const normalized = normalizeApiBase(value);
  if (/^https?:\/\/[^/]+$/i.test(normalized)) return normalized;
  return "";
}

/**
 * 通用 HTTP 请求函数：发送请求并解析 JSON 响应。
 * 自动处理 Content-Type、Authorization 头，以及错误情况。
 */
async function requestJson(url, options = {}, token = "") {
  const headers = {
    Accept: "application/json",
    ...options.headers,
  };
  if (options.body instanceof FormData) {
    delete headers["Content-Type"];
  } else {
    headers["Content-Type"] = headers["Content-Type"] || "application/json";
  }
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(url, { ...options, headers });
  const raw = await response.text();
  const contentType = response.headers.get("content-type") || "";
  let data = null;
  try {
    data = raw ? JSON.parse(raw) : null;
  } catch {
    if (contentType.includes("text/html") || raw.trimStart().startsWith("<!doctype html>")) {
      throw new Error("BACKEND_HTML_RESPONSE");
    }
    throw new Error(`接口返回非 JSON：${raw}`);
  }
  if (!response.ok) throw new Error(data?.detail || data?.message || `请求失败：${response.status}`);
  return data;
}

/** 将 ISO 时间字符串格式化为 HH:MM 格式（用于界面显示） */
function formatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** 自定义 Hook：让 textarea 输入框根据内容自动调整高度（最大240px） */
function useAutoResizeTextarea() {
  const textareaRef = useRef(null);
  const resizeTextarea = () => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 240)}px`;
    }
  };
  return { textareaRef, resizeTextarea };
}

/** 生成4位随机图形验证码字符串（由大写字母和数字组成） */
function randomCaptcha() {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  return Array.from({ length: 4 }, () => chars[Math.floor(Math.random() * chars.length)]).join("");
}

/** 图形验证码 Canvas 组件：在 Canvas 上绘制带干扰线的验证码，点击可刷新 */
function CaptchaCanvas({ code, onRefresh }) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#f6f9ff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    for (let i = 0; i < 18; i += 1) {
      ctx.strokeStyle = `rgba(${80 + i * 8}, ${100 + i * 4}, 180, 0.22)`;
      ctx.beginPath();
      ctx.moveTo(Math.random() * 180, Math.random() * 60);
      ctx.lineTo(Math.random() * 180, Math.random() * 60);
      ctx.stroke();
    }
    ctx.font = "bold 26px Inter, Arial";
    ctx.textBaseline = "middle";
    const offsets = [16, 53, 92, 132];
    [...code].forEach((ch, idx) => {
      ctx.save();
      ctx.translate(offsets[idx], 32);
      ctx.rotate(((Math.random() * 14) - 7) * Math.PI / 180);
      ctx.fillStyle = ["#2f66ff", "#1a2640", "#5b7cff", "#3f57a8"][idx % 4];
      ctx.fillText(ch, 0, 0);
      ctx.restore();
    });
  }, [code]);
  return <canvas ref={ref} width="180" height="60" className="captcha" onClick={onRefresh} />;
}

/** 粒子动画背景组件：在登录页面绘制浮动的几何粒子和连线效果 */
function ParticlesCanvas() {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    let w, h, particles, animId, t = 0;
    const shapes = ["circle", "ring", "square", "diamond", "triangle", "cross", "hexagon"];
    const colors = [
      [47,102,255], [100,140,255], [60,180,220], [130,100,255], [80,160,200],
    ];
    function resize() {
      w = canvas.width = canvas.offsetWidth;
      h = canvas.height = canvas.offsetHeight;
    }
    function init() {
      resize();
      particles = Array.from({ length: 72 }, () => {
        const col = colors[Math.floor(Math.random() * colors.length)];
        return {
          x: Math.random() * w, y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.35, vy: (Math.random() - 0.5) * 0.35,
          r: Math.random() * 3 + 1.2,
          o: Math.random() * 0.28 + 0.08,
          shape: shapes[Math.floor(Math.random() * shapes.length)],
          rot: Math.random() * Math.PI * 2,
          rotV: (Math.random() - 0.5) * 0.012,
          pulse: Math.random() * Math.PI * 2,
          col,
        };
      });
    }
    function drawShape(p) {
      const { x, y, r, shape, rot, col, o } = p;
      const sz = r * 1.6;
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(rot);
      ctx.globalAlpha = o + Math.sin(p.pulse + t * 0.02) * 0.06;
      const fill = `rgba(${col[0]},${col[1]},${col[2]},1)`;
      const stroke = `rgba(${col[0]},${col[1]},${col[2]},0.8)`;
      ctx.fillStyle = fill;
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 0.8;
      switch (shape) {
        case "circle":
          ctx.beginPath(); ctx.arc(0, 0, sz, 0, Math.PI * 2); ctx.fill(); break;
        case "ring":
          ctx.beginPath(); ctx.arc(0, 0, sz, 0, Math.PI * 2); ctx.stroke(); break;
        case "square":
          ctx.fillRect(-sz, -sz, sz * 2, sz * 2); break;
        case "diamond":
          ctx.beginPath(); ctx.moveTo(0, -sz * 1.3); ctx.lineTo(sz, 0); ctx.lineTo(0, sz * 1.3); ctx.lineTo(-sz, 0); ctx.closePath(); ctx.fill(); break;
        case "triangle":
          ctx.beginPath(); ctx.moveTo(0, -sz * 1.2); ctx.lineTo(sz * 1.1, sz * 0.8); ctx.lineTo(-sz * 1.1, sz * 0.8); ctx.closePath(); ctx.fill(); break;
        case "cross":
          ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(-sz, 0); ctx.lineTo(sz, 0); ctx.moveTo(0, -sz); ctx.lineTo(0, sz); ctx.stroke(); break;
        case "hexagon":
          ctx.beginPath(); for (let i = 0; i < 6; i++) { const a = Math.PI / 3 * i - Math.PI / 6; ctx.lineTo(Math.cos(a) * sz, Math.sin(a) * sz); } ctx.closePath(); ctx.stroke(); break;
      }
      ctx.restore();
    }
    function draw() {
      t++;
      ctx.clearRect(0, 0, w, h);
      for (const p of particles) {
        p.x += p.vx; p.y += p.vy; p.rot += p.rotV;
        if (p.x < -10) p.x = w + 10; if (p.x > w + 10) p.x = -10;
        if (p.y < -10) p.y = h + 10; if (p.y > h + 10) p.y = -10;
        drawShape(p);
      }
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x;
          const dy = particles[i].y - particles[j].y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < 130) {
            const a = 0.055 * (1 - dist / 130);
            const ci = particles[i].col, cj = particles[j].col;
            ctx.beginPath();
            ctx.moveTo(particles[i].x, particles[i].y);
            ctx.lineTo(particles[j].x, particles[j].y);
            ctx.strokeStyle = `rgba(${(ci[0]+cj[0])>>1},${(ci[1]+cj[1])>>1},${(ci[2]+cj[2])>>1},${a})`;
            ctx.lineWidth = 0.7;
            ctx.stroke();
          }
        }
      }
      animId = requestAnimationFrame(draw);
    }
    init(); draw();
    window.addEventListener("resize", resize);
    return () => { cancelAnimationFrame(animId); window.removeEventListener("resize", resize); };
  }, []);
  return <canvas ref={ref} className="auth-particles" />;
}

/**
 * 登录/注册页面组件。
 * 包含登录和注册两个表单，带图形验证码校验和粒子动画背景。
 * 登录成功后会自动切换到聊天主页面。
 */
function AuthPage({ apiBase, setApiBase, status, checkHealth, onLogin, onRegister }) {
  const [mode, setMode] = useState("login");
  const [transitionState, setTransitionState] = useState("idle");
  const [pagePhase, setPagePhase] = useState("enter");
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [captcha, setCaptcha] = useState(randomCaptcha());
  const [captchaInput, setCaptchaInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");

  useEffect(() => { const t = setTimeout(() => setToast(""), 2600); return () => clearTimeout(t); }, [toast]);
  useEffect(() => { setPagePhase("enter"); const t = setTimeout(() => setPagePhase("idle"), 260); return () => clearTimeout(t); }, [mode]);

  function switchMode(nextMode) {
    if (busy) return;
    setToast("");
    setTransitionState("switching");
    setPagePhase("switch-out");
    setTimeout(() => { setMode(nextMode); setPagePhase("switch-in"); setTransitionState("idle"); }, 180);
  }

  async function submitLogin() {
    if (busy) return;
    if (captchaInput.trim().toUpperCase() !== captcha) { setToast("图形码错误"); setCaptcha(randomCaptcha()); setCaptchaInput(""); return; }
    setBusy(true);
    setTransitionState("switching");
    try { await onLogin({ account, password }); setTransitionState("success"); setTimeout(() => setTransitionState("idle"), 380); }
    catch (e) { setToast(e.message); setCaptcha(randomCaptcha()); setCaptchaInput(""); setTransitionState("idle"); }
    finally { setBusy(false); }
  }

  async function submitRegister() {
    if (busy) return;
    if (account.trim().length < 3 || account.trim().length > 64) { setToast("账号长度需在 3 ~ 64 个字符之间"); return; }
    if (password.length < 6 || password.length > 128) { setToast("密码长度需在 6 ~ 128 个字符之间"); return; }
    setBusy(true);
    setTransitionState("switching");
    try { await onRegister({ account, password }); setToast("注册成功，请登录"); setPassword(""); switchMode("login"); setTransitionState("success"); setTimeout(() => setTransitionState("idle"), 380); }
    catch (e) { setToast(e.message); setTransitionState("idle"); }
    finally { setBusy(false); }
  }

  return <div className={`auth-shell ${transitionState}`}><ParticlesCanvas /><div className="auth-geo-bottom"><svg viewBox="0 0 1440 180" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg"><polygon points="0,180 120,90 200,140 320,60 440,120 520,40 640,100 720,20 840,80 960,30 1040,70 1120,10 1200,50 1320,25 1440,60 1440,180" fill="rgba(30,50,100,.06)" /><polygon points="0,180 80,130 180,160 300,100 420,140 540,80 660,130 780,60 900,110 1020,50 1140,90 1260,40 1380,70 1440,90 1440,180" fill="rgba(47,102,255,.05)" /><polygon points="0,180 60,150 160,170 280,120 400,155 520,110 640,150 760,100 880,140 1000,95 1120,130 1240,85 1360,115 1440,130 1440,180" fill="rgba(130,100,255,.04)" /><line x1="120" y1="90" x2="320" y2="60" stroke="rgba(47,102,255,.08)" strokeWidth="0.8" /><line x1="320" y1="60" x2="520" y2="40" stroke="rgba(47,102,255,.08)" strokeWidth="0.8" /><line x1="520" y1="40" x2="720" y2="20" stroke="rgba(47,102,255,.1)" strokeWidth="0.8" /><line x1="720" y1="20" x2="960" y2="30" stroke="rgba(47,102,255,.1)" strokeWidth="0.8" /><line x1="960" y1="30" x2="1120" y2="10" stroke="rgba(47,102,255,.08)" strokeWidth="0.8" /><line x1="1120" y1="10" x2="1320" y2="25" stroke="rgba(47,102,255,.08)" strokeWidth="0.8" /><line x1="200" y1="140" x2="440" y2="120" stroke="rgba(130,100,255,.06)" strokeWidth="0.6" /><line x1="640" y1="100" x2="840" y2="80" stroke="rgba(130,100,255,.06)" strokeWidth="0.6" /><line x1="1040" y1="70" x2="1200" y2="50" stroke="rgba(130,100,255,.06)" strokeWidth="0.6" /><circle cx="720" cy="20" r="3" fill="rgba(47,102,255,.15)" /><circle cx="1120" cy="10" r="2.5" fill="rgba(47,102,255,.12)" /><circle cx="320" cy="60" r="2.5" fill="rgba(130,100,255,.12)" /><circle cx="960" cy="30" r="2" fill="rgba(60,180,220,.12)" /><polygon points="520,35 525,45 515,45" fill="rgba(47,102,255,.1)" /><polygon points="1320,20 1325,30 1315,30" fill="rgba(130,100,255,.1)" /><polygon points="200,135 206,145 194,145" fill="rgba(60,180,220,.08)" /></svg></div><div className={`auth-card ${pagePhase} ${transitionState}`}><div className={`auth-hero ${pagePhase}`}><div className="auth-hero-decoration" /><div className="auth-badge">RAG Studio</div><h1>{mode === "login" ? "欢迎回来" : "创建新账号"}</h1><p>角色扮演知识工作台 — 基于 RAG 的智能对话与知识检索平台</p><p style={{marginTop:12,fontSize:12,color:"rgba(255,255,255,.45)"}}>Retrieval-Augmented Generation</p></div><div className={`auth-panel ${pagePhase}`}><div className="field-grid"><label>API 地址</label><div style={{display:"flex",gap:6,alignItems:"center"}}><input value={apiBase} onChange={(e) => setApiBase(e.target.value)} style={{flex:1}} /><button type="button" className="secondary" onClick={checkHealth} style={{marginTop:4,minWidth:72}}>检测</button><span className={`badge ${status === "在线" ? "ok" : "bad"}`} style={{marginTop:4}}>{status}</span></div></div><div className="auth-divider">{mode === "login" ? "账号登录" : "注册新账号"}</div><div className="field-grid"><label>账号</label><input value={account} onChange={(e) => setAccount(e.target.value)} placeholder={mode === "register" ? "3 ~ 64 个字符" : "请输入账号"} /><label>密码</label><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder={mode === "register" ? "至少 6 个字符" : "请输入密码"} /></div><div className="captcha-row"><div><label>图形验证码</label><input value={captchaInput} onChange={(e) => setCaptchaInput(e.target.value)} placeholder="输入右侧验证码" /></div><CaptchaCanvas code={captcha} onRefresh={() => setCaptcha(randomCaptcha())} /></div><div className="row auth-actions">{mode === "login" ? <><button type="button" disabled={busy} onClick={submitLogin}>登录</button><button type="button" className="secondary" disabled={busy} onClick={() => switchMode("register")}>注册新账号</button></> : <><button type="button" disabled={busy} onClick={submitRegister}>提交注册</button><button type="button" className="secondary" disabled={busy} onClick={() => switchMode("login")}>返回登录</button></>}</div></div></div>{toast ? <div className="toast">{toast}</div> : null}</div>;
}

/** 图标组件：根据 name 返回对应的 SVG 图标（菜单、用户、发送、搜索等） */
function Icon({ name }) {
  const common = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" };
  const icons = {
    menu: <svg {...common}><path d="M4 6h16M4 12h16M4 18h16" /></svg>,
    chevronLeft: <svg {...common}><path d="M14 6l-6 6 6 6" /></svg>,
    api: <svg {...common}><path d="M4 7h16v10H4z" /><path d="M8 11h8" /></svg>,
    user: <svg {...common}><path d="M20 21a8 8 0 10-16 0" /><circle cx="12" cy="8" r="4" /></svg>,
    role: <svg {...common}><path d="M4 18V8l8-4 8 4v10l-8 4-8-4z" /></svg>,
    book: <svg {...common}><path d="M5 4h11a3 3 0 013 3v13a2 2 0 00-2-2H6a2 2 0 00-2 2V6a2 2 0 012-2z" /></svg>,
    refresh: <svg {...common}><path d="M20 6v6h-6" /><path d="M20 12a8 8 0 10-2.34 5.66" /></svg>,
    send: <svg {...common}><path d="M22 2L11 13" /><path d="M22 2l-7 20-4-9-9-4z" /></svg>,
    download: <svg {...common}><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></svg>,
    plus: <svg {...common}><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>,
    edit: <svg {...common}><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" /><path d="M18.5 2.5a2.12 2.12 0 013 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>,
    trash: <svg {...common}><polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6" /><path d="M10 11v6" /><path d="M14 11v6" /></svg>,
    upload: <svg {...common}><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" /></svg>,
    moon: <svg {...common}><path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" /></svg>,
    sun: <svg {...common}><circle cx="12" cy="12" r="5" /><line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" /><line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" /><line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" /><line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" /></svg>,
    search: <svg {...common}><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>,
    close: <svg {...common}><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>,
  };
  return icons[name] || null;
}

/** 会话列表项组件：显示单个会话的标题、预览和操作按钮（重命名/删除） */
function ConversationItemCard({ item, isActive, onSelect, onDelete, onRename }) {
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(item.title);
  function startRename(e) { e.stopPropagation(); setEditTitle(item.title); setEditing(true); }
  function confirmRename(e) { e.stopPropagation(); if (editTitle.trim() && editTitle.trim() !== item.title) { onRename(item.id, editTitle.trim()); } setEditing(false); }
  function cancelRename(e) { e.stopPropagation(); setEditing(false); }
  function handleDelete(e) { e.stopPropagation(); if (confirm("确定删除这条对话吗？")) onDelete(item.id); }
  return (
    <div className={`conversation-item ${isActive ? "active" : ""}`} onClick={() => onSelect(item.id)}>
      <div className="conversation-item-head">
        {editing ? (
          <input className="rename-input" value={editTitle} onChange={(e) => setEditTitle(e.target.value)} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => { if (e.key === "Enter") confirmRename(e); if (e.key === "Escape") cancelRename(e); }} autoFocus />
        ) : (
          <strong>{item.title}</strong>
        )}
        <span>{item.updatedAtLabel}</span>
      </div>
      <p>{item.preview || "暂无内容"}</p>
      <div className="conversation-item-actions">
        {editing ? (
          <><button type="button" className="conv-action-btn" onClick={confirmRename}>✓</button><button type="button" className="conv-action-btn" onClick={cancelRename}>✗</button></>
        ) : (
          <><button type="button" className="conv-action-btn" onClick={startRename}>重命名</button><button type="button" className="conv-action-btn delete" onClick={handleDelete}>删除</button></>
        )}
      </div>
    </div>
  );
}

/**
 * 聊天主页面组件（登录后显示）。
 * 包含三栏布局：左侧边栏（设置/角色）、中间聊天区域、右侧会话列表。
 * 支持流式聊天、会话管理、角色管理、知识库上传、消息搜索、管理员仪表盘等功能。
 */
function ChatPage({ apiBase, setApiBase, status, checkHealth, userId, onLogout, characters, selectedCharacterId, setSelectedCharacterId, selectedCharacter, messages, loadingHistory, question, setQuestion, sendMessage, loadHistory, latestKnowledge, knowledgeList, busy, streaming, chatEndRef, conversations, activeConversationId, onCreateConversation, onSelectConversation, onDeleteConversation, onRenameConversation, onExportConversation, isAdmin, onCreateCharacter, onUpdateCharacter, onDeleteCharacter, onUploadDataset, darkMode, toggleDarkMode, onSearchMessages, fetchAdminStats, fetchAdminUsers, fetchAdminConversations, fetchAdminKnowledge }) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [showCharForm, setShowCharForm] = useState(false);
  const [charForm, setCharForm] = useState({ name: "", domain: "", persona: "", prompt_template: "" });
  const [editingCharId, setEditingCharId] = useState(null);
  const [charDatasetFile, setCharDatasetFile] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(null);
  const [showSearch, setShowSearch] = useState(false);
  const [searchKeyword, setSearchKeyword] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [showDashboard, setShowDashboard] = useState(false);
  const [dashStats, setDashStats] = useState(null);
  const [dashUsers, setDashUsers] = useState([]);
  const [dashConvs, setDashConvs] = useState([]);
  const [dashKnowledge, setDashKnowledge] = useState([]);
  const datasetRef = useRef(null);
  const [thinkingMs, setThinkingMs] = useState(0);
  useEffect(() => { if (!streaming) { setThinkingMs(0); return; } setThinkingMs(0); const t = setInterval(() => setThinkingMs(s => s + 10), 10); return () => clearInterval(t); }, [streaming]);
  const searchTimerRef = useRef(null);
  const { textareaRef, resizeTextarea } = useAutoResizeTextarea();
  async function openDashboard() {
    setShowDashboard(true);
    const [s, u, c, k] = await Promise.all([fetchAdminStats(), fetchAdminUsers(), fetchAdminConversations(), fetchAdminKnowledge()]);
    if (s) setDashStats(s);
    setDashUsers(u || []);
    setDashConvs(c || []);
    setDashKnowledge(k || []);
  }
  function handleSearchInput(val) {
    setSearchKeyword(val);
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    if (!val.trim()) { setSearchResults([]); return; }
    searchTimerRef.current = setTimeout(async () => {
      setSearching(true);
      const results = await onSearchMessages(val.trim());
      setSearchResults(results);
      setSearching(false);
    }, 400);
  }
  useEffect(() => { resizeTextarea(); }, [question]);
  useEffect(() => {
    const base = resolveApiBase(apiBase);
    if (apiBase.trim() && !base) return;
    const timer = setTimeout(() => {
      checkHealth();
    }, 250);
    return () => clearTimeout(timer);
  }, [apiBase]);

  const recentConversations = conversations.slice(0, 6);
  const olderConversations = conversations.slice(6);
  const leftSidebar = <aside className={`sidebar left-sidebar ${sidebarCollapsed ? "collapsed" : ""}`}><div className="sidebar-top"><button className="ghost icon-btn" onClick={() => setSidebarCollapsed((v) => !v)} aria-label={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}><Icon name={sidebarCollapsed ? "menu" : "chevronLeft"} /></button><span className={`badge ${status === "在线" ? "ok" : "bad"}`}>{status}</span><button className="ghost icon-btn" onClick={toggleDarkMode} aria-label="切换主题"><Icon name={darkMode ? "sun" : "moon"} /></button></div>{!sidebarCollapsed ? <><div className="sidebar-header"><div className="brand"><h1>RAG Studio</h1><p>角色扮演知识工作台</p></div></div><div className="panel panel-compact"><div className="panel-title"><Icon name="api" /><span>连接</span></div><label>API 地址</label><input value={apiBase} onChange={(e) => setApiBase(e.target.value)} /><button type="button" className="secondary full-btn" onClick={checkHealth}>重新检测</button></div><div className="panel panel-compact"><div className="panel-title"><Icon name="user" /><span>账号</span></div><p className="meta">user_id: {userId}</p><button type="button" className="secondary full-btn" onClick={onLogout}>退出登录</button></div><div className="panel panel-compact"><div className="panel-title"><Icon name="role" /><span>角色</span></div><div className="char-list" style={{display:"flex",flexDirection:"column",gap:3,maxHeight:160,overflowY:"auto"}}>{characters.map((c) => <div key={c.id} className={`char-item${c.id===selectedCharacterId?" active":""}`} style={{display:"flex",alignItems:"center",gap:4,padding:"4px 6px",borderRadius:8,cursor:"pointer",fontSize:11,background:c.id===selectedCharacterId?"var(--accent, #2f66ff)":"transparent",color:c.id===selectedCharacterId?"#fff":"var(--text)"}} onClick={()=>setSelectedCharacterId(c.id)}><span style={{flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{c.name} / {c.domain}</span>{c.id>3&&isAdmin&&<button className="conv-action-btn delete" style={{padding:"2px 5px",fontSize:10,flexShrink:0}} onClick={e=>{e.stopPropagation();if(confirm(`确定删除角色「${c.name}」？`))onDeleteCharacter(c.id);}}>删除</button>}</div>)}</div><p className="meta">{selectedCharacter?.persona || "暂无角色描述"}</p></div>{showCharForm && <div className="panel panel-compact" style={{animation:"panelEnter .25s"}}><div className="panel-title"><Icon name="edit" /><span>{editingCharId?"编辑角色":"新建角色"}</span></div><input placeholder="角色名称" value={charForm.name} onChange={e=>setCharForm(p=>({...p,name:e.target.value}))} style={{marginBottom:4}}/><input placeholder="领域（如：历史、心理学）" value={charForm.domain} onChange={e=>setCharForm(p=>({...p,domain:e.target.value}))} style={{marginBottom:4}}/><textarea placeholder="人设描述" rows={2} value={charForm.persona} onChange={e=>setCharForm(p=>({...p,persona:e.target.value}))} style={{marginBottom:4,fontSize:11}}/><textarea placeholder="提示模板（可选）" rows={2} value={charForm.prompt_template} onChange={e=>setCharForm(p=>({...p,prompt_template:e.target.value}))} style={{marginBottom:4,fontSize:11}}/><div style={{marginBottom:4}}><label style={{fontSize:11,color:"var(--text-secondary)"}}>数据集（可选，支持 txt/pdf/md/csv/json/jsonl）</label><input type="file" accept=".txt,.pdf,.md,.csv,.json,.jsonl" onChange={e=>setCharDatasetFile(e.target.files?.[0]||null)} style={{fontSize:11,marginTop:2}} /></div>{uploadProgress!==null&&<div style={{marginBottom:6}}><div style={{height:6,borderRadius:3,background:"var(--panel-border)",overflow:"hidden"}}><div style={{height:"100%",borderRadius:3,background:"linear-gradient(90deg,#2f66ff,#7aa1ff)",width:`${uploadProgress}%`,transition:"width .3s ease"}} /></div><span style={{fontSize:10,color:"var(--text-secondary)"}}>{uploadProgress<100?"上传清洗中…":"完成！"}</span></div>}<div style={{display:"flex",gap:4}}><button className="secondary full-btn" onClick={async()=>{if(!charForm.name.trim()){return;}let cid=editingCharId;if(editingCharId){await onUpdateCharacter(editingCharId,charForm);}else{const created=await onCreateCharacter({...charForm,role_type:"custom"});cid=created?.id;}if(charDatasetFile&&cid){setUploadProgress(10);const tick=setInterval(()=>setUploadProgress(p=>Math.min(p+8,90)),500);try{await onUploadDataset(cid,charDatasetFile);clearInterval(tick);setUploadProgress(100);setTimeout(()=>{setUploadProgress(null);setShowCharForm(false);setCharDatasetFile(null);},800);}catch{clearInterval(tick);setUploadProgress(null);}return;}setShowCharForm(false);setCharDatasetFile(null);}}>保存</button><button className="secondary full-btn" onClick={()=>{setShowCharForm(false);setCharDatasetFile(null);setUploadProgress(null);}}>取消</button></div></div>}<div className="panel panel-compact">{isAdmin && <><div className="panel-title"><Icon name="book" /><span>知识库</span></div><p className="meta">当前连接后端 API</p><div className="file-list">{knowledgeList.slice(0, 2).map((k) => <div key={k.id} className="file-item"><span>{k.original_filename}</span><em>{k.status}</em></div>)}</div>{isAdmin && <div style={{display:"flex",gap:"4px",marginTop:"6px",flexWrap:"wrap"}}><button className="conv-action-btn" onClick={()=>{setEditingCharId(null);setCharForm({name:"",domain:"",persona:"",prompt_template:""});setShowCharForm(true);}}>+ 新建角色</button>{selectedCharacter && <><button className="conv-action-btn" onClick={()=>{setEditingCharId(selectedCharacter.id);setCharForm({name:selectedCharacter.name,domain:selectedCharacter.domain,persona:selectedCharacter.persona,prompt_template:selectedCharacter.prompt_template||""});setShowCharForm(true);}}>编辑</button><button className="conv-action-btn delete" onClick={()=>{if(confirm("确定删除该角色？"))onDeleteCharacter(selectedCharacter.id);}}>删除</button><button className="conv-action-btn" onClick={()=>datasetRef.current?.click()}>上传数据集</button><input ref={datasetRef} type="file" accept=".txt,.pdf,.md,.csv,.json,.jsonl" hidden onChange={(e)=>{const f=e.target.files?.[0];if(f)onUploadDataset(selectedCharacter.id,f);e.target.value="";}}/></>}</div>}</>}</div></> : <div className="drawer-strip"><button className="drawer-icon" onClick={() => setSidebarCollapsed(false)} aria-label="展开侧边栏"><Icon name="menu" /></button><button className="drawer-icon mini" onClick={checkHealth} aria-label="检测连接"><Icon name="refresh" /></button><button className="drawer-icon mini" onClick={onLogout} aria-label="退出登录"><Icon name="user" /></button></div>}</aside>;
  const renderConvList = (list) => list.map((item) => <ConversationItemCard key={item.id} item={item} isActive={item.id === activeConversationId} onSelect={onSelectConversation} onDelete={onDeleteConversation} onRename={onRenameConversation} />);
  const rightSidebar = <aside className="sidebar right-sidebar"><div className="sidebar-top"><span className="badge ok">会话</span></div><div className="panel panel-compact conversation-panel"><div className="panel-title"><Icon name="book" /><span>新对话</span></div><button type="button" className="full-btn new-chat-btn" onClick={onCreateConversation}>+ 新对话</button><div className="conversation-group-title"><span>最近会话</span><span>{recentConversations.length}</span></div><div className="conversation-list">{recentConversations.length === 0 ? <div className="conversation-empty">还没有保存的对话记录</div> : renderConvList(recentConversations)}</div>{olderConversations.length > 0 ? <><div className="conversation-group-title"><span>更早记录</span><span>{olderConversations.length}</span></div><div className="conversation-list">{renderConvList(olderConversations)}</div></> : null}</div></aside>;
  return <div className={`layout ${sidebarCollapsed ? "collapsed" : ""}`}>{leftSidebar}<main className="chat-main"><div className="chat-stack"><header className="chat-head"><div className="chat-head-left"><div className="avatar-mark">{selectedCharacter?.name?.slice(0, 1) || "R"}</div><div><h2>{selectedCharacter?.name || "未选择角色"}</h2><p>{selectedCharacter?.domain || "请选择角色开始会话"}</p></div></div><button className="secondary icon-text-btn" type="button" onClick={loadHistory}><Icon name="refresh" />刷新历史</button><button className="secondary icon-text-btn" type="button" onClick={onExportConversation}><Icon name="download" />导出</button><button className="secondary icon-text-btn" type="button" onClick={()=>setShowSearch(v=>!v)}><Icon name="search" />搜索</button>{isAdmin && <button className="secondary icon-text-btn" type="button" onClick={openDashboard}><Icon name="api" />仪表盘</button>}</header>{showSearch && <div className="search-overlay" style={{padding:"10px 18px",background:"var(--card-bg)",border:"1px solid var(--card-border)",borderRadius:16,animation:"panelEnter .2s"}}><div style={{display:"flex",gap:8,alignItems:"center"}}><input value={searchKeyword} onChange={e=>handleSearchInput(e.target.value)} placeholder="搜索历史消息…" style={{flex:1,marginTop:0}} autoFocus /><button className="ghost icon-btn" onClick={()=>{setShowSearch(false);setSearchKeyword("");setSearchResults([]);}}><Icon name="close" /></button></div>{searching && <p className="meta" style={{marginTop:6}}>搜索中…</p>}{!searching && searchResults.length > 0 && <div style={{maxHeight:240,overflowY:"auto",marginTop:6}}>{searchResults.map(r=><div key={r.message_id} className="conversation-item" style={{cursor:"pointer",marginBottom:4}} onClick={()=>{onSelectConversation(r.conversation_id);setShowSearch(false);setSearchKeyword("");setSearchResults([]);}}><div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:2}}>会话 #{r.conversation_id} · {r.created_at?.slice(0,16)}</div><div style={{fontSize:12}}><strong>Q:</strong> {r.user_message?.slice(0,60)}</div><div style={{fontSize:11,color:"var(--text-secondary)"}}><strong>A:</strong> {r.ai_reply?.slice(0,80)}</div></div>)}</div>}{!searching && searchKeyword && searchResults.length === 0 && <p className="meta" style={{marginTop:6}}>无匹配结果</p>}</div>}<section className="chat-body"><div className="chat-scroll-area">{loadingHistory ? <div className="loading-stack"><div className="skeleton" /><div className="skeleton short" /><div className="skeleton" /></div> : messages.length === 0 ? <div className="empty empty-card">还没有消息，输入问题开始第一轮对话。</div> : messages.map((m, idx) => <article key={`${idx}-${m.role}`} className={`bubble ${m.role} bubble-enter`} style={{ animationDelay: `${Math.min(idx, 12) * 70}ms` }}><div className="bubble-top"><span className="bubble-role">{m.role === "assistant" ? (m.ragUsed ? "AI + 向量库检索" : "AI 回复") : "你"}</span><time>{formatTime(m.time)}</time></div><p>{m.text || (m.role === "assistant" ? "正在思考(" + (thinkingMs / 1000).toFixed(2) + "s)" : "")}</p></article>)}<div ref={chatEndRef} /></div></section><footer className="chat-foot"><div className="input-shell"><textarea ref={textareaRef} rows={1} value={question} onChange={(e) => { setQuestion(e.target.value); resizeTextarea(); }} placeholder="输入你的问题，回车发送（Shift+Enter 换行）" onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } }} /><button className="send-btn" disabled={busy || streaming} onClick={sendMessage}>{streaming ? "思考中..." : <><Icon name="send" />发送</>}</button></div></footer></div></main>{rightSidebar}{showDashboard && <div className="dashboard-overlay" style={{position:"fixed",inset:0,zIndex:999,background:"rgba(0,0,0,.45)",display:"grid",placeItems:"center"}} onClick={()=>setShowDashboard(false)}><div style={{background:"var(--card-bg)",border:"1px solid var(--card-border)",borderRadius:20,padding:24,width:"min(90vw,720px)",maxHeight:"80vh",overflowY:"auto",animation:"panelEnter .25s"}} onClick={e=>e.stopPropagation()}><div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:16}}><h2 style={{margin:0}}>管理员仪表盘</h2><button className="ghost icon-btn" onClick={()=>setShowDashboard(false)}><Icon name="close" /></button></div>{dashStats && <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(120px,1fr))",gap:10,marginBottom:16}}>{[["用户",dashStats.total_users],["会话",dashStats.total_conversations],["消息",dashStats.total_messages],["角色",dashStats.total_characters],["知识库",dashStats.total_knowledge]].map(([label,val])=><div key={label} style={{background:"var(--panel-bg)",border:"1px solid var(--panel-border)",borderRadius:14,padding:"12px 14px",textAlign:"center"}}><div style={{fontSize:22,fontWeight:800,color:"#2f66ff"}}>{val}</div><div style={{fontSize:11,color:"var(--text-secondary)",marginTop:2}}>{label}</div></div>)}</div>}<div style={{marginBottom:12}}><h3 style={{margin:"0 0 6px",fontSize:14}}>用户列表</h3><div style={{maxHeight:140,overflowY:"auto"}}><table style={{width:"100%",fontSize:11,borderCollapse:"collapse"}}><thead><tr style={{textAlign:"left",color:"var(--text-secondary)"}}><th style={{padding:"3px 6px"}}>ID</th><th style={{padding:"3px 6px"}}>账号</th><th style={{padding:"3px 6px"}}>昵称</th><th style={{padding:"3px 6px"}}>注册时间</th></tr></thead><tbody>{dashUsers.map(u=><tr key={u.id} style={{borderTop:"1px solid var(--panel-border)"}}><td style={{padding:"3px 6px"}}>{u.id}</td><td style={{padding:"3px 6px"}}>{u.account}</td><td style={{padding:"3px 6px"}}>{u.nickname||"-"}</td><td style={{padding:"3px 6px"}}>{u.created_at?.slice(0,16)}</td></tr>)}</tbody></table></div></div><div style={{marginBottom:12}}><h3 style={{margin:"0 0 6px",fontSize:14}}>最近会话</h3><div style={{maxHeight:140,overflowY:"auto"}}><table style={{width:"100%",fontSize:11,borderCollapse:"collapse"}}><thead><tr style={{textAlign:"left",color:"var(--text-secondary)"}}><th style={{padding:"3px 6px"}}>ID</th><th style={{padding:"3px 6px"}}>用户</th><th style={{padding:"3px 6px"}}>标题</th><th style={{padding:"3px 6px"}}>更新</th></tr></thead><tbody>{dashConvs.map(c=><tr key={c.id} style={{borderTop:"1px solid var(--panel-border)"}}><td style={{padding:"3px 6px"}}>{c.id}</td><td style={{padding:"3px 6px"}}>{c.user_id}</td><td style={{padding:"3px 6px"}}>{c.title?.slice(0,20)||"-"}</td><td style={{padding:"3px 6px"}}>{c.updated_at?.slice(0,16)}</td></tr>)}</tbody></table></div></div><div><h3 style={{margin:"0 0 6px",fontSize:14}}>知识库文件</h3><div style={{maxHeight:140,overflowY:"auto"}}><table style={{width:"100%",fontSize:11,borderCollapse:"collapse"}}><thead><tr style={{textAlign:"left",color:"var(--text-secondary)"}}><th style={{padding:"3px 6px"}}>ID</th><th style={{padding:"3px 6px"}}>角色</th><th style={{padding:"3px 6px"}}>文件名</th><th style={{padding:"3px 6px"}}>状态</th></tr></thead><tbody>{dashKnowledge.map(k=><tr key={k.id} style={{borderTop:"1px solid var(--panel-border)"}}><td style={{padding:"3px 6px"}}>{k.id}</td><td style={{padding:"3px 6px"}}>{k.character_id}</td><td style={{padding:"3px 6px"}}>{k.original_filename?.slice(0,30)}</td><td style={{padding:"3px 6px"}}>{k.status}</td></tr>)}</tbody></table></div></div></div></div>}</div>;
}

/**
 * App 根组件：整个应用的入口。
 * 管理全局状态（登录状态、角色列表、会话列表、消息等），
 * 根据登录状态决定显示登录页还是聊天页。
 */
export default function App() {
  const [apiBase, setApiBase] = useState(() => localStorage.getItem("api_base") || defaultApiBase);
  const [status, setStatus] = useState("未检测");
  const [token, setToken] = useState(localStorage.getItem("token") || "");
  const [userId, setUserId] = useState(Number(localStorage.getItem("user_id") || 0));
  const [characters, setCharacters] = useState([]);
  const [selectedCharacterId, setSelectedCharacterId] = useState(0);
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [knowledgeList, setKnowledgeList] = useState([]);
  const [latestKnowledge, setLatestKnowledge] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [activeConversationId, setActiveConversationId] = useState(() => Number(localStorage.getItem("active_conversation_id") || 0));
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [toast, setToast] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [darkMode, setDarkMode] = useState(() => localStorage.getItem("theme") === "dark");
  const userCoordsRef = useRef(null); // { latitude, longitude } 浏览器定位坐标
  const chatEndRef = useRef(null);
  useEffect(() => { document.documentElement.setAttribute("data-theme", darkMode ? "dark" : "light"); localStorage.setItem("theme", darkMode ? "dark" : "light"); }, [darkMode]);
  function toggleDarkMode() { setDarkMode(v => !v); }
  const selectedCharacter = useMemo(() => characters.find((c) => c.id === Number(selectedCharacterId)), [characters, selectedCharacterId]);
  useEffect(() => { const t = setTimeout(() => setToast(""), 2600); return () => clearTimeout(t); }, [toast]);
  useEffect(() => {
    if (!chatEndRef.current) return;
    chatEndRef.current.scrollIntoView({ behavior: streaming ? "auto" : "smooth", block: "end" });
  }, [messages, streaming]);
  useEffect(() => { localStorage.setItem("api_base", apiBase); }, [apiBase]);
  useEffect(() => { if (token) localStorage.setItem("token", token); if (userId) localStorage.setItem("user_id", String(userId)); }, [token, userId]);

  // 登录成功后自动请求浏览器定位权限
  useEffect(() => {
    if (!token || !userId) return;
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        userCoordsRef.current = { latitude: pos.coords.latitude, longitude: pos.coords.longitude };
        console.log("[定位成功]", userCoordsRef.current);
      },
      (err) => console.warn("[定位失败]", err.message),
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 }
    );
  }, [token, userId]);
  useEffect(() => { if (activeConversationId) localStorage.setItem("active_conversation_id", String(activeConversationId)); }, [activeConversationId]);

  /** 拼接完整的 API URL（如果设置了自定义 API 地址则使用之） */
  function apiUrl(path) {
    const base = resolveApiBase(apiBase);
    return base ? `${base}${path}` : path;
  }
  /** 检查后端服务是否在线 */
  async function checkHealth() {
    try {
      const data = await requestJson(apiUrl("/api/health"));
      setStatus(data.status === "ok" ? "在线" : "异常");
    } catch (e) {
      if (e.message === "BACKEND_HTML_RESPONSE") {
        setStatus("离线");
        return;
      }
      setStatus("离线");
      if (apiBase.trim()) setToast(e.message);
    }
  }
  /** 从后端加载角色列表 */
  async function loadCharacters(authToken = token) {
    try {
      const data = await requestJson(apiUrl("/api/characters"), {}, authToken);
      const list = Array.isArray(data) ? data : data?.data || [];
      setCharacters(list);
      if (list.length && !selectedCharacterId) setSelectedCharacterId(list[0].id);
      return list;
    } catch (e) {
      if (e.message === "BACKEND_HTML_RESPONSE") {
        setToast("后端接口未就绪，无法加载角色列表");
        return [];
      }
      throw e;
    }
  }
  /** 创建新角色（管理员操作） */
  async function onCreateCharacter(data) {
    try {
      const result = await requestJson(apiUrl("/api/characters"), { method: "POST", body: JSON.stringify(data) }, token);
      await loadCharacters();
      setToast("角色创建成功");
      return result;
    } catch (e) { setToast(e.message); }
  }
  /** 更新角色信息（管理员操作） */
  async function onUpdateCharacter(id, data) {
    try {
      await requestJson(apiUrl(`/api/characters/${id}`), { method: "PATCH", body: JSON.stringify(data) }, token);
      await loadCharacters();
      setToast("角色已更新");
    } catch (e) { setToast(e.message); }
  }
  /** 删除角色（管理员操作） */
  async function onDeleteCharacter(id) {
    try {
      await requestJson(apiUrl(`/api/characters/${id}`), { method: "DELETE" }, token);
      if (Number(selectedCharacterId) === id && characters.length > 1) {
        setSelectedCharacterId(characters.find(c => c.id !== id)?.id || 0);
      }
      await loadCharacters();
      await loadConversations();
      setActiveConversationId(0);
      setMessages([]);
      setToast("角色已删除");
    } catch (e) { setToast(e.message); }
  }
  /** 上传数据集文件（管理员操作） */
  async function onUploadDataset(characterId, file) {
    try {
      const formData = new FormData();
      formData.append("file", file);
      const result = await requestJson(apiUrl(`/api/characters/${characterId}/dataset`), { method: "POST", body: formData }, token);
      setToast(`数据集已清洗：${result.cleaned_chars} 字`);
    } catch (e) { setToast(e.message); }
  }
  /** 搜索历史消息 */
  async function onSearchMessages(keyword) {
    try {
      const data = await requestJson(apiUrl(`/api/chat/search?keyword=${encodeURIComponent(keyword)}&limit=30`), {}, token);
      return data?.data || [];
    } catch (e) { setToast(e.message); return []; }
  }
  async function fetchAdminStats() {
    try {
      return await requestJson(apiUrl("/api/admin/stats"), {}, token);
    } catch (e) { setToast(e.message); return null; }
  }
  async function fetchAdminUsers() {
    try {
      return await requestJson(apiUrl("/api/admin/users"), {}, token);
    } catch (e) { setToast(e.message); return []; }
  }
  async function fetchAdminConversations() {
    try {
      return await requestJson(apiUrl("/api/admin/conversations"), {}, token);
    } catch (e) { setToast(e.message); return []; }
  }
  async function fetchAdminKnowledge() {
    try {
      return await requestJson(apiUrl("/api/admin/knowledge"), {}, token);
    } catch (e) { setToast(e.message); return []; }
  }
  /** 用户注册 */
  async function onRegister(payload) {
    try {
      await requestJson(apiUrl("/api/auth/register"), { method: "POST", body: JSON.stringify(payload) });
    } catch (e) {
      if (e.message === "BACKEND_HTML_RESPONSE") {
        throw new Error("后端接口未启动，无法注册");
      }
      throw e;
    }
  }
  /** 用户登录：验证成功后保存 token 并加载角色列表 */
  async function onLogin(payload) {
    try {
      const data = await requestJson(apiUrl("/api/auth/login"), { method: "POST", body: JSON.stringify(payload) });
      const nextToken = data?.access_token || data?.data?.access_token || "";
      const nextUserId = data?.user_id || data?.data?.user_id || 0;
      if (!nextToken || !nextUserId) throw new Error("登录响应缺少 token 或 user_id");
      setToken(nextToken);
      setUserId(nextUserId);
      await loadCharacters(nextToken);
      try { const me = await requestJson(apiUrl("/api/auth/me"), {}, nextToken); setIsAdmin(!!me?.is_admin); } catch { setIsAdmin(false); }
      setToast("登录成功");
    } catch (e) {
      if (e.message === "BACKEND_HTML_RESPONSE") {
        throw new Error("后端接口未启动，无法登录");
      }
      throw e;
    }
  }
  /** 退出登录：清除所有本地状态和 localStorage */
  function onLogout() { setToken(""); setUserId(0); setIsAdmin(false); localStorage.removeItem("token"); localStorage.removeItem("user_id"); setMessages([]); setLatestKnowledge([]); setConversations([]); setActiveConversationId(0); }
  async function onCreateConversation() { await createConversationRecord(); }
  function onSelectConversation(id) { const target = conversations.find((item) => item.id === id); if (!target) return; setActiveConversationId(id); setMessages([]); setLatestKnowledge([]); loadHistory(id); }

  async function onDeleteConversation(conversationId) {
    try {
      await requestJson(apiUrl(`/api/chat/conversations/${conversationId}?user_id=${userId}`), { method: "DELETE" }, token);
      setConversations((prev) => prev.filter((c) => c.id !== conversationId));
      if (activeConversationId === conversationId) { setActiveConversationId(0); setMessages([]); setLatestKnowledge([]); }
      setToast("对话已删除");
    } catch (e) { setToast(e.message); }
  }

  async function onRenameConversation(conversationId, newTitle) {
    try {
      await requestJson(apiUrl(`/api/chat/conversations/${conversationId}`), { method: "PATCH", body: JSON.stringify({ user_id: userId, title: newTitle }) }, token);
      setConversations((prev) => prev.map((c) => c.id === conversationId ? { ...c, title: newTitle } : c));
      setToast("已重命名");
    } catch (e) { setToast(e.message); }
  }

  /** 从后端加载当前角色的会话列表 */
  async function loadConversations(characterId = selectedCharacterId) {
    if (!userId) return [];
    try {
      const data = await requestJson(apiUrl(`/api/chat/conversations?user_id=${userId}${characterId ? `&character_id=${characterId}` : ""}`), {}, token);
      const list = (data?.data || []).map((item) => ({ id: item.id, title: item.title, preview: item.preview, updatedAt: item.updated_at, updatedAtLabel: formatTime(item.updated_at), character_id: item.character_id }));
      setConversations(list);
      if (!activeConversationId && list.length) setActiveConversationId(list[0].id);
      return list;
    } catch (e) {
      if (e.message === "BACKEND_HTML_RESPONSE") return [];
      setToast(e.message);
      return [];
    }
  }

  /** 加载指定会话的历史消息 */
  async function loadHistory(conversationId = activeConversationId) {
    if (!userId || !conversationId) return [];
    setLoadingHistory(true);
    try {
      const data = await requestJson(apiUrl(`/api/chat/history?user_id=${userId}&conversation_id=${conversationId}&limit=50`), {}, token);
      const history = (data?.data || []).flatMap((item) => [{ role: "user", text: item.user_message, time: item.created_at }, { role: "assistant", text: item.ai_reply, time: item.created_at, ragUsed: !!item.rag_used }]);
      setMessages(history);
      return history;
    } catch (e) {
      if (e.message === "BACKEND_HTML_RESPONSE") return [];
      setToast(e.message);
      return [];
    } finally { setLoadingHistory(false); }
  }

  /** 加载当前角色的知识文档列表 */
  async function loadKnowledge() {
    if (!selectedCharacterId) return;
    try {
      const data = await requestJson(apiUrl(`/api/knowledge/list?character_id=${selectedCharacterId}`), {}, token);
      setKnowledgeList(data?.data || []);
    } catch (e) {
      if (e.message === "BACKEND_HTML_RESPONSE") return;
      setToast(e.message);
    }
  }

  /** 在后端创建一条新的会话记录 */
  async function createConversationRecord(seedText = "") {
    try {
      const title = seedText ? seedText.slice(0, 18) : "新对话";
      const conversation = await requestJson(apiUrl("/api/chat/conversations"), { method: "POST", body: JSON.stringify({ user_id: userId, character_id: Number(selectedCharacterId), question: title }) }, token);
      const item = conversation?.data;
      if (item?.id) {
        const record = { id: item.id, title: item.title, preview: item.preview, updatedAt: item.updated_at, updatedAtLabel: formatTime(item.updated_at), character_id: item.character_id };
        setConversations((prev) => [record, ...prev.filter((v) => v.id !== record.id)]);
        setActiveConversationId(item.id);
        setMessages([]);
        setLatestKnowledge([]);
        setQuestion("");
        return item.id;
      }
      throw new Error("创建新对话失败，请检查后端返回");
    } catch (e) {
      if (e.message === "BACKEND_HTML_RESPONSE") throw new Error("后端接口未启动，无法创建新对话");
      throw e;
    }
  }

  /** 本地同步会话的预览文本和更新时间（避免重新请求后端） */
  function syncConversation(conversationId, messagesList, previewText = "") {
    if (!conversationId) return;
    const lastText = previewText || messagesList[messagesList.length - 1]?.text || "";
    const now = new Date().toISOString();
    setConversations((prev) => prev.map((item) => item.id === conversationId ? { ...item, preview: lastText, updatedAt: now, updatedAtLabel: formatTime(now), title: item.title || (lastText ? lastText.slice(0, 18) : "新对话") } : item));
  }

  /** 发送消息：通过 SSE 流式接收 AI 回复（打字机效果） */
  async function sendMessage() {
    if (!userId || !selectedCharacterId || !question.trim() || busy || streaming) return;
    const prompt = question.trim();
    const userMessage = { role: "user", text: prompt, time: new Date().toISOString() };
    try {
      setBusy(true);
      setQuestion("");
      setMessages((prev) => [...prev, userMessage]);
      setStreaming(true);
      const assistantMessage = { role: "assistant", text: "", time: new Date().toISOString() };
      setMessages((prev) => [...prev, assistantMessage]);

      let conversationId = activeConversationId;
      let ragUsed = false;
      const coords = userCoordsRef.current || {};
      const body = JSON.stringify({ user_id: userId, character_id: Number(selectedCharacterId), question: prompt, conversation_id: conversationId || 0, latitude: coords.latitude || null, longitude: coords.longitude || null });
      const headers = { "Content-Type": "application/json" };
      if (token) headers.Authorization = `Bearer ${token}`;
      const response = await fetch(apiUrl("/api/chat/stream"), { method: "POST", headers, body });
      if (!response.ok) {
        const errData = await response.json().catch(() => null);
        throw new Error(errData?.detail || `请求失败：${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let fullText = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6).trim();
          if (payload === "[DONE]") continue;
          try {
            const evt = JSON.parse(payload);
            if (evt.conversation_id && !conversationId) {
              conversationId = evt.conversation_id;
              setActiveConversationId(conversationId);
            }
            if (evt.rag_used !== undefined) {
              ragUsed = evt.rag_used;
              setMessages((prev) => {
                const next = [...prev];
                const idx = next.length - 1;
                if (idx >= 0 && next[idx].role === "assistant") next[idx] = { ...next[idx], ragUsed };
                return next;
              });
            }
            if (evt.chunk) {
              fullText += evt.chunk;
              const captured = fullText;
              setMessages((prev) => {
                const next = [...prev];
                const idx = next.length - 1;
                if (idx >= 0 && next[idx].role === "assistant") next[idx] = { ...next[idx], text: captured, ragUsed };
                return next;
              });
            }
            if (evt.replace) {
              fullText = evt.replace;
              const captured = fullText;
              setMessages((prev) => {
                const next = [...prev];
                const idx = next.length - 1;
                if (idx >= 0 && next[idx].role === "assistant") next[idx] = { ...next[idx], text: captured, ragUsed };
                return next;
              });
            }
          } catch { /* skip malformed */ }
        }
      }

      syncConversation(conversationId, [...messages, userMessage, { ...assistantMessage, text: fullText }], prompt);
      await Promise.all([loadKnowledge(), loadConversations()]);
    } catch (e) {
      setToast(e.message === "BACKEND_HTML_RESPONSE" ? "后端接口未启动，无法发送消息" : e.message);
    } finally {
      setStreaming(false);
      setBusy(false);
    }
  }

  /** 导出当前会话为 Markdown 文件并触发浏览器下载 */
  async function onExportConversation() {
    if (!activeConversationId) { setToast("请先选择一个会话"); return; }
    try {
      const headers = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      const resp = await fetch(apiUrl(`/api/chat/export?user_id=${userId}&conversation_id=${activeConversationId}`), { headers });
      if (!resp.ok) throw new Error("导出失败");
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `conversation_${activeConversationId}.md`;
      a.click();
      URL.revokeObjectURL(url);
      setToast("导出成功");
    } catch (e) { setToast(e.message); }
  }

  useEffect(() => {
    const base = resolveApiBase(apiBase);
    if (apiBase.trim() && !base) {
      setStatus("未检测");
      return;
    }
    checkHealth();
    if (token) {
      loadCharacters().catch((e) => setToast(e.message));
      loadConversations().catch(() => {});
      loadKnowledge();
      requestJson(apiUrl("/api/auth/me"), {}, token).then((me) => setIsAdmin(!!me?.is_admin)).catch(() => setIsAdmin(false));
      if (activeConversationId) loadHistory(activeConversationId);
    }
  }, []);
  useEffect(() => {
    if (token) {
      setActiveConversationId(0);
      setMessages([]);
      loadConversations().then((list) => {
        if (list && list.length) setActiveConversationId(list[0].id);
      }).catch(() => {});
      loadKnowledge();
    }
  }, [selectedCharacterId, userId, token]);
  useEffect(() => { if (activeConversationId) loadHistory(activeConversationId); }, [activeConversationId]);

  useEffect(() => { const link = document.querySelector("link[rel='icon']"); if (link) link.href = (!token || !userId) ? "/favicon-login.svg" : "/favicon-chat.svg"; document.title = (!token || !userId) ? "RAG 角色扮演系统 - 登录" : "RAG 角色扮演系统"; }, [token, userId]);

  if (!token || !userId) return <AuthPage apiBase={apiBase} setApiBase={setApiBase} status={status} checkHealth={checkHealth} onLogin={onLogin} onRegister={onRegister} />;
  return <><ChatPage apiBase={apiBase} setApiBase={setApiBase} status={status} checkHealth={checkHealth} userId={userId} onLogout={onLogout} characters={characters} selectedCharacterId={selectedCharacterId} setSelectedCharacterId={setSelectedCharacterId} selectedCharacter={selectedCharacter} messages={messages} loadingHistory={loadingHistory} question={question} setQuestion={setQuestion} sendMessage={sendMessage} loadHistory={loadHistory} latestKnowledge={latestKnowledge} knowledgeList={knowledgeList} busy={busy} streaming={streaming} chatEndRef={chatEndRef} conversations={conversations} activeConversationId={activeConversationId} onCreateConversation={onCreateConversation} onSelectConversation={onSelectConversation} onDeleteConversation={onDeleteConversation} onRenameConversation={onRenameConversation} onExportConversation={onExportConversation} isAdmin={isAdmin} onCreateCharacter={onCreateCharacter} onUpdateCharacter={onUpdateCharacter} onDeleteCharacter={onDeleteCharacter} onUploadDataset={onUploadDataset} darkMode={darkMode} toggleDarkMode={toggleDarkMode} onSearchMessages={onSearchMessages} fetchAdminStats={fetchAdminStats} fetchAdminUsers={fetchAdminUsers} fetchAdminConversations={fetchAdminConversations} fetchAdminKnowledge={fetchAdminKnowledge} />{toast ? <div className="toast">{toast}</div> : null}</>;
}
