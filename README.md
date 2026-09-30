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

手动运行还可设置 `sources=configured/all/journals` 和 `llm_mode=configured/disabled`。默认 `configured` 保留现有 preset 与 `CUSTOM_CONFIG` 的合并结果；其他选项在 `CUSTOM_CONFIG` 之后应用，只影响本次运行，不修改仓库 Variables、Secrets 或长期来源选择。`sources=all` 选择全部五个来源，保留已配置的学科和窗口，并为 legacy 配置中未配置的平台补上必要默认值。`sources=journals` 只检索期刊。`llm_mode=disabled` 使用原摘要，不读取 LLM 凭据、不调用 LLM，也不切换模型或供应商。运行日志仅列出生效来源、LLM 开关与邮件/RSS 开关，不打印完整配置、收件人、API 地址或凭据。

邮件和 RSS 区分 AI 摘要、AI 失败后的原摘要回退、未生成摘要。旧状态没有可靠的摘要来源信息时标记为 `Summary (legacy; origin unknown)`，不会重新发送已经投递的记录。摘要调用失败时保留原摘要并汇总降级警告，仍允许投递，因此工作流成功不等于 AI 摘要成功。确定性“模型不可用”的 404 会停止本次后续 LLM 请求；普通路由 404、网络等暂时错误不会触发该熔断。首次请求先完成探测再开启并发；探测成功后若模型失效，已在途请求不能撤回。下次运行重新探测，不自动启用收费模型。

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

### 独立配置预印本研究方向

`preprint_interests` 与普通期刊的 presets、ISSN、窗口独立。未指定或根值为 `null` 时沿用旧 `source.<平台>.category`；已有配置无需迁移。新配置中 `categories` 优先于旧 `category`，去除前后空白。示例为已确认的方向：

```yaml
preprint_interests:
  arxiv:
    categories: [physics.chem-ph, physics.comp-ph, cond-mat.mtrl-sci, cond-mat.soft, cs.LG, cs.AI]
  biorxiv:
    categories: [biophysics, biochemistry]
  medrxiv:
    enabled: false
  researchsquare:
    backend: openalex
    source_ids: [S4306525896, S4306402450]
    type: [preprint]
    subfield:
      - Physical and Theoretical Chemistry
      - Materials Chemistry
      - General Materials Science
      - Condensed Matter Physics
      - Artificial Intelligence
      - Biophysics
      - Biochemistry
```

上述配置保存在 `config/interests.yaml`。本地用 `--config-name=interests`；Actions 本次选择 `preprint_profile=interests`，会在 `CUSTOM_CONFIG` 后应用该方向配置，不改长期 Variables。它只改变预印本筛选，不改变来源选择；如需期刊和全部允许的预印本来源，同时选 `sources=all`。默认 `preprint_profile=configured` 保持原行为。长期启用可将这段映射加入已有 `CUSTOM_CONFIG`（保留其他设置），或显式选择 `PAPER_CONFIG=interests` 并检查旧覆盖配置。

`enabled: false` 独立于来源列表，在 `sources=all` 和旧 `executor.source` 合并后仍禁止该平台检索。本例明确关闭 medRxiv。`categories: null` 仅继承旧配置，不表示关闭；`categories: ['*']` 才明确选择全部分类。空列表、空映射、空字符串、未知配置字段/平台和错误类型会报错，不会自动放宽范围。分类检查包含格式校验，并不内置各平台完整、不断变化的分类词典；拼写错误可能返回零结果，请使用平台原生名称。禁用来源不删除历史 RSS 或待投递状态。

