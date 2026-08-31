# dcs-service probe

`probe.py` 是人工运行脚本，不属于自动测试，也不会被默认 pytest 执行。它按
V1 协议依次检查 `/health`、`/api/v1/info`、`/api/v1/tag`、History 和 Event。

示例：

```bash
python experiments/dcs_service/probe.py \
  --base-url http://127.0.0.1:18080 \
  --tag TI-021007_AI1_PV.CV \
  --minutes 5
```

也可以明确提供源本地时间：

```bash
python experiments/dcs_service/probe.py \
  --base-url http://127.0.0.1:18080 \
  --tag TI-021007_AI1_PV.CV \
  --from 2026-08-30T08:00:00 \
  --to 2026-08-30T08:05:00
```

脚本不会假设 UTC，不会给时间追加 `Z` 或 offset，也不会打印完整 CSV。

