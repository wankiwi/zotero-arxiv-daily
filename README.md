# Zotero 文献邮件推荐

本项目基于 [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) 修改。保留原项目根据 Zotero 文库摘要计算相关性、排序并发送邮件的核心流程，感谢原作者及贡献者。遵循 [GNU AGPL v3 许可证](LICENSE)。

## 增加的功能

- 普通期刊检索：Nature 系列及 JACS、JCTC、PRL、Science、PNAS、ACS Catalysis、npj Computational Materials、Angewandte Chemie International Edition、Chemical Science、Machine Learning: Science and Technology 等，使用出版商订阅源与 Crossref 元数据。
- 独立预印本兴趣配置：arXiv、bioRxiv 分类与 Research Square 的 OpenAlex 分类分别设置，不影响普通期刊；当前兴趣配置关闭 medRxiv。
- DOI、URL、版本及历史推荐去重，保存渠道投递状态，失败后保留待发送记录。
- CPU 编码优化、进程内模型复用和可选本地向量缓存；LLM 不可用时保留原摘要，并明确摘要来源。
- GitHub Actions 单次覆盖来源、兴趣配置、日期窗口和收件人，日志避免输出凭据。

**当前仅发送邮件，不生成或部署 RSS。** 旧 `CUSTOM_CONFIG` 的 `rss.enabled: true` 或旧运行输入 `output=both/rss` 不会重新启用工作流发布。RSS 输出代码仅保留给明确配置的本地调用。

## 最小配置与运行

在仓库 **Settings → Secrets and variables → Actions** 配置：

- Secrets：`ZOTERO_ID`、`ZOTERO_KEY`、`SENDER`、`SENDER_PASSWORD`、`RECEIVER`。
- Variables：`PAPER_CONFIG=interests`；`CUSTOM_CONFIG` 填写下面的 YAML，并按实际邮件服务修改 SMTP 设置。现有 CSTNET 用户可保留 `mail.cstnet.cn`、SSL 端口 `994`。

```yaml
email:
  smtp_server: mail.cstnet.cn
  smtp_port: 994
llm:
  enabled: false
executor:
  max_paper_num: 50
  send_empty: true
output:
  email:
    enabled: true
  rss:
    enabled: false
```

账户与密钥由环境变量读取，不要直接写进 YAML 或提交仓库。关闭 LLM 时不需要调用收费摘要 API；邮件显示原摘要。若另行启用 LLM，需自行确认提供商、模型和费用。

到 **Actions → Send emails daily → Run workflow**，选择所需分支，设置 `sources=all`、`preprint_profile=interests`、`llm_mode=disabled`、`output=email`。收件人留空使用 `RECEIVER`，或填写本次收件人；`window_days` 留空保留各来源窗口。定时运行固定使用 `interests`、全部允许来源、关闭 LLM、仅邮件；旧 `CUSTOM_CONFIG` 不能覆盖这些选择，收件人始终来自现有 `RECEIVER` secret。定时任务每天 UTC 22:00（北京时间次日 06:00），GitHub 只运行默认分支上的定时工作流，且可能延迟。

兴趣列表在 [config/interests.yaml](config/interests.yaml)：arXiv 按原生分类、bioRxiv 按原生分类筛选；Research Square 使用 `S4306525896` 来源和指定 OpenAlex subfield，在所有主题中匹配。同名 subfield 在不同父领域下的精确匹配取并集，不猜测或固定 ID。不要恢复已失效的 `S4306402450`。`enabled: false` 优先禁用平台，`categories: null` 继承旧分类，空列表无效。

当前窗口并非“只取当天”：arXiv 回溯 7 天，普通期刊从 UTC 七天前零点至当前，bioRxiv 和 Research Square 包含 UTC 昨天与今天。去重后按相关性选择最多 50 篇；历史发送失败的条目也可能补发。分数不是概率。

`paper-state` 分支保存去重及待发送状态，勿删除或重置后盲目重跑。SMTP 接受邮件不等于邮件已到达收件箱。部分来源失败会明确报告，成功投递记录仍会保存。Crossref 未收录的新刊若有有效出版商订阅源，会明确警告并降级为该源；这不代表完整覆盖历史窗口。限流、服务器错误及无可用源仍会报错。向量缓存仅限本地，不上传私人文库原文或向量。

`Test` 工作流只运行隔离测试，不读取投递凭据或发送邮件；正式邮件请使用 `Send emails daily`。

本地安装与测试：

```bash
uv sync --frozen
uv run pytest
# 包含真实模型测试，需要模型已缓存或允许下载：
uv run pytest -m ""
```

本地运行前配置所需环境变量，再执行 `uv run python -m zotero_arxiv_daily.main --config-name=interests llm.enabled=false executor.max_paper_num=50`；此命令会实际发送邮件，不是模拟测试。
