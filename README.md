# Zotero 每日论文推荐

基于 [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily)，保留并感谢上游作者与贡献者。代码沿用 [GNU AGPL v3](LICENSE)。本仓库继续使用原名称和 fork 关系，不迁移、不删除仓库。

## 功能与每日行为

1. 读取 Zotero 期刊论文、会议论文和预印本；按 collection 路径选择兴趣语料。有多个 collection 的论文只要命中任一排除路径就会被排除。没有可用摘要的 Zotero 条目不参与兴趣向量，但仍参与“已在文库”去重。
2. 从出版社 RSS/Crossref、arXiv、bioRxiv、medRxiv、Research Square 和 OpenReview 获取新论文。每个源按自己的日期窗口检索；来源错误会记录并导致任务失败状态，其他成功来源及成功投递记录仍保留。
3. DOI、URL、arXiv/Research Square 版本、规范化标题及持久历史共同去重。不同 DOI 的同名文章不会被随意合并。已有 Zotero 条目默认不再推荐。
4. 清理 HTML/JATS/转义实体。缺少摘要的 DOI 论文可依次从 Crossref 和 OpenAlex 合法公开元数据恢复，并验证返回 DOI；不编造摘要，不绕过付费墙。失败或仍缺摘要时明确显示 `No abstract available`，排序注明 `title only`。
5. 本地或 API embedding 与 Zotero 摘要比较，按文库加入时间加权；分数不是概率。先按相关性选 **25 篇期刊、15 篇预印本**，再从剩余符合条件且未见过的候选中**无放回随机选 5 篇**。随机组和前两组无重叠；OpenReview默认归预印本；经官方venue ID及pdate双重确认的已发表TMLR归期刊，已录用会议论文保留conference状态并归“预印本（含会议论文）”列。未知状态不猜测为已发表。
6. 不足时仅发送实际候选，日志显示各组实际数/目标数，不跨组补满、不重复、不扩大领域。等待重试的投递占用对应组名额；随机结果存入历史，失败重试不重新抽样。旧历史无组标记时按来源归期刊/预印本。
7. 邮件桌面为期刊、预印本、随机推荐三列，各列从 1 编号；窄屏（≤720px）竖排为三组。纯文本保留同样的三组与独立编号。使用 email-safe table，Aptos → Calibri → Arial → Helvetica → sans-serif，不下载字体。客户端可能忽略媒体查询，因此最终显示仍取决于客户端。
8. LLM 可按论文摘要/全文生成**中文一句话** TLDR，；作者单位仅保留来源元数据，不再调用付费模型提取。AI 成功、未启用、失败回退和旧摘要均标明来源；模型不可用时停止后续无效请求。没有摘要/全文不调用 TLDR API。用户已批准启用 LLM，每日上限 **¥0.20**；本分支取消定时强制关闭，但运行前必须通过下述预算校验。已依据公开API非推理模式约定建立保守峰值价预算守卫，待合并、初始化账本及启用配置后方可调用，中文提示词不代表已执行翻译。成功的中文摘要下方仍完整保留原摘要，分别标注；不会因成功生成摘要而隐藏或截断原文。
9. SMTP 成功后记录投递；支持本地显式启用 RSS。仓库工作流固定只发邮件，禁用 RSS/Pages；旧 CUSTOM_CONFIG 中 RSS 开关不能重新启用发布。SMTP 接受不等同于收件箱到账；SMTP 接受后进程崩溃但状态尚未保存仍存在邮件协议固有的重复窗口。

## 快速开始与安全测试

Python ≥3.13，依赖锁定在 `uv.lock`：

```bash
uv sync --frozen
uv run --frozen pytest                    # 隔离测试，不发真实邮件、不调用付费模型
uv run --frozen pytest -m slow            # embedding 模型测试，可能下载模型
uv run --frozen python scripts/preview_email.py  # 仅合成示例，生成 HTML/纯文本
```

Actions Secrets：`ZOTERO_ID`、`ZOTERO_KEY`、`SENDER`、`SENDER_PASSWORD`、`RECEIVER`；启用 LLM 才需要 `OPENAI_API_KEY`、`OPENAI_API_BASE`。密钥只能通过环境变量引用，不应放进普通变量或提交代码。

