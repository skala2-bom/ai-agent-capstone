import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "rag-mcp")]
import graph as G
import whatif as W
from documents import chunks, load_documents
from docstore import DocumentStore, RagError
from embeddings import Embeddings, EmbeddingError
from rag_fixture import FixtureEmbeddings


def shortage(item, qty, orderable=False):
    return {"item_id": item, "plan_id": "PP-03", "plan_date": "2026-11-23",
            "projected_stock": -qty, "orderable": orderable}


class Guardrails(unittest.TestCase):
    def test_scope_allows_other_existing_shortages(self):
        before = {"shortages": [shortage("A", 10), shortage("B", 20)]}
        after = {"shortages": [shortage("B", 20)]}
        self.assertTrue(W.judge(before, after, [shortage("A", 10)])["passed"])
        after["shortages"][0]["projected_stock"] = -21
        self.assertFalse(W.judge(before, after, [shortage("A", 10)])["passed"])

    def test_excess_cancel_cannot_create_orderable_shortage(self):
        excess = {"item_id": "COIN-CELL", "qty": 800, "rule_id": "INV-003"}
        before = {"excesses": [excess]}
        self.assertFalse(W.judge(before, {"shortages": [shortage("COIN-CELL", 600, True)]}, [excess])["passed"])
        self.assertFalse(W.judge(before, before, [excess])["passed"])
        self.assertTrue(W.judge(before, {"warnings": [{"item_id": "MCU"}]}, [excess])["passed"])

    def test_no_double_application_after_execution(self):
        delay = {"op": "request_due_date_change", "po_id": "PO-2610-106", "new_due_date": "2026-11-27"}
        split = {"tool": "split_po", "args": {"po_id": "PO-2610-106", "split_qty": 1000, "new_due_date": "2026-11-23"}}
        self.assertEqual(W.remaining_ops([delay], split), [delay])
        due = {"tool": "request_due_date_change", "args": {"po_id": "PO-2610-106", "new_due_date": "2026-11-23"}}
        self.assertEqual(W.remaining_ops([delay], due), [])

    def test_renumbered_rejected_candidate_is_not_reselected(self):
        a = {"option_id": "OPT-1", "tool": "cancel_po", "args": {"po_id": "PO-2610-110"},
             "feasible": True, "resolves": True, "side_effects": []}
        self.assertEqual(G.candidates({"options": [{**a, "option_id": "OPT-99"}], "tried": [W.option_key(a)]}), [])

    def test_part_name_does_not_invent_plan_change(self):
        c = W.rule_classify("플러그 케이스 해결 방법 만들어 줘", [])
        self.assertEqual((c.intent, c.whatif, c.unclear), ("ADJUST", [], None))
        self.assertEqual(W.rule_classify("도어 ECN 규정은?", []).intent, "QA")
        self.assertTrue(W.consistency_errors("허브를 2/30로", [], []))

    def test_planner_masks_nested_costs(self):
        self.assertEqual(G.mask({"amount": 1, "a": [{"saved_amount": 2, "unit_price": 3}]}, "PLANNER"),
                         {"amount": None, "a": [{"saved_amount": None, "unit_price": None}]})


class RejectionOutcome(unittest.TestCase):
    """거절 뒤 재검토 결과: 대안 없음·검토 실패·한도 초과·재제안을 구분한다 (decisions 6.30)"""
    def state(self, **extra):
        rejected = {"proposal_id": "p1", "target": "INV-003|CASE-DOOR-V1", "status": "REJECTED",
                    "reason": "입고 취소 불가. 입고 수량 수정 가능", "option": {"item_id": "CASE-DOOR-V1"}}
        other = {"proposal_id": "p2", "target": "INV-003|COIN-CELL", "status": "EXECUTED", "option": {"item_id": "COIN-CELL"}}
        return {"active_target": "INV-003|CASE-DOOR-V1", "proposals": [rejected, other], "reject_count": 1,
                "rejections": [{"proposal_id": "p1"}],
                "current_impact": {"excesses": [{"item_id": "CASE-DOOR-V1", "item_name": "도어센서 옛 케이스"}]}, **extra}

    def outcome(self, update):
        return next(p for p in update["proposals"] if p["proposal_id"] == "p1")["outcome"]

    def test_no_alternative_names_item_and_next_action(self):
        o = self.outcome(G.skip_target(self.state()))
        self.assertEqual((o["code"], o["item_name"]), ("NO_ALTERNATIVE", "도어센서 옛 케이스"))
        self.assertIn("담당자가 검토", o["next_action"])

    def test_review_error_is_not_no_alternative(self):
        o = self.outcome(G.skip_target(self.state(evidence_error="도구 오류: Client failed to connect")))
        self.assertEqual(o["code"], "REVIEW_FAILED")
        self.assertIn("대안이 없다는 뜻은 아닙니다", o["message"])

    def test_limit_and_reproposal(self):
        self.assertEqual(self.outcome(G.skip_target(self.state(reject_count=G.MAX_REJECTS + 1)))["code"], "LIMIT")
        s = self.state(task_id="t", targets=[], verify={}, selected_option={"item_id": "CASE-DOOR-V1"})
        self.assertEqual(self.outcome(G.save_proposal(s))["code"], "REPROPOSED")

    def test_other_items_untouched_and_target_without_rejection_has_no_outcome(self):
        update = G.skip_target(self.state())
        self.assertNotIn("outcome", next(p for p in update["proposals"] if p["proposal_id"] == "p2"))
        self.assertNotIn("proposals", G.skip_target(self.state(active_target="INV-001|MCU")))


