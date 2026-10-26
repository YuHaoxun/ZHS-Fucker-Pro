# ZHS Fucker Pro

智慧树（zhihuishu）网课自动化工具。图形界面为主，命令行向下兼容。

当前版本 **2.6.3** ｜ Python **3.10+** ｜ 优先支持 Windows

---

## 这是什么

一个直接调网站接口、绕开前端页面的自动化工具，用来处理智慧树上那些纯机械的活：

- 校内学分课（hike）与知到共享学分课的视频学习
- 视频弹题自动作答
- AI 课（页面上带「掌握度」的那种）的资源学习与知识点测验
- 普通共享课的章节平时测试（实验功能）

因为不走浏览器前端，也就绕开了「检测到 DevTools 就停止播放」那套限制。

**它不做什么**：不解人脸识别，不绕过验证码（撞上会停下来等你手动解），不代考期末考试。
每节末尾都写了已验证范围，没验证过的题型不会硬套上去。

---

## 功能一览

| 能力 | 状态 |
|---|---|
| 校内学分课（hike）视频 | 可用 |
| 知到共享学分课视频 | 可用 |
| 视频弹题自动作答 | 可用 |
| AI 课资源学习 | 可用 |
| AI 课知识点测验（单选/多选/判断/填空） | 可用，需配置 AI 接口 |
| 普通共享课章节平时测试（题型 1/2/14） | 实验，已在六份卷子上实测 |
| 风控验证码 | 暂停等人工，解完自动续跑 |
| 见面课 / 互评 / 期末考试 / 组合题 / 图片附件题 | **不支持** |

---

## 环境要求

- **Python 3.10 及以上**（代码里用了 `match` 和 `X | Y` 类型标注，3.9 会直接语法报错）
- 依赖包（全部必需，缺任何一个都起不来）：

| 包 | 用途 |
|---|---|
| `requests` | 全部 HTTP 请求 |
| `pycryptodome` | 知到 / 共享课接口的 AES、RSA |
| `Pillow` | 二维码与图片显示 |
| `OpenAI` | AI 答题 |
| `tiktoken` | 课件长文本切分估算 |

图形界面用的是 `tkinter`，Python 官方安装包自带，不用额外装。
即使只想刷视频，`fucker.py` 顶部也会 `import tiktoken` 和 `from openai import OpenAI`，所以这五个包一个都不能省。

---

## 安装

```bash
git clone https://github.com/YuHaoxun/ZHS-Fucker-Pro.git
cd ZHS-Fucker-Pro
pip install -r requirements.txt
```

或者直接下载 ZIP 解压，效果一样。

---

## 快速开始（图形界面）

```bash
python gui.py
```

Windows 上也可以双击 `启动控制器.bat`（无黑框启动），
或者先跑一次 `创建桌面快捷方式.py`，在桌面生成「ZHS Fucker Pro」图标。

界面布局：

| 区域 | 内容 |
|---|---|
| 左上 | 状态灯 + `AI 答题设置` |
| **1. 登录** | `扫码登录` / `重新扫码（清凭证）` / `打开二维码文件夹` / `清除登录状态` |
| **2. 课程** | `拉取课程清单` / `刷新清单` / `编辑 AI 课清单` / `共享课平时测试` / `开始刷选中的课` / `AI课仅学习资源（不答题）` |
| **3. 可选设置** | 播放倍速（预设「快捷」默认 3 倍）、指定课程 ID |
| 右侧 | 课程清单预览，可直接编辑并 `保存清单修改` / `用记事本打开` |
| 底部 | 运行日志，带 `清空` / `停止` |

典型流程四步：

1. 点 `扫码登录`，用「知到」App 扫码
2. 点 `拉取课程清单`，右侧出现你所有的课
3. 勾选要刷的课
4. 点 `开始刷选中的课`

`AI课仅学习资源（不答题）` 是纯挂时长模式，不消耗任何 AI 额度，也不需要填密钥。