Actions Variables：`CUSTOM_CONFIG`（YAML mapping，无 `defaults`）、`PAPER_CONFIG`（默认 all）、`SMTP_SERVER`、`SMTP_PORT`、`LLM_MODEL`、`OPENAI_API_BASE`（可由同名 secret 提供）。`RSS_SITE_URL` 仅本地 RSS 有意义。工作流中的 embedding API 密钥需用户另行配置，默认使用本地模型。

**配置优先级：** preset → CUSTOM_CONFIG → 显式 dispatch overrides → 仓库投递策略。定时触发固定 `interests`、已保存兴趣列表、所有启用来源、25/15/5 配额、上限45、中文语言、强制¥0.20预算守卫（是否请求LLM遵循CUSTOM_CONFIG）、只邮件、收件人为 `RECEIVER`、历史路径为 `data/recommendations.json`。CUSTOM_CONFIG 可继续保留 SMTP、排除路径和窗口等非强制项。

每日 cron 为 **20:17 UTC（次日 04:17 Asia/Singapore）**，只在默认分支执行且可能延迟。推送 feature 分支不会启用其定时配置。`paper-state` 分支存储成功/待投递历史；不要清空、重置或删除。`Keep Alive` 沿用既有每30天定时，不新增任务。

手工 Actions → Send emails daily 的参数：

| 参数 | 值与作用 |
|---|---|
| `sources` | `configured` 保留配置；`all` 所有支持来源，再应用 enabled；`journals` 仅期刊 |
| `preprint_profile` | `configured` 保留配置；`interests` 使用仓库保存的完整兴趣列表 |
| `llm_mode` | `configured` 使用配置；`disabled` 禁止模型调用 |
| `window_days` | 留空保留各源窗口，或1–90统一覆盖 |
| `recipient` | 留空使用配置；填写一个邮箱只覆盖本次 |
| `output` | 历史值 configured/email/rss/both 仍接受，但工作流始终只邮件 |

实际发送的本地例子（**会发邮件**，先设置环境变量）：

```bash
uv run --frozen python -m zotero_arxiv_daily.main --config-name=interests llm.enabled=false
# 仅期刊，沿用全局上限而不用三组配额：
uv run --frozen python -m zotero_arxiv_daily.main --config-name=journals llm.enabled=false executor.max_paper_num=25
```

## 当前配置的脱敏示例

以下为已启用配置与每日投递策略的脱敏示例。已替换 collection 名称、用户标识与所有凭据；`archive/**` **仅为示例，不是用户真实排除路径**。主分支预算守卫及持久账本已生效，在线配置为中文摘要、DeepSeek-V4-Flash、每日¥0.20估算记账额度、25/15/5配额、OpenReview开启和RSS关闭。OpenReview官方登录及公开稿件检索已验证；LLM是否成功以每次运行的生成状态及预算账本为准。GitHub不能读回已有secret，所有凭据继续由用户管理。

```yaml
zotero:
  user_id: ${oc.env:ZOTERO_ID}
  api_key: ${oc.env:ZOTERO_KEY}
  include_path: null
  ignore_path: ["archive/**"]  # 脱敏占位，按自己的 collection 修改
email:
  sender: ${oc.env:SENDER}
  receiver: ${oc.env:RECEIVER}
  smtp_server: mail.cstnet.cn
  smtp_port: 994
  sender_password: ${oc.env:SENDER_PASSWORD}
llm:
  enabled: true
  budget: {enabled: true, daily_cny: 0.20}
  language: Chinese
  input_mode: abstract
  api:
    key: ${oc.env:OPENAI_API_KEY}
    base_url: ${oc.env:OPENAI_API_BASE}
  generation_kwargs:
    model: deepseek-ai/DeepSeek-V4-Flash  # 配置值，尚未实测可用性
executor:
  source: [journals, arxiv, biorxiv, researchsquare, openreview]
  max_paper_num: 45
  quotas: {journals: 25, preprints: 15, random: 5}
  debug: false
  send_empty: true
source:
  arxiv: {window_days: 1}
  biorxiv: {window_days: 1}
  researchsquare: {window_days: 1}
abstracts:
  enabled: true
  max_papers: 50
output:
  email: {enabled: true}
  rss: {enabled: false}
preprint_interests:
  arxiv:
    categories: [physics.chem-ph, physics.comp-ph, cond-mat.mtrl-sci, cond-mat.soft, cs.LG, cs.AI]
  biorxiv:
    categories: [biophysics, biochemistry]
  medrxiv: {enabled: false}
  researchsquare:
    backend: openalex
    source_ids: [S4306525896]
    type: [preprint]
    subfield: [Physical and Theoretical Chemistry, Materials Chemistry, General Materials Science, Condensed Matter Physics, Artificial Intelligence, Biophysics, Biochemistry]
  openreview:
    enabled: true
    venues: [ICLR, NeurIPS, ICML, TMLR, CoRL]
    subject_areas: [AI for Science, Machine Learning, Scientific Machine Learning]
    keywords: [atomistic, molecular, chemistry, materials, molecular dynamics]
```

