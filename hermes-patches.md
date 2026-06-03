# Hermes 本地补丁记录

> 这些是对 `~/.hermes/hermes-agent/` 的本地修改，维护在 `local-patches` 分支上。
>
> **工作流**：
> 1. 所有 patch 以独立 commit 存在于 `local-patches` 分支（`git log --oneline local-patches ^main`）
> 2. `hermes update` 更新 `main` 后，在 `local-patches` 上 reset + 手动 re-apply
> 3. Fork 备份：`git push fork local-patches`（remote name: `fork`，即 `github-hermes:xzmzm/hermes-agent.git`）
> 4. 旧分支保留为 `local-patches-archive`（完整历史，含废弃 commit）
>
> ⚠️ `pip install -e .` **必须**用 `source venv/bin/activate`，裸 `pip` 默认走 miniconda3 会静默挂起。
> ⚠️ GitHub push 需要 VPN 关闭（Mullvad 下 HTTPS 超时）。

---

## Active Patches (10 patch commits + 1 fix commit + docs on `local-patches`)

Rebased on upstream `6a72af044` (v0.15.1, +881 commits from `1e71b7180`)。
每个 patch 修改不同文件，零文件重叠。

### Patch #1 — 对话标题 reasoning budget retry

**Commit:** `1029b8b9d`
**File:** `agent/title_generator.py`（`generate_title()` L54-75）

#### 演进

1. **初始问题**: `max_tokens=30`，经常截断。我们本地改到 500。
2. **上游采纳**: commit `f41031af` 将 `max_tokens` 改为 `500`，与我们的补丁一致。
3. **新问题 (2026-04-27)**: 500 对 reasoning model 仍不够——reasoning tokens 吃光全部 budget，`content: null` + `finish_reason: "length"`。
4. **最终修复**: 检测 `finish_reason == "length"` 且 content 为空时，翻倍 `max_tokens` 重试一次。

**逻辑**：第一次 500 tokens → 如果 content 非空或 finish_reason 非 `length`，直接用结果；否则 500→1000 重试。最多两次调用，正常无额外开销。

---

### Patch #2 — 主客户端 HERMES_DEFAULT_HEADERS + RooCode UA fallback

**Commit:** `375ae2e6a`
**File:** `agent/agent_init.py`（L656-670）
**Env:** `HERMES_DEFAULT_HEADERS`（JSON dict）

在 profile-level `default_headers` fallback 之后，读取 `HERMES_DEFAULT_HEADERS` 环境变量。未设置则 fallback 到 RooCode UA headers。

**⚠️ `User-Agent` 必须大写**——小写 `user-agent` 会作为独立 key 与 SDK 内部的大写版本共存，httpx 优先采用先遇到的大写版本，我们的会被忽略。

**`.env` 配置：**
```bash
HERMES_DEFAULT_HEADERS={"http-referer":"https://github.com/RooVetGit/Roo-Cline","User-Agent":"RooCode/3.53.0","x-title":"Roo Code"}
```

---

### Patch #4 — Background review 发送完整文本回复

**Commit:** `c756e2f4d`
**File:** `agent/background_review.py`（L498-537）

从 review_messages 中提取最后一条 assistant 文本响应，与 tool action summary 合并通过 `background_review_callback` 发送。

**之前**：只发 `💡 Skill 'xxx' created`
**之后**：
```
💾 Self-improvement review: Skill 'xxx' created

主人，银月已将值得保存的经验整理好了：……
```

---

### Patch #7 — 修复 `_is_ollama_glm_backend()` 误判本地代理

**Commit:** `63182c22d`
**File:** `run_agent.py`（L1040）

**问题**：`_is_ollama_glm_backend()` 最后用 `is_local_endpoint()` 做 fallback，导致 aichatproxy (localhost:8000) 等代理被误判为 Ollama 后端，触发 stop token 误报 + 自动 continuation。

**修复**：`return bool(self.base_url and is_local_endpoint(self.base_url))` → `return False`。显式 ollama/`:11434` 检查不受影响。

---

### Patch #8 — Telegram `/resume` 编号 + inline keyboard

