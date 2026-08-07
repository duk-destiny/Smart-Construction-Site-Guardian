"""演示数据集生成（P6 交付物）：

构建一份独立演示库 `data/demo/app_demo.db`，包含：
- 1 个安全员账号（safety / demo1234）与 1 个管理员账号（admin / admin1234）
- ≥10 条覆盖 低/一般/较大/重大 四级的模拟整改工单（含任务父记录以满足外键）
同时导出可移植的 `data/demo/work_orders_seed.json` 供答辩/前端演示。

用法：python scripts/seed_demo.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import secrets

import bcrypt

from dao.db import get_conn, init_db
from dao.models import UserDAO, TaskDAO, WorkOrderDAO

DEMO_DIR = os.path.join(ROOT, "data", "demo")
DEMO_DB = os.path.join(DEMO_DIR, "app_demo.db")
SEED_JSON = os.path.join(DEMO_DIR, "work_orders_seed.json")

# 四级风险模拟工单（隐患描述 / 违反条款 / 整改要求 / 工人白话提示）
_SEED = [
    {
        "risk_level": "重大",
        "hazard_desc": "动火点 3 米内存放油漆桶等易燃物，火花引燃风险极高；未办理动火审批，无专职监火人。",
        "clause": "第一条 动火作业必须设置专职监火人；第三条 动火前应清除周边易燃物",
        "requirement": "立即停火；补办动火审批；指定专职监火人；清空 3 米内易燃物并配备灭火器材。",
        "worker_notice": "师傅先别动火！旁边有油漆桶太危险，火花一溅就着。先去找监火人，把审批办了，把边上易燃物清走，配好灭火器再干。",
    },
    {
        "risk_level": "重大",
        "hazard_desc": "密闭空间内动火，未检测可燃气体浓度，通风不足，存在爆炸与中毒风险。",
        "clause": "第二条 受限空间动火前应检测可燃气体浓度并强制通风",
        "requirement": "立即停止；强制通风并检测可燃气体浓度合格后方可作业；安排外部监护。",
        "worker_notice": "这地方不通风还密闭，先测一下有没有可燃气体！没测合格千万别点，先通风、安排人在外面盯着。",
    },
    {
        "risk_level": "较大",
        "hazard_desc": "高处动火未系挂防火毯，火花飞溅至下层电缆桥架。",
        "clause": "第四条 高处动火应采取防火花飞溅措施",
        "requirement": "铺设防火毯/接火盘接住火花；下层电缆桥架做阻燃遮挡。",
        "worker_notice": "上面动火火花往下掉，下面全是电缆！铺块防火毯接住火花，电缆也挡一下，别把皮烧了。",
    },
    {
        "risk_level": "较大",
        "hazard_desc": "灭火器压力表指针在红区（已失效），动火现场无有效灭火器材。",
        "clause": "第二条 动火现场应配备合格灭火器材",
        "requirement": "更换合格灭火器；现场至少配置 2 具且在有效期内。",
        "worker_notice": "你这灭火器指针都红了，打不出粉的！换两个好使的放旁边，真着火能救命。",
    },
    {
        "risk_level": "较大",
        "hazard_desc": "监火人擅自离岗超过 10 分钟，动火作业仍持续进行。",
        "clause": "第一条 监火人不得擅离职守",
        "requirement": "监火人立即返岗；离岗期间暂停动火。",
        "worker_notice": "监火人不能走！人一走就没人看火了。让他回来盯着，他不在就先停。",
    },
    {
        "risk_level": "一般",
        "hazard_desc": "动火作业区未设置警戒线与警示标识，无关人员可随意进入。",
        "clause": "第二条 动火现场应设置警戒与警示标识",
        "requirement": "拉设警戒线并悬挂动火警示标识，闲人免进。",
        "worker_notice": "这圈没拉警戒线，外人容易闯进来。拉根带子、挂个牌子，提醒别靠近。",
    },
    {
        "risk_level": "一般",
        "hazard_desc": "作业人员未佩戴防护面罩，电弧光暴露伤害眼睛。",
        "clause": "第四条 高处/焊接作业应佩戴防护面罩",
        "requirement": "佩戴焊接防护面罩与防护手套后方可作业。",
        "worker_notice": "焊的时候光太强，眼睛受不了还伤皮肤。戴上防护面罩和手套再干。",
    },
    {
        "risk_level": "一般",
        "hazard_desc": "动火结束后未留观确认，未确认无复燃即离开。",
        "clause": "第三条 动火结束应清除火种并确认无复燃",
        "requirement": "作业后留观 30 分钟，确认无复燃、无余烬方可离开。",
        "worker_notice": "活干完别急着走，再盯半小时，确认没火星复燃了再撤。",
    },
    {
        "risk_level": "低",
        "hazard_desc": "动火前已清理周边易燃物，监火人在岗，审批齐全，仅提示保持通讯畅通。",
        "clause": "第一条/第二条/第三条 均已落实",
        "requirement": "保持对讲畅通，监火人持续监护即可。",
        "worker_notice": "你这准备得挺规范，保持联系、监火人别走开就行，注意安全。",
    },
    {
        "risk_level": "低",
        "hazard_desc": "防护到位、审批齐全、作业规范，无隐患。",
        "clause": "无",
        "requirement": "无需整改，按规程继续作业。",
        "worker_notice": "一切合规，按平时那样干就行，注意安全。",
    },
    {
        "risk_level": "一般",
        "hazard_desc": "乙炔气瓶与氧气瓶间距不足 5 米，存在回火风险。",
        "clause": "第二条 气瓶间距应符合安全规范",
        "requirement": "两瓶间距拉开至 5 米以上，并加装防倾倒与防晒措施。",
        "worker_notice": "乙炔和氧气瓶挨太近了，回火很危险！分开 5 米以上，瓶也要固定好。",
    },
    {
        "risk_level": "较大",
        "hazard_desc": "雨天露天动火，未采取防雨防潮措施，电缆破损有漏电风险。",
        "clause": "第二条 露天动火应采取防雨防潮与绝缘措施",
        "requirement": "搭建防雨棚、更换破损电缆、加装漏电保护后方可作业。",
        "worker_notice": "下雨天露天动火不行，电缆还破了会漏电！搭个雨棚、换好线、加漏保再干。",
    },
]


def main() -> None:
    os.makedirs(DEMO_DIR, exist_ok=True)
    if os.path.exists(DEMO_DB):
        os.remove(DEMO_DB)
    conn = get_conn(DEMO_DB)
    init_db(conn)

    users = UserDAO(conn)
    tasks = TaskDAO(conn)
    wo = WorkOrderDAO(conn)

    _safety_pass = os.getenv("DEMO_SAFETY_PASS") or secrets.token_urlsafe(12)
    _admin_pass = os.getenv("DEMO_ADMIN_PASS") or secrets.token_urlsafe(12)
    print(f"演示账号密码 → safety: {_safety_pass}  admin: {_admin_pass}")
    safety_id = users.insert("safety", bcrypt.hashpw(_safety_pass.encode(), bcrypt.gensalt()).decode(), "safety")
    _admin_id = users.insert("admin", bcrypt.hashpw(_admin_pass.encode(), bcrypt.gensalt()).decode(), "admin")

    rows = []
    for i, item in enumerate(_SEED, 1):
        tid = tasks.insert(safety_id, json.dumps({"seq": i, "watcher": "监火人", "extinguisher": "配备"}, ensure_ascii=False), "done")
        wid = wo.insert(tid, item["hazard_desc"], item["clause"], item["requirement"],
                        item["risk_level"], item["worker_notice"])
        rows.append({"id": wid, "task_id": tid, **item})

    # 导出可移植 JSON
    with open(SEED_JSON, "w", encoding="utf-8") as f:
        json.dump({"users": ["safety/demo1234", "admin/admin1234"],
                   "work_orders": rows}, f, ensure_ascii=False, indent=2)

    print(f"演示库已生成：{DEMO_DB}")
    print(f"演示数据：{SEED_JSON}")
    print(f"账号：safety/demo1234（安全员），admin/admin1234（管理员）")
    print(f"工单数：{len(rows)}（重大/较大/一般/低 四级覆盖）")
    conn.close()


if __name__ == "__main__":
    main()
