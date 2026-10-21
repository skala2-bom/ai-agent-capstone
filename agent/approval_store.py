"""Durable receipt, approval binding, execution journal and event outbox.

All writes use the agent account/schema. The backend remains the sole writer of
business data. Holding a PostgreSQL session lock serializes each graph thread
across workers/processes; the inbox survives an HTTP 202 and process restart.
"""
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
import observability as O


class ApprovalStore:
    def __init__(self, dsn):
        self.pool = AsyncConnectionPool(dsn, min_size=1, max_size=12, open=False,
                                        kwargs={"autocommit": True, "row_factory": dict_row})

    async def open(self):
        await self.pool.open()
        await self.pool.wait()
        async with self.pool.connection() as c:
            await c.execute((Path(__file__).parent / "sql/001_approval_workflow.sql").read_text())

    async def close(self):
        await self.pool.close()

    async def query(self, sql, params=()):
        async with self.pool.connection() as c:
            cur = await c.execute(sql, params)
            return await cur.fetchall() if cur.description else []

    async def receive(self, key, task_id, thread_id, kind, payload):
        rows = await self.query("""INSERT INTO agent.workflow_command
            (command_id, task_id, thread_id, kind, payload) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING command_id""", (key, task_id, thread_id, kind, Jsonb(payload)))
        if not rows:
            old = (await self.query("SELECT * FROM agent.workflow_command WHERE command_id=%s", (key,)))[0]
            if (old["task_id"], old["thread_id"], old["kind"], old["payload"]) != (task_id, thread_id, kind, payload):
                raise ValueError("같은 요청 ID에 다른 내용이 전달되었습니다.")
        return bool(rows)

    async def threads(self):
        return await self.query("""SELECT thread_id, min(created_at) AS first_at
            FROM agent.workflow_command WHERE status='PENDING'
            GROUP BY thread_id ORDER BY first_at LIMIT 100""")

    async def next_command(self, thread_id):
        rows = await self.query("""SELECT * FROM agent.workflow_command
            WHERE thread_id=%s AND status='PENDING'
            ORDER BY CASE WHEN kind='START' THEN 0 WHEN payload->>'decision'='APPROVED' THEN 1 ELSE 2 END,
                     created_at LIMIT 1""", (thread_id,))
        return rows[0] if rows else None

    async def finish_command(self, key, status="DONE", error=None):
        trace = O.current()
        if trace and not trace.finishing:
            trace.command_completion = (key, status, error)
            return
        await self.query("UPDATE agent.workflow_command SET status=%s, error=%s WHERE command_id=%s",
                         (status, error, key))

    @asynccontextmanager
    async def thread_lock(self, thread_id):
        async with self.pool.connection() as c:
            key = "approval-workflow:" + thread_id
            cur = await c.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked", (key,))
            locked = (await cur.fetchone())["locked"]
            try:
                yield locked
            finally:
                if locked:
                    await c.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (key,))

    async def begin_registration(self, proposal_id, task_id, payload):
        rows = await self.query("""INSERT INTO agent.approval_binding (proposal_id,task_id,payload)
            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *""", (proposal_id, task_id, Jsonb(payload)))
        if rows:
            return rows[0], True
        return (await self.query("SELECT * FROM agent.approval_binding WHERE proposal_id=%s", (proposal_id,)))[0], False

    async def bind(self, proposal_id, status, approval_id=None, error=None):
        await self.query("""UPDATE agent.approval_binding SET status=%s,approval_id=%s,error=%s
            WHERE proposal_id=%s""", (status, approval_id, error, proposal_id))

    async def attempt(self, approval_id):
        rows = await self.query("SELECT * FROM agent.execution_attempt WHERE approval_id=%s", (approval_id,))
        return rows[0] if rows else None

    async def begin_attempt(self, task_id, proposal_id, approval_id):
        return bool(await self.query("""INSERT INTO agent.execution_attempt (task_id,proposal_id,approval_id)
            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING approval_id""", (task_id, proposal_id, approval_id)))

    async def finish_attempt(self, approval_id, ok, result):
        await self.query("""UPDATE agent.execution_attempt SET status=%s,result=%s,error=%s
            WHERE approval_id=%s""", ("SUCCEEDED" if ok else "UNKNOWN", Jsonb(result) if ok else None,
                                       None if ok else str(result), approval_id))

    async def event(self, task_id, payload):
        trace = O.current()
        if trace and not trace.finishing and payload.get("type") in {"DONE", "FAILED"}:
            trace.terminal_event = (task_id, payload)
            return
        # Stable key makes replay after a graph checkpoint crash harmless.
        key = hashlib.sha256(json.dumps([task_id, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        await self.query("""INSERT INTO agent.workflow_outbox (event_key,task_id,payload)
            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""", (key, task_id, Jsonb(O.enrich(payload))))

    async def previous_trace(self, task_id):
        rows = await self.query("""SELECT payload->>'traceId' AS trace_id FROM agent.workflow_outbox
            WHERE task_id=%s AND payload->>'type'='TRACE' ORDER BY id DESC LIMIT 1""", (task_id,))
        return rows[0]["trace_id"] if rows else None

    async def pending_events(self):
        # One event per task at a time preserves progress-before-DONE ordering.
        return await self.query("""SELECT DISTINCT ON (task_id) * FROM agent.workflow_outbox
            WHERE NOT delivered ORDER BY task_id,id LIMIT 100""")

    async def delivered(self, event_id):
        await self.query("UPDATE agent.workflow_outbox SET delivered=true WHERE id=%s", (event_id,))
