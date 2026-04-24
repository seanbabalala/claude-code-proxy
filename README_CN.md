<h1 align="center">Claude Code Proxy</h1>

<p align="center">
  <em>让 Claude Code 接入任意 OpenAI 兼容网关，协议翻译全自动。</em>
</p>

<p align="center">
  <a href="https://github.com/seanbabalala/claude-code-proxy/actions/workflows/ci.yml"><img src="https://github.com/seanbabalala/claude-code-proxy/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white" alt="Python 3.11+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="https://github.com/seanbabalala/claude-code-proxy/issues"><img src="https://img.shields.io/github/issues/seanbabalala/claude-code-proxy" alt="Issues"></a>
  <a href="README.md">English</a> | <strong>中文</strong>
</p>

---

## 这是个什么东西？

不少公司内部已经有了 Claude 模型的调用通道，但走的是 OpenAI 兼容的 `chat/completions` 接口。问题来了——Claude Code 只认 Anthropic 自家的 Messages 协议，两边协议不通，Claude Code 自然接不上。

这个代理就是干这件事的：**在中间做一层实时协议翻译**，Claude Code 照常发请求，代理帮你把格式转成上游网关能理解的样子，再把响应翻译回来。不用改客户端，不用改上游，加一层代理就行。

```
Claude Code  ──Anthropic Messages──▶  claude-code-proxy  ──chat/completions──▶  你的网关
```

## 支持哪些能力

- **普通对话** — 非流式和流式（SSE）都支持
- **工具调用** — `tool_use` / `tool_result` 双向翻译，Claude Code 的读文件、写文件、执行命令全部正常工作
- **Token 计数** — `count_tokens` 接口，基于启发式估算（不是精确值，但够用）
- **模型列表** — `GET /v1/models`，让 Claude Code 能看到可用模型
- **模型映射** — 客户端请求 `claude-sonnet-4-5`，上游实际走 `claude-opus-4-6-v1`？随你配
- **强制锁定** — 不管客户端选什么模型，一律走你指定的那个
- **请求面板** — 终端里用 Rich 渲染彩色面板，请求/响应一目了然，调试起来很舒服
- **启动检查** — 自动读取 `~/.claude/settings.json`，如果配置不对会提前告警

## 三步跑起来

```bash
# 1. 拉代码、装依赖
git clone https://github.com/seanbabalala/claude-code-proxy.git
cd claude-code-proxy
uv sync          # 用 pip 也行：pip install -e .

# 2. 配置
cp .env.example .env
# 打开 .env，至少把 UPSTREAM_BASE_URL 改成你的网关地址

# 3. 启动
uv run python -m claude_code_proxy
```

默认监听 `http://127.0.0.1:8000`。

## 让 Claude Code 走代理

最简单的方式，直接设环境变量：

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8000
export ANTHROPIC_AUTH_TOKEN=local-dev-token
claude
```

但如果你日常用的是交互式 `claude` 命令，**建议写到配置文件里**，不然 `~/.claude/settings.json` 里的值会覆盖环境变量，导致代理看起来"没生效"：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8000",
    "ANTHROPIC_AUTH_TOKEN": "local-dev-token"
  }
}
```

> **踩坑提示：** 改完环境变量后 Claude Code 还是不走代理？先看看 `~/.claude/settings.json` 里有没有硬编码别的地址。这个文件的优先级比 shell export 高。

## 环境变量说明

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `UPSTREAM_BASE_URL` | **必填。** 上游 `chat/completions` 的完整 URL | — |
| `UPSTREAM_API_KEY` | 调用上游时带的 Token。留空则透传客户端的 Key | 空 |
| `GATEWAY_API_KEY` | 代理本身的鉴权 Token。留空则不鉴权 | 空 |
| `DEFAULT_UPSTREAM_MODEL` | 兜底模型名，客户端没指定或映射不到时用这个 | — |
| `DEFAULT_MAX_TOKENS` | 兜底 token 预算 | `32000` |
| `FORCE_UPSTREAM_MODEL` | 设为 `true` 则无视客户端选的模型，一律用 `DEFAULT_UPSTREAM_MODEL` | `false` |
| `MODEL_MAP_JSON` | 模型名映射表，JSON 格式。比如 `{"claude-sonnet-4-5":"my-claude-v1"}` | `{}` |
| `PRETTY_LOGS` | 终端彩色面板开关 | `true` |
| `LOG_PAYLOAD_MAX_CHARS` | 日志里请求体预览的截断长度 | `1400` |
| `HOST` | 监听地址 | `127.0.0.1` |
| `PORT` | 监听端口 | `8000` |
| `UPSTREAM_TIMEOUT_SECONDS` | 上游请求超时时间 | `180` |

## 协议翻译对照

看一眼就知道代理在干什么。

### 请求方向（Anthropic → OpenAI）

| Claude Code 发出的 | 代理转成的 |
|---|---|
| `system`（顶层字段） | `messages[0].role = "system"` |
| `tool_use` content block | `tool_calls[].type = "function"` |
| `tool_result` content block | `role = "tool"` 消息 |
| `max_tokens` | `max_completion_tokens` |
| `tools[].input_schema` | `tools[].function.parameters` |
| `tool_choice.type = "any"` | `tool_choice = "required"` |

### 响应方向（OpenAI → Anthropic）

| 上游返回的 | 代理翻译成的 |
|---|---|
| `choices[0].message.content` | `content[].type = "text"` |
| `choices[0].message.tool_calls` | `content[].type = "tool_use"` |
| `finish_reason = "tool_calls"` | `stop_reason = "tool_use"` |
| SSE `delta.content` | SSE `text_delta` |
| SSE `delta.tool_calls` | SSE `content_block_start` + `input_json_delta` |

## 跑测试

```bash
uv run pytest
```

## 已知局限

- **Token 计数是估算的** — 用 JSON 序列化后的字节数除以 4，不是精确分词。对 Claude Code 的使用场景来说够了，但别拿它当计费依据。
- **多模态内容会被降级** — 图片等非文本 block 会被拍平成文字占位符，不会原样转发。
- **只做 Claude Code 兼容** — 不追求覆盖 Anthropic API 的所有接口，够用就行。

## 参与贡献

欢迎提 Issue 和 PR！贡献前请先看看 [Contributing Guide](CONTRIBUTING.md)。

## 开源协议

[MIT](LICENSE)
