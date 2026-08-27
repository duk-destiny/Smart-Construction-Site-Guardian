-- 智护工地 · 施工安全智能体 数据库 Schema（SQLite 3, WAL）
-- 8 表 + 索引 + 审计仅追加触发器 + 3 视图
-- 全部使用 IF NOT EXISTS，保证 init_db() 幂等可重复执行
-- 类型约定：时间用 TEXT(ISO8601)，布尔用 INTEGER(0/1)；外键需 PRAGMA foreign_keys=ON

-- 用户表（v0.2 起支持第三角色 responsible=整改责任人）
CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,
    username   TEXT NOT NULL UNIQUE,
    pwd_hash   TEXT NOT NULL,
    role       TEXT NOT NULL CHECK(role IN ('safety','admin','responsible')),
    created_at TEXT NOT NULL
);

-- 任务表（一次检测任务；source 标记输入来源 camera/upload/text）
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    permit_json TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    source      TEXT NOT NULL DEFAULT 'upload',
    created_at  TEXT NOT NULL
);

-- 视觉检测结果
CREATE TABLE IF NOT EXISTS detections (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    frame_path      TEXT,
    cls             TEXT NOT NULL,
    conf            REAL NOT NULL,
    bbox_json       TEXT,
    violation_desc  TEXT
);

-- 规范合规结果
CREATE TABLE IF NOT EXISTS compliances (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(id),
    verdict     TEXT NOT NULL,
    clause_no   TEXT,
    clause_text TEXT,
    score       REAL
);

-- 风险融合结论（含人工改判）
CREATE TABLE IF NOT EXISTS risks (
    id               TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL REFERENCES tasks(id),
    risk_level       TEXT NOT NULL CHECK(risk_level IN ('低','一般','较大','重大')),
    reasons_json     TEXT,
    filtered_fp_json TEXT,
    override_level   TEXT,
    override_reason  TEXT
);

-- 整改工单 + 工人提示 + 派发/整改/验收生命周期（v0.2 工单闭环）
-- status 流转：open(待整改) → submitted(待验收) → closed(已销项)；
-- 验收驳回回到 open 并留 review_reason，同单可多轮整改；
-- 「逾期」为派生状态（deadline < now 且未 closed），不入库避免状态机膨胀。
CREATE TABLE IF NOT EXISTS work_orders (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL REFERENCES tasks(id),
    hazard_desc    TEXT NOT NULL,
    clause         TEXT,
    requirement    TEXT,
    risk_level     TEXT NOT NULL,
    worker_notice  TEXT,
    assignee_id    TEXT REFERENCES users(id),
    status         TEXT NOT NULL DEFAULT 'open',
    dispatched_at  TEXT,
    deadline       TEXT,
    submitted_note TEXT,
    submitted_imgs TEXT,
    approved_by    TEXT,
    approved_at    TEXT,
    closed_at      TEXT,
    review_reason  TEXT,
    created_at     TEXT NOT NULL
);

-- 审计日志（仅追加）
CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT,
    action      TEXT NOT NULL,
    detail_json TEXT,
    created_at  TEXT NOT NULL
);

-- 实时检测记录（B3）：每帧每个检测项一行，用于历史追踪/合规率统计/日期筛选
CREATE TABLE IF NOT EXISTS detection_records (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,            -- 一次实时监测会话（进入页面生成）
    scene_id    TEXT,                     -- 来源场景（construction_ppe/hot_work）
    mode        TEXT NOT NULL DEFAULT 'realtime',  -- realtime / upload
    frame_status TEXT NOT NULL,           -- 该帧三级合规：合规/警告/不合规
    cls         TEXT NOT NULL,            -- 项目隐患键
    conf        REAL,
    image_path  TEXT,
    source      TEXT,
    severity    TEXT,                     -- safe/warning/critical
    created_at  TEXT NOT NULL
);

-- Agent 运行证据链：每次研判链路中的 Agent 输入输出摘要，用于追溯与答辩展示
CREATE TABLE IF NOT EXISTS agent_runs (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(id),
    agent       TEXT NOT NULL,
    status      TEXT NOT NULL,
    cost_ms     INTEGER NOT NULL DEFAULT 0,
    input_json  TEXT,
    output_json TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL
);

