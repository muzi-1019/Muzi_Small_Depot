/**
 * 本文件的作用：前端应用的入口文件。
 * 负责将 React 应用挂载到 HTML 页面中的 #root 元素上。
 * React.StrictMode 会在开发模式下启用额外的检查和警告。
 */

import React from "react";              // React 核心库
import ReactDOM from "react-dom/client"; // React DOM 渲染器
import App from "./App";                 // 主应用组件
import "./styles.css";                   // 全局样式

// 将 App 组件渲染到页面上 id="root" 的 DOM 元素中
ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