---

## 登录

**目前只支持二维码登录**，账号密码登录已被平台关闭（`main.py` 里 `qrlogin` 强制为真）。

- 扫码用「知到」App：底部「我的」→ 右上角「扫一扫」。**微信扫码无效**。
- 二维码会在界面里弹窗显示，同时存进 `qr/` 目录；过期会自动刷新，不用管。
- 登录成功后若 `save_cookies` 为真，凭证写入 `cookies.json`，短期内免扫码。
- `cookies.json` 等同于你的账号，**不要提交到仓库，也不要发给别人**。

网页端登录 ≠ 脚本能登录。脚本只认自己扫码拿到的那套 cookie，浏览器里登录了没用。

---

## 刷课

### 课程 ID 从哪来

| 课程类型 | ID 位置 |
|---|---|
| 校内学分课 | 网址里的 `courseId` |
| 知到共享学分课 | 网址里的 `recruitAndCourseId` |
| AI 课 | 需要 `courseId` + `classId` 两个，只能从清单里拿，不能手填 |

### 拉清单

图形界面点 `拉取课程清单`，或命令行：

```bash
python main.py --fetch
```

会写出两份文件：

- `execution.json` — 校内学分课 / 共享学分课，用 `-c` 刷
- `ai_courses.json` — AI 课，用 `-ai <courseId> <classId>` 刷

两份都能手动编辑，删掉不想刷的条目即可。改完记得检查 JSON 语法。

### 速度与时限

默认播放速度是站点允许的最大值。GUI 的「快捷」预设默认 3 倍。
`-l 25` 表示单节课最多刷 25 分钟——想拿「习惯分」的时候用。

速度别开太夸张，进度跳变太明显容易触发风控。

---

## 命令行用法

```bash
python main.py                          # 有清单就按清单刷，没有就刷全部
python main.py -c 123456                # 只刷指定课程，可传多个
python main.py -c 123456 -v 1989 604    # 只刷指定视频
python main.py -s 4 -l 25 -d            # 4 倍速 / 单课 25 分钟 / 调试日志
python main.py -c 123456 --proxy http://127.0.0.1:8080
python main.py -ai 1234567890 654321            # AI 课：学习 + 答题
python main.py -ai 1234567890 654321 --noexam   # AI 课：只学资源
```

参数表：

| 参数 | 说明 |
|---|---|
| `-c, --course` | 课程 ID，可输入多个 |
| `-v, --videos` | 视频 ID，可输入多个 |
| `-u, --username` / `-p, --password` | 账号密码，**当前无效**，仅保留 |
| `-q, --qrlogin` | 二维码登录（强制开启，这个开关实际恒真） |
| `-s, --speed` | 播放倍速，想要秒过可以调高，但不推荐 |
| `-t, --threshold` | 完成阈值，默认 `0.91`；填大于 `1.0` 可重复刷 |
| `-l, --limit` | 单节课时限（分钟），`0` 为不限 |
| `-d, --debug` | 调试日志，**会记录请求内容，别把日志外发** |
| `-f, --fetch` | 拉取课程清单并写入 `execution.json` / `ai_courses.json` |
| `--proxy` | 代理，支持 `http://` `https://` `socks5://`，或用 `all://` 同时设置 |
| `--show_in_terminal` | 把二维码打印到终端 |
| `--image_path` | 二维码保存目录，留空则不保存 |
| `--tree_view` / `--progressbar_view` | 目录树 / 进度条开关。**注意**：参数类型是 `type=bool`，命令行传任何值都会变成 `True`，想关只能改 `config.json` |
| `-ai, --aicourse <COURSE_ID> <CLASS_ID>` | 刷 AI 课，需要两个参数 |
| `--noexam` | 配合 `-ai` 使用，只学资源不答题，**不需要任何 AI 密钥** |
| `-h, --help` | 显示帮助 |

