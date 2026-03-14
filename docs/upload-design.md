# 文件上传与展示设计方案

## 1. 现状分析

### 当前流程
```
拖拽文件 → 上传到服务器 → 返回 @file_xxx 提及 → 插入到输入框 → 发送
```

### 问题
- 用户看不到上传了什么，只能看到 `@file_xxx` 文本
- 无法预览/删除已拖入的文件
- 消息中的图片/文件需要点击才能查看
- 依赖 @ 符号提及，不直观

---

## 2. 目标流程

```
拖拽文件 → 本地预览(缩略图) → 用户确认 → 发送 → 消息中独立显示预览
```

---

## 3. UI 设计

### 3.1 输入框区域 (Chat Input)

#### 拖拽区域
- 整个聊天区域支持拖拽
- 拖入时显示半透明蓝色覆盖层 + 文字提示 "Drop files here"

#### 预览区域 (新增加)
位置：输入框上方，紧邻输入框

```
┌─────────────────────────────────────────────────┐
│  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │  🖼️     │  │  🖼️     │  │  📄     │   ✕    │
│  │ thumb1  │  │ thumb2  │  │ file.pdf│        │
│  │  120x120│  │ 120x120 │  │         │        │
│  └─────────┘  └─────────┘  └─────────┘        │
│                                                  │
│  ┌──────────────────────────────────────────┐  │
│  │ Type your message...                      │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

#### 预览卡片元素
| 元素 | 说明 |
|------|------|
| 缩略图 | 图片显示 120x120 缩略图，cover 裁剪 |
| 文件图标 | 非图片显示文件类型图标 + 文件名 |
| 删除按钮 | 右上角 ✕，hover 显示 |
| 文件名 | 截断显示，最多 12 字符 |

#### 样式
- 网格布局，gap: 8px
- 最大显示 4 个预览，超出横向滚动
- 背景：dark mode 下 #1e293b，light mode 下 #f1f5f9
- 圆角：8px
- hover 时边框高亮 #3b82f6

---

### 3.2 消息显示区域

#### 用户消息 - 带附件

```
┌─────────────────────────────────────────────────┐
│  ┌──────────────────────────────────────────┐  │
│  │  这里是我的消息内容                        │  │
│  │                                          │  │
│  │  ┌─────────┐  ┌─────────┐              │  │
│  │  │  🖼️     │  │  📄     │              │  │
│  │  │ image1  │  │ doc.pdf │              │  │
│  │  └─────────┘  └─────────┘              │  │
│  └──────────────────────────────────────────┘  │
│                           10:30 AM              │
└─────────────────────────────────────────────────┘
```

#### 预览交互
- 点击缩略图 → 打开全屏预览弹窗
- 非图片文件 → 点击下载

#### 预览弹窗 (Lightbox)
```
┌─────────────────────────────────────────────────┐
│                                                 │
│                   ┌─────────┐                   │
│                   │         │                   │
│                   │  Image  │                   │
│                   │         │                   │
│                   └─────────┘                   │
│                                                 │
│              image.png (2.3 MB)                 │
│                     ✕                           │
└─────────────────────────────────────────────────┘
```

---

## 4. 功能模块设计

### 4.1 文件上传模块

```javascript
// 新增 state
state = {
  pendingFiles: [],  // 待发送的文件列表
}

// 拖拽处理
async function handleFileDrop(files) {
  for (const file of files) {
    // 本地创建预览
    const preview = await createLocalPreview(file);
    state.pendingFiles.push({
      file,
      preview,
      id: generateId()
    });
  }
  renderInputPreview();
}

// 创建本地预览
async function createLocalPreview(file) {
  if (file.type.startsWith('image/')) {
    return URL.createObjectURL(file);
  }
  return { type: 'file', name: file.name };
}
```

### 4.2 预览渲染模块

```javascript
function renderInputPreview() {
  const container = document.getElementById('input-preview-area');
  container.innerHTML = state.pendingFiles.map(f => `
    <div class="preview-item" data-id="${f.id}">
      ${f.preview.type === 'image' 
        ? `<img src="${f.preview.url}" />`
        : `<div class="file-icon">${getFileIcon(f.file.name)}</div>`
      }
      <button class="remove-btn" onclick="removePendingFile('${f.id}')">✕</button>
    </div>
  `).join('');
}
```

### 4.3 发送模块

```javascript
async function sendMessage() {
  const text = getInputText();
  
  // 上传所有待发送文件
  const attachments = [];
  for (const pf of state.pendingFiles) {
    const uploaded = await uploadFile(pf.file);
    attachments.push(uploaded);
  }
  
  // 发送消息（带附件信息）
  await sendToAgent({
    text,
    attachments  // 附件URL列表
  });
  
  // 清空
  state.pendingFiles = [];
  renderInputPreview();
}
```

### 4.4 消息渲染模块

```javascript
// 后端返回消息格式
message = {
  id: "msg_xxx",
  role: "user",
  content: "消息文本",
  attachments: [
    { type: "image", url: "https://...", name: "photo.jpg" },
    { type: "file", url: "https://...", name: "doc.pdf" }
  ]
}