**Commit:** `8da940f5b`
**Files:** `gateway/run.py` + `gateway/platforms/telegram.py`

**`gateway/run.py` — `_handle_resume_command()`**：
- 返回类型 `Optional[str]`（None = 已自行发送，不重复）
- 无参数时显示编号列表：`1. Title` `2. Title`（融合 upstream i18n `t()`）
- `/resume 1` 按编号恢复
- 标题匹配失败时 fallback 尝试 session ID 直接匹配
- Telegram 平台自动调 `adapter.send_resume_picker()` 发送 inline keyboard，返回 None

**`gateway/platforms/telegram.py`**：
- `send_resume_picker()` — `format_message()` + MARKDOWN_V2 + inline keyboard（每行 2 按钮，callback_data=`resume:<session_id>`）；失败 fallback 纯文本
- `resume:` callback handler — auth check → edit message 去按钮 → 合成 MessageEvent → dispatch 走正常 `/resume` 管道

**效果：**
```
用户: /resume
Bot:  [i18n 列表头]
      1. 研究量子计算
      2. KLSE Stock Analysis
      [1. 研究量子计算] [2. KLSE Stock Analysis]  ← 可点击
```

支持三种恢复：`/resume 1`（编号）、`/resume 研究量子计算`（标题）、点击按钮。

**⚠️ 踩坑笔记：**
1. **不要预转义标题** — `t()` + `format_message()` 内部已处理转义，手动 `_escape_mdv2()` 导致双重转义
2. **不要手动加 `\.`** — 用 `{idx}. ` 而非 `{idx}\\\. `，`format_message` 会自动转义
3. **不要绕过 `format_message`** — 直接用 `_escape_mdv2()` 会漏转义 backtick 等字符

---

### Patch #9 — `/model` 模型不在 listing 时 warn-but-accept

**Commit:** `f37b876aa`
**File:** `hermes_cli/models.py`（L3675）

**问题**：Z.AI Pro/Max 的 `glm-5` 在 coding endpoint 可用但不在 `/v1/models` listing 里，Hermes 直接报错拒绝。代码注释写着 "Accept anyway — Warn but allow" 但 `accepted` 字段写反了 `False`。

**修复**：`accepted: False, persist: False` → `accepted: True, persist: True, recognized: False`。

---

### Patch #11 — Cross-provider search 对 custom/localhost 开放

**Commit:** `0051e67f1`
**File:** `hermes_cli/model_switch.py`（L831）

删除 `detect_provider_for_model()` 调用前的 `and not is_custom` 条件。让 localhost/proxy 用户（如 aichatproxy）也能触发 cross-provider 模型搜索。

---

### Patch #13 — Auxiliary client HERMES_DEFAULT_HEADERS fallback

**Commit:** `71a3aa83f`
**File:** `agent/auxiliary_client.py`（6 处 fallback）

新增 `_get_hermes_default_headers()` helper（读 env JSON，fallback RooCode UA）。注入到 6 个 client 创建路径：

1. `_resolve_api_key_provider` — pool path
2. `_resolve_api_key_provider` — credentials path
3. `_try_custom_endpoint`
4. `_to_async_client`
5. `resolve_provider_client`
6. `_refresh_nous_auxiliary_client`

每处都只在 `default_headers` 未设置时才 fallback。与 patch #2 互补（#2 管主客户端，#13 管 auxiliary 客户端）。

---

### Patch #14 — pooled `openai-codex` credentials honor `HERMES_CODEX_BASE_URL`

**Commit:** `92d8ba235`
**File:** `hermes_cli/runtime_provider.py`（`_resolve_runtime_from_pool_entry()` L309-312）
**Env:** `HERMES_CODEX_BASE_URL`

#### 问题

`openai-codex` 主对话在某些路径会从 credential pool entry 解析 runtime credentials。该路径之前只用 `entry.runtime_base_url` / `entry.base_url`，为空时直接 fallback 到：

```text
https://chatgpt.com/backend-api/codex
```

因此即使 `.env` 中设置：

