from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise SystemExit("请先安装 streamlit：pip install -r requirements.txt") from exc


API_URL = "https://ai-saas.eastmoney.com/poc/stream/ask"
MAX_HISTORY = 5


def get_api_key(user_value: str = "") -> str:
    value = user_value.strip()
    if value:
        return value

    env_value = os.getenv("EM_API_KEY", "").strip()
    if env_value:
        return env_value

    try:
        return str(st.secrets.get("EM_API_KEY", "")).strip()
    except Exception:
        return ""


def build_payload(question: str, deep_think: bool, channel_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "question": question.strip(),
        "deepThink": deep_think,
    }
    if channel_id.strip():
        payload["channelId"] = channel_id.strip()
    return payload


def parse_event_line(line: str) -> dict[str, Any] | None:
    if not line.startswith("data:"):
        return None
    data_part = line[5:].strip()
    if not data_part or data_part == "[DONE]":
        return None
    try:
        event = json.loads(data_part)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def fetch_stream(api_key: str, question: str, deep_think: bool, channel_id: str) -> dict[str, Any]:
    payload = build_payload(question, deep_think, channel_id)
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "em_api_key": api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        },
    )

    state: dict[str, Any] = {
        "payload": payload,
        "http_status": None,
        "channel_id": "",
        "q_id": "",
        "trace_id": "",
        "warning": "",
        "text_chunks": [],
        "final_text": "",
        "ref_index_list": [],
        "news_index_list": [],
        "graph_events": [],
        "raw_events": [],
        "error": "",
    }

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            state["http_status"] = response.status
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                event = parse_event_line(line)
                if not event:
                    continue

                state["raw_events"].append(event)
                message_type = event.get("messageType", "")

                if message_type == "METADATA":
                    state["channel_id"] = event.get("channelId", "") or state["channel_id"]
                    state["q_id"] = event.get("qId", "") or state["q_id"]
                    state["warning"] = event.get("warning", "") or state["warning"]
                elif message_type == "TEXT":
                    state["text_chunks"].append(event.get("text", ""))
                elif message_type == "FINAL_TEXT":
                    state["final_text"] = event.get("text", "")
                elif message_type == "REF_INDEX":
                    state["ref_index_list"].extend(event.get("refIndexList", []))
                elif message_type == "NEWS_INDEX":
                    state["news_index_list"].extend(event.get("newsIndexList", []))
                elif message_type == "GRAPH":
                    state["graph_events"].append(event)
                elif message_type == "FINISH":
                    state["trace_id"] = event.get("traceId", "") or state["trace_id"]
                elif message_type == "ERROR":
                    state["error"] = event.get("reason", "接口返回错误")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        state["error"] = f"HTTP {exc.code}: {exc.reason}\n{body}"
    except urllib.error.URLError as exc:
        state["error"] = f"请求失败: {exc.reason}"

    return state


def merge_text(chunks: list[str]) -> str:
    return "".join(chunk for chunk in chunks if chunk)


def split_think_block(text: str) -> tuple[str, str, str]:
    start = text.find("<think>")
    end = text.find("</think>")
    if start == -1 or end == -1 or end < start:
        return text.strip(), "", ""
    before = text[:start].strip()
    think = text[start + len("<think>") : end].strip()
    after = text[end + len("</think>") :].strip()
    return before, think, after


def render_sources(title: str, items: list[dict[str, Any]], empty_tip: str) -> None:
    st.subheader(title)
    if not items:
        st.info(empty_tip)
        return

    for idx, item in enumerate(items, start=1):
        item_title = item.get("title") or item.get("name") or f"来源 {idx}"
        with st.expander(f"{idx}. {item_title}", expanded=False):
            st.write("**类型**")
            st.write(item.get("type", "-"))
            st.write("**编号**")
            st.write(item.get("refId", item.get("code", "-")))
            st.write("**引用方式**")
            st.write(item.get("referenceType", item.get("source", "-")))
            st.write("**发布时间**")
            st.write(item.get("publishDate", item.get("date", "-")))
            if item.get("jumpUrl"):
                st.write("**链接**")
                st.write(item["jumpUrl"])
            if item.get("summaryContent"):
                st.write("**摘要**")
                st.write(item["summaryContent"])
            if item.get("markdown"):
                st.code(item["markdown"], language="markdown")


def render_graph_events(events: list[dict[str, Any]]) -> None:
    st.subheader("图表消息")
    if not events:
        st.info("当前没有返回图表类消息。")
        return

    for idx, event in enumerate(events, start=1):
        with st.expander(f"图表消息 {idx}", expanded=False):
            st.json(event)