- arXiv 使用[原生分类](https://arxiv.org/category_taxonomy)，日期窗口检索通过[官方 API](https://info.arxiv.org/help/api/user-manual.html)的 `cat:` 筛选；可选关键词通过 `ti:` / `abs:` 查询再本地复核，仍遵循 `include_cross_list`。
- bioRxiv / medRxiv 使用各自的原生分类名称，[官方 API](https://api.biorxiv.org/)按每个分类独立请求和分页，并复核返回分类。新方向配置使用日期窗口；未指定窗口时默认回溯 1 天，旧的无方向配置保留旧检索行为。
- Research Square 的 `interests` 配置使用 OpenAlex。两个 `locations.source.id` 取 OR，`type:preprint` 与七个子学科取 AND；子学科使用 `topics.subfield.id`，匹配任意已分配 topic 的子学科，不限于排名第一的 `primary_topic.subfield.id`。这是[官方定义的两种覆盖语义](https://help.openalex.org/data/subfields/)，可以保留主学科不同的交叉研究。没有分配这些子学科或已经被归为 `article` 的记录仍不匹配。

OpenAlex 后端先请求 `/sources/<ID>` 核对 ID 与 Research Square 名称，再分页读取 `/subfields`，按上列**精确名称**解析 ID；不猜测映射、不模糊匹配。名称缺失、歧义、来源身份不符时失败并报告。作品按[官方 cursor 分页](https://help.openalex.org/api/paging/)抓取，每页 100 条，分页截断/循环明确报错。日期窗口使用 `from_publication_date` / `to_publication_date`，不是索引新增日期或 Research Square 修订日期；OpenAlex 收录延迟及合并已发表版本可能导致窗口漏检，不能承诺抓全平台新增稿件。

返回后复核来源、类型、子学科和日期，按 OpenAlex work ID 与规范化 DOI 去重；同窗口中返回多个 Research Square DOI 版本时保留最新再排除撤稿。窗口/服务端过滤外的其他版本不会额外查询，撤稿信息亦受 OpenAlex 元数据时效限制。无摘要仍保留论文；有倒排摘要则重建；无 DOI 使用 OpenAlex work URL。若后续才补 DOI，历史身份可能改变。不会下载全文。OpenAlex 后端忽略旧 `keywords` 近似筛选，单次 `preprint_profile=interests` 会整体替换旧方向映射；未选该配置的旧 Crossref 后端保持兼容。

[当前官方认证说明](https://help.openalex.org/api/authentication/)允许免 key 基础请求，本实现仅使用无 key API，不读取/创建凭据、不启用付费或付费增量过滤器。401/403/429 会报告访问或配额错误，无付费回退。开发环境访问 `api.openalex.org` 被代理 `403 Forbidden` 阻止，因此截至本次修改，**两个来源身份和七项实际名称-ID 映射尚未完成在线核验**；上述安全校验须在可访问 API 的环境中实际通过后，才算完成集成验证。测试中的子学科 ID 为明确标注的合成数据，不是声称核实的映射。

arXiv、bioRxiv、medRxiv 和旧 Crossref 后端可选 `keywords` 为字面短语列表，短语间取 OR、与分类取 AND，忽略大小写及标点分隔，不做语义扩展或自动词干推导。普通期刊完全不受这些关键词影响；不要把 arXiv 分类代码套用到其他平台。

### CPU embedding 性能与本地缓存

同一模型的权重默认可能为 bfloat16；缺少原生 BF16 指令的 CPU 会付出较高开销。`reranker.local.cpu_dtype: auto` 在具备原生 BF16 的 CPU 保留原精度，在其他 CPU 上将同一权重转为 float32；GPU 不受此设置影响。可显式选择 `native` 或 `float32`。没有更换模型、量化、缩短 8192 模型上限或删减候选；余弦相似度及时间衰减评分公式保持原样。不同精度存在浮点差异，临界相近分数可能交换位置，不能承诺跨精度逐位相同。

线程默认同时尊重 CPU affinity 和 cgroup 配额，也可通过 `reranker.local.cpu_threads` 指定正整数。日志只打印设备、精度、线程数、批大小、唯一文本数、缓存命中数、token 长度统计和编码耗时，不打印文库文本。默认批大小仍为 32；已有排序前 DOI/标题/投递历史去重保持不变，未缩短 7 天窗口或抽样候选。

开发机隔离基准（公开合成文本、同一模型 revision；不是 GitHub 实测）：将 oneDNN 的 CPU ISA 限制为 AVX2 后，4 条 303–453 token 文本、2 线程、batch=4，bfloat16 耗时 17.43 秒，float32 耗时 4.89 秒（约 3.56 倍）。最大余弦差 0.00129，样本邻居排序一致。本机原生 BF16 下，16 条 90–174 token 文本，4 线程比 1 线程快约 2.55 倍；增大 batch 从 4 到 16 未改善这组样本。因此默认不武断增大批大小。实际 GitHub CPU、文库长度和端到端吞吐仍须从下一次正式日志验证；90 分钟上限未上调。

本地 reranker 自动复用进程内向量。可选 `reranker.local.cache_dir` 开启本机磁盘缓存，默认 `null`。缓存命名空间包含实际模型 commit、模型名、编码参数（含 task/prompt）、prompt 定义、最大序列长度、精度、设备、线程数和相关库版本，文本使用哈希键；模型没有可确认的 commit 时禁用磁盘缓存。文件不存原文，但**向量和文本哈希仍是私密衍生数据**。缓存文件以私有权限原子写入，校验损坏后重算，不加载 pickle；磁盘写入失败保留内存结果。参数/模型变化会使旧缓存失效，旧目录可由用户自行清理。

缓存仅供本地安全位置使用，不要把目录设在 `public/`、共享路径或提交到 Git。工作流未添加任何向量 cache/artifact 上传，`paper-state` 的文件白名单未变；因此 GitHub 临时 runner 之间目前不复用私有向量。要实现跨运行持久缓存，需要先确认私有存放位置和访问范围。真实模型的 16 条样本缓存测试：冷调用 10.54 秒（含加载），同进程命中 0.0044 秒，新实例从磁盘命中 2.01 秒（含加载）；两次缓存结果与冷调用相似度矩阵逐位相同。

SMTP 994 与 465 均使用隐式 TLS；其他端口继续使用 STARTTLS，不允许明文降级。994 的修正依据 [CSTNET 官方客户端说明](https://help.cstnet.cn/changjianwenti/youjianshoufa/MailMaster.html)，保留 `mail.cstnet.cn:994`，无需改邮箱地址或端口。
