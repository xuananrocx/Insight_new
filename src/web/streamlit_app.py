"""AMD AI Assistant - Streamlit Web UI。

四个标签页：
- 💬 问答：与 AI 对话，可对答案点赞（→ 进入审批队列）
- 📚 知识库：投喂文件 / 触发扫描 / 查看/删除文件
- ✅ 审批：审批点赞的问答对
- 🩺 系统状态：健康检查

启动：streamlit run src/web/streamlit_app.py
默认连接 http://localhost:8000（FastAPI）

架构约束：
    Streamlit 只通过 HTTP 调 FastAPI，绝不直接访问 Chroma / SQLite。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

# 把项目根加入 sys.path（独立运行 streamlit 时需要）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import __version__  # noqa: E402
from src.web import api_client  # noqa: E402


# ===== 页面全局设置 =====
st.set_page_config(
    page_title="AMD AI Assistant",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ===== 会话状态 =====
def _init_state() -> None:
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []   # list of {"role","content","citations"}
    if "last_answer" not in st.session_state:
        st.session_state.last_answer = None  # 用于反馈


_init_state()


# ===== 侧边栏 =====
with st.sidebar:
    st.title("🤖 AMD AI Assistant")
    st.caption(f"v{__version__} - 华锐 AMD 行情系统智能运维助手")

    h = api_client.health()
    if h.get("status") == "ok":
        st.success("✅ API 在线")
        vec = h.get("vector_db", {})
        llm = h.get("llm", {})
        st.caption(f"向量库 chunks: {sum(vec.get('counts', {}).values())}")
        chain = llm.get("effective_chat_chain", [])
        if not chain:
            st.warning("⚠️ 没有可用的 LLM provider（缺 API key）")
        else:
            st.caption(f"当前 chat provider: {' → '.join(chain)}")
        st.caption(f"投喂文件夹: `{h.get('feed_folder')}`")
    else:
        st.error(f"❌ API 不可用：{h.get('error', '未知错误')}")
        st.caption("请先启动 FastAPI：`python -m src.main --api-only`")

    st.divider()
    st.subheader("快速链接")
    st.markdown(f"- [API 文档]({api_client._get_base_url()}/docs)")
    st.markdown(f"- [健康检查]({api_client._get_base_url()}/health)")


# ===== 标签页 =====
tab_qa, tab_kb, tab_review, tab_system = st.tabs(
    ["💬 问答", "📚 知识库", "✅ 审批", "🩺 系统"]
)


# ===== 💬 问答 =====
with tab_qa:
    st.header("💬 智能问答")

    # 历史对话
    for i, msg in enumerate(st.session_state.chat_history):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("citations"):
                with st.expander(f"📎 引用来源（{len(msg['citations'])} 条）"):
                    for j, c in enumerate(msg["citations"], 1):
                        st.markdown(
                            f"**[{j}] {c.get('title', '')}** "
                            f"_{c.get('section_label', '')}_ "
                            f"`{c.get('source_name', '')}` "
                            f"(score: {c.get('score', 0):.3f})"
                        )
                        st.caption(c.get("text_snippet", ""))

    # 用户输入
    if user_q := st.chat_input("基于知识库提问，比如：AMD 行情断线如何排查？"):
        st.chat_message("user").markdown(user_q)
        st.session_state.chat_history.append({"role": "user", "content": user_q})

        # 构造 history 给后端（仅最后 6 轮）
        history_for_api = [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state.chat_history[:-1][-6:]
        ]

        try:
            with st.spinner("正在检索知识库并生成答案..."):
                resp = api_client.qa_ask(user_q, top_k=5, history=history_for_api)
        except Exception as e:
            st.error(f"调用失败：{e}")
            resp = None

        if resp:
            answer = resp.get("answer", "")
            citations = resp.get("citations", [])
            used_provider = resp.get("used_provider", "")

            st.chat_message("assistant").markdown(answer)
            if citations:
                with st.expander(f"📎 引用来源（{len(citations)} 条）"):
                    for j, c in enumerate(citations, 1):
                        st.markdown(
                            f"**[{j}] {c.get('title', '')}** "
                            f"_{c.get('section_label', '')}_ "
                            f"`{c.get('source_name', '')}` "
                            f"(score: {c.get('score', 0):.3f})"
                        )
                        st.caption(c.get("text_snippet", ""))

            st.session_state.chat_history.append({
                "role": "assistant",
                "content": answer,
                "citations": citations,
            })
            st.session_state.last_answer = {
                "question": user_q,
                "answer": answer,
                "citations": citations,
                "used_provider": used_provider,
            }

    # 反馈区
    if st.session_state.last_answer:
        st.divider()
        st.subheader("这个答案有帮助吗？")
        col1, col2, col3 = st.columns([1, 1, 4])
        with col1:
            if st.button("👍 有用", type="primary"):
                try:
                    r = api_client.feedback_submit({
                        **st.session_state.last_answer,
                        "rating": 1,
                    })
                    if r.get("feedback_id"):
                        st.success(f"已加入待审批队列 (id={r['feedback_id']})")
                    else:
                        st.info(r.get("message", "已记录"))
                except Exception as e:
                    st.error(f"提交失败：{e}")
        with col2:
            if st.button("👎 没用"):
                try:
                    api_client.feedback_submit({
                        **st.session_state.last_answer,
                        "rating": -1,
                    })
                    st.info("已记录，谢谢反馈")
                except Exception as e:
                    st.error(f"提交失败：{e}")
        with col3:
            note = st.text_input("补充说明（可选）", key="fb_note")

        if st.button("🗑️ 清空对话"):
            st.session_state.chat_history = []
            st.session_state.last_answer = None
            st.rerun()


# ===== 📚 知识库 =====
with tab_kb:
    st.header("📚 知识库管理")

    col_a, col_b, col_c = st.columns(3)
    try:
        stats = api_client.kb_stats()
        col_a.metric("已入库文件", stats.get("files_done", 0))
        col_b.metric("总 chunks", stats.get("total_chunks", 0))
        col_c.metric("待审批", stats.get("feedback_pending", 0))
    except Exception as e:
        st.error(f"获取统计失败：{e}")

    st.divider()

    # 触发扫描
    col_scan1, col_scan2 = st.columns([1, 3])
    with col_scan1:
        if st.button("🔄 扫描投喂文件夹", type="primary"):
            with st.spinner("扫描中..."):
                try:
                    r = api_client.kb_scan()
                    result = r.get("result", {})
                    st.success(
                        f"扫描完成：✨新增 {result.get('added', 0)} | "
                        f"♻️更新 {result.get('updated', 0)} | "
                        f"⏭️跳过 {result.get('skipped', 0)} | "
                        f"❌失败 {result.get('failed', 0)} | "
                        f"🗑️移除 {result.get('removed', 0)}"
                    )
                    if result.get("errors"):
                        with st.expander(f"⚠️ 错误详情（{len(result['errors'])} 条）"):
                            for e in result["errors"][:20]:
                                st.write(f"- `{e.get('file')}`: {e.get('error')}")
                except Exception as e:
                    st.error(f"扫描失败：{e}")

    # 文件上传
    st.subheader("📤 上传单文件")
    uploaded = st.file_uploader(
        "选择文件（支持 PDF/Word/Excel/Markdown 等）",
        type=None,
    )
    if uploaded is not None:
        if st.button("📥 上传并入库"):
            try:
                with st.spinner(f"上传 {uploaded.name}..."):
                    r = api_client.kb_upload(uploaded.name, uploaded.getvalue())
                if r.get("ok"):
                    st.success(
                        f"上传成功：✨新增 {r.get('added', 0)} | "
                        f"♻️更新 {r.get('updated', 0)}"
                    )
                elif r.get("skipped"):
                    st.info("文件未变化，已跳过")
                else:
                    st.error(f"上传失败：{r.get('errors', '未知错误')}")
            except Exception as e:
                st.error(f"上传失败：{e}")

    # 文件列表
    st.subheader("📋 文件列表")
    filter_status = st.selectbox(
        "筛选状态",
        ["all", "done", "pending", "failed"],
        index=0,
    )
    try:
        files = api_client.kb_files(
            status=None if filter_status == "all" else filter_status,
        )
    except Exception as e:
        st.error(f"获取文件列表失败：{e}")
        files = []

    if files:
        st.caption(f"共 {len(files)} 个文件")
        for f in files:
            status_emoji = {
                "done": "✅", "pending": "⏳", "failed": "❌",
            }.get(f["status"], "❓")
            with st.expander(
                f"{status_emoji} {f['relative_path']} "
                f"({f['file_type']}, {f['chunk_count']} chunks)"
            ):
                col_meta1, col_meta2 = st.columns([3, 1])
                with col_meta1:
                    st.caption(f"大小: {f['file_size']:,} bytes")
                    st.caption(f"Hash: `{f['content_hash'][:16]}...`")
                    if f.get("source_package"):
                        st.caption(f"来自压缩包: `{f['source_package']}`")
                    if f.get("error_message"):
                        st.error(f"错误: {f['error_message']}")
                with col_meta2:
                    if st.button("🗑️ 删除", key=f"del_{f['id']}"):
                        try:
                            api_client.kb_delete(f["relative_path"])
                            st.success("已删除")
                            st.rerun()
                        except Exception as e:
                            st.error(f"删除失败：{e}")
    else:
        st.info("知识库为空。请先投喂文档（放到投喂文件夹）后点击「扫描」。")


# ===== ✅ 审批 =====
with tab_review:
    st.header("✅ 反馈审批")

    try:
        pending = api_client.feedback_list("pending")
    except Exception as e:
        st.error(f"获取待审批列表失败：{e}")
        pending = []

    if not pending:
        st.info("📭 暂无待审批的反馈")
    else:
        st.caption(f"共 {len(pending)} 条待审批")

    for fb in pending:
        with st.expander(
            f"#{fb['id']} | {fb['question'][:60]}{'...' if len(fb['question']) > 60 else ''}"
        ):
            st.markdown(f"**问题：** {fb['question']}")
            st.markdown(f"**答案：**")
            st.markdown(fb["answer"])
            if fb.get("sources_json"):
                try:
                    sources = json.loads(fb["sources_json"]) if isinstance(fb["sources_json"], str) else fb["sources_json"]
                    if sources:
                        st.caption("引用来源：")
                        for s in sources:
                            st.caption(f"- {s.get('source_name', '')} ({s.get('section_label', '')})")
                except Exception:
                    pass
            st.caption(f"提交时间：{fb.get('created_at', '')} | provider: {fb.get('used_provider', '')}")

            col_a, col_b, col_c = st.columns([1, 1, 3])
            with col_a:
                if st.button("✅ 通过并入库", key=f"appr_{fb['id']}", type="primary"):
                    try:
                        r = api_client.feedback_review(fb["id"], "approved")
                        if r.get("ok"):
                            st.success("已通过并写入知识库")
                            st.rerun()
                        else:
                            st.error(f"失败：{r.get('error')}")
                    except Exception as e:
                        st.error(f"失败：{e}")
            with col_b:
                if st.button("❌ 拒绝", key=f"rej_{fb['id']}"):
                    try:
                        r = api_client.feedback_review(fb["id"], "rejected")
                        if r.get("ok"):
                            st.success("已拒绝")
                            st.rerun()
                        else:
                            st.error(f"失败：{r.get('error')}")
                    except Exception as e:
                        st.error(f"失败：{e}")

    # 已审批
    st.divider()
    with st.expander("📜 查看已通过"):
        try:
            approved = api_client.feedback_list("approved")
            for fb in approved:
                st.markdown(f"#{fb['id']} **{fb['question'][:60]}**")
                st.caption(f"审批人: {fb.get('reviewer', '')} | 时间: {fb.get('reviewed_at', '')}")
        except Exception as e:
            st.error(f"获取失败：{e}")


# ===== 🩺 系统 =====
with tab_system:
    st.header("🩺 系统状态")

    h = api_client.health()
    st.json(h)

    st.divider()
    st.subheader("Feature Flags")
    try:
        st.json(h.get("feature_flags", {}))
    except Exception:
        pass