class Evidence(unittest.IsolatedAsyncioTestCase):
    async def test_valid_ops_with_intent_explanation_in_unclear_are_retried(self):
        op = W.WhatifOp(op="reschedule_plan", plan_id="PP-02", new_date="2027-01-04")
        model = Mock()
        # This test covers classification retries, not the conservative byte-count fallback.
        # Model-backed token counting is tested separately by the Harness tests.
        model.get_num_tokens_from_messages.return_value = 1000
        model.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=[
            {"raw": None, "parsed": W.Classification(intent="IMPACT", whatif=[op], unclear="문제 있어와 해결 요청이 함께 있어요")},
            {"raw": None, "parsed": W.Classification(intent="ADJUST", whatif=[op])},
        ])
        plans = [{"plan_id": "PP-02", "product_id": "FG-DOOR", "product_name": "도어",
                  "plan_date": "2026-11-18", "qty": 800}]
        with patch.object(G, "llm", model):
            result, errors, turns = await G.classify_question(
                "도어센서 11/18 생산을 1/4로 미루면 문제 있어? 해결 방법 만들어 줘.", plans)
        self.assertEqual((result.intent, result.unclear, errors, turns), ("ADJUST", None, [], 2))

    async def test_explicit_resolution_is_not_lost_after_long_whatif(self):
        model = Mock()
        model.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value={"raw": None, "parsed": W.Classification(intent="IMPACT")})
        with patch.object(G, "llm", model):
            result, errors, _ = await G.classify_question(
                "스위치 수량을 바꾸면 어떻게 돼? 결품과 과잉 해결 방법 만들어 줘.", [])
        self.assertEqual(result.intent, "ADJUST")
        self.assertFalse(errors)
        self.assertFalse(W.explicit_adjust_request("해결안 검수 규정이 뭐야?"))

    async def test_resource_must_match_search_version(self):
        hit = {"doc_id": "ECN-2026-014", "title": "설계변경", "clause_no": "1.1",
               "version": "v1.2", "uri": "doc://ECN-2026-014/1.1", "content": "케이스 변경"}
        with patch.object(G, "call_tool", AsyncMock(return_value=(True, "옛 허브 MCU 교체"))):
            source, error = await G.read_hit({}, hit)
        self.assertIsNone(source)
        self.assertIn("변경", error)

    async def test_no_evidence_blocks_approval(self):
        opt = {"item_id": "MCU", "tool": "split_po", "summary": "분할"}
        with patch.object(G, "call_tool", AsyncMock(return_value=(False, "[RAG_NOT_INDEXED]"))):
            result = await G.collect_evidence({"selected_option": opt})
        self.assertEqual(G.after_evidence(result), "skip_target")

    async def test_qa_cannot_read_guessed_uri(self):
        from langchain_core.messages import AIMessage
        state = {"messages": [AIMessage("", tool_calls=[{"id": "1", "name": "read_doc",
                                                          "args": {"uri": "doc://MADE-UP/1.1"}}])]}
        with patch.object(G, "call_tool", AsyncMock()) as call:
            result = await G.tools_rag(state)
        call.assert_not_awaited()
        self.assertEqual(result["evidence"], [])

    async def test_changed_approved_cost_is_not_executed(self):
        opt = {"item_id": "MCU", "target_rule": "INV-001", "tool": "split_po",
               "args": {"po_id": "PO-2609-001", "split_qty": 100, "new_due_date": "2026-11-25"},
               "feasible": True, "resolves": True, "added_cost": 10, "risk": "LOW"}
        state = {"selected_option": opt, "decision": {"approvalId": "a"},
                 "current_ops": [], "targets": [], "active_target": "INV-001|MCU"}
        with patch.object(G, "call_tool", AsyncMock(side_effect=[
            (True, {}), (True, {"options": [{**opt, "added_cost": 20}]})
        ])) as call:
            result = await G.execute_change(state)
        self.assertFalse(result["executed"]["ok"])
        self.assertEqual([c.args[1] for c in call.await_args_list], ["analyze_impact", "compare_options"])


