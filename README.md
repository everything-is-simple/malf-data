# malf-data

T02 upstream data pipeline component for **AI MALF RiskBench**.

## 状态（2026-08-04 更新）

✅ **T02 已完成并解除阻塞**（DECISION-004 公开 lifecycle facts 合同）+ **B 三周期聚合已完成**（DECISION-005，day→week/month）。测试 **36 passed**（2026-08-04 实跑；历史 17 passed 为 T02 解除阻塞时数值）。

## 职责

- 读取权威本地 TDX `.day` 数据（`tdx_reader.py`，32 字节二进制解析，源整数域）；
- 通过 `MALFDriver` 逐 bar 产出确定性 MALF 快照（Core→Lifespan→Rank→Position→Service 五层串行，44 字段 `WaveStructuralSnapshot`）；
- 写入 DuckDB（`duckdb_adapter.py`，44 列、自然主键 `(symbol, timeframe, bar_dt)`、逐行 COMMIT）；
- 断点续传（`ingest.py`：完整前缀 replay + 跳过已提交 bar + lineage_hash 确定性）。

## 不做什么

不修改 TDX 输入源、不碰 malf-engine 内部、不生成交易信号、不连接券商。

## 使用

```python
from malf_data.ingest import ingest_symbol
from pathlib import Path

result = ingest_symbol(
    "sh510050",
    tdx_root=Path(r"Z:\new_tdx64"),
    db_path=Path(r"Z:\ai-malf-riskbench-data\riskbench.duckdb"),
    data_stale=True,
)
# → IngestResult(symbol, timeframe, inserted_rows, lineage_hash)
```

## 测试

```powershell
python -m pytest tests -q   # 预期 36 passed（2026-08-04 实跑）
```
