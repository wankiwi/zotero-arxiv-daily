<p align="center">
  <a href="" rel="noopener">
 <img width=200px height=200px src="assets/logo.svg" alt="logo"></a>
</p>

<h3 align="center">Zotero-arXiv-Daily</h3>

<div align="center">

  [![Status](https://img.shields.io/badge/status-active-success.svg)]()
  ![Stars](https://img.shields.io/github/stars/TideDra/zotero-arxiv-daily?style=flat)
  [![GitHub Issues](https://img.shields.io/github/issues/TideDra/zotero-arxiv-daily)](https://github.com/TideDra/zotero-arxiv-daily/issues)
  [![GitHub Pull Requests](https://img.shields.io/github/issues-pr/TideDra/zotero-arxiv-daily)](https://github.com/TideDra/zotero-arxiv-daily/pulls)
  [![License](https://img.shields.io/github/license/TideDra/zotero-arxiv-daily)](/LICENSE)
  [<img src="https://api.gitsponsors.com/api/badge/img?id=893025857" height="20">](https://api.gitsponsors.com/api/badge/link?p=PKMtRut1dWWuC1oFdJweyDSvJg454/GkdIx4IinvBblaX2AY4rQ7FYKAK1ZjApoiNhYEeduIEhfeZVIwoIVlvcwdJXVFD2nV2EE5j6lYXaT/RHrcsQbFl3aKe1F3hliP26OMayXOoZVDidl05wj+yg==)

</div>

---

<p align="center"> Recommend new arxiv papers of your interest daily according to your Zotero library.
    <br> 
</p>

> [!IMPORTANT]
> Please keep an eye on this repo, and merge your forked repo in time when there is any update of this upstream, in order to enjoy new features and fix found bugs.

## 🧐 About <a name = "about"></a>

> Track new scientific researches of your interest by just forking (and staring) this repo!😊

*Zotero-arXiv-Daily* finds arxiv papers that may attract you based on the context of your Zotero library, and then sends the result to your mailbox📮. It can be deployed as Github Action Workflow with **zero cost**, **no installation**, and **few configuration** of Github Action environment variables for daily **automatic** delivery.

## ✨ Features
- Totally free! All the calculation can be done in the Github Action runner locally within its quota (for public repo).
- AI-generated TL;DR for you to quickly pick up target papers.
- Affiliations of the paper are resolved and presented.
- Links of PDF and code implementation (if any) presented in the e-mail.
- List of papers sorted by relevance with your recent research interest.
- Fast deployment via fork this repo and set environment variables in the Github Action Page.
- Support LLM API for generating TL;DR of papers.
- Ignore unwanted Zotero papers using a list of glob patterns.
- Support multiple sources of papers to retrieve:
  - arxiv
  - biorxiv
  - medrxiv

## 📷 Screenshot
![screenshot](./assets/screenshot.png)

## 🚀 Usage

当前版本不设置 `PAPER_CONFIG` 时默认订阅全部指定期刊和四个预印本平台，输出 RSS。配置方法见下方“指定期刊推荐、RSS 与 GitHub Pages”。下方旧 arXiv 邮件教程请配合 `PAPER_CONFIG=legacy` 使用；本地对应 `--config-name=legacy`。
### Quick Start
1. Fork (and star😘) this repo.
![fork](./assets/fork.png)

2. Set Github Action environment variables.
![secrets](./assets/secrets.png)

Below are all the secrets you need to set. They are invisible to anyone including you once they are set, for security.

| Key |Description | Example |
| :---  | :---  | :--- |
| ZOTERO_ID  | User ID of your Zotero account. **User ID is not your username, but a sequence of numbers**Get your ID from [here](https://www.zotero.org/settings/security). You can find it at the position shown in this [screenshot](https://github.com/TideDra/zotero-arxiv-daily/blob/main/assets/userid.png). | 12345678  |
| ZOTERO_KEY | An Zotero API key with read access. Get a key from [here](https://www.zotero.org/settings/security).  | AB5tZ877P2j7Sm2Mragq041H   |
| SENDER | The email account of the SMTP server that sends you email. | abc@qq.com |
| SENDER_PASSWORD | The password of the sender account. Note that it's not necessarily the password for logging in the e-mail client, but the authentication code for SMTP service. Ask your email provider for this.   | abcdefghijklmn |
| RECEIVER | The e-mail address that receives the paper list. | abc@outlook.com |
| OPENAI_API_KEY | API Key when using the API to access LLMs. You can get FREE API for using advanced open source LLMs in [SiliconFlow](https://cloud.siliconflow.cn/i/b3XhBRAm). | sk-xxx |
| OPENAI_API_BASE | API URL when using the API to access LLMs. | https://api.siliconflow.cn/v1 |

Then you should also set a public variable `CUSTOM_CONFIG` for your custom configuration.
![vars](./assets/repo_var.png)
![custom_config](./assets/config_var.png)
Paste the following content into the value of `CUSTOM_CONFIG` variable:
```yaml
zotero:
  user_id: ${oc.env:ZOTERO_ID}
  api_key: ${oc.env:ZOTERO_KEY}
  include_path: null # Or e.g. ["2026/survey/**", "2026/reading-group/**"]

email:
  sender: ${oc.env:SENDER}
  receiver: ${oc.env:RECEIVER}
  smtp_server: smtp.qq.com
  smtp_port: 465
  sender_password: ${oc.env:SENDER_PASSWORD}

llm:
  api:
    key: ${oc.env:OPENAI_API_KEY}
    base_url: ${oc.env:OPENAI_API_BASE}
  generation_kwargs:
    model: gpt-4o-mini

source:
  arxiv:
    category: ["cs.AI","cs.CV","cs.LG","cs.CL"]
    include_cross_list: false # Set to true to include arXiv cross-list papers in these categories.

executor:
  debug: ${oc.env:DEBUG,null}
  source: ['arxiv']
```
Set `source.arxiv.include_cross_list: true` if you want cross-listed papers included in selected arXiv categories.
>[!NOTE]
> `${oc.env:XXX,yyy}` means the value of the environment variable `XXX`. If the variable is not set, the default value `yyy` will be used.

Here is the full configuration, `???` means the value must be filled in:
```yaml
zotero:
  user_id: ??? # User ID of your Zotero account.
  api_key: ??? # An Zotero API key with read access.
  include_path: null # A list of glob patterns marking the Zotero collections that should be included. Example: ["2026/survey/**", "2026/reading-group/**"]

source:
  arxiv:
    category: null # The categories of target arxiv papers. Find the abbr of your research area from [here](https://arxiv.org/category_taxonomy). Example: ["cs.AI","cs.CV","cs.LG","cs.CL"]
    include_cross_list: false # Whether to include arXiv cross-list papers in subscribed categories. Example: true
  biorxiv:
    category: null # The categories of target biorxiv papers. Find categories from [here](https://www.biorxiv.org/). Example: ["biochemistry","animal behavior and cognition"]
  medrxiv:
    category: null # The categories of target medrxiv papers. Find categories from [here](https://www.medrxiv.org/) Example: ["psychiatry and clinical psychology", "neurology"]

email:
  sender: ??? # The email account of the SMTP server that sends you email. Example: abc@qq.com
  receiver: ??? # The email account that receives the paper list. Example: abc@outlook.com
  smtp_server: ??? # The SMTP server that sends the email. Ask your email provider (Gmail, QQ, Outlook, ...) for its SMTP server. Example: smtp.qq.com
  smtp_port: ??? # The port of SMTP server. Example: 465
  sender_password: ??? # The password of the sender account. Note that it's not necessarily the password for logging in the e-mail client, but the authentication code for SMTP service. Ask your email provider for this. Example: abcdefghijklmn

llm:
  api:
    key: ??? # API Key of your LLM API. Example: sk-xxx
    base_url: ??? # API URL of your LLM API. Example: https://api.openai.com/v1
  generation_kwargs:
  # Arguments for the LLM API. See [here](https://platform.openai.com/docs/api-reference/chat/create) for more details.
    max_tokens: 16384
    model: ???
  language: English # Preferred language for the TL;DR. Example: English

reranker:
  local:
    model: jinaai/jina-embeddings-v5-text-nano # The Hugging Face model name of the local embedding model. Example: jinaai/jina-embeddings-v5-text-nano
    encode_kwargs:
    # The kwargs for the encode method of the local embedding model. Details see [here](https://www.sbert.net/docs/package_reference/SentenceTransformer.html#sentence_transformers.SentenceTransformer.encode)
      task: retrieval
      prompt_name: document
  api:
    key: null # API Key of your embedding model API. Example: sk-xxx
    base_url: null # API URL of your embedding model API. Example: https://api.openai.com/v1
    model: null # The model name of the embedding model. Example: text-embedding-3-large
    batch_size: null # The batch size for embedding API requests. Adjust to match your provider's limit. Example: 64

executor:
  debug: false # Whether to use debug mode. Example: true
  send_empty: false # Whether to send an empty email even if no new papers today. Example: true
  max_paper_num: 100 # The maximum number of the papers presented in the email. Example: 100
  source: ??? # The sources of papers to retrieve. Example: ['arxiv','biorxiv','medrxiv']
  reranker: local # The reranker to use. Example: 'local' or 'api'
```

That's all! Now you can test the workflow by manually triggering it:
![test](./assets/test.png)

> [!NOTE]
> The Test workflow uses the selected PAPER_CONFIG with debug mode and disabled delivery history, retaining up to 10 papers per preprint platform. It uses real configured output channels and credentials. Any generated RSS is retained as the `test-rss` artifact for inspection; this test does not deploy Pages or restore/save the remote delivery history. The main workflow runs daily within each source’s configured date window; weekends and holidays may have no new arXiv submissions.

Then check the log and the receiver email after it finishes.

By default, the main workflow runs on 22:00 UTC everyday. You can change this time by editting the workflow config `.github/workflows/main.yml`.

### Local Running
Supported by [uv](https://github.com/astral-sh/uv), this workflow can easily run on your local device if uv is installed:
```bash
# set all the environment variables
# export ZOTERO_ID=xxxx
# ...
cd zotero-arxiv-daily
uv run --frozen python -m zotero_arxiv_daily.main
```

## 🚀 Sync with the latest version
This project is in active development. You can subscribe this repo via `Watch` so that you can be notified once we publish new release.

![Watch](./assets/subscribe_release.png)


## 📖 How it works
*Zotero-arXiv-Daily* firstly retrieves all the papers in your Zotero library and all the papers released in the previous day, via corresponding API. Then it calculates the embedding of each paper's abstract via an embedding model. The score of a paper is its weighted average similarity over all your Zotero papers (newer paper added to the library has higher weight). The TLDR of each paper is generated by LLM, given the text extracted by pymupdf4llm.

## 📌 Limitations
- The recommendation algorithm is very simple, it may not accurately reflect your interest. Welcome better ideas for improving the algorithm!
- High `MAX_PAPER_NUM` can lead the execution time exceed the limitation of Github Action runner (6h per execution for public repo, and 2000 mins per month for private repo). Commonly, the quota given to public repo is definitely enough for individual use. If you have special requirements, you can deploy the workflow in your own server, or use a self-hosted Github Action runner, or pay for the exceeded execution time.

## 👯‍♂️ Contribution
Any issue and PR are welcomed! But remember that **each PR should merge to the `dev` branch**.

## 📃 License
Distributed under the AGPLv3 License. See `LICENSE` for detail.

## ❤️ Acknowledgement
- [pyzotero](https://github.com/urschrei/pyzotero)
- [arxiv](https://github.com/lukasschwab/arxiv.py)
- [sentence_transformers](https://github.com/UKPLab/sentence-transformers)

## ☕ Buy Me A Coffee
If you find this project helpful, welcome to sponsor me via WeChat or via [ko-fi](https://ko-fi.com/tidedra).
![wechat_qr](assets/wechat_sponsor.JPG)


## 🌟 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=TideDra/zotero-arxiv-daily&type=Date)](https://star-history.com/#TideDra/zotero-arxiv-daily&Date)

## 指定期刊推荐、RSS 与 GitHub Pages

除了现有预印本来源，现在可以按期刊订阅候选文献，再使用 Zotero 文库的兴趣相似度排序。期刊预设如下：

| 配置 ID | 期刊 |
| --- | --- |
| `jacs` | Journal of the American Chemical Society |
| `jctc` | Journal of Chemical Theory and Computation |
| `prl` | Physical Review Letters |
| `nature` | Nature 主刊 |
| `nature_family` | Nature 主刊和 Nature 品牌期刊，包含 Nature Reviews |
| `science` | Science |
| `science_advances` | Science Advances |
| `jcp` | The Journal of Chemical Physics |
| `jpcl` | The Journal of Physical Chemistry Letters |

期刊候选来自官方 RSS 和 Crossref 的 ISSN 精确查询。Crossref 按发表时间窗口查询并完整分页，读取实际线上/纸本发表日期，避免将 DOI 创建时间当作发表时间。默认回溯最近 7 天，每次重叠抓取，通过持久状态去重。更晚入库的文献可以用较大的 `window_days` 回补；这不能保证发现任意延迟入库或抓取窗口之外的文章。

`nature_family` 启用时，每周从 Nature 官方 `https://www.nature.com/siteindex` 更新以 Nature 命名的期刊清单，包含 Reviews，新增期刊自动加入。Crossref 按正式刊名解析 ISSN 并缓存。内置清单用于离线启动参考，**未经当前在线核验**。推荐配置 `config/journals.yaml` 启用 `require_live_catalog=true`：首次访问或过期清单刷新失败时明确报错，避免将旧清单当作完整覆盖。其他配置可允许缓存/内置清单降级，并从日志检查覆盖情况。

期刊缺少摘要时使用标题进行评分，输出会标注 `title only`。没有摘要或正文时不生成 AI 内容；没有 PDF 时提供文章页面或 DOI 链接。不会绕过出版商的全文访问权限。来源明确标记的更正、撤稿、社论等会被过滤，元数据未注明文章类型的条目仍可能包含非研究内容。

### 本地运行

使用 Python 3.13 和仓库锁定的依赖：

```bash
uv sync --frozen
# 在本地 .env 或进程环境中配置 ZOTERO_ID、ZOTERO_KEY。
# 启用 LLM 时还需要 OPENAI_API_KEY 和对应的 API 地址/模型。
uv run --frozen python -m zotero_arxiv_daily.main --config-name=journals
```

该配置默认订阅上述全部期刊，只生成 `public/feed.xml`，不要求 SMTP 设置。保留最近 30 天、最多 300 条 RSS 推荐记录。

```bash
# 不调用 LLM，RSS 中显示原摘要；本地 embedding 模型仍需首次下载。
uv run --frozen python -m zotero_arxiv_daily.main --config-name=journals llm.enabled=false

# 仅启用所需期刊，并增大回溯窗口。
uv run --frozen python -m zotero_arxiv_daily.main --config-name=journals \
  'source.journals.presets=[jacs,jctc,prl,jcp,jpcl]' source.journals.window_days=14

# 同时保留预印本来源；须设置其分类。
uv run --frozen python -m zotero_arxiv_daily.main --config-name=journals \
  'executor.source=[journals,arxiv]' 'source.arxiv.category=[physics.chem-ph,cond-mat.mtrl-sci]'

# 同时发邮件；需 SENDER、RECEIVER、SENDER_PASSWORD 以及正确的 SMTP 设置。
uv run --frozen python -m zotero_arxiv_daily.main --config-name=journals output.email.enabled=true
```

需要自定义期刊时，可在配置中增加 `source.journals.custom`，填写正式名称、ISSN/eISSN 和可选官方 RSS：

```yaml
source:
  journals:
    custom:
      - id: my_journal
        title: 正式期刊名称
        issns: [有效的ISSN]
        rss: null
```

推荐数量由 `executor.max_paper_num` 控制，可设置 `executor.min_score`。分数沿用原有 embedding 相似度算法，期刊与预印本共用阈值；并非插件里的 LLM 评分。`zotero.include_path` / `ignore_path` 决定兴趣画像；去重默认覆盖整个文库，可用 `executor.exclude_existing=false` 关闭。

### GitHub Actions 发布

1. Fork/更新仓库，在 Actions Secrets 中配置 `ZOTERO_ID`、`ZOTERO_KEY`；使用 LLM 时配置 `OPENAI_API_KEY`。
2. `PAPER_CONFIG` 不设置或留空时，默认使用 `all`：上述全部指定期刊，以及 arXiv、bioRxiv、medRxiv、Research Square 的全部学科预印本。可在 Actions Variables 选择以下配置：

   | `PAPER_CONFIG` | 候选来源 |
   | --- | --- |
   | `all` / `default` / 不设置 | 全部指定期刊 + 全部预印本平台 |
   | `journals` | 全部指定期刊 |
   | `preprints` | arXiv、bioRxiv、medRxiv、Research Square |
   | `arxiv` / `biorxiv` / `medrxiv` / `researchsquare` | 单个预印本平台 |
   | `legacy` | 原有 arXiv 分类和邮件配置 |

   新配置默认生成 RSS。`all`、`preprints` 及各平台配置默认 arXiv 回溯 7 天，其他预印本回溯 1 天，期刊回溯 7 天；`category: ["*"]` 表示全部学科。arXiv 使用提交日期 API 查询；7 天重叠窗口覆盖通常的审核和周末公告延迟，持久状态去重，超过窗口的延迟仍需手动扩大窗口回补。显式改为 1 天可能漏掉刚公开的论文。bioRxiv/medRxiv 使用分页日期 API，Research Square 使用 Crossref 的 `10.21203` 前缀及预印本类型查询。日期精度只有天的平台包含窗口边界日，因此会有重叠；持久状态避免重复推荐。Research Square 缺少摘要时按标题评分，同一论文的 `/v1`、`/v2` 等版本只推荐一次。
3. 可选 Variables：`OPENAI_API_BASE`、`LLM_MODEL`、`RSS_SITE_URL`；邮件需要额外配置 Secrets `SENDER`、`RECEIVER`、`SENDER_PASSWORD` 和 Variables `SMTP_SERVER`、`SMTP_PORT`。
4. 在 **Settings → Pages → Source** 选择 **GitHub Actions**，启用工作流。
5. 运行 **Daily papers and RSS**。手动触发可选择 `configured`、`email`、`rss`、`both`，以及 1–90 天的统一回溯窗口；留空沿用各平台配置。可选 `recipient` 为本次运行指定一个收件邮箱，优先于 `CUSTOM_CONFIG` 和 `RECEIVER`；留空保留原配置，不修改长期收件人。定时触发为北京时间每天 06:00，GitHub 调度可能延迟。
6. 完成 Pages 部署后，在 Zotero Feed 中订阅 `https://<用户名>.github.io/<仓库名>/feed.xml`。以实际 Pages 地址为准。

可用 `CUSTOM_CONFIG` 同时选择期刊和预印本，并限定学科，例如：

```yaml
executor:
  source: [journals, arxiv, researchsquare]
source:
  journals:
    presets: [jacs, jctc, jpcl]
  arxiv:
    category: [physics.chem-ph, cond-mat.mtrl-sci]
    window_days: 7
  researchsquare:
    window_days: 2
```

`PAPER_CONFIG` 仍为 `config/` 下的 YAML 文件名，不含 `.yaml`；本地使用 `--config-name=all`、`--config-name=preprints` 或 `--config-name=researchsquare`，省略参数也默认全部来源。全部学科的候选数量和模型计算量较大，可用上述配置缩小范围。分页达到 `max_pages` 限制时会明确报错，避免把部分结果误当作完整结果。

`CUSTOM_CONFIG` 仍可作为 YAML 配置覆盖使用，工作流生成忽略的 `config/runtime.yaml`，不会覆盖版本库的 `config/custom.yaml` 或打印配置内容。`vars.REPOSITORY` / `vars.REF` 的旧跨仓库执行选项已移除，工作流始终运行当前仓库的触发版本。CI 使用冻结锁文件。

工作流将推荐历史保存到独立的 **`paper-state` 数据分支**，无需切换开发分支。它需要 `contents: write` 权限；组织策略/分支规则必须允许该分支的更新。数据包含已选推荐及投递状态，不包含 Zotero 文库原始数据、凭据或全文。Pages 只发布 `public/` 中的 RSS；其中的文献选择和相关性分数可被公开访问。

邮件、RSS 独立记录结果：邮件失败时仍生成并发布有效 RSS，工作流同时报告错误；下次运行重试未成功的邮件，单次最多发送 `max_paper_num` 条。新的 Zotero 读取或排序失败时，仍会处理已保存的待投递记录，并明确报告本次失败。某一期刊失败会留下告警并使任务最终失败，其他来源的推荐仍可输出；异常元数据按条隔离，不丢弃同一期刊的正常记录。当没有新结果时保留 RSS 历史。邮件投递与状态持久化之间发生进程退出时，重试仍可能重复发邮件，SMTP 无法提供严格的“恰好一次”保证。网络/进程中断后应检查 Actions 日志。

本地运行需保留 `data/recommendations.json`、`data/journal_catalog.json`；本地多进程请勿同时写同一状态路径。GitHub 工作流已使用统一并发组避免冲突。状态默认保留 90 天，RSS 保留期应不超过状态保留期；超过状态保留期的文章重新回溯时可能再次推送。只需重新发布已有 RSS 时可手动重跑工作流。

### 性能与故障处理

- arXiv 全文在去重、评分和数量截断之后下载；`executor.fetch_full_text=false` 可省去全文下载。
- 期刊请求默认 4 个并发任务，支持 `source.journals.workers`（1–16），HTTP 连接复用、有限重试与限流退避。
- 本地 embedding 模型在同一进程复用；API embedding 对相同文本只请求一次并复用客户端。API 返回乱序索引、零向量或缺失结果时明确报错。
- 关闭全文下载时，`executor.enrichment_workers`（1–8）可并行生成 TLDR；开启全文时保持串行，避免在工作线程中 fork PDF 子进程。
- Tokenizer 无法下载时使用保守 UTF-8 字节上限继续调用 LLM，并记录告警；联网恢复后新进程会重新尝试，下载仍保留原始校验。
- SMTP 465 使用隐式 TLS，其他端口使用 STARTTLS，不再自动降级为明文认证。

开发验证：

```bash
uv run --frozen pytest -q                         # 默认离线功能测试
uv run --frozen pytest -m slow                   # 可选：下载模型的实际 embedding 测试
```

测试包括精确期刊筛选、Crossref 分页、Nature 目录解析、缺失摘要、RSS 历史、独立渠道失败重试、筛选后全文提取、embedding 去重，以及本地真实 Git 数据分支保存/恢复。真实出版商访问、个人 API、SMTP 投递与 GitHub Pages 部署需另行集成验证。
