"""RAG 问答：检索 + 引用来源。

流程：
1. 把用户问题 embedding
2. 在向量库（主库 + 反馈库）检索 top-k 相关 chunks
3. 拼接 prompt：上下文 + 问题
4. 调 LLM 生成回答
5. 返回：答案 + 引用来源列表 + 使用的 provider
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.core import llm_client, vector_store
from src.core.config import settings


@dataclass
class Citation:
    """答案的引用来源。"""

    source_path: str
    source_name: str
    title: str
    section_label: str
    file_type: str
    text_snippet: str       # 命中的原文片段
    score: float = 0.0      # 相似度得分

    def to_dict(self) -> dict:
        return {
            "source_path": self.source_path,
            "source_name": self.source_name,
            "title": self.title,
            "section_label": self.section_label,
            "file_type": self.file_type,
            "text_snippet": self.text_snippet,
            "score": self.score,
        }


@dataclass
class Answer:
    """一次问答的结果。"""

    question: str
    answer: str
    citations: list[Citation] = field(default_factory=list)
    used_provider: str = ""
    used_chunks: int = 0
    model_context_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
            "used_provider": self.used_provider,
            "used_chunks": self.used_chunks,
        }


def _build_prompt(question: str, contexts: list[dict]) -> list[dict]:
    """构造 chat messages。

    System prompt 强调：
    - 只基于提供的上下文回答
    - 必须给出引用编号
    - 不知道就说不知道
    """
    ctx_block = ""
    for i, c in enumerate(contexts, 1):
        title = c.get("title") or c.get("source_name") or "未知来源"
        section = c.get("section_label") or ""
        loc = f"（{title} - {section}）" if section else f"（{title}）"
        ctx_block += f"[{i}]{loc}\n{c['text']}\n\n"

    system = (
        "你是华锐 AMD 行情系统的运维专家助手。请基于下面提供的『上下文』回答用户问题。\n"
        "严格要求：\n"
        "1. 只能使用上下文中的信息，禁止编造。\n"
        "2. 如果上下文不足以回答，明确说『知识库中未找到相关内容』，并建议查阅哪些资料。\n"
        "3. 回答中每条事实陈述后用 [1] [2] 这样的编号标注引用来源。\n"
        "4. 涉及操作步骤时，给出清晰的 1/2/3 步骤。\n"
        "5. 回答使用中文，结构清晰。\n"
    )
    user = f"上下文：\n\n{ctx_block}\n\n用户问题：{question}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def ask(
    question: str,
    top_k: int | None = None,
    history: list[dict] | None = None,
) -> Answer:
    """问答。

    参数：
        question: 用户问题
        top_k: 检索多少条上下文（默认从 config 读）
        history: 多轮对话历史 [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}]

    返回：
        Answer 对象，含答案、引用、provider
    """
    qa_cfg = settings.config.get("qa", {})
    k = top_k or qa_cfg.get("top_k", 5)

    # 1. embedding 用户问题
    client = llm_client.get_client()
    q_vecs, embed_provider = client.embed([question])
    q_vec = q_vecs[0]

    # 2. 检索（主库 + 反馈库，合并）
    search_collections = [vector_store.COLLECTION_NAME]
    if settings.is_enabled("feedback_loop.manual_feedback"):
        search_collections.append(vector_store.FEEDBACK_COLLECTION_NAME)

    hits = vector_store.query_collections(q_vec, k=k, collections=search_collections)

    if not hits:
        # 知识库为空
        return Answer(
            question=question,
            answer="知识库中暂无内容。请先投喂文档（放到投喂文件夹中，并调用 /api/v1/knowledge/scan 触发扫描）。",
            used_provider=embed_provider,
            used_chunks=0,
        )

    # 3. 构造 prompt
    contexts_for_prompt = [
        {
            "text": h["text"],
            "title": h.get("title", ""),
            "source_name": h.get("source_name", ""),
            "section_label": h.get("section_label", ""),
        }
        for h in hits
    ]
    messages = _build_prompt(question, contexts_for_prompt)
    if history:
        # 在 system 之后、当前 user 之前插入历史
        messages = [messages[0]] + history + [messages[-1]]

    # 4. 调 LLM
    chat_cfg = qa_cfg.get("chat_options", {})
    answer_text, chat_provider = client.chat(
        messages,
        temperature=chat_cfg.get("temperature", 0.2),
        max_tokens=chat_cfg.get("max_tokens", 1500),
    )

    # 5. 装配 Answer
    citations = [
        Citation(
            source_path=h.get("source_path", ""),
            source_name=h.get("source_name", ""),
            title=h.get("title", ""),
            section_label=h.get("section_label", ""),
            file_type=h.get("file_type", ""),
            text_snippet=h["text"][:300],
            score=h.get("score", 0.0),
        )
        for h in hits
    ]
    return Answer(
        question=question,
        answer=answer_text,
        citations=citations,
        used_provider=chat_provider,
        used_chunks=len(hits),
    )


def add_approved_qa(question: str, answer: str, metadata: dict | None = None) -> None:
    """把审批通过的 Q&A 写入 feedback collection，下次检索可命中。"""
    client = llm_client.get_client()
    vecs, _ = client.embed([question])
    qid = vector_store.make_chunk_id(
        source_path="feedback",
        content_hash=question[:200],
        chunk_index=0,
    )
    vector_store.upsert_chunks(
        [{
            "id": qid,
            "text": f"问题：{question}\n\n解答：{answer}",
            "embedding": vecs[0],
            "metadata": {
                "source_path": "feedback",
                "source_name": "审批通过的人工问答",
                "title": question[:80],
                "section_label": "FAQ",
                "file_type": "qa",
                **(metadata or {}),
            },
        }],
        collection_name=vector_store.FEEDBACK_COLLECTION_NAME,
    )