## 全参数参考

完整默认值见 [config/base.yaml](config/base.yaml)。`???` 必须由 preset、CUSTOM_CONFIG 或环境补齐；`${oc.env:NAME,default}` 为环境引用。CLI 使用 `section.key=value` 覆盖，列表例子：`'zotero.ignore_path=["archive/**"]'`。

### 文库、排序、执行

| 参数 | 默认/意义与例子 |
|---|---|
| `zotero.user_id`, `api_key` | Zotero 用户 ID / 只读 API key，均必填 |
| `zotero.include_path`, `ignore_path` | null 或 glob 字符串列表；如 `["2026/reading/**"]`。排除优先，空列表不筛选。`name` 与 `name/**` 的路径含义不同，按实际 collection 层级选择 |
| `executor.source` | 启用来源列表；journals/arxiv/biorxiv/medrxiv/researchsquare/openreview |
| `executor.reranker` | `local`（默认）或 `api` |
| `executor.quotas` | base=null 使用旧全局上限；interests={journals:25,preprints:15,random:5}。三个非负整数，总和>0且≤max_paper_num |
| `executor.max_paper_num` | base=100，interests/定时=45；限制单次推荐与投递 |
| `executor.min_score` | -10；低于该相关性分数的不参与任何组，包括随机 |
| `executor.exclude_existing` | true；排除已在整个 Zotero 文库中的论文，不只兴趣子集 |
| `executor.send_empty` | false；允许无新论文时发空邮件，存在源错误时不会伪装成正常空结果 |
| `executor.debug` | false；保留兼容的旧调试选项，当前SMTP不输出会话内容 |
| `executor.fetch_full_text` | 保留旧配置兼容；摘要输入现由llm.input_mode控制，abstract模式不下载全文，full_text模式只对选中论文、可调用模型时获取支持的全文 |
| `executor.enrichment_workers` | base=1，journal 系列=4；1–8，仅 llm.input_mode=abstract 时并发；全文提取不进入线程池 |
| `reranker.local.model` | `jinaai/jina-embeddings-v5-text-nano-retrieval` |
| `reranker.local.revision` | null；可锁定模型 commit/revision |
| `reranker.local.cpu_dtype` | auto；允许auto/native/float32；auto在不支持原生BF16时转float32，native保留模型dtype |
| `reranker.local.cpu_threads` | null；尊重 CPU affinity/cgroup，可显式设线程数 |
| `reranker.local.cache_dir` | null；可选本地 embedding 缓存，含私有向量，不上传 |
| `reranker.local.encode_kwargs` | 传给模型 encode，默认 `{task: retrieval, prompt_name: document}` |
| `reranker.api.key`, `base_url`, `model`, `batch_size` | API embedding 配置；如环境key、OpenAI兼容URL、模型名、64；只在api模式使用 |

### 来源与兴趣过滤

`preprint_interests: null` 保留旧 source 分类；否则为平台 mapping。`enabled: false` 禁用平台；未列入 executor.source 的平台不会仅因 enabled=true 自动运行。分类内 OR，关键词内 OR，不同过滤维度 AND。关键词为大小写无关的字面词/短语，按词边界匹配 title 或 abstract，不跨两个字段拼接，不支持通配符。`categories: null` 继承 source.category；空数组无效。

