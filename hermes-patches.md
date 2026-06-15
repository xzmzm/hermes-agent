# Hermes 本地补丁记录

> 这些是对 `~/.hermes/hermes-agent/` 的本地修改，维护在 `local-patches` 分支上。
>
> **工作流**：
> 1. patch 维护在 `local-patches` 分支；历史上为独立 commit，2026-06-16 起合并为单个 squashed commit（详见底部 Commit history）
> 2. `hermes update` 更新 `main` 后：`git diff <merge-base>..local-patches -- '*.py' > /tmp/netpatch.diff` 生成净 diff，切到新 main 建临时分支 `git apply --3way /tmp/netpatch.diff`，仅极少数冲突需手解；逐 commit `rebase` 在 754+ commits 上会连锁冲突（patch-8 run.py 为典型），squashed reapply 更干净
> 3. Fork 备份：`git push fork local-patches`（remote name: `fork`，即 `github-hermes:xzmzm/hermes-agent.git`）
> 4. 旧分支保留为 `local-patches-archive`（完整历史，含废弃 commit）；每次更新前打 tag `local-patches-pre-update-<date>`
>
> ⚠️ `pip install -e .` **必须**用 `source venv/bin/activate`，裸 `pip` 默认走 miniconda3 会静默挂起。
> ⚠️ GitHub push 需要 VPN 关闭（Mullvad 下 HTTPS 超时）。

---

## Active Patches (14 numbered patch entries + fix/refactor commits + quota feature commits + docs on `local-patches`)

Rebased on upstream `60cc42e38`（v0.16.0+，+754 commits from `c3055d618` v0.16.0）。净 diff 经 `git apply --3way` 贴成 squashed commit `cd7c8ae37`。
每个 patch 尽量保持独立；`gateway/run.py` 目前含 quota dispatcher + Patch #18 auto-TTS dedup 修复。

### Patch #1 — 对话标题 reasoning budget retry

**Commit:** `30edebfed`
**File:** `agent/title_generator.py`（`generate_title()` L54-75）

#### 演进

1. **初始问题**: `max_tokens=30`，经常截断。我们本地改到 500。
2. **上游采纳**: commit `f41031af` 将 `max_tokens` 改为 `500`，与我们的补丁一致。
3. **新问题 (2026-04-27)**: 500 对 reasoning model 仍不够——reasoning tokens 吃光全部 budget，`content: null` + `finish_reason: "length"`。
4. **最终修复**: 检测 `finish_reason == "length"` 且 content 为空时，翻倍 `max_tokens` 重试一次。

**逻辑**：第一次 500 tokens → 如果 content 非空或 finish_reason 非 `length`，直接用结果；否则 500→1000 重试。最多两次调用，正常无额外开销。

---

### Patch #2 — 主客户端 HERMES_DEFAULT_HEADERS + RooCode UA fallback

**Commit:** `0db8c116e`
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

**Commit:** `ffb05ad7e`
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

**Commit:** `0d4ae64c8`
**File:** `run_agent.py`（L1040）

**问题**：`_is_ollama_glm_backend()` 最后用 `is_local_endpoint()` 做 fallback，导致 aichatproxy (localhost:8000) 等代理被误判为 Ollama 后端，触发 stop token 误报 + 自动 continuation。

**修复**：`return bool(self.base_url and is_local_endpoint(self.base_url))` → `return False`。显式 ollama/`:11434` 检查不受影响。

---

### Patch #8 — Telegram `/resume` 编号 + inline keyboard

**Commit:** `436f388b7`
**Files:** `gateway/platforms/telegram.py` + `gateway/slash_commands.py`

**`gateway/slash_commands.py` — `_handle_resume_command()`**（mixin，v0.16.0 从 run.py 提取）：
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

**Commit:** `420794042`
**File:** `hermes_cli/models.py`（L3675）

**问题**：Z.AI Pro/Max 的 `glm-5` 在 coding endpoint 可用但不在 `/v1/models` listing 里，Hermes 直接报错拒绝。代码注释写着 "Accept anyway — Warn but allow" 但 `accepted` 字段写反了 `False`。