// 前端渲染
function renderMessage(msg) {
  if (msg.attachments?.length) {
    return `
      <div class="message-content">${escapeHtml(msg.content)}</div>
      <div class="message-attachments">
        ${msg.attachments.map(a => renderAttachment(a)).join('')}
      </div>
    `;
  }
  // 无附件时保持原样
}
```

---

## 5. 移除 @ 符号依赖

### 5.1 需要移除的功能

| 功能 | 当前实现 | 改动 |
|------|----------|------|
| 文件上传 | 返回 @file_xxx | 移除，不再插入文本 |
| My Uploads | 插入 @file_xxx | 改为选择后直接添加到预览区 |

### 5.2 My Uploads 改造

```
点击 My Uploads → 弹出文件选择面板 → 选中文件 →
添加到 state.pendingFiles → 显示在预览区（不再插入 @ 文本）
```

---

## 6. 后端配合

### 6.1 上传接口 (已有)
```
POST /api/files/upload
→ 返回 { url, name, type, size }
```

### 6.2 消息格式改造
消息需要携带附件信息，格式：
```json
{
  "content": "用户消息",
  "attachments": [
    { "type": "image", "url": "...", "name": "..." },
    { "type": "file", "url": "...", "name": "..." }
  ]
}
```

---

## 7. 实施计划

### Phase 1: 输入框预览
- [ ] 创建输入框预览区域 DOM
- [ ] 拖拽时创建本地预览
- [ ] 实现预览卡片 UI（图片缩略图 + 文件图标）
- [ ] 实现删除功能

### Phase 2: 发送流程
- [ ] 改造发送逻辑，先上传文件再发消息
- [ ] 消息包含附件信息

### Phase 3: 消息展示
- [ ] 消息渲染支持 attachments
- [ ] 实现图片/文件独立显示
- [ ] 实现点击预览 Lightbox

### Phase 4: 清理
- [ ] 移除 @file_ 文本插入逻辑
- [ ] 改造 My Uploads 选择逻辑

---

## 8. 兼容性

- 图片: jpg, png, gif, webp, svg
- 文件: pdf, zip, txt, md, json 等
- 最大文件大小: 50MB
- 最大同时预览数: 10 个

---

## 9. 样式参考

```css
/* 预览区域 */
.input-preview-area {
  display: grid;
  grid-template-columns: repeat(auto-fill, 100px);
  gap: 8px;
  padding: 8px;
  background: var(--bg-secondary);
  border-radius: 8px;
}

/* 预览卡片 */
.preview-item {
  position: relative;
  width: 100px;
  height: 100px;
  border-radius: 8px;
  overflow: hidden;
  border: 2px solid transparent;
  transition: border-color 0.2s;
}

.preview-item:hover {
  border-color: var(--accent);
}

/* 删除按钮 */
.preview-item .remove-btn {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 20px;
  height: 20px;
  background: rgba(0,0,0,0.6);
  border-radius: 50%;
  color: white;
  opacity: 0;
  transition: opacity 0.2s;
}

.preview-item:hover .remove-btn {
  opacity: 1;
}
```

---

## 10. 待讨论

1. ~~是否保留 @ 符号的其他用途（@agent）？~~ ✅ 保留，用于 @agent 和未来 @多个agent
2. ~~文件选择面板是否需要重新设计？~~ ✅ 保持不变，选择后显示预览
3. ~~是否需要进度条显示上传进度？~~ ✅ 需要

---

## 11. 确认的决策

| 项目 | 决定 |
|------|------|
| @ 符号 | 保留，仅用于 @agent |
| 文件选择面板 | 保持现有 UI，选择后显示预览 |
| 上传进度条 | 需要，显示在预览卡片上 |

---

## 12. 进度条设计

### 预览卡片 + 进度条
```
┌────────────────────────┐
│  ┌──────────────┐     │
│  │     🖼️       │  ✕  │
│  │   (thumb)    │     │
│  │              │     │
│  │ ████████░░░  │     │
│  │    80%       │     │
│  └──────────────┘     │
│  filename.jpg         │
└────────────────────────┘
```

### 进度条样式
- 高度: 4px
- 背景: #e2e8f0
- 进度: #3b82f6
- 圆角: 2px
