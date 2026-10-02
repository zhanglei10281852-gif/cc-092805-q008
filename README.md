# 安宁礼仪与公墓运营服务

这是一个供殡仪馆、公墓和合作医疗机构使用的 Python 后端服务，统一管理逝者业务档案、遗体保管交接、送别厅与火化设备预约、服务订单、墓位权属、账单收款和审计时间线。系统把容易产生争议的交接、排程与收费动作保存在本地 SQLite 中，支持在单个 Linux 应用容器内离线运行。

## 运行环境

- Python 3.11
- FastAPI 与 Uvicorn
- SQLite 3，由 Python 标准库提供

## 安装

依次执行 python -m venv .venv、source .venv/bin/activate、python -m pip install -e ".[dev]"。可通过 PEACEFUL_CARE_DATABASE_PATH 指定数据库文件，默认写入项目的 data 目录。

## 初始化与启动

先执行 python -m app.cli init-db 和 python -m app.cli check-db，再用 uvicorn app.main:app --host 0.0.0.0 --port 8432 启动。健康检查为 GET /api/system/health。殡葬业务接口位于 /api/mortuary，涵盖档案、交接、资源、预约、服务订单、墓位权属、账单和时间线。墓位迁移接口位于 /api/relocation，涵盖同意规则、迁移案件、亲属意见、司法阻断、实施步骤和批量改造筛查。

## 墓位迁移流程

迁移案件在创建时冻结旧墓位的权属版本与快照，此后权属发生续期等变更会阻断评估与实施，需通过 refreeze-right 重新冻结。亲属意见必须附关系证明编号，证明经核验后才计入评估；同一亲属可多次提交，以最新一条为准，历史意见全部保留。同意评估按案件配置的规则（最少同意人数、允许反对人数、是否全体一致）判断，每次评估落一条决定记录，载明依赖的亲属身份、证明文件与权属版本，可通过 /cases/{id}/decisions 查询。司法暂停、身份争议或欠费争议登记为阻断，解除前评估与实施步骤均不可推进。新墓位确认、遗骨交接、施工完成、旧墓位关闭四步严格按序登记，各步携带幂等键，重复提交返回原记录；旧墓位关闭后原权属置为 relocated。撤回案件仅改变状态，已收意见、决定与步骤全部保留。批量改造可将案件加入批次，通过 /batches/{id}/conflicts 识别同一亲属跨案件重复授权与新墓位冲突。

## 测试与编译检查

测试命令：python -m pytest

编译命令：python -m compileall -q app tests

API 与 CLI 冒烟命令：python -m app.cli smoke、python -m app.cli mortuary-demo

## 目录结构

- app/mortuary：档案、保管交接、资源排程、权属和账单领域
- app/relocation：墓位迁移案件、亲属同意、司法阻断与批量改造领域
- app/api：登录、角色、审计及系统管理接口
- app/core：时钟、安全、异常、隐私与分页能力
- app/repositories：通用身份和审计数据访问
- app/services：会话、权限、后台任务及维护服务
- tests：领域、接口、异常路径和身份回归测试

## 一致性约定

SQLite 连接启用外键、WAL、忙等待和即时事务。业务档案采用外部编号去重，保管交接与预约保留幂等键，服务订单开票后不可再次开票，支付流水不能重复分配。关键状态变化同时写入领域时间线；会话令牌仅保存摘要，审计记录不会保存明文密码或令牌。
