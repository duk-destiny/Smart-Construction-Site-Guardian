# E2E 浏览器验收测试（6 阶段全链路）

基于 Playwright（chromium）的端到端验收脚本，覆盖登录/上报/工单闭环/实时与历史/
管理端/Agent 研判 6 个阶段。全量回归已验证 112/112 通过。
测试后端跑在**独立临时库** `data/tmp_e2e_test.db` 上，绝不触碰生产 `data/app.db`。

## 前置依赖

- Python 环境：项目根 `.venv313`（含 playwright、uvicorn、fastapi 等，见 requirements.txt）
- 浏览器：`playwright install chromium`（首次需执行）
- 前端 dev server 与临时库后端（见下方启动顺序）
- 仓库根存在 `docs/说明文档.pdf`（Phase 5 知识库导入用）；该文件被 .gitignore 排除，
  **他机运行需自备任意合规 PDF 放到该路径**
- Phase 4 的证据缩略图断言依赖 `data/alarms/` 下运行期产生的告警图（同样不入库）；
  他机首跑时该依赖由 Phase 4 自身流程（告警夹具写库 + 转工单）产生数据满足，无需预置
- 临时库存在种子账号（由 `launcher.py` 启动时经后端自举（`api.main` lifespan →
  `core/bootstrap.py`）对空库自动幂等补种，无需手动执行任何脚本）：
  `admin/admin123`、`lisi/demo1234`、`safety/demo1234`
- 验证形态声明：本套件基于 **5173 dev server** 验证，不覆盖生产托管形态（FastAPI 8000
  直接托管 dist）。发版前建议 `npm run build` 后用 `launcher.py` 起 8000 对登录页做一次冒烟

## 启动顺序（3 个终端）

1. 临时库后端（8000）：
   `python tests/e2e/launcher.py`
2. 前端 dev server（5173）：
   `cd frontend; npm run dev -- --port 5173`
3. 运行测试（仓库根）：
   `python tests/e2e/run_all.py`（一键串行 01→06，注入 PYTHONIOENCODING=utf-8）

也可单跑某阶段：`python tests/e2e/test_0N_xxx.py`（脚本可独立运行）。

## 账号约定（首登强制改密）

首轮运行会触发强制改密，改后密码固定为：

| 账号 | 初始密码 | 改后密码 |
| --- | --- | --- |
| admin | admin123 | Admin@E2E123 |
| lisi | demo1234 | Lisi@E2E123 |
| safety | demo1234 | Safety@E2E123 |

重跑场景：脚本对密码不符有改密兜底（见 test_02 login_admin）；
最彻底的做法是销毁临时库后重跑（见文末）。

## 6 阶段数据依赖链（必须按序）

1. `test_01_login.py`：登录/首登强制改密/角色路由守卫 → 产出改密后账号。
2. `test_02_report.py`：统一上报（影像研判上传夹具 `fixture_smoke.jpg`、
   文字线索建单、对话查询）→ 产出影像任务与 open 工单；
   产物 id 写入 `phase2_ids.txt`（运行产物，已 gitignore）。
3. `test_03_orders.py`：工单派发 → lisi 提交整改 → 驳回重改 → 验收销项（依赖 2 的工单）。
4. `test_04_realtime_history.py`：实时监测（告警夹具直接写库）+ 历史分析图表（依赖 2 的任务数据）。
5. `test_05_admin.py`：管理端 7 页签（用户治理/模型/知识库/推送/自检/审计/纠偏）。
6. `test_06_agent.py`：Agent 研判页空态/无效任务/重新研判轮询回流（依赖 2 的已研判任务）。

## 截图与运行产物

截图输出到 `tests/e2e/shots/`（已加入 .gitignore，目录自动创建）。
`tests/e2e/phase2_ids.txt` 为阶段产物排查文件，同样忽略。

## 测试后销毁临时库

全部数据都在临时库及其附属文件里，删掉即恢复干净：

```powershell
Remove-Item data\tmp_e2e_test.db, data\tmp_e2e_test.db-journal,
  data\tmp_e2e_test.db-wal, data\tmp_e2e_test.db-shm -ErrorAction SilentlyContinue
```

（先确保 8000 端口的临时后端已停，否则文件被占用。）