信息优先级：**命令行 > 配置文件 > 交互输入**。

---

## 配置文件

首次运行会自动生成 `config.json`，字段的参考版本在仓库根目录的 `config.example.json`。
不用手动复制，程序自己生成的那份才生效。

```json
{
    "username": "",
    "password": "",
    "qrlogin": true,
    "save_cookies": true,
    "proxies": {},
    "logLevel": "INFO",
    "tree_view": true,
    "progressbar_view": true,
    "check_update": false,
    "qr_extra": {
        "show_in_terminal": null,
        "ensure_unicode": false
    },
    "image_path": "",
    "captcha": {
        "wait": true,
        "timeout": 900,
        "interval": 20,
        "max_retry": 3
    },
    "pushplus": {
        "enable": false,
        "token": ""
    },
    "bark": {
        "enable": false,
        "token": "https://example.com/xxxxxxxxx"
    },
    "config_version": "1.5.0",
    "ai": {
        "enabled": true,
        "use_zhidao_ai": true,
        "openai": {
            "api_base": "https://api.openai.com",
            "api_key": "sk-",
            "model_name": "claude-3-5-sonnet-20240620"
        },
        "ppt_processing": {
            "provide_to_ai": false,
            "moonShot": {
                "base_url": "https://api.moonshot.cn/v1",
                "api_key": "sk-",
                "delete_after_convert": true
            }
        },
        "use_stream": true
    }
}
```

字段说明：

| 字段 | 默认 | 说明 |
|---|---|---|
| `username` / `password` | 空 | 账号密码，仅 `-u/-p` 相关逻辑使用，当前无效 |
| `qrlogin` | `true` | 二维码登录，强制启用 |
| `save_cookies` | `true` | 登录后保存 `cookies.json`，短期免扫码 |
| `proxies` | `{}` | 代理，如 `{"https": "http://127.0.0.1:7890"}`；Windows 上也能解决 Clash 造成的证书错误 |
| `logLevel` | `INFO` | `NOTSET` / `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `tree_view` | `true` | 是否打印课程目录树 |
| `progressbar_view` | `true` | 是否打印视频进度条 |
| `check_update` | `false` | 启动时查 GitHub 是否有新版。默认关，因为 `raw.githubusercontent.com` 国内很慢，而每次启动都会跑一遍 |
| `qr_extra.show_in_terminal` | `null` | 终端打印二维码；`null` 表示 Windows 下默认开启 |
| `qr_extra.ensure_unicode` | `false` | 只用 Unicode 字符画二维码 |
| `image_path` | 空 | 二维码保存目录，留空不保存 |
| `captcha.wait` | `true` | 撞到验证码时暂停等人工解决；关掉就回到「直接放弃这门课」 |
| `captcha.timeout` | `900` | 单次等待验证码的最长秒数 |
| `captcha.interval` | `20` | 探测风控是否解除的间隔秒数 |
| `captcha.max_retry` | `3` | 同一个视频因验证码最多重试几次 |
| `pushplus.enable` / `token` | `false` / 空 | [pushplus](https://www.pushplus.plus) 推送 |
| `bark.enable` / `token` | `false` / 空 | [bark](https://github.com/Finb/Bark) 推送 |
| `config_version` | `1.5.0` | 版本低于程序内置值时，启动会自动补齐缺失字段 |
| `ai.*` | 见下节 | AI 答题配置 |

> `ai.openai` 那段默认值是上游留下的占位（地址指向 OpenAI，模型名却是 Claude），
> 真要用必须换成你自己的接口。

---

## AI 答题

AI 课需要 `courseId` + `classId` 两个 ID，从 `ai_courses.json` 里拿。

```bash
python main.py --fetch                          # 先拉清单
python main.py -ai 1234567890 654321            # 学习 + 答题
python main.py -ai 1234567890 654321 --noexam   # 只学资源，不需要密钥
```

`ai` 段各字段：

| 字段 | 说明 |
|---|---|
| `ai.enabled` | 是否启用 AI 答题 |
| `ai.use_zhidao_ai` | `true` 用知到自带 AI（免密钥）；`false` 才走下面的 `ai.openai` |
| `ai.openai.api_base` | 接口根地址，如 `https://api.deepseek.com` |
| `ai.openai.api_key` | 你的密钥 |
| `ai.openai.model_name` | 模型标识，按供应商文档填 |
| `ai.use_stream` | 是否流式接收回复 |
| `ai.ppt_processing.provide_to_ai` | 是否把课件 PPT 转文本给 AI 参考，`false` 时完全不读课件 |
| `ai.ppt_processing.moonShot.*` | 上面为 `true` 时才需要的 Moonshot 配置 |