| 参数 | 默认/意义与例子 |
|---|---|
| `preprint_interests.<platform>.enabled` | true；支持所有五个预印本平台 |
| `preprint_interests.<platform>.keywords` | 可省略；非空短语列表，如 `[molecular dynamics, materials]` |
| `preprint_interests.<arxiv/biorxiv/medrxiv>.categories` | 各平台原生分类。arXiv如 `[cs.LG, physics.chem-ph]`；bioRxiv如 `[biophysics]`；`['*']` 全分类 |
| `source.<arxiv/biorxiv/medrxiv>.category` | base=null；legacy分类配置，被对应 interests.categories 覆盖 |
| `source.arxiv.include_cross_list` | false；RSS模式是否包括交叉分类 |
| `source.arxiv.window_days` | base=null 使用最新RSS announcement；all=7，以日期窗口检索 API；当前私有配置覆盖为1 |
| `source.<biorxiv/medrxiv>.window_days` | base=null 使用原始模式；all=1，按日期窗口分页 |
| `source.<biorxiv/medrxiv>.max_pages` | 100；分页安全上限，达到未完成时报告错误 |
| `source.<platform>.conversion_delay` | 可选0秒；BaseRetriever 转换间隔（OpenReview自行转换，不使用此项） |
| `source.researchsquare.window_days`, `max_pages`, `mailto` | 1、100、null；日期窗口、页上限、Crossref polite pool 联系邮箱 |
| `preprint_interests.researchsquare.backend` | `openalex` 使用公开OpenAlex；未设时旧Crossref backend |
| `preprint_interests.researchsquare.source_ids` | `[S4306525896]`；实时核对确为Research Square，不能猜ID |
| `preprint_interests.researchsquare.type` | 仅 `[preprint]` |
| `preprint_interests.researchsquare.subfield` | 非空OpenAlex正式子领域名称列表；实时按精确名称解析，重名取所有精确匹配 |
| `source.openreview.enabled` | true；可以关闭来源；interests中的 enabled 优先 |
| `source.openreview.venues` | ICLR、NeurIPS、ICML、TMLR、CoRL；interests.openreview.venues 可覆盖 |
| `source.openreview.subject_areas` | AI for Science、Machine Learning、Scientific Machine Learning；interests 同名参数优先，不支持的值报错 |
| `source.openreview.keywords` | atomistic、molecular、chemistry、materials、molecular dynamics；interests 同名参数优先 |
| `source.openreview.window_days`, `max_pages` | 7、100；1–90天，正整数页上限；每页最多1000条 |
| `source.journals.presets` | jacs,jctc,prl,nature_family,science,science_advances,jcp,jpcl,pnas,acs_catalysis,npjcompumats,angew,chemical_science,mlst |
| `source.journals.custom` | []；自定义 `{id: example, title: Example Journal, issns: ['1234-5679'], rss: 'https://publisher.example/feed'}`，必须提供可核验ISSN或正式名称；示例ISSN只是格式演示 |
| `source.journals.window_days` | 7；从UTC零点向前回溯到现在，以出版日期而非索引日期筛选 |
| `source.journals.workers` | 4；1–16并发期刊；Crossref请求全局限速 |
| `source.journals.max_pages` | 100；每ISSN Crossref cursor分页上限 |
| `source.journals.use_rss` | true；合并出版社RSS与Crossref，不只依赖RSS覆盖历史窗口 |
| `source.journals.discover_nature` | true；刷新Nature官方目录，缓存7天 |
| `source.journals.require_live_catalog` | base=false，journals及派生preset=true；刷新失败时显式报错而非声称完整覆盖 |
| `source.journals.catalog_cache` | data/journal_catalog.json；目录及ISSN缓存 |
| `source.journals.mailto` | null；Crossref请求联系邮箱，勿将私人邮箱写入公共示例 |
| `abstracts.enabled`, `max_papers`, `mailto` | base=false、50、null；interests/定时启用。最多对50个缺摘要且有DOI候选查询两个元数据源；401/403/429后跳过该provider，保留警告 |

### OpenReview 的真实范围与限制

仅主会议，不含workshops。API v2 `/groups` 核验组，再读取其 `submission_id` 或 `submission_name`，按 submission invitation 拉取公开 notes，避免仅按 accepted venueid 丢失审稿中论文。年度组覆盖当前/上一年，ICLR另含下一年；TMLR无年度。修改时间扫描窗口内实际更新记录，未发表记录日期优先 `odate`（首次公开），缺失时退至 cdate/tcdate（创建时间）；经venue ID和pdate核验的已发表记录使用pdate，以包含新发表的旧投稿。无持久增量水位，重叠窗口+delivery history去重，因此失败不会推进水位；窗口外首次公开但元数据日期错误的记录可能无法恢复，不能声称完整历史覆盖。