class Documents(unittest.TestCase):
    def test_corpus_hashes_chunks_and_correct_ecn(self):
        docs = load_documents(ROOT / "data")
        self.assertEqual(len(docs), 6)
        ecn = next(d for d in docs if d["doc_id"] == "ECN-2026-014")
        clause = next(c for c in ecn["chunks"] if c["clause_no"] == "1.1")
        self.assertIn("MCU", clause["content"])
        self.assertIn("아니다", clause["content"])
        self.assertEqual(len({(d["doc_id"], c["clause_no"]) for d in docs for c in d["chunks"]}),
                         sum(len(d["chunks"]) for d in docs))

    def test_duplicate_and_empty_clause_rejected(self):
        for text in ("## 1.1\ntext\n## 1.1\nother", "## 1.1\n\n", "# no clauses"):
            with self.assertRaises(ValueError):
                chunks(text)

    def test_no_api_key_never_uses_fake_vectors(self):
        with self.assertRaisesRegex(EmbeddingError, "RAG_KEY_MISSING"):
            Embeddings("text-embedding-3-small", 1536, "").embed(["hello"])


@unittest.skipUnless(os.getenv("RAG_TEST_DB_URL"), "격리 pgvector DB가 필요합니다.")
class Database(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dsn = os.environ["RAG_TEST_DB_URL"]
        if not dsn.endswith("/capstone_ai_test"):
            raise RuntimeError("테스트 DB 이름을 확인하세요.")
        cls.store = DocumentStore(dsn)
        cls.docs = load_documents(ROOT / "data")
        cls.embedder = FixtureEmbeddings()

    def tearDown(self):
        self.store.index(self.docs, self.embedder)

    def test_index_idempotence_search_resource_filter_and_model_guard(self):
        self.store.index(self.docs, self.embedder)
        self.assertEqual(self.store.index(self.docs, self.embedder)["updated"], 0)
        hits = self.store.search("도어센서 케이스 MCU 교체 아니다", 3, "ECN", self.embedder)
        self.assertTrue(hits)
        for hit in hits:
            self.assertEqual(hit["doc_id"], "ECN-2026-014")
            self.assertEqual(self.store.read(hit["doc_id"], hit["clause_no"]), hit["content"])
        bad = FixtureEmbeddings()
        bad.model = "different-model"
        with self.assertRaisesRegex(RagError, "RAG_INDEX_MISMATCH"):
            self.store.search("설계변경", 3, None, bad)
        with self.assertRaisesRegex(RagError, "INVALID_ARGUMENT"):
            self.store.search("설계변경", 11, None, self.embedder)

    def test_reindex_updates_metadata_deletes_stale_and_rolls_back_failure(self):
        changed = copy.deepcopy(self.docs)
        changed[0]["version"] = "test-v2"
        changed[0]["chunks"] = changed[0]["chunks"][:1]
        self.store.index(changed[:-1], self.embedder)
        with self.assertRaisesRegex(RagError, "NOT_FOUND"):
            self.store.read(self.docs[-1]["doc_id"])
        self.assertEqual(self.store.search("구매", 1, "POLICY", self.embedder)[0]["version"], "test-v2")
        class Broken(FixtureEmbeddings):
            model = "broken-rebuild"
            def embed(self, texts):
                raise EmbeddingError("injected failure")
        with self.assertRaisesRegex(RagError, "injected failure"):
            self.store.index(self.docs, Broken())
        self.assertEqual(self.store.search("구매", 1, "POLICY", self.embedder)[0]["version"], "test-v2")

    def test_rag_cannot_read_business_data(self):
        import psycopg
        with self.store.connect() as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("select * from public.purchase_order")


if __name__ == "__main__":
    unittest.main()