```bash
HERMES_CODEX_BASE_URL=http://localhost:8000/v1
```

也可能被 pooled credentials 路径忽略，导致请求不走本地 aichatproxy。

#### 修复

在 `provider == "openai-codex"` 分支内读取 profile-aware `get_env_value("HERMES_CODEX_BASE_URL")`，并优先于 pool entry/default 使用：

```diff
+        env_base_url = str(get_env_value("HERMES_CODEX_BASE_URL") or "").strip().rstrip("/")
-        base_url = base_url or DEFAULT_CODEX_BASE_URL
+        base_url = env_base_url or base_url or DEFAULT_CODEX_BASE_URL
```

#### 验证

```text
provider= openai-codex
api_mode= codex_responses
base_url= http://localhost:8000/v1
api_key_present= True
```

---

### Patch #16 — image_gen/openai-codex: route image generation through aichatproxy

**Commit:** pending
**File:** `plugins/image_gen/openai-codex/__init__.py`
**Env:** `HERMES_CODEX_BASE_URL`

#### 问题

Hermes 的 `image_gen/openai-codex` plugin 硬编码 `_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"`，导致 image generation 请求绕过 localhost aichatproxy，而 chat/vision 的 Codex 请求正常经过 proxy。

#### 修复

```diff
+import os

-_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
+_CODEX_BASE_URL = os.environ.get(
+    "HERMES_CODEX_BASE_URL",
+    "https://chatgpt.com/backend-api/codex",
+).rstrip("/")
```

#### 依赖

aichatproxy 需要有 `gpt-5.4` → Codex backend 的 route（`supports_responses_api: true`），因为 image plugin 的 host model 是 `gpt-5.4`。

---

### Patch #15 — `/quota` 通过本地 aichatproxy 查询当前模型额度

**Commit:** pending
**Files:** `hermes_cli/commands.py`, `hermes_cli/quota.py`, `cli.py`, `gateway/run.py`
**Local service:** `http://localhost:8000/api/quota`（由 `~/prj/aichatproxy` 提供）

新增 `/quota` slash command：

- CLI：`/quota [model]`
- Gateway/Telegram：`/quota [model]`
- 默认使用当前模型；可传 model 覆盖。
- Hermes 只负责解析当前 runtime credentials，并把 Bearer token 传给本地 aichatproxy。
- aichatproxy 根据 route 的 `upstream_url` / model / provider 判断 quota adapter。

首批支持：

- Z.ai / GLM：`https://api.z.ai/api/monitor/usage/quota/limit` 或中国站 `https://open.bigmodel.cn/api/monitor/usage/quota/limit`
- DeepSeek：`https://api.deepseek.com/user/balance`
- Xiaomi MiMo：`https://platform.xiaomimimo.com/api/v1/tokenPlan/usage`（实测需要 Xiaomi web-login cookie；仅 Token Plan API key 会返回 401）
- OpenAI Codex OAuth：`https://chatgpt.com/backend-api/wham/usage`，并透传 `ChatGPT-Account-Id` + `User-Agent: codex-cli`

**踩坑笔记：**

1. Codex 不是 `/backend-api/codex/usage`；该旧路径返回 HTML 403。Hermes 现有 `agent.account_usage` 使用的是 `/backend-api/wham/usage`。
2. Codex OAuth 必须优先使用 Hermes 当前刷新后的 Bearer token，不能只依赖 aichatproxy route 表里的静态 `api_key`。
3. 但 Z.ai / DeepSeek / Xiaomi 这类 API-key provider 必须使用 aichatproxy route 表中的 `api_key`，不能误用 Hermes 当前模型传过来的 Codex Bearer。
4. Codex `/wham/usage` 返回 primary/secondary 两个窗口：primary 是 5h，secondary 是 7d；渲染时要把 `reset_after_seconds` / `reset_at` 转成人能看的本地时间。
5. 本机环境存在坏的 `SSL_CERT_FILE`，httpx 客户端需 `trust_env=False`，否则创建 client 时会因证书路径不存在报 `FileNotFoundError`。
6. Xiaomi MiMo 的 console quota API 在 `/api/v1/tokenPlan/usage`，不是 SPA fallback `/tokenPlan/usage`；仅用 Token Plan API key 会返回 401，需要 web-login cookie。
7. 修改 aichatproxy 后不要随意重启服务；当前 Telegram 对话可能正走这个 proxy。若需要重启，告知用户，由用户自行操作。可用离线导入 + monkeypatch 验证 route/endpoint 选择。
8. Z.ai 的 `reset_at` 是毫秒级时间戳（如 `1781338292982`），渲染时需转成本地可读时间；Limits 详情里的 `reset_at` / `used_percent` 也要格式化，不能直接输出原始数字。
---