官方content字段按 `.value`解包，subject标量/数组都支持。NeurIPS `ai_4_physical_sciences` / `machine_learning_for_sciences`、ICML `applications->chemistry_physics_and_earth_sciences`、ICLR physical sciences 标签映射到科学方向。TMLR和部分CoRL年份无subject；请求包含宽泛的 **Machine Learning** 时，核验的这五个ML主venue作为明确记录的venue-scope fallback，仍必须通过领域关键词过滤。仅配置科学方向且无subject时，用title/abstract/author关键词中的明确科学ML短语匹配；否则排除。每篇保存 `subject_match_reason`，日志报告fallback数。

关键词仅匹配标题、摘要或作者keywords，包括CoRL `primary_keyword`、`secondary_keyword`、`free_keyword_1/2`；**绝不使用subject标签充当关键词**。venue/subject/keyword组间AND，组内OR。不声称OpenReview有统一subject taxonomy。不绕过403 challenge；403、限流、分页不完整或schema缺失均为可见来源失败。API v1-only旧年份不伪装成功。

参考：[官方检索说明](https://docs.openreview.net/how-to-guides/data-retrieval-and-modification/how-to-get-all-notes-for-submissions-reviews-rebuttals-etc)、[Note日期字段](https://docs.openreview.net/reference/api-v2/entities/note/fields)、[API v2规范](https://api2.openreview.net/docs/api.yml)。

### LLM、邮件、输出与历史

| 参数 | 默认/意义与例子 |
|---|---|
| `llm.enabled` | base=true；私有配置控制启用，定时不再强制关闭。预算守卫未验证时仍不调用 |
| `llm.budget.enabled`, `daily_cny` | true、0.20；所有Actions强制启用且上限0.20，不能被CUSTOM_CONFIG绕过。本地将其设为false也只会禁止模型调用，不会开放无限额调用 |
| `llm.input_mode` | `abstract`（默认）或 `full_text`。abstract不为摘要下载全文；full_text尝试来源支持的合法全文，发送完整提取文本，若不可得/超上下文/超预算输入界限则明确回退abstract，邮件和日志注明原因 |
| `llm.language` | Chinese；prompt明确要求一句话，失败不将英文原摘要冒称中文摘要 |
| `llm.api.key`, `base_url` | OpenAI兼容服务key/URL；环境引用，不输出值 |
| `llm.generation_kwargs` | 保留model选择；其他历史参数不再原样透传。受控请求固定max_tokens=96、n=1、enable_thinking=false，不允许额外参数绕过预约。历史max_tokens=16384不会生效 |
| `email.sender`, `receiver`, `sender_password` | 发件人、单收件人、SMTP授权码/密码；工作流从secrets解析 |
| `email.smtp_server`, `smtp_port` | 如smtp.qq.com/465，当前配置为mail.cstnet.cn/994；465/994使用SMTP_SSL，其他端口要求STARTTLS |
| `output.email.enabled` | true；工作流强制true |
| `output.rss.enabled` | false；仅显式本地调用可启用，工作流强制false |
| `output.rss.path`, `title`, `site_url` | public/feed.xml、Daily Paper Recommendations、空；RSS文件、频道名、站点地址 |
| `output.rss.retention_days`, `max_items` | 30、300；feed保留时间/条数 |
| `state.enabled` | base=false，journals及派生preset=true；RSS也强制启用本地历史 |
| `state.path`, `retention_days` | data/recommendations.json、90；原子写入JSON。超保留期的历史不再去重 |

Preset：`base`仅默认，`custom`环境和旧arXiv示例，`legacy`组合二者；`journals`配置通用凭据和期刊；`preprints`四个既有预印本平台；`all`期刊及全部预印本含OpenReview；`interests`在all上应用当前兴趣和25/15/5；`default`等同all；`arxiv/biorxiv/medrxiv/researchsquare`为单平台。历史preset仍保留兼容，不是重复废代码。

## 维护与排错

`Executor`组织取文库→源检索→去重→摘要恢复→排序/分组→摘要生成→状态/投递。`retriever/`注册来源，`reranker/`注册embedding实现，`selection.py`统一分组和待投递配额，`abstracts.py`处理合法元数据恢复，`protocol.py`记录摘要来源/分组，`construct_email.py`生成三列HTML及纯文本，`state.py`保存投递，`scripts/prepare_workflow.py`处理有效策略，`scripts/workflow_state.py`持久化Git状态。

无结果先查源错误、窗口、关键词、ignore_path、阈值、文库及历史去重；不要通过清历史强行再发。模型失败看结构化摘要状态，不打印API响应体或secret。OpenReview403是访问限制，不是没有论文。Crossref未索引但出版社RSS正常时会标注覆盖限制；临时网络错误仍失败。测试工作流不载入真实投递凭据；只有手工运行发送工作流才发送真实邮件。

## ¥0.20/UTC日预算与激活条件

仅约束本仓库受控请求，不是账户级扣费上限；其他客户端或旧版本不在保护范围内。所有公开摘要生成入口均要求BudgetRequests预约；缺失budget配置默认要求守卫，false禁止调用而不是无限额。付费单位提取已移除，保留出版社单位元数据。

保留用户选择 `deepseek-ai/DeepSeek-V4-Flash`。已核对[公开API约定](https://docs.siliconflow.cn/docs/api/chat-completions-post)及[官方价格](https://www.siliconflow.cn/pricing)，使用 `https://api.siliconflow.cn/v1`（仅默认443端口）、`enable_thinking=false`、n=1、max_tokens=96，不传reasoning_effort/thinking_budget，不重试、不自动补写。即使在低价时段也按输入3元/百万、输出9元/百万的峰值计费。价格记录核验于2026-10-01；建议于2026-10-08 UTC结束前复核。2026-10-09 UTC起若未复核，继续按最后复核费率运行并保留每日¥0.20估算记账额度，同时在邮件（HTML及纯文本）和日志醒目提醒；供应商涨价时实际费用可能超过估算及¥0.20，不承诺实际费用的绝对硬上限。已知新价格时应更新费率并按预算减少请求。登录保护的用户控制台未被读取，不声称已验证其中的账户设置。

用户输入最多768 UTF-8字节，系统输入最多256字节，再保留128 framing tokens，共按1152输入token上界预约。原摘要仍在邮件完整保留，仅模型上下文缩短。每次最坏预约 `(1152×3 + 96×9)/1,000,000 = ¥0.00432`，45篇合计¥0.1944。预算是基于已发布接口约定的工程上限，不是对供应商未来涨价或违规计费的绝对保证。

任何调用前须在共享paper-state分支的llm_budget.json原子提交当天完整额度。普通fast-forward push实现并发比较交换，不强推、不改投递记录。失败、账本损坏/丢失、当天已预约均不调用；崩溃/超时不返还额度，当天后续运行回退原摘要。运行中串行预扣及实际请求，跨UTC日停止；模型ID、usage缺失/无效、token计数越界、reasoning_content/reasoning_tokens异常会终止所有排队调用。实际付费请求边界强制client.with_options(max_retries=0)，直接调用库函数也不能保留SDK默认重试。

首次激活需明确初始化（本命令会向paper-state添加账本，但不调用模型）：

```bash
uv run --frozen python scripts/bootstrap_budget.py --initialize-new-ledger
```

初始化必须使用非浅克隆的完整历史，并核对远端账本。只允许该分支从未存在账本时初始化；若账本曾存在后丢失，必须恢复旧账本而非重建空额度。常规推荐路径绝不自动初始化。先将守卫代码合并并初始化，再在CUSTOM_CONFIG设置llm.enabled=true；旧main不应先启用。endpoint/model/密钥值仍由现有secret和配置解析，不打印或改写凭据。付费验证必须占用同一UTC日预约，不能另开不计费的“测试”路径。

### OpenReview访问故障的实际影响

当前WSL测试：五个venue的/groups均成功，/notes均返回403。不会绕过挑战或将403当作零论文。执行器会继续其他成功来源，发送并记录选中的论文，然后以“来源不完整”错误结束，因此Actions显示失败，即使邮件已成功；重跑仍用历史去重，不清空已送记录。

恢复需要合法API访问恢复并重新验证notes请求，或操作者明确选择一次不含OpenReview的手工来源组合（例如sources=journals）。这会降低覆盖范围，不能称为OpenReview修复。定时任务的保存兴趣配置仍启用OpenReview，不能靠CUSTOM_CONFIG里被覆盖的interest开关假装已关闭。没有自动静默禁用或代理绕行。

### 选择摘要输入

```yaml
llm:
  input_mode: abstract # 默认：只用原摘要，不下载全文
# 若确需原文：改为 full_text
```

`full_text`当前复用arXiv合法HTML/PDF/TeX提取（HTML含表格），不承诺所有出版社全文可获取，不绕过付费墙。输入是完整的已提取文本，仍可能受文档解析质量影响。其他来源未提供全文时回退abstract；不会把截断前缀标为全文。全文超过当前768字节用户输入界限时回退，该小额度通常只能支持摘要。回退不额外调用模型，也不隐瞒原因：`summary_input_source`及`summary_input_fallback`保存在状态，并显示在邮件元信息。无原摘要可回退时不声称成功生成。无论输入选哪一种，成功摘要后的原摘要仍完整显示。

## OpenReview 官方登录与访问限制

公开匿名请求可能收到403挑战或429限流；这不是“没有论文”。官方网页[挑战提示](https://github.com/openreview/openreview-web/blob/master/app/challenge/route.js)提供登录路径，本实现遵循[官方API v2客户端](https://github.com/openreview/openreview-py/blob/master/openreview/api/client.py)的 `/login` 与 Bearer 会话流程。

在仓库 **Settings → Secrets and variables → Actions → New repository secret** 分别设置 `OPENREVIEW_USERNAME`（账户登录邮箱/用户名）及 `OPENREVIEW_PASSWORD`（账户密码）；不要发到聊天、提交到代码或放入 CUSTOM_CONFIG。两者都缺省时继续匿名公开API；只填一个时明确报错。工作流仅向检索步骤注入这两个环境变量。登录失败只记录脱敏错误，令牌仅留在内存，不输出响应正文、不复制浏览器cookie、不绕过挑战、不换IP。启用MFA的账户可能无法无人值守登录，此时明确停止OpenReview检索，请通过官方支持解决账户认证，程序不会自动发送验证码或降低账户安全设置。

即使登录成功也只接收 `readers` 明确包含 `everyone` 且 `nonreaders` 缺省或为空列表的公开稿件；非空或格式异常的排除列表会拒绝该稿件，字段级排除列表同样使对应字段不可用，私有审稿内容不进入摘要/邮件。403应核查账号访问权限；429应等待下一次运行并遵守限流，登录不保证解除所有服务端限制。其他来源仍可投递并保存历史，随后任务以源不完整失败；不得将错误伪装成空结果。


凭据设置后可在 Actions 的 **Test** 工作流选择 `mode=openreview`，在待验证分支运行只读验证：仅注入上述OpenReview secrets，不注入邮件/LLM凭据，不访问投递历史、不调用模型。先验证登录和TMLR公开样本，通过后检查其余四个会场；总请求不超过22、每秒最多一次、不重试或跟随重定向。日志仅含阶段、HTTP状态及计数，不保存稿件或认证响应。每会场最多10条近期修改样本；匹配数为该有限样本通过原有日期/主题/关键词条件的结果，不代表当天完整配额。


`mode=openreview_full` 执行完整的有界覆盖诊断：所有已发现的当前/前一年会场及下一年ICLR，按投稿邀请分页，每页1000条、最多160次请求，逐阶段输出公开ACL、日期、关键词和主题计数。使用官方Notes分页参数；不以创建日期截断，避免漏掉早投稿而新公开的论文。生产检索也完整分页后应用原日期窗口，达到页数上限会明确报错。历史正例诊断只在内存中评估相同主题条件，不改变生产日期窗口，不输出论文或账号内容。

2026-10-01公开API验证：55,589条公开记录、129条在原7天窗口内、1条TMLR同时匹配日期/关键词/主题，历史同主题正例1,164条。各会场并非每天发布新稿；不能把每组10条最近修改记录的小样本零命中解释为生产零结果，配额不足仍按实际合格数量投递。
