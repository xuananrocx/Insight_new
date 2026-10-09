# Source baseline audit

2026-10-09. Canonical source: E:/Claude Code Project/Personal/Insight.
F:/OrcaProject/Personal/Insight-development is a preserved comparison copy, not a release source.

Comparison ignores CRLF/LF only. Historical matches do not prove a whole copy is safe to delete.

| Different path | Finding |
|---|---|
| `README.md` | matches historical Git blob |
| `build/Info.plist` | matches historical Git blob |
| `config.yaml` | matches historical Git blob |
| `docs/retrieval-modes.md` | requires review; no exact historical match |
| `frontend/index.html` | matches historical Git blob |
| `frontend/package.json` | matches historical Git blob |
| `frontend/pnpm-lock.yaml` | matches historical Git blob |
| `frontend/src/App.tsx` | matches historical Git blob |
| `frontend/src/components/ai-call-log-config-card.tsx` | matches historical Git blob |
| `frontend/src/components/app-sidebar.tsx` | matches historical Git blob |
| `frontend/src/components/batch-upload-dialog.tsx` | matches historical Git blob |
| `frontend/src/components/chat-history.tsx` | matches historical Git blob |
| `frontend/src/components/confirm-dialog.tsx` | matches historical Git blob |
| `frontend/src/components/interrupted-tasks-banner.tsx` | matches historical Git blob |
| `frontend/src/components/kb-concepts-table.tsx` | matches historical Git blob |
| `frontend/src/components/kb-document-graph.tsx` | matches historical Git blob |
| `frontend/src/components/kb-global-summary-card.tsx` | matches historical Git blob |
| `frontend/src/components/markdown-content.tsx` | matches historical Git blob |
| `frontend/src/components/rebuild-confirm-modal.tsx` | matches historical Git blob |
| `frontend/src/components/rebuild-progress-dialog.tsx` | matches historical Git blob |
| `frontend/src/components/retrieval-mode-select.tsx` | matches historical Git blob |
| `frontend/src/components/stage-row.tsx` | matches historical Git blob |
| `frontend/src/components/stats-cards.tsx` | matches historical Git blob |
| `frontend/src/components/thinking-panel.tsx` | requires review; no exact historical match |
| `frontend/src/components/top-k-select.tsx` | matches historical Git blob |
| `frontend/src/components/topbar.tsx` | matches historical Git blob |
| `frontend/src/components/ui/select.tsx` | matches historical Git blob |
| `frontend/src/components/upload-activity-layer.tsx` | matches historical Git blob |
| `frontend/src/hooks/use-background.ts` | matches historical Git blob |
| `frontend/src/hooks/use-chat-sessions.ts` | matches historical Git blob |
| `frontend/src/hooks/use-local-storage.ts` | matches historical Git blob |
| `frontend/src/index.css` | matches historical Git blob |
| `frontend/src/lib/api.ts` | matches historical Git blob |
| `frontend/src/pages/ai-logs-page.tsx` | matches historical Git blob |
| `frontend/src/pages/chat-page.tsx` | requires review; no exact historical match |
| `frontend/src/pages/kb-detail-page.tsx` | matches historical Git blob |
| `frontend/src/pages/kb-page.tsx` | matches historical Git blob |
| `frontend/src/pages/knowledge-page.tsx` | matches historical Git blob |
| `frontend/src/pages/logs-page.tsx` | matches historical Git blob |
| `frontend/src/pages/settings-page.tsx` | matches historical Git blob |
| `frontend/vite.config.ts` | matches historical Git blob |
| `requirements.txt` | matches historical Git blob |
| `scripts/smoke_retrieval_modes.py` | matches historical Git blob |
| `src/__init__.py` | matches historical Git blob |
| `src/api/main.py` | matches historical Git blob |
| `src/api/routes_ai_logs.py` | matches historical Git blob |
| `src/api/routes_feedback.py` | matches historical Git blob |
| `src/api/routes_kbs.py` | matches historical Git blob |
| `src/api/routes_knowledge.py` | matches historical Git blob |
| `src/api/routes_logs.py` | matches historical Git blob |
| `src/api/routes_qa.py` | matches historical Git blob |
| `src/api/routes_sessions.py` | matches historical Git blob |
| `src/api/routes_settings.py` | matches historical Git blob |
| `src/core/ai_call_logger.py` | matches historical Git blob |
| `src/core/llm_client.py` | matches historical Git blob |
| `src/core/llm_providers.py` | matches historical Git blob |
| `src/core/vector_store.py` | matches historical Git blob |
| `src/db/metadata_db.py` | matches historical Git blob |
| `src/feedback/approval.py` | matches historical Git blob |
| `src/knowledge/ai_summarizer.py` | matches historical Git blob |
| `src/knowledge/batch_upload.py` | matches historical Git blob |
| `src/knowledge/ingestion.py` | matches historical Git blob |
| `src/knowledge/kb_pack.py` | matches historical Git blob |
| `src/knowledge/parsers/xlsx_parser.py` | matches historical Git blob |
| `src/qa/bm25_index.py` | matches historical Git blob |
| `src/qa/deep_ai.py` | requires review; no exact historical match |
| `src/qa/rag.py` | matches historical Git blob |
| `src/qa/reranker.py` | matches historical Git blob |
| `tests/test_batch_upload_stages.py` | matches historical Git blob |
| `tests/test_bm25_index.py` | matches historical Git blob |
| `tests/test_deep_ai.py` | requires review; no exact historical match |
| `tests/test_enhanced_retrieval.py` | matches historical Git blob |
| `tests/test_provider_config.py` | matches historical Git blob |
| `tests/test_retrieval_modes.py` | matches historical Git blob |

## Additional files in reviewed source directories


No copy files were merged, overwritten or deleted. Preserve unmatched files for manual review.
Git remotes: origin = Insight_new; legacy-origin retained for historical reference; default push = origin.
Existing release tags are preserved; app version remains 0.1.1.
