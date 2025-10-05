# VPS 限速监控（plugins.v2）

一个定时检测 VPS 是否被限速并发送通知的插件。支持通过 MoviePilot 的消息通道（如 OneBot、Telegram 等已启用的通知插件）发送结果。

## 功能

- 连接 ServerControlPanel(SCP) SOAP 接口，拉取 VPS 列表与详情
- 检测网卡 `trafficThrottled` 状态，汇总被限速的机器
- 通过 MoviePilot 通知事件统一下发（无需在本插件内直接调用 OneBot）
- 支持 Cron 定时与手动“立即运行一次”

## 安装与依赖

- 需要在运行环境安装 `requests` 与 `zeep`：
  - `pip install requests zeep`
- 将本目录放置至 `MoviePilot-Plugins/plugins.v2/vpsmonitor`
- 在 MoviePilot 中加载三方插件包（参考 MoviePilot-Plugins 仓库说明）

> 提示：插件提供“兼容旧 TLS 的不安全模式”，仅在服务端证书异常且无法升级时短期启用，存在中间人风险，请慎用。

## 配置项

- 启用插件：是否启用本插件
- Cron 定时：标准 5 字段 crontab 表达式（例如 `*/10 * * * *` 每 10 分钟）
- 立即运行一次：保存后马上执行一次检测
- WSDL 地址：默认 `https://www.servercontrolpanel.de/WSEndUser?wsdl`
- 客户号 / 密码：SCP 登录凭据（建议使用专用子账号）
- 语言：SCP 接口语言（默认 `en`）
- 发送“全部正常”通知：未限速时也发送绿色提示
- 不安全 TLS（跳过证书校验）：仅用于老旧服务端兼容，默认关闭
- 调试日志：输出完整接口返回（可能包含敏感字段，建议仅本地调试时开启）

## 通知

- 被限速：标题 `⚠️ VPS 被限速`，内容为被限速机器清单
- 全部正常：标题 `🟢 所有 VPS 正常`，内容为统计数量

消息将通过 MoviePilot 的 `NoticeMessage` 事件分发，最终由已启用的消息插件（如 OneBot、Telegram 等）发送。

## 常见问题

- 缺少依赖：请安装 `requests` 与 `zeep`
- 证书问题：可暂时开启“不安全 TLS”，但建议从根因修复证书/协议
- 频率控制：若接口存在频控，请适当拉长 Cron 间隔

