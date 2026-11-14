#!/usr/bin/env python3
"""回放 replies 全部轮次，对账入账并产出 plan.txt / daily.txt。

规则：
- OK      -> 入账（挂起期间账户的第一份 OK 记“恢复入账”），流水按 04:00 切账务日
- EMPTY   -> 空窗，登记 gap 表
- LIMIT   -> 停排（当日不再排该账户）
- AUTH    -> 挂起，该账户整段挂起，直到再次出现 OK 回执才恢复
- 重跑幂等：flow 中已存在的流水不重复入账，gap 用 INSERT OR IGNORE
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta


def acct_day(time_str):
    """银行账务日凌晨四点切换：04:00 前的交易记前一账务日。"""
    dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    if dt.hour < 4:
        dt -= timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default="/workspace/accounts.json")
    ap.add_argument("--replies", default="/workspace/replies")
    ap.add_argument("--ledger", default="/workspace/ledger.sqlite")
    ap.add_argument("--plan", default="/workspace/plan.txt")
    ap.add_argument("--daily", default="/workspace/daily.txt")
    args = ap.parse_args()

    with open(args.accounts, encoding="utf-8") as f:
        manifest = [json.loads(line) for line in f if line.strip()]
    known = {a["acct_id"] for a in manifest}

    con = sqlite3.connect(args.ledger)
    cur = con.cursor()

    suspended = set()   # AUTH 挂起中的账户
    plan_lines = []

    files = sorted(fn for fn in os.listdir(args.replies) if fn.endswith(".json"))
    for fn in files:
        name = fn[:-len(".json")]
        with open(os.path.join(args.replies, fn), encoding="utf-8") as f:
            r = json.load(f)
        acct = r["acct_id"]
        qday = r["query_day"]
        status = r["status"]
        items = r.get("items", [])

        if acct not in known:
            print(f"warning: {name} 账户 {acct} 不在任务清单内", flush=True)

        if status == "OK":
            if acct in suspended:
                disp = "恢复入账"
                suspended.discard(acct)
            else:
                disp = "入账"
            for it in items:
                t = it["time"]
                amt = int(it["amount"])
                ad = acct_day(t)
                exists = cur.execute(
                    "SELECT 1 FROM flow WHERE acct_id=? AND day=? AND time=? AND amount=?",
                    (acct, qday, t, amt),
                ).fetchone()
                if exists:
                    continue  # 对账命中，已 OK 的日子不重复入账
                cur.execute(
                    "INSERT INTO flow(acct_id, day, time, amount, acct_day) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (acct, qday, t, amt, ad),
                )
                cur.execute(
                    "INSERT INTO balance(acct_id, amount) VALUES (?, ?) "
                    "ON CONFLICT(acct_id) DO UPDATE SET amount = amount + excluded.amount",
                    (acct, amt),
                )
        elif status == "EMPTY":
            disp = "空窗"
            cur.execute(
                "INSERT OR IGNORE INTO gap(acct_id, day) VALUES (?, ?)", (acct, qday)
            )
        elif status == "LIMIT":
            disp = "停排"
        elif status == "AUTH":
            disp = "挂起"
            suspended.add(acct)
        else:
            disp = "未知状态"

        plan_lines.append(f"{name} {acct} {qday} {status} {disp}")

    con.commit()

    with open(args.plan, "w", encoding="utf-8") as f:
        f.write("\n".join(plan_lines) + "\n")

    daily = cur.execute(
        "SELECT acct_day, COUNT(*), SUM(amount) FROM flow "
        "GROUP BY acct_day ORDER BY acct_day"
    ).fetchall()
    with open(args.daily, "w", encoding="utf-8") as f:
        for d, cnt, total in daily:
            f.write(f"{d} {cnt} {total}\n")

    con.close()


if __name__ == "__main__":
    main()
