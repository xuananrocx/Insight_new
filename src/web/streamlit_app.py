"""Insight - Streamlit Web UI。

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
    page_title="Insight",
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
    st.title("🤖 Insight")
    st.caption(f"版本 v{__version__} · 智能知识问答助手")

    h = api_client.health()
    if h.get("status") == "ok":
        st.success("✅ 后端服务在线")
        vec = h.get("vector_db", {})
        llm = h.get("llm", {})
        st.caption(f"知识库片段数：{sum(vec.get('counts', {}).values())}")
        chain = llm.get("effective_chat_chain", [])
        if not chain:
            st.warning("⚠️ 没有可用的对话模型（缺少 API key）")
        else:
            st.caption(f"当前对话模型：{' → '.join(chain)}")
        st.caption(f"投喂文件夹：`{h.get('feed_folder')}`")
    else:
        st.error(f"❌ 后端服务不可用：{h.get('error', '未知错误')}")
        st.caption("请先启动 FastAPI：`python -m src.main --api-only`")

    st.divider()
    st.subheader("快速链接")
    base_url = api_client._get_base_url()
    st.markdown(f"- [📖 API 接口文档]({base_url}/docs)")
    st.markdown(f"- [💚 健康检查]({base_url}/health)")


# ===== 标签页 =====
tab_qa, tab_kb, tab_review, tab_system = st.tabs(
    ["💬 问答", "📚 知识库", "✅ 审批", "🩺 系统"]
)


# ===== 💬 问答 =====
with tab_qa:
    st.header("💬 智能问答")
    st.caption("向 AI 提问，系统会从知识库中检索相关文档并生成回答。点赞的回答会进入审批队列。")

    # 历史对话
    for i, msg in enumerate(st.session_state.chat_history):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("citations"):
                with st.expander(f"📎 引用来源（共 {len(msg['citations'])} 条）"):
                    for j, c in enumerate(msg["citations"], 1):
                        st.markdown(
                            f"**[{j}] {c.get('title', '')}** "
                            f"_{c.get('section_label', '')}_ "
                            f"`{c.get('source_name', '')}` "
                            f"（相似度：{c.get('score', 0):.3f}）"
                        )
                        st.caption(c.get("text_snippet", ""))

    # 用户输入
    if user_q := st.chat_input("请输入您的问题，例如：如何排查系统故障？"):
        st.chat_message("user").markdown(user_q)
        st.session_state.chat_history.append({"role": "user", "content": user_q})

        # 构造 history 给后端（仅最后 6 轮）
        history_for_api = [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state.chat_history[:-1][-6:]
        ]

        try:
            with st.spinner("正在检索知识库并生成答案，请稍候..."):
                resp = api_client.qa_ask(user_q, top_k=5, history=history_for_api)
        except Exception as e:
            st.error(f"查询失败：{e}")
            resp = None

        if resp:
            answer = resp.get("answer", "")
            citations = resp.get("citations", [])
            used_provider = resp.get("used_provider", "")

            st.chat_message("assistant").markdown(answer)
            if citations:
                with st.expander(f"📎 引用来源（共 {len(citations)} 条）"):
                    for j, c in enumerate(citations, 1):
                        st.markdown(
                            f"**[{j}] {c.get('title', '')}** "
                            f"_{c.get('section_label', '')}_ "
                            f"`{c.get('source_name', '')}` "
                            f"（相似度：{c.get('score', 0):.3f}）"
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
                        st.success(f"已加入待审批队列（编号 {r['feedback_id']}）")
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
                    st.info("已记录，感谢反馈")
                except Exception as e:
                    st.error(f"提交失败：{e}")
        with col3:
            note = st.text_input("补充说明（可选）", key="fb_note")

        if st.button("🗑️ 清空当前对话"):
            st.session_state.chat_history = []
            st.session_state.last_answer = None
            st.rerun()


# ===== 📚 知识库 =====
with tab_kb:
    st.header("📚 知识库管理")
    st.caption("把相关文档投喂给系统，AI 才能基于这些内容回答问题。")

    col_a, col_b, col_c = st.columns(3)
    try:
        stats = api_client.kb_stats()
        col_a.metric("已入库文件", stats.get("files_done", 0))
        col_b.metric("知识片段总数", stats.get("total_chunks", 0))
        col_c.metric("待审批反馈", stats.get("feedback_pending", 0))
    except Exception as e:
        st.error(f"获取统计数据失败：{e}")

    st.divider()

    # 触发扫描
    col_scan1, col_scan2 = st.columns([1, 3])
    with col_scan1:
        if st.button("🔄 扫描投喂文件夹", type="primary"):
            with st.spinner("正在扫描投喂文件夹，请稍候..."):
                try:
                    r = api_client.kb_scan()
                    result = r.get("result", {})
                    st.success(
                        f"扫描完成：✨新增 {result.get('added', 0)}　|　"
                        f"♻️更新 {result.get('updated', 0)}　|　"
                        f"⏭️跳过 {result.get('skipped', 0)}　|　"
                        f"❌失败 {result.get('failed', 0)}　|　"
                        f"🗑️移除 {result.get('removed', 0)}"
                    )
                    if result.get("errors"):
                        with st.expander(f"⚠️ 错误详情（共 {len(result['errors'])} 条）"):
                            for e in result["errors"][:20]:
                                st.write(f"- `{e.get('file')}`：{e.get('error')}")
                except Exception as e:
                    st.error(f"扫描失败：{e}")

    # 文件上传
    st.subheader("📤 上传单个文件")
    uploaded = st.file_uploader(
        "选择文件（支持 PDF / Word / Excel / Markdown 等格式）",
        type=None,
    )
    if uploaded is not None:
        if st.button("📥 上传并添加到知识库"):
            try:
                with st.spinner(f"正在上传 {uploaded.name}..."):
                    r = api_client.kb_upload(uploaded.name, uploaded.getvalue())
                if r.get("ok"):
                    st.success(
                        f"上传成功：✨新增 {r.get('added', 0)}　|　"
                        f"♻️更新 {r.get('updated', 0)}"
                    )
                elif r.get("skipped"):
                    st.info("文件内容未变化，已跳过")
                else:
                    st.error(f"上传失败：{r.get('errors', '未知错误')}")
            except Exception as e:
                st.error(f"上传失败：{e}")

    # 文件列表
    st.subheader("📋 文件列表")
    filter_options = {
        "all": "全部",
        "done": "已入库",
        "pending": "处理中",
        "failed": "失败",
    }
    filter_keys = list(filter_options.keys())
    selected_filter = st.selectbox(
        "按状态筛选",
        filter_keys,
        format_func=lambda k: filter_options[k],
        index=0,
    )
    try:
        files = api_client.kb_files(
            status=None if selected_filter == "all" else selected_filter,
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
            status_text = {
                "done": "已入库", "pending": "处理中", "failed": "失败",
            }.get(f["status"], "未知")
            with st.expander(
                f"{status_emoji} {f['relative_path']} "
                f"（{f['file_type']}，{f['chunk_count']} 个片段，{status_text}）"
            ):
                col_meta1, col_meta2 = st.columns([3, 1])
                with col_meta1:
                    st.caption(f"文件大小：{f['file_size']:,} 字节")
                    st.caption(f"内容指纹：`{f['content_hash'][:16]}...`")
                    if f.get("source_package"):
                        st.caption(f"来自压缩包：`{f['source_package']}`")
                    if f.get("error_message"):
                        st.error(f"错误信息：{f['error_message']}")
                with col_meta2:
                    if st.button("🗑️ 删除", key=f"del_{f['id']}"):
                        try:
                            api_client.kb_delete(f["relative_path"])
                            st.success("已从知识库中删除")
                            st.rerun()
                        except Exception as e:
                            st.error(f"删除失败：{e}")
    else:
        st.info("📭 知识库为空。请先把相关文档放到投喂文件夹，再点击「扫描投喂文件夹」。")


# ===== ✅ 审批 =====
with tab_review:
    st.header("✅ 反馈审批")
    st.caption("用户点赞过的问答会进入这里，审批通过后会自动沉淀到知识库，让 AI 越用越聪明。")

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
            f"编号 #{fb['id']} | {fb['question'][:60]}{'...' if len(fb['question']) > 60 else ''}"
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
                            st.caption(f"- {s.get('source_name', '')}（{s.get('section_label', '')}）")
                except Exception:
                    pass
            st.caption(f"提交时间：{fb.get('created_at', '')}　|　回答所用模型：{fb.get('used_provider', '')}")

            col_a, col_b, col_c = st.columns([1, 1, 3])
            with col_a:
                if st.button("✅ 通过并入库", key=f"appr_{fb['id']}", type="primary"):
                    try:
                        r = api_client.feedback_review(fb["id"], "approved")
                        if r.get("ok"):
                            st.success("已通过并写入知识库，AI 下次回答会参考此内容")
                            st.rerun()
                        else:
                            st.error(f"操作失败：{r.get('error')}")
                    except Exception as e:
                        st.error(f"操作失败：{e}")
            with col_b:
                if st.button("❌ 拒绝", key=f"rej_{fb['id']}"):
                    try:
                        r = api_client.feedback_review(fb["id"], "rejected")
                        if r.get("ok"):
                            st.success("已拒绝，不会写入知识库")
                            st.rerun()
                        else:
                            st.error(f"操作失败：{r.get('error')}")
                    except Exception as e:
                        st.error(f"操作失败：{e}")

    # 已审批
    st.divider()
    with st.expander("📜 查看已通过的反馈"):
        try:
            approved = api_client.feedback_list("approved")
            if not approved:
                st.caption("暂无已通过的反馈")
            for fb in approved:
                st.markdown(f"#{fb['id']} **{fb['question'][:60]}**")
                st.caption(f"审批人：{fb.get('reviewer', '')}　|　时间：{fb.get('reviewed_at', '')}")
        except Exception as e:
            st.error(f"获取失败：{e}")


# ===== 🩺 系统 =====
with tab_system:
    st.header("🩺 系统状态")

    # Embedding 模型切换
    st.subheader("🧠 向量化模型（Embedding）")
    st.caption("向量化模型负责把文档和问题转成数字向量，用于语义检索。模型越大效果越好但更慢。")
    try:
        emb = api_client.embedding_status()
        st.caption(f"当前模型：`{emb.get('current_model', '')}`　|　向量维度：{emb.get('dimensions', '?')}")

        models = emb.get("available_models", [])
        if models:
            active_name = emb.get("current_model", "")
            options = {m["name"]: f"{m['label']}（{m['size']}）- {m['description']}" for m in models}
            selected = st.selectbox(
                "选择向量化模型",
                options=list(options.keys()),
                format_func=lambda k: options[k],
                index=list(options.keys()).index(active_name) if active_name in options else 0,
            )
            if selected != active_name:
                if st.button("🔄 确认切换", type="primary"):
                    with st.spinner(f"正在加载 {selected}，首次切换需下载模型，请耐心等待..."):
                        try:
                            r = api_client.embedding_switch(selected)
                            st.success(f"已切换到 `{r.get('current_model', selected)}`")
                            st.warning("⚠️ 切换模型后向量维度可能改变，建议重新扫描投喂文件夹以获得最佳检索效果。")
                            st.rerun()
                        except Exception as e:
                            st.error(f"切换失败：{e}")
        else:
            st.info("未配置可选模型")
    except Exception as e:
        st.error(f"获取向量化模型状态失败：{e}")

    st.divider()

    # 系统健康
    st.subheader("📊 系统健康详情")
    h = api_client.health()
    st.json(h)

    st.divider()
    st.subheader("⚙️ 功能开关")
    st.caption("配置文件中的各项功能开关状态，可在 config.yaml 中修改。")
    try:
        st.json(h.get("feature_flags", {}))
    except Exception:
        pass