-- 人工纠偏反馈样本：安全员改判/误报/漏报记录，用于证据链与后续训练集构建
CREATE TABLE IF NOT EXISTS feedback_samples (
    id                   TEXT PRIMARY KEY,
    task_id              TEXT NOT NULL REFERENCES tasks(id),
    user_id              TEXT,
    auto_risk_level      TEXT,
    corrected_risk_level TEXT NOT NULL,
    reason               TEXT NOT NULL,
    feedback_type        TEXT NOT NULL DEFAULT 'override',
    source_json          TEXT,
    image_path           TEXT,
    detection_json       TEXT,
    corrected_labels_json TEXT,
    status               TEXT NOT NULL DEFAULT 'pending',
    reviewed_by          TEXT,
    reviewed_at          TEXT,
    created_at           TEXT NOT NULL
);

-- 告警生命周期：实时监测产生的高风险告警，支持确认/误报/已处理
CREATE TABLE IF NOT EXISTS alarm_events (
    id          TEXT PRIMARY KEY,
    session_id  TEXT,
    task_id     TEXT,
    scene_id    TEXT,
    cls         TEXT,
    conf        REAL,
    status      TEXT NOT NULL DEFAULT 'new'
                CHECK(status IN ('new','confirmed','false_alarm','resolved')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT,
    reviewed_by TEXT
);

-- 外部推送留痕：每次告警推送记录（webhook/企业微信/钉钉），支持重试与失败追溯
CREATE TABLE IF NOT EXISTS notification_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alarm_id   TEXT NOT NULL,                -- 软引用告警 ID（测试/跳过类推送无对应告警）
    channel    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    error      TEXT,
    created_at TEXT NOT NULL
);

-- 模型版本注册：记录训练数据、指标与 ONNX 路径，支持版本切换与回滚
CREATE TABLE IF NOT EXISTS model_registry (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    version    TEXT NOT NULL,
    path       TEXT NOT NULL,
    data_yaml  TEXT,
    imgsz      INTEGER,
    mAP50      REAL,
    mAP50_95   REAL,
    active     INTEGER NOT NULL DEFAULT 0,
    notes      TEXT,
    created_at TEXT NOT NULL
);

-- 规范文档登记
CREATE TABLE IF NOT EXISTS kb_docs (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    imported_by  TEXT,
    created_at   TEXT NOT NULL
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_tasks_user        ON tasks(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_detections_task   ON detections(task_id);
CREATE INDEX IF NOT EXISTS idx_compliances_task  ON compliances(task_id);
CREATE INDEX IF NOT EXISTS idx_risks_task        ON risks(task_id);
CREATE INDEX IF NOT EXISTS idx_workorders_task   ON work_orders(task_id);
CREATE INDEX IF NOT EXISTS idx_audit_user_time   ON audit_logs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_kbdocs_name       ON kb_docs(filename);
CREATE INDEX IF NOT EXISTS idx_detrec_session     ON detection_records(session_id);
CREATE INDEX IF NOT EXISTS idx_detrec_time        ON detection_records(created_at);
CREATE INDEX IF NOT EXISTS idx_detrec_cls         ON detection_records(cls);
CREATE INDEX IF NOT EXISTS idx_agent_runs_task    ON agent_runs(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_time      ON feedback_samples(created_at);
CREATE INDEX IF NOT EXISTS idx_alarm_status       ON alarm_events(status, created_at);
CREATE INDEX IF NOT EXISTS idx_notify_alarm       ON notification_logs(alarm_id, created_at);
CREATE INDEX IF NOT EXISTS idx_model_name_active  ON model_registry(name, active);

-- 触发器：禁止更新审计日志
CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs is append-only: UPDATE denied');
END;

-- 触发器：禁止删除审计日志
CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs is append-only: DELETE denied');
END;

-- 视图：任务汇总（任务 + 风险等级 + 工单概要）
CREATE VIEW IF NOT EXISTS v_task_summary AS
SELECT t.id AS task_id, t.user_id, t.created_at,
       r.risk_level, r.override_level,
       w.hazard_desc, w.clause, w.requirement, w.worker_notice
FROM tasks t
LEFT JOIN risks r        ON r.task_id = t.id
LEFT JOIN work_orders w  ON w.task_id = t.id;

-- 视图：重大风险未改判任务
CREATE VIEW IF NOT EXISTS v_high_risk AS
SELECT task_id, risk_level, reasons_json
FROM risks
WHERE risk_level = '重大' AND override_level IS NULL;

-- 视图：最近审计（管理页）
CREATE VIEW IF NOT EXISTS v_audit_recent AS
SELECT a.id, a.user_id, u.username, a.action, a.detail_json, a.created_at
FROM audit_logs a
LEFT JOIN users u ON u.id = a.user_id
ORDER BY a.created_at DESC;