def main() -> None:
    st.set_page_config(page_title="妙想流式接口演示", page_icon="🧠", layout="wide")

    st.title("妙想流式接口演示")
    st.caption("支持流式输出、研报/资讯溯源和多轮对话；密钥通过 Secrets 或环境变量读取，不写死在代码里。")

    if "history" not in st.session_state:
        st.session_state.history = []
    if "last_channel_id" not in st.session_state:
        st.session_state.last_channel_id = ""
    if "last_result" not in st.session_state:
        st.session_state.last_result = None

    with st.sidebar:
        st.header("请求参数")
        api_key_input = st.text_input(
            "API Key（可留空，优先读 Secrets/环境变量）",
            value="",
            type="password",
        )
        question = st.text_area(
            "问题",
            value="请总结东方财富近3个月的研报核心观点，并给出投资建议和风险提示",
            height=120,
        )
        deep_think = st.checkbox("开启深度思考", value=True)
        channel_id = st.text_input(
            "channelId（多轮对话可填上一轮返回值）",
            value=st.session_state.last_channel_id,
        )
        show_raw = st.checkbox("显示原始事件", value=True)
        run_btn = st.button("开始请求", type="primary", use_container_width=True)
        st.divider()
        st.write("**部署提示**")
        st.caption("上线时请把 EM_API_KEY 放到 Streamlit Cloud 的 Secrets 面板里。")

    if run_btn:
        api_key = get_api_key(api_key_input)
        if not api_key:
            st.error("没有找到 API Key。请填写左侧输入框，或在 Secrets / EM_API_KEY 中配置。")
            return

        with st.status("正在连接妙想接口...", expanded=True):
            result = fetch_stream(
                api_key=api_key,
                question=question.strip(),
                deep_think=deep_think,
                channel_id=channel_id.strip(),
            )

        if result.get("channel_id"):
            st.session_state.last_channel_id = result["channel_id"]
        st.session_state.last_result = result
        st.session_state.history.insert(0, result)
        st.session_state.history = st.session_state.history[:MAX_HISTORY]

    latest = st.session_state.last_result

    if latest:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("HTTP", latest.get("http_status") or "-")
        with col2:
            st.metric("channelId", latest.get("channel_id") or "未返回")
        with col3:
            st.metric("研报引用数", len(latest.get("ref_index_list", [])))
        with col4:
            st.metric("原始事件数", len(latest.get("raw_events", [])))

        left, right = st.columns([1.15, 1])

        with left:
            st.subheader("实时回答")
            if latest.get("error"):
                st.error(latest["error"])

            st.write("**请求体**")
            st.code(json.dumps(latest.get("payload", {}), ensure_ascii=False, indent=2), language="json")

            st.write("**会话信息**")
            st.json(
                {
                    "channelId": latest.get("channel_id", ""),
                    "qId": latest.get("q_id", ""),
                    "traceId": latest.get("trace_id", ""),
                    "warning": latest.get("warning", ""),
                }
            )

            final_text = latest.get("final_text") or merge_text(latest.get("text_chunks", []))
            if final_text:
                prefix, think_text, suffix = split_think_block(final_text)
                answer_text = " ".join(part for part in [prefix, suffix] if part).strip()
                if think_text:
                    st.write("**思考内容**")
                    st.info(think_text)
                    st.write("**最终回答**")
                    st.markdown(answer_text or "(无可展示最终回答)")
                else:
                    st.write("**最终回答**")
                    st.markdown(final_text)
            else:
                st.info("还没有可展示的文本。")

            with st.expander("流式片段", expanded=False):
                st.code("\n".join(latest.get("text_chunks", [])) or "(无片段)", language="text")

            st.download_button(
                "下载本次 JSON",
                data=json.dumps(latest, ensure_ascii=False, indent=2),
                file_name="miaoxiang_result.json",
                mime="application/json",
                use_container_width=True,
            )

        with right:
            st.subheader("溯源与结构化信息")
            render_sources("研报溯源", latest.get("ref_index_list", []), "这次请求没有返回研报溯源。")
            render_sources("资讯溯源", latest.get("news_index_list", []), "这次请求没有返回资讯溯源。")
            render_graph_events(latest.get("graph_events", []))
            if show_raw:
                st.subheader("原始事件")
                st.json(latest.get("raw_events", []))
    else:
        st.info("先填密钥、选问题，然后点击“开始请求”。")

    st.divider()
    st.subheader("最近请求")
    if st.session_state.history:
        for idx, item in enumerate(st.session_state.history, start=1):
            title = item.get("payload", {}).get("question", "(无问题)")
            with st.expander(f"{idx}. {title}", expanded=False):
                st.write(
                    {
                        "channelId": item.get("channel_id", ""),
                        "qId": item.get("q_id", ""),
                        "traceId": item.get("trace_id", ""),
                    }
                )
                st.code(json.dumps(item.get("payload", {}), ensure_ascii=False, indent=2), language="json")
    else:
        st.caption("这里会保留最近 5 次请求，方便对比和调试。")


if __name__ == "__main__":
    main()
