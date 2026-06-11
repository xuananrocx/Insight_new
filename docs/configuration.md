# 配置详解

所有配置集中在项目根的 `config.yaml`。本地覆盖请创建 `config.local.yaml`（已被 gitignore）。

## 配置覆盖规则

```
config.yaml（仓库内，团队共享）
    ↓ 深度合并（deep merge）
config.local.yaml（本地，gitignored）
    ↓ 最终生效
```

例：在 `config.local.yaml` 写：

```yaml
llm:
  chat_provider: moonshot    # 临时切到 Moonshot 测试
```

只覆盖这一项，其他保持 config.yaml 的值。

## 关键配置项说明

### feature_flags

所有功能的总开关。**默认值遵循安全优先原则。**

```yaml
feature_flags:
  knowledge_base:
    enabled: true                  # 知识库主功能
    auto_extract_package: true     # 程序包自动解包
    image_ocr: false               # OCR（高资源消耗，默认关）

  qa:
    enabled: true
    web_ui: true                   # 启用 Web 对话界面
    show_sources: true             # 答案显示引用来源
    feedback_buttons: true         # 启用点赞/点踩

  feedback_loop:
    manual_feedback: true          # 通道1：人工点赞反哺
    log_feedback: false            # 通道2：日志反哺（Phase 2）
    auto_feedback:                 # 通道3：AI 自动反哺
      enabled: false               # 默认关
      require_approval: true       # 即使开启也要人工审批
      min_confidence: 0.85         # AI 置信度阈值

  realtime_watch: false            # 文件夹实时监听（Phase 2）
```

### paths

```yaml
paths:
  feed_folder: ~/AMD-Knowledge-Feeds    # 投喂目录（可改绝对路径）
  vector_db: ./data/vector_db           # Chroma 数据
  extracted: ./data/extracted           # 程序包解压临时
  feedback_queue: ./data/feedback_queue
  raw_uploads: ./data/raw_uploads
  metadata_db: ./data/metadata.db       # SQLite 元数据
  cache: ./data/cache
```

`~` 会自动展开为用户家目录。`./` 相对项目根。

### llm

多 provider 配置，按需启用。

```yaml
llm:
  chat_provider: deepseek              # 当前用谁对话
  embedding_provider: qwen             # 当前用谁做 embedding
  fallback_chain: [deepseek, qwen, moonshot]   # 故障切换顺序

  providers:
    deepseek:
      api_key_env: DEEPSEEK_API_KEY    # 从 .env 读
      base_url: https://api.deepseek.com/v1
      chat_model: deepseek-chat
      embedding_model: null            # DeepSeek 暂无 embedding
      enabled: true

    qwen:
      api_key_env: DASHSCOPE_API_KEY
      base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
      chat_model: qwen-plus
      embedding_model: text-embedding-v3
      embedding_dimensions: 1024
      enabled: true
    ...
```

**切换 provider 三步：**
1. 确认对应 API key 已配置在 `.env`
2. 修改 `chat_provider` 或 `embedding_provider`
3. 重启服务

### package_extraction

程序包解包规则，全部走配置，**改规则不改代码。**

```yaml
package_extraction:
  supported_archives:          # 哪些扩展名视为压缩包
    - .tar.gz
    - .tar
    - .zip
    - .gz

  extract_file_whitelist:      # 解压后只处理这些后缀
    - .md
    - .txt
    - .pdf
    - .docx
    - .xlsx
    - .csv
    - .json
    - .xml
    - .ini
    - .conf
    - .yaml
    - .sh
    - .py
    - .h              # 头文件常含错误码定义
    - .sql
    - .log
    # ... 完整列表见 config.yaml

  extract_file_blacklist:      # 黑名单优先级高于白名单
    - .dll
    - .exe
    - .so
    - .bin
    # ...

  skip_directories:            # 跳过的目录（支持通配符）
    - __MACOSX
    - .git
    - node_modules

  max_file_size_mb: 50         # 单文件大小上限
  cleanup_after_days: 7        # 解压目录清理周期
```

**新增文件类型**：编辑白名单 + 重启即可。

### parser

文档解析选项。

```yaml
parser:
  max_file_size_mb: 50
  pdf_mode: text_only          # text_only / with_tables
  chunk:
    size: 500                  # 每块字符数
    overlap: 50                # 块之间重叠
```

`chunk.size` 影响 RAG 质量：
- 太小：上下文不足，答案片面
- 太大：检索精度下降，token 浪费
- 推荐 300-800，500 是平衡点

### rag

RAG 检索参数。

```yaml
rag:
  top_k: 5                     # 检索前 K 个 chunk
  similarity_threshold: 0.3    # 最低相似度
  context_window: 4000         # 拼给 LLM 的最大上下文字符数
```

## 环境变量（.env）

API key 等敏感信息放 `.env`，不进仓库。

```bash
DEEPSEEK_API_KEY=sk-xxx
DASHSCOPE_API_KEY=sk-xxx
MOONSHOT_API_KEY=sk-xxx
OPENAI_API_KEY=sk-xxx
APP_ENV=development
APP_LOG_LEVEL=INFO
```

`.env` 通过 `python-dotenv` 自动加载，代码里用 `os.getenv("DEEPSEEK_API_KEY")` 读取。
