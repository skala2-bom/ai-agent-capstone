"""Optional real-model extraction smoke test; demo DB reads only, OpenAI cost applies."""
import asyncio
import psycopg
from psycopg.rows import dict_row
import integration_scenarios as S
import graph as G
import receipt_delay as D
import whatif as W


async def main():
    if G.llm is None:
        raise RuntimeError("Real-model mode and OPENAI_API_KEY are required")
    with psycopg.connect(S.DSN, row_factory=dict_row) as conn:
        rows = conn.execute("select po_id,po.item_id,i.name as item_name,qty,due_date::text,status from purchase_order po join item i on i.item_id=po.item_id where status='OPEN' order by due_date,po_id").fetchall()
        plans = conn.execute("select plan_id,product_id,i.name as product_name,plan_date::text,qty from production_plan p join item i on i.item_id=p.product_id").fetchall()
    questions = ["11/13 와이파이칩 입고일이 일주일 늦어졌어. 문제있나?",
                 "11/13 와이파이칩 입고일이 일주일 늦어지면 문제있나?",
                 "11/20 와이파이칩 입고일이 일주일 늦어졌어. 문제있나?"]
    for index, question in enumerate(questions):
        c, errors, turns = await G.classify_question(question, plans, receipts=rows)
        if index == 2:
            assert errors or c.unclear, c
            assert "PO-2609-005" in "; ".join(errors) and "PO-2609-006" in "; ".join(errors), errors
        else:
            assert not errors and not c.unclear and c.intent == "IMPACT", (c, errors)
            ops = [W.normalize(o) for o in c.whatif]
            assert ops == [{"op":"request_due_date_change", "po_id":"PO-2609-002", "new_due_date":"2026-11-20"}], ops
            assert D.snapshot(question, ops, rows)["reported"] == (index == 0)
        print(f"PASS real classification {index+1}: {turns} calls", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