**修复**：`accepted: False, persist: False` → `accepted: True, persist: True, recognized: False`。

---

### Patch #11 — Cross-provider search 对 custom/localhost 开放

**Commit:** `ffaf62ace`
**File:** `hermes_cli/model_switch.py`（L831）

删除 `detect_provider_for_model()` 调用前的 `and not is_custom` 条件。让 localhost/proxy 用户（如 aichatproxy）也能触发 cross-provider 模型搜索。

---

### Patch #13 — Auxiliary client HERMES_DEFAULT_HEADERS fallback

**Commit:** `63dc7d2d9`
**File:** `agent/auxiliary_client.py`（6 处 fallback）

新增 `_get_hermes_default_headers()` helper（读 env JSON，fallback RooCode UA）。注入到 6 个 client 创建路径：

1. `_resolve_api_key_provider` — pool path
2. `_resolve_api_key_provider` — credentials path
3. `_try_custom_endpoint`
4. `_to_async_client`
5. `resolve_provider_client`
6. `_refresh_nous_auxiliary_client`

每处都只在 `default_headers` 未设置时才 fallback。与 patch #2 互补（#2 管主客户端，#13 管 auxiliary 客户端）。

**v0.16.0 变更**：上游新增 `_apply_user_default_headers()`（读 config.yaml `model.default_headers`）。银月将两者合并：先 `_get_hermes_default_headers()` 设置默认值，再 `_apply_user_default_headers()` 叠加用户配置。

---

### Patch #14 — pooled `openai-codex` credentials honor `HERMES_CODEX_BASE_URL`

**Commit:** `a8dddc3ef`
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

### Patch #15 — `/quota` 通过本地 aichatproxy 查询当前模型额度

**Commit:** `f1f85b5b1` + `1439e318e`（修复 gateway /quota 读取 config.yaml）+ `b78979a16`（gateway 透传 runtime credentials）+ `6678b81a0`（显式 `/quota <model>` 解析 provider/key）
**Files:** `hermes_cli/commands.py`, `hermes_cli/quota.py`, `cli.py`, `gateway/run.py`, `gateway/slash_commands.py`
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
9. Gateway `/quota` 不能只读 config.yaml 后裸调 aichatproxy：Codex route 通常 `api_key` 为空，必须从 per-session `/model` override 或 `resolve_runtime_provider()` 取 fresh OAuth Bearer token，并作为 `api_key` 传给 `fetch_quota_async()`。否则 aichatproxy `/api/quota` 会返回 HTTP 400：`No API key/Bearer token available for selected route`。
10. 显式 `/quota <model>` 不能复用当前会话 provider/key。例如当前会话是 `openai-codex` 时，`/quota glm-5.1` 必须通过 `detect_provider_for_model()` 识别为 `zai`，再从 `resolve_api_key_provider_credentials("zai")` 取得 `GLM_API_KEY`（即便是 placeholder `a1a1a1`），让 aichatproxy 替换为真实 route key。否则会把 Codex OAuth token 发给 Z.AI quota endpoint，得到 401 `Could not parse your authentication token`。

---

### Patch #16 — image_gen/openai-codex: route image generation through aichatproxy

**Commit:** `70fa51eff`
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

### Fix — Gateway provider failure 包含原始错误详情

**Commit:** `89787893c`
**File:** `gateway/run.py`

Provider 返回错误时，Telegram 回复中包含原始 error detail（而不只是 generic message），方便排查。

> ⚠️ **v0.16.0 rebase 注意**：上游 main 已包含 `error_detail` 在回复中，此 patch 已被上游采纳。保留 commit 不影响。

### Refactor — 恢复干净上游 run.py/cli.py，删除冗余方法

**Commit:** `774bb12be`