支持的题型：**单选、多选、判断、客观填空**（填空按空位 ID 映射文字，不按编号当答案）。
题型不在范围内、选项 ID 对不上、或答案为空的，一律**停止而不是乱猜**。

生成答案后会先写本地草稿（`ai_exam_drafts/`）再提交，恢复时核对题干、题型和选项 ID，
所以即使选项顺序被打乱也能对上。已判分的正确答案缓存优先级高于草稿。

---

## 共享课平时测试（实验）

普通共享课有三种完全不同的东西，别混为一谈：视频弹题、章节平时测试、AI 课知识点测验。
这个入口只处理第二种。按钮在 GUI 的「2. 课程」区。

1. 打开对话框，粘贴本课程任意一份平时测试的作答页链接（程序会记住最近一次）
2. 默认勾选「处理本课程全部未提交的普通平时测试」；取消勾选则只处理链接指向的那一份
3. 先点 `读取检查`，确认登录状态与题目可读
4. 点 `AI 完成并交卷`：逐题生成 → 保存 → 逐题读回 → 全卷读回，全部确认后才交卷
5. 点 `查看结果` 看平台给出的原始分数

支持范围：**单选、多选、判断**（`questionType.id` 为 `1` / `2` / `14`）。
填空、组合题、图片/附件题、见面课、互评会**直接停止**，不会只提交一部分答案。

- 已提交的章节自动跳过，不会为了提高分数重做
- 草稿与进度存 `shared_test_state/`，不作为已判分缓存
- 凭证失效时用 `重新扫码登录测试`，不会动原来的课程凭证
- 平台弹出安全验证时程序暂停，用 `打开测试网页` 在浏览器里解完再运行

详见 [共享课平时测试说明](./共享课平时测试说明.md)。

---

## 遇到验证码

智慧树会在频率过高时弹滑块或图形验证码。程序的处理方式是**暂停等你**：

- 界面弹窗提示「刷课已暂停」，同时用浏览器打开对应课程页
- 你在浏览器里登录同一账号，把验证码解掉
- 程序每 20 秒用只读接口探测一次，察觉已解除就自动接着刷当前视频
- 实在不想解，点「放弃这门课」，跳过当前视频而不是整门课

超时（默认 900 秒）仍未解除，程序会记录原因并跳过，不会假装成功。

---

## 失败与恢复

对「不确定」的处理原则是先查证、再动作：

| 场景 | 行为 |
|---|---|
| 保存答案超时 | 先读回服务器状态；确认已存就继续，确实没存才重试（有次数上限，间隔递增） |
| 服务器明确拒绝 | 立即停止，不盲目重试 |
| 交卷超时 | 只查状态，不自动重发，避免把「未知结果」当成「没交」 |
| 未达掌握度 | 记录待处理项、继续后面的知识点，遍历结束后统一报告，不静默跳过 |
| 中途异常 | `finally` 里停掉心跳，不留后台请求 |

---

## 目录结构

