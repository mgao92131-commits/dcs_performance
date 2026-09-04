# 班次考核邮件通知

邮件模块只读取已经发布的 Result Package：目录中的 `result.json` 和其中引用的
`images/*.png`。它不会访问 DCS、重新计算考核，也不会读取月度 Excel。

## 配置

复制项目根目录的 `notification.config.example.json` 为本机未跟踪的
`notification.config.json`，填写 SMTP 和甲/乙/丙班收件邮箱。实际邮箱配置已在
`.gitignore` 中忽略，不要提交到 Git。

SMTP 密钥只能通过环境变量提供：

```powershell
$env:DCS_SMTP_PASSWORD = "<smtp-password>"
```

配置中的 `security` 支持 `ssl` 和 `starttls`。如果不配置 `username`，发送器会
按未认证 SMTP 发送，不会读取密码环境变量。

## 独立发送

```powershell
dcs-performance send-email --package .\assessment_reports\<run-id> --config .\notification.config.json
dcs-performance send-email --package .\assessment_reports\<run-id> --preview --config .\notification.config.json
dcs-performance send-email --package .\assessment_reports\<run-id> --dry-run --config .\notification.config.json
dcs-performance send-email --package .\assessment_reports\<run-id> --resend --config .\notification.config.json
```

`--preview` 输出 HTML 和纯文本正文，不连接 SMTP；`--dry-run` 只解析和渲染，不
发送或写入发送状态。成功发送后，状态默认记录到配置文件旁的
`.email-notification-state.json`，同一 `run_id` 再次发送会跳过；需要补发时使用
`--resend`。SMTP 失败会记录失败状态但不会标记为已发送，后续可直接重试。

正分考核点才会出现在邮件中。没有扣分项目的班次也会发送“无扣分”通知。
邮件时间使用 `YYYY-MM-DD HH:MM` 短格式（省略秒）。HTML 先展示班次摘要和扣分汇总，
班次摘要按三行纵向展示班次时间、扣分项目和合计扣分，班组显示在邮件标题中，
每个扣分点只保留标题和对应 PNG，不展开事件级细则；PNG 以 CID inline image 嵌入
HTML 正文，同时提供纯文本替代正文。

## 接入月报更新

在现有更新命令后增加 `--send-email`：

```powershell
python 报表\report.py update --date 2026-09-03 --team 甲 `
  --send-email --email-config .\notification.config.json
```

只有 Result Package 生成成功、校验成功且 Excel 原子保存成功后才发送。邮件失败
不会回滚这两个已经保存的结果，命令会返回非零并提示可用独立 `send-email` 命令
补发。