v0.16.0 rebase 后，`gateway/run.py` 和 `cli.py` 保留了 40+ 个旧 `_handle_*` 方法（上游已迁移到 `GatewaySlashCommandsMixin` 和 `CLICommandsMixin`）。此 commit 将两个文件恢复为上游 main 版本，仅保留 3 行 quota dispatcher。效果：

- `gateway/run.py` vs main: +3 lines
- `cli.py` vs main: +3 lines
- 零删除上游代码，纯 additive

### Fix — 恢复 GatewayRunner mixin 继承

**Commit:** `b1144267f`

Rebase 时 `class GatewayRunner:` 丢失了上游新增的 `(GatewayKanbanWatchersMixin, GatewaySlashCommandsMixin)` 继承和对应 import，导致 `/resume` 报 `AttributeError`。已恢复。

---

### Patch #17 — `fetch_models()` 传入 config-resolved `base_url`

**Commit:** `6299f299a`
**Files:** `providers/base.py`, `hermes_cli/models.py`

**问题**：`.env` 里设置了 `GLM_BASE_URL=http://localhost:8000/zai/v1`（通过 aichatproxy 路由），但 `/model` picker 的 `fetch_models()` 只用 provider profile 硬编码的 `base_url`（`https://api.z.ai/api/coding/paas/v4`），导致 `/v1/models` 请求绕过 aichatproxy，返回空或错误，最终 fallback 到 2 个模型（glm-5, glm-4-9b）。

**修复**：
- `ProviderProfile.fetch_models()` 新增 `base_url` 参数，优先级：`models_url` > `caller base_url` > `self.base_url`
- `provider_model_ids()` 将 `.env` 解析的 `base_url` 传入 `fetch_models(base_url=base_url)`

---

### Patch #18 — `/voice tts` auto-TTS 去重只看当前轮

**Commit:** `ddbd9491e`
**Files:** `gateway/run.py`, `tests/gateway/test_voice_command.py`

#### 问题

`/voice tts` 会把当前 chat 的 voice mode 设为 `all`，文案为：

```text
Auto-TTS enabled.
All replies will include a voice message.
```

但 `GatewayRunner._should_send_voice_reply()` 的 dedup 逻辑扫描完整 `agent_messages` 历史：只要历史任意一轮 assistant 曾调用过 `text_to_speech` tool，之后所有普通文字回复都会被误判为“本轮已手动 TTS”，从而永久跳过自动语音。

#### 修复

将 `agent_result["history_offset"]` 传入 `_should_send_voice_reply()`，并在检查 `text_to_speech` tool calls 时只扫描当前轮消息切片：

```diff
+        current_turn_messages = (
+            agent_messages[history_offset:]
+            if history_offset and len(agent_messages) >= history_offset
+            else agent_messages
+        )
...
-            for msg in agent_messages
+            for msg in current_turn_messages
```

#### 语义

- 当前轮 agent 手动调用 `text_to_speech` → 自动 TTS 跳过，避免重复音频。
- 历史轮次曾调用 `text_to_speech` → 不影响当前轮 `/voice tts`。
- 与同文件中媒体自动追加使用 `history_offset` 只处理当前轮的做法保持一致。

#### 验证

```text
python -m pytest tests/gateway/test_voice_command.py::TestAutoVoiceReply -q -o 'addopts='
13 passed in 0.94s
```

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

- ~~`_escape_title_mdv2()` + `_MDV2_ESCAPE_RE` in `gateway/run.py`~~ — v0.16.0 rebase 后 run.py 恢复为干净上游版，此条过时。
- **v0.16.0 rebase**：从 `gateway/run.py` 删除 40 个冗余 `_handle_*` 方法（上游已迁移到 mixin），从 `cli.py` 删除 33 个（同理）。见 refactor commit `774bb12be`。

### Patch #19 — cron-session 审批检查免疫 os.environ 污染

**Commit:** `330bc8bcb`
**File:** `tools/approval.py`

#### 问题

