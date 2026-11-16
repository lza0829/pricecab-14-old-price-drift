# 06-pricecab（价签柜）

Pricecab — 改价单按预约时刻生效，现行价签可查，并与销售夹具对账。

与 Feedstitch（多源归并）、Batchgate（作业编排）、Syncledger（双端对账）、Changecast（CDC 投影）、Proofpack（采样放行）主链路不同。  
不是电商订单/购物车，不是仓库进出库 CRUD，不是考勤 OA。夹具全本地。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5600 |
| API | http://localhost:10600 |

## 主链（0-1）

选商品建改价单（写明生效时刻）→ 拨业务钟或点「应用到期」→ 现行价签更新 → 销售对账看一致/不一致

## 旧价漂移（old-price drift）

建单时记下的 `old_price`，到应用时可能已被别的单/手动调价改过。应用每行前都会用价签本现行价与建单旧价核对，不一致即**漂移**；绝不静默按过期旧价覆盖。

- **策略互斥二选一**（设置项 `drift_strategy`，默认 `reject`）：
  - `reject` 遇漂移拒单：不写该单任何新价，单保持 `scheduled`（可处理态）并记漂移原因，修复价差后可再次应用；
  - `allow` 允许漂移应用：写入新价，`price_history` 与批次明细同时记录**期望旧价 / 实际旧价 / 漂移标记**，销售对账仍按正式历史链（`price_history`）。
- **拒绝粒度固定为「单」**：一张单任一行漂移即整单拒绝（同单干净行也不写），同批其他单不受影响。
- **提交时再检测**：应用分两阶段——先只读预检快照，再逐单开写事务提交；提交时每行重读现行价，与快照不一致即判定「应用期间被并发改写」，以新读数重算漂移并记 `rechecked`；写入用比较交换（`WHERE current_price=期望旧价`）兜底，过期读数永远写不成功。
- **审计与互证**：批次记 `applied_count / rejected_count / drift_count`（漂移放行行数）与策略；批次明细（`GET /api/apply/batches/{id}/lines`）逐行记录期望/实际旧价、漂移与再检测标记、决定与原因；`GET /api/prices/verify` 交叉核对价签本与历史链头（价格 + 来源单）。

## 手动调价（其他入口）

`POST /api/prices/adjust` 直接改写价签并落历史（来源记为手动），是改价单之外改价的另一入口——它会让待应用单的建单旧价漂移，应用时由漂移检测兜底。前端「现行价签」页可操作。

## 测试

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

覆盖：无漂移成功、漂移下 reject/allow 两种策略、拒绝粒度为单、应用中并发改写触发提交时再检测、策略非法值拒绝、价签来源互证。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。