| 文件 / 目录 | 作用 |
|---|---|
| `main.py` | 命令行入口，读配置、解析参数、分发任务 |
| `gui.py` | **图形界面入口**，桌面快捷方式指向它 |
| `fucker.py` | `Fucker` 类，核心逻辑（另含 `ExamCtx` / `Openai` / `PptToTxt`） |
| `shared_tests.py` | 共享课平时测试的独立实现（原生 AES/RSA 协议） |
| `shared_public_key.pem` | 上面那个模块用的公开协议资源 |
| `sign.py` | 生成 hike 接口所需的 `signature` 参数 |
| `zd_utils.py` | 知到接口的工具，如生成 `ev` 和 `secretStr` |
| `utils.py` | 路径解析、二维码显示、cookie 转换 |
| `logger.py` | 按等级把日志写入不同文件 |
| `push.py` | pushplus / bark 推送 |
| `ObjDict.py` | 支持属性访问的 `dict` |
| `selftest/` | 离线回归测试 |
| `images/` | 文档插图 |
| `decrypt/` | 逆向分析用的工具与样本，非必要 |
| `config.json` | 首次运行自动生成（**不入库**） |
| `cookies.json` | 登录凭证（**不入库**） |
| `execution.json` / `ai_courses.json` | 课程清单（**不入库**） |
| `meta.json` | 版本、分支、作者，用于更新检查 |
| `requirements.txt` | 依赖清单 |
| `使用说明.md` | 更细的图文版说明 |
| `共享课平时测试说明.md` | 共享课测试功能单独说明 |
| `AGENTS.md` | 给 AI 编码助手看的维护约束 |

---

## 回归测试

离线、不联网，改完逻辑建议跑一遍：

```bash
python -m unittest discover -s selftest -p test_ai_resilience.py   # AI 韧性 15 项
python -m unittest discover -s selftest -p test_shared_tests.py    # 共享课平时测试 21 项
```

`selftest/run_all.py`、`test_e2e.py` 等是早期端到端脚本，会真的发请求，按需单独运行。

---

## 常见问题

**课程列表一直是空的？**
读 `execution.json` / `ai_courses.json` 时统一用 UTF-8。若你的系统是中文 Windows（默认 cp936），
旧版本会解析失败。当前版本读写都显式指定了编码，正常不会再出现。

**提示 `Failed to check update`？**
只是连不上 GitHub，不影响任何功能。`check_update` 默认就是关的。

**开了 Clash 之后所有请求都超时？**
全局模式 + TUN 会把「禁用 HTTP 代理」的请求也接管。切回**规则模式**，程序里保留 TLS 校验，不要关。

**日志乱码？**
GUI 与子进程之间统一用 UTF-8 传输。若仍乱码，检查 `chcp` 与终端编码。

**点「开始刷课」瞬间就结束了？**
先看日志末尾的汇总行（成功几个、失败几个）。如果状态是红色「被验证码拦下」，按上面《遇到验证码》处理。

**能不能刷期末考试？**
不能。见面课、互评、期末考试、组合题、图片附件题都不在支持范围内。

---

## 已知限制

- 只支持二维码登录，账号密码登录已失效
- AI 答题需要自备 OpenAI 兼容接口（或用知到自带 AI）
- 共享课平时测试只验证了题型 1/2/14，其余题型会停
- `--tree_view` / `--progressbar_view` 在命令行上关不掉（`type=bool` 的历史问题）
- AI 答案可能出错，程序完成 ≠ 试卷满分，成绩以平台为准

---

## 许可证与致谢

本项目基于 [VermiIIi0n/fuckZHS](https://github.com/VermiIIi0n/fuckZHS) 修改而来，
遵循其 MIT 许可证（见 [LICENSE](./LICENSE)，版权归原作者）。

本仓库在其基础上增加了：图形界面、AI 课答题、共享课平时测试、验证码等待、
答案草稿与读回核验等可靠性机制，并重写了文档。

仅供学习与个人研究使用。使用本工具所产生的一切后果由使用者自行承担，
请自行遵守所在学校的规定与平台服务条款。