### Fix — Gateway provider failure 包含原始错误详情

**Commit:** `77b70d731`
**File:** `gateway/run.py`

Provider 返回错误时，Telegram 回复中包含原始 error detail（而不只是 generic message），方便排查。

---

---

## Discarded Patches (upstream 已实现或更好)

| 旧 # | 描述 | 原因 |
|------|------|------|
| ~~3~~ | zai stop token 误判修复 | 上游彻底删除了 `_detect_stop_misreports()` |
| ~~5~~ | 前台超时强制钳制 | 上游现在 reject + 建议 background mode |
| ~~6~~ | 中断后快速退出 agent loop | 上游在 `conversation_loop.py` 有 interrupt check |
| ~~10~~ | 429 硬限制直接反馈 | 上游有 `usage_limit_reached` 检测 + credential pool rotation |
| ~~12~~ | Background review cache-aware | 上游有 `_cached_system_prompt` copy + `set_thread_tool_whitelist` + session pinning，更优雅 |
| ~~16~~ | MEDIA 标签 .html/.htm 扩展名 | 上游 v0.15.1 重构 `MEDIA_DELIVERY_EXTS` 为共享 source of truth，已包含 `.html`/`.htm` |

---

## Dead code removed (pre-rebase 清理)

- `_escape_title_mdv2()` + `_MDV2_ESCAPE_RE` in `gateway/run.py` — 从未被调用，`format_message()` 处理所有 MarkdownV2 转义。

---

## Branch structure

| Branch | 用途 |
|--------|------|
| `main` | 上游最新 `6a72af044` (v0.15.1)，不做修改 |
| `local-patches` | 10 patch commits + 1 fix commit + docs，基于最新 main |
| `local-patches-archive` | 完整旧历史（27 commits，含废弃的 pre-v0.14 commits） |
| Tag `local-patches-pre-update-20260530` | v0.15.1 rebase 之前的 local-patches 快照 |
| Tag `local-patches-pre-update-20260523` | v0.14.x rebase 之前的 local-patches 快照 |
| Tag `local-patches-pre-v0.14` | v0.14.0 rebase 之前的快照 |

---

## Commit history

```
92d8ba235 patch-14: honor HERMES_CODEX_BASE_URL for pooled Codex credentials
2f07d25a3 docs: update hermes-patches.md for 2026-05-30 v0.15.1 rebase
dae98ff0b docs: update hermes-patches.md for 2026-05-23 rebase (new commit hashes, +2 fix commits)
77b70d731 fix(gateway): include raw error detail in provider failure Telegram replies
71a3aa83f patch-13: apply HERMES_DEFAULT_HEADERS to all auxiliary HTTP clients
0051e67f1 patch-11: allow cross-provider search for custom/localhost providers
f37b876aa patch-9: model not in API listing -> accepted with warning
8da940f5b patch-8: Telegram /resume with numbered sessions + inline keyboard buttons
63182c22d patch-7: fix _is_ollama_glm_backend() false positive on localhost/proxy endpoints
c756e2f4d patch-4: background review send full text + tool summary
375ae2e6a patch-2: read custom headers from HERMES_DEFAULT_HEADERS env var (JSON)
1029b8b9d patch-1: title generation reasoning budget retry
```

*最后更新: 2026-06-04（rebase on `6a72af044` v0.15.1, 10 patches + 1 pending, 6 discarded, +1 fix commit）*
