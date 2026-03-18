# Moltbook Digest

[English README](./README.md)

`moltbook-digest` 用于从 Moltbook 抓取帖子与评论证据，并生成可分析语料。  
它支持两类解读方式：

- `agent`：不调用外部 LLM，由你的 agent 读取语料并产出分析
- `litellm`：通过 LiteLLM 调用你配置的模型接口自动生成分析报告

## 功能概览

- 语义检索 Moltbook 内容（`/search`）
- 自动展开帖子正文与评论树（`/posts/{id}`、`/posts/{id}/comments`）
- 生成分析安全语料：`brief.md` + `evidence.json`
- 支持双路径解读：`agent` / `litellm`
- 支持最小化 LLM 配置，常见 provider 默认值内置在代码中

## 目录结构

- `moltbook-digest/scripts/moltbook_digest.py`：主脚本
- `moltbook-digest/config.example.yaml`：LLM 配置模板
- `moltbook-digest/requirements.txt`：依赖
- `moltbook-digest/SKILL.md`：Skill 使用说明
- `moltbook-digest/references/api.md`：API 参考与实践建议

## 安装

在仓库根目录执行：

```bash
pip install -r moltbook-digest/requirements.txt
```

## 配置

复制配置模板：

```bash
cp moltbook-digest/config.example.yaml moltbook-digest/config.yaml
```

默认模板是：

- `active_provider: agent`
- 不需要外部 API key

如果你要用外部 LLM，把 `active_provider` 改成目标 provider，并填对应 key：

- `openai`
- `claude`
- `gemini`
- `siliconflow`
- `minimax`
- `volcengine`

说明：

- 真实配置文件 `moltbook-digest/config.yaml` 已在 `.gitignore` 忽略
- 模板只保留常改字段，`api_base` / `api_key_env` 等默认值由代码内置
- `analysis.prompt_template` 已放入配置文件，支持占位符：
  `{analysis_question}`、`{analysis_language}`、`{report_structure}`、`{analysis_input}`
- 默认模板明确要求按用户偏好语言（`{analysis_language}`）输出

### Agent 配置要求（MUST）

当 agent 协助配置本 skill 时，必须执行：

1. 在给出任何配置建议前，先阅读 `moltbook-digest/config.example.yaml`。
2. 先询问用户希望如何设置 `active_provider`（`agent` 还是外部 provider）。
3. 询问用户使用哪个模型/provider，以及是否需要自定义 `analysis.prompt_template`。
4. 询问凭据注入方式（使用现有环境变量，还是写入本地 `config.yaml`）。
5. 在生成分析结果前，确认用户偏好的 `analysis_language`。

不要在未询问用户的情况下默认 provider、模型、密钥处理方式或输出语言。

## 快速开始

### 1) 仅采集证据（不解读）

```bash
python3 moltbook-digest/scripts/moltbook_digest.py \
  --query "agent memory architecture" \
  --query "agent memory failures and tradeoffs" \
  --analysis-mode none \
  --llm-config /tmp/not-exists.yaml
```

`--analysis-mode none` 在任何 provider 配置下都只做采集，不做解读。

### 2) Agent 解读（推荐默认）

确保 `config.yaml` 中 `active_provider: agent`，然后运行：

```bash
python3 moltbook-digest/scripts/moltbook_digest.py \
  --query "agent memory governance" \
  --analysis-mode auto \
  --llm-config moltbook-digest/config.yaml
```

脚本会自动进入 agent 解读路径，输出 `analysis_input.md` 和 `agent_handoff.md`。

### 3) LiteLLM 自动解读

把 `active_provider` 改为外部 provider（例如 `openai`）并填 key，运行：

```bash
python3 moltbook-digest/scripts/moltbook_digest.py \
  --query "long-running agent memory patterns" \
  --analysis-mode auto \
  --llm-config moltbook-digest/config.yaml
```

脚本会自动进入 LiteLLM 路径并产出 `analysis_report.md`。

你也可以显式覆盖：

```bash
python3 moltbook-digest/scripts/moltbook_digest.py \
  --query "agent memory" \
  --analysis-mode litellm \
  --active-provider claude \
  --litellm-model "anthropic/claude-3-7-sonnet-latest" \
  --llm-config moltbook-digest/config.yaml
```

## 输出文件

每次运行默认输出到：

- `output/moltbook-digest/<timestamp>-<slug>/`

核心文件：

- `brief.md`：完整帖子正文 + 代表性评论（分析语料）
- `evidence.json`：结构化原始证据
- `analysis_input.md`：统一分析输入上下文（分析模式开启时）
- `agent_handoff.md`：给 agent 的解读任务模板（agent 模式）
- `analysis_report.md`：LLM 生成的最终报告（litellm 模式）

## 常用参数

- `--query`：可重复，建议 2-5 个自然语言查询
- `--max-posts`：扩展帖子上限
- `--comment-limit`：每帖评论拉取上限
- `--submolt`：按 submolt 过滤
- `--analysis-mode`：`none | agent | litellm | auto`
- `--analysis-question`：明确报告要回答的问题
- `--analysis-language`：报告语言，默认 `zh-CN`
- `--llm-config`：LLM 配置文件路径
- `--active-provider`：覆盖配置中的 provider

查看完整参数：

```bash
python3 moltbook-digest/scripts/moltbook_digest.py --help
```

## 模式规则

- `analysis-mode=none` -> 仅采集，不解读
- `analysis-mode=agent` -> 输出 `analysis_input.md` + `agent_handoff.md`
- `analysis-mode=litellm` -> 通过 LiteLLM 产出 `analysis_report.md`
- `analysis-mode=auto` + `active_provider=agent` -> 自动走 agent 解读
- `analysis-mode=auto` + 外部 provider -> 自动走 LiteLLM 解读
- `analysis-mode=auto` + 未显式配置 provider -> 回退到默认解析 provider（`agent`）

## 安全建议

- 不要提交真实 `config.yaml` 或任何 API key
- 仅在可信环境里配置外部 provider 密钥
- 报告中应明确区分“证据”与“推断”
