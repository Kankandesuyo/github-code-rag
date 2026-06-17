from openai import OpenAI

from app.config import get_settings


class LLMServiceError(RuntimeError):
    pass


def build_context(chunks: list[dict]) -> str:
    context_blocks: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        metadata = chunk["metadata"]
        context_blocks.append(
            "\n".join(
                [
                    f"[Source {index}]",
                    f"file_path: {metadata['file_path']}",
                    f"chunk_index: {metadata['chunk_index']}",
                    "content:",
                    chunk["content"],
                ]
            )
        )
    return "\n\n---\n\n".join(context_blocks)


def answer_question(question: str, chunks: list[dict]) -> str:
    settings = get_settings()
    if not chunks:
        return "不知道。知识库中没有检索到可用于回答该问题的相关内容。"
    if not settings.deepseek_api_key:
        raise LLMServiceError("DEEPSEEK_API_KEY is not configured")

    client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
    context = build_context(chunks)

    system_prompt = (
        "你是一个代码仓库 RAG 问答助手。必须只基于用户提供的检索片段回答。"
        "如果片段中没有答案，明确回答不知道。"
        "回答必须包含引用来源文件路径，必要时包含 chunk_index。"
        "不要编造代码、命令、依赖或文件。"
    )
    user_prompt = (
        f"检索片段如下：\n{context}\n\n"
        f"用户问题：{question}\n\n"
        "请用中文回答，并在回答中引用来源文件路径。"
    )

    try:
        response = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
    except Exception as exc:
        raise LLMServiceError(f"failed to call DeepSeek API: {exc}") from exc

    answer = response.choices[0].message.content
    return answer or "不知道。模型没有返回有效回答。"