`cron/scheduler.py:1497` 用 `os.environ["HERMES_CRON_SESSION"] = "1"` 标记 cron 会话——这是**进程级**变量。当 scheduler 内嵌于 gateway 进程（我们的部署方式），任何 cron 任务一跑，此标记便永久烙进 gateway，此后**每一条**正常对话的 agent 都被 approval.py（4 处检查）误判为 cron 会话，导致 `execute_code` 及 dangerous command 被 BLOCKED，直到重启 gateway。

上游注释自己承认「persists for the lifetime of the scheduler process」，预设 scheduler 独立进程；同进程部署下此预设崩溃。上游至今未修。

#### 修复

新增 `_is_active_cron_session()` 辅助函数，将 4 处 `env_var_enabled("HERMES_CRON_SESSION")` 替换之。判定逻辑：cron 标记**且**无入站 platform（真 cron job 的 platform 被 scheduler 设空，正常对话必带 telegram/discord 等）。`_get_session_platform()` 读 ContextVar，不受 os.environ 污染。

```python
def _is_active_cron_session() -> bool:
    if not env_var_enabled("HERMES_CRON_SESSION"):
        return False
    return not _get_session_platform()
```

#### 四场景验证

| 场景 | CRON 标记 | platform | 结果 | 期望 |
|------|-----------|----------|------|------|
| 正常对话(被污染) | 1 | telegram | False | ✓ 不再误拦 |
| 真 cron job | 1 | (空) | True | ✓ 仍识别 |
| CLI/无标记 | 未设 | telegram | False | ✓ |
| 正常对话(无污染) | 未设 | (空) | False | ✓ |

#### 备注

为什么不用 session_id 前缀（`cron_*`）：scheduler 调 `set_session_vars(platform="", ...)` 时把 `_SESSION_ID` ContextVar 无条件设空（L31），阻断了 os.environ fallback，导致 session_id 信号在真 cron 内失效。platform 是唯一可靠的并发安全区分信号。

---

## Branch structure

| Branch | 用途 |
|--------|------|
| `main` | 上游最新 `60cc42e38`（v0.16.0+，+754 from `c3055d618`），不做修改 |
| `local-patches` | 14 numbered patch entries + quota feature + refactor/fix commits + docs，基于最新 main |
| `local-patches-archive` | 完整旧历史（27 commits，含废弃的 pre-v0.14 commits） |
| Tag `local-patches-pre-update-20260608` | v0.16.0 rebase 之前的 local-patches 快照 |
| Tag `local-patches-pre-update-20260530` | v0.15.1 rebase 之前的 local-patches 快照 |
| Tag `local-patches-pre-update-20260523` | v0.14.x rebase 之前的 local-patches 快照 |
| Tag `local-patches-pre-v0.14` | v0.14.0 rebase 之前的快照 |

---

## Commit history

2026-06-16: `local-patches` 合并为单个 squashed commit，以 `git apply --3way` 重贴到上游 `60cc42e38`（+754 commits）。逐 commit rebase 在 patch-8 (`gateway/run.py`) 触发 8 处冲突且会连锁（其 inline-keyboard 逻辑早已迁至 `slash_commands.py`），squashed reapply 一步成型，仅剩 1 处注释冲突需手解。

```
330bc8bcb patch-19: immune cron-session approval checks to os.environ pollution
a9c87ebdc docs: update hermes-patches.md for 2026-06-16 squashed reapply on 60cc42e38
cd7c8ae37 chore: reapply local patches onto upstream 60cc42e38
60cc42e38 fix(inventory): deduplicate models... (upstream base)
```

历史 per-patch commit（35 个，2026-05-23 → 2026-06-10，base `c3055d618` v0.16.0）完整保存在：
- tag `local-patches-pre-update-20260616`
- fork 分支 `local-patches-archive`

*最后更新: 2026-06-16（patch-19 cron 污染免疫，commit `330bc8bcb`；squashed reapply on `60cc42e38` via `git apply --3way`，commit `cd7c8ae37`；14 numbered patches + raw-error-detail，5 discarded；pre-update 快照 tag `local-patches-pre-update-20260616`）*
