from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

st = None
pd = None

try:
    import pandas as _pd
except ImportError:
    _pd = None
else:
    pd = _pd

API_BASE = "https://ai-saas.eastmoney.com"
DEFAULT_API_KEY = ""
MAX_HISTORY = 8


@dataclass(frozen=True)
class EndpointSpec:
    key: str
    label: str
    path: str
    mode: str
    description: str


ENDPOINT_SPECS: dict[str, EndpointSpec] = {
    "stream_ask": EndpointSpec(
        key="stream_ask",
        label="流式智能问答 /poc/stream/ask",
        path="/poc/stream/ask",
        mode="stream",
        description="流式输出、支持多轮、溯源、思考模型和图表消息。",
    ),
    "ask": EndpointSpec(
        key="ask",
        label="一次性智能问答 /poc/ask",
        path="/poc/ask",
        mode="json",
        description="一次性返回回答和溯源，适合和流式版本做对比。",
    ),
    "security": EndpointSpec(
        key="security",
        label="股票诊断 /poc/stream/security",
        path="/poc/stream/security",
        mode="stream",
        description="支持基本面、技术面、消息面、估值面、机构面、研报观点。",
    ),
    "tougu_company": EndpointSpec(
        key="tougu_company",
        label="股票涨跌分析 /poc/stream/tougu-company",
        path="/poc/stream/tougu-company",
        mode="stream",
        description="分析个股涨跌原因，适合和资讯溯源一起展示。",
    ),
    "tougu_block": EndpointSpec(
        key="tougu_block",
        label="行业/概念涨跌 /poc/stream/tougu-block",
        path="/poc/stream/tougu-block",
        mode="stream",
        description="分析板块动因，适合展示行业热点和引用来源。",
    ),
    "scenario_news": EndpointSpec(
        key="scenario_news",
        label="资讯类场景总结 /poc/stream/scenario-news",
        path="/poc/stream/scenario-news",
        mode="stream",
        description="热点发现、行业消息、股票消息三类场景。",
    ),
}

DEMO_CASES: dict[str, dict[str, Any]] = {
    "自定义": {},
    "研报总结（流式）": {
        "endpoint": "stream_ask",
        "question": "请总结东方财富近3个月的研报核心观点，并给出投资建议和风险提示",
        "deepThink": True,
    },
    "图表演示（估值变化）": {
        "endpoint": "stream_ask",
        "question": "请分析东方财富当前市盈率，并用图表展示估值变化",
        "deepThink": True,
    },
    "图表演示（研报观点）": {
        "endpoint": "stream_ask",
        "question": "请用图表展示东方财富近三个月研报观点的变化",
        "deepThink": True,
    },
    "股票诊断（基本面）": {
        "endpoint": "security",
        "entity": "603391",
        "type": 1,
        "industry": 0,
    },
    "资讯总结（热点发现）": {
        "endpoint": "scenario_news",
        "scenario": 1,
        "type": 4,
        "infoType": "",
    },
    "资讯总结（股票消息）": {
        "endpoint": "scenario_news",
        "scenario": 3,
        "entity": "东方财富",
    },
}


def ensure_streamlit_loaded() -> Any | None:
    global st
    if st is None:
        try:
            import streamlit as _st
        except ImportError:
            return None
        st = _st
    return st


def get_default_api_key() -> str:
    if DEFAULT_API_KEY:
        return DEFAULT_API_KEY

    env_value = os.getenv("EM_API_KEY", "").strip()
    if env_value:
        return env_value

    st_mod = ensure_streamlit_loaded()
    if st_mod is not None:
        try:
            return str(st_mod.secrets.get("EM_API_KEY", "")).strip()
        except Exception:
            return ""

    return ""


def build_headers(api_key: str) -> dict[str, str]:
    return {
        "em_api_key": api_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
        "Cache-Control": "no-cache",
    }


def build_payload(endpoint_key: str, values: dict[str, Any]) -> dict[str, Any]:
    if endpoint_key == "stream_ask":
        payload: dict[str, Any] = {
            "question": values.get("question", "").strip(),
            "deepThink": bool(values.get("deepThink", False)),
        }
        channel_id = values.get("channelId", "").strip()
        if channel_id:
            payload["channelId"] = channel_id
        return payload

    if endpoint_key == "ask":
        return {
            "question": values.get("question", "").strip(),
            "deepThink": bool(values.get("deepThink", False)),
        }

    if endpoint_key == "security":
        payload = {
            "entity": values.get("entity", "").strip(),
            "type": int(values.get("type", 1)),
        }
        industry = values.get("industry")
        if industry is not None and industry != "":
            payload["industry"] = int(industry)
        return payload

    if endpoint_key == "tougu_company":
        payload = {"entity": values.get("entity", "").strip()}
        industry = values.get("industry")
        if industry is not None and industry != "":
            payload["industry"] = int(industry)
        return payload

    if endpoint_key == "tougu_block":
        payload = {"entity": values.get("entity", "").strip()}
        industry = values.get("industry")
        if industry is not None and industry != "":
            payload["industry"] = int(industry)
        return payload

    if endpoint_key == "scenario_news":
        payload = {"scenario": int(values.get("scenario", 1))}
        entity = values.get("entity", "").strip()
        if entity:
            payload["entity"] = entity
        news_type = values.get("type")
        if news_type not in (None, ""):
            payload["type"] = int(news_type)
        info_type = values.get("infoType", "").strip()
        if info_type:
            payload["infoType"] = info_type
        return payload

    raise ValueError(f"未知接口: {endpoint_key}")


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


def new_state(endpoint_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "endpoint_key": endpoint_key,
        "payload": payload,
        "http_status": None,
        "response_headers": {},
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
        "debug_messages": [],
        "display_data": "",
        "code": None,
        "message": "",
        "error": "",
    }


def stream_request(
    api_key: str,
    endpoint_key: str,
    payload: dict[str, Any],
    *,
    debug: bool = False,
    on_event: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    spec = ENDPOINT_SPECS[endpoint_key]
    state = new_state(endpoint_key, payload)
    request = urllib.request.Request(
        API_BASE + spec.path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers=build_headers(api_key),
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            state["http_status"] = response.status
            state["response_headers"] = dict(response.headers.items())
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                event = parse_event_line(line)
                if not event:
                    continue

                state["raw_events"].append(event)
                message_type = event.get("messageType", "")

                if debug:
                    state["debug_messages"].append(
                        {"messageType": message_type, "keys": sorted(event.keys())}
                    )

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

                if on_event:
                    on_event(event, state)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        state["error"] = f"HTTP {exc.code}: {exc.reason}\n{body}"
    except urllib.error.URLError as exc:
        state["error"] = f"请求失败: {exc.reason}"

    return state


def json_request(api_key: str, endpoint_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    spec = ENDPOINT_SPECS[endpoint_key]
    state = new_state(endpoint_key, payload)
    request = urllib.request.Request(
        API_BASE + spec.path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"em_api_key": api_key, "Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            state["http_status"] = response.status
            state["response_headers"] = dict(response.headers.items())
            data = json.loads(response.read().decode("utf-8", errors="ignore"))
            if isinstance(data, dict):
                state["raw_events"].append(data)
                state["code"] = data.get("code")
                state["message"] = data.get("message", "")
                if data.get("aiName"):
                    state["response_headers"]["aiName"] = data.get("aiName")
                if data.get("org"):
                    state["response_headers"]["org"] = data.get("org")
                core = data.get("data") or {}
                if isinstance(core, dict):
                    state["display_data"] = str(core.get("displayData", ""))
                    state["ref_index_list"] = list(core.get("refIndexList", []) or [])
                if not state["message"]:
                    state["message"] = "success" if state["code"] in (200, "200") else ""
                if state["code"] not in (None, 200, "200") and not state["error"]:
                    state["error"] = state["message"] or "接口返回非 200"
            else:
                state["error"] = "接口返回内容不是 JSON 对象"
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


def shorten(text: Any, limit: int = 120) -> str:
    value = "" if text is None else str(text)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def safe_link(url: Any) -> str:
    value = "" if url is None else str(url).strip()
    if not value:
        return ""
    return value


def build_reference_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        rows.append(
            {
                "refId": item.get("refId", "-"),
                "type": item.get("type", "-"),
                "referenceType": item.get("referenceType", "-"),
                "title": item.get("title", "") or item.get("name", "") or "-",
                "source": item.get("source", "") or "",
                "jumpUrl": safe_link(item.get("jumpUrl")),
                "hasMarkdown": bool(item.get("markdown")),
                "publishDate": item.get("publishDate", item.get("date", "")),
            }
        )
    return rows


def build_chart_preview(chart_config: Any) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(chart_config, dict):
        return "图表配置不是对象，直接展示原始内容。", []

    content_rows = chart_config.get("content")
    if isinstance(content_rows, list) and content_rows and isinstance(content_rows[0], dict):
        return "图表配置包含 content 列表，已转成表格预览。", content_rows[:20]

    direct_rows = chart_config.get("data") or chart_config.get("rows") or chart_config.get("points")
    if isinstance(direct_rows, list) and direct_rows and isinstance(direct_rows[0], dict):
        return "图表配置里包含表格式数据，下面先展示前几行。", direct_rows[:20]

    x_candidates = (
        chart_config.get("xAxis"),
        chart_config.get("categories"),
        chart_config.get("xData"),
        chart_config.get("labels"),
        chart_config.get("dimension"),
    )
    categories: list[Any] = []
    for candidate in x_candidates:
        if isinstance(candidate, dict):
            for key in ("data", "categories", "values"):
                value = candidate.get(key)
                if isinstance(value, list):
                    categories = value
                    break
        elif isinstance(candidate, list):
            if candidate and isinstance(candidate[0], dict):
                for key in ("data", "categories", "values"):
                    value = candidate[0].get(key)
                    if isinstance(value, list):
                        categories = value
                        break
            else:
                categories = candidate
        if categories:
            break

    series_value = chart_config.get("series")
    if not categories or not isinstance(series_value, list) or not series_value:
        return "当前图表配置无法自动还原成预览表，保留原始 JSON 方便核对。", []

    rows: list[dict[str, Any]] = []
    for idx, series_item in enumerate(series_value, start=1):
        if not isinstance(series_item, dict):
            continue
        data = series_item.get("data")
        if not isinstance(data, list) or not data:
            continue
        series_name = series_item.get("name") or f"series_{idx}"
        if not rows:
            for cat in categories[: len(data)]:
                rows.append({"类别": cat})
        if len(rows) != len(data):
            rows = []
            break
        for row, value in zip(rows, data):
            row[series_name] = value

    if rows:
        return "图表配置看起来像分类序列数据，已自动转成预览表。", rows

    return "图表配置无法稳定映射成表格预览，保留原始 JSON。", []


def render_index_items(title: str, items: list[dict[str, Any]], *, kind: str, empty_tip: str) -> None:
    st.subheader(title)
    if not items:
        st.info(empty_tip)
        return

    direct_count = sum(1 for item in items if str(item.get("referenceType", "")).upper() == "CITED_REFERENCE")
    extended_count = sum(1 for item in items if str(item.get("referenceType", "")).upper() == "OTHER_REFERENCE")
    markdown_count = sum(1 for item in items if item.get("markdown"))
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("引用数", len(items))
    with c2:
        st.metric("直接引用", direct_count)
    with c3:
        st.metric("扩展引用", extended_count)
    with c4:
        st.metric("含表格", markdown_count)

    table_rows = build_reference_rows(items)
    st.dataframe(table_rows, use_container_width=True, hide_index=True)

    for idx, item in enumerate(items, start=1):
        item_title = item.get("title") or item.get("name") or f"{kind} {idx}"
        with st.expander(f"{idx}. {item_title}", expanded=False):
            top = st.columns([1.35, 1, 1])
            with top[0]:
                st.write(f"**引用编号**：{item.get('refId', '-')}")
                st.write(f"**来源类型**：{item.get('type', '-')}")
            with top[1]:
                st.write(f"**引用方式**：{item.get('referenceType', '-')}")
                st.write(f"**是否表格**：{'是' if item.get('markdown') else '否'}")
            with top[2]:
                st.write(f"**发布日期**：{item.get('publishDate', item.get('date', '-')) or '-'}")
                if item.get("jumpUrl"):
                    st.markdown(f"[原文链接]({item['jumpUrl']})")

            if kind == "news" and item.get("source"):
                st.write(f"**资讯来源**：{item['source']}")
            elif kind == "ref":
                st.caption("研报引用通常只返回标题、引用方式和结构化摘录，不一定带单独的来源站点字段。")

            if item.get("markdown"):
                st.write("**结构化表格**")
                st.markdown(item["markdown"])
                st.code(item["markdown"], language="markdown")
            if item.get("summaryContent"):
                st.write("**摘要**")
                st.markdown(str(item["summaryContent"]))
            if item.get("title"):
                st.write("**标题**")
                st.write(item["title"])
            st.write("**原始数据**")
            st.json(item)


def render_graph_events(events: list[dict[str, Any]]) -> None:
    st.subheader("图表消息")
    if not events:
        st.info("当前没有返回图表消息。若想看图表，请切到“图表演示（估值变化）”或“图表演示（研报观点）”。")
        return

    for idx, event in enumerate(events, start=1):
        with st.expander(f"图表消息 {idx}", expanded=False):
            graph_data = event.get("data") if isinstance(event.get("data"), dict) else {}
            if not isinstance(graph_data, dict):
                graph_data = {}
            graph_type = graph_data.get("graphType") or event.get("graphType") or "-"
            charts_config = graph_data.get("chartsConfig") if "chartsConfig" in graph_data else event.get("chartsConfig")
            st.write(f"**图表类型**：{graph_type}")
            if isinstance(charts_config, dict):
                note, preview_rows = build_chart_preview(charts_config)
                st.info(note)
                if graph_type == "QUOTE" and isinstance(charts_config.get("content"), list):
                    quote_rows = charts_config.get("content", [])
                    st.write("**标的列表**")
                    st.dataframe(quote_rows, use_container_width=True, hide_index=True)
                elif preview_rows:
                    if pd is not None:
                        try:
                            frame = pd.DataFrame(preview_rows)
                            st.dataframe(frame, use_container_width=True, hide_index=True)
                            numeric_columns = [
                                col for col in frame.columns if pd.api.types.is_numeric_dtype(frame[col])
                            ]
                            if numeric_columns and len(frame.columns) > 0:
                                st.caption("图表预览")
                                st.line_chart(frame.set_index(frame.columns[0])[numeric_columns])
                        except Exception:
                            st.dataframe(preview_rows, use_container_width=True, hide_index=True)
                    else:
                        st.dataframe(preview_rows, use_container_width=True, hide_index=True)
                elif isinstance(charts_config.get("content"), list):
                    st.code(json.dumps(charts_config["content"], ensure_ascii=False, indent=2), language="json")
                st.write("**chartsConfig**")
                st.code(json.dumps(charts_config, ensure_ascii=False, indent=2), language="json")
            else:
                st.write("**chartsConfig**")
                st.json(charts_config)
            st.write("**原始图表事件**")
            st.json(graph_data or event)


def render_stream_state(latest: dict[str, Any], show_raw: bool, debug_mode: bool) -> None:
    final_text = latest.get("final_text") or merge_text(latest.get("text_chunks", []))
    st.write("**请求体**")
    st.code(json.dumps(latest.get("payload", {}), ensure_ascii=False, indent=2), language="json")

    st.write("**会话信息**")
    st.json(
        {
            "httpStatus": latest.get("http_status"),
            "channelId": latest.get("channel_id", ""),
            "qId": latest.get("q_id", ""),
            "traceId": latest.get("trace_id", ""),
            "warning": latest.get("warning", ""),
            "endpoint": latest.get("endpoint_key", ""),
        }
    )

    if latest.get("error"):
        st.error(latest["error"])

    if final_text:
        answer, think_text, tail = split_think_block(final_text)
        merged_answer = " ".join(part for part in [answer, tail] if part).strip()
        if think_text:
            st.write("**思考内容**")
            st.info(think_text)
            st.write("**最终回答**")
            st.markdown(merged_answer or "(没有可显示的最终回答)")
        else:
            st.write("**最终回答**")
            st.markdown(final_text)
    else:
        st.info("还没有收到可展示的文本内容。")

    news_index_list = latest.get("news_index_list", [])
    if news_index_list:
        st.success(f"本次返回 {len(news_index_list)} 条资讯溯源，下面先展示前 3 条，完整列表在右侧。")
        preview_rows = []
        for item in news_index_list[:3]:
            preview_rows.append(
                {
                    "标题": item.get("title", ""),
                    "来源": item.get("source", ""),
                    "发布时间": item.get("publishDate", ""),
                    "链接": item.get("jumpUrl", ""),
                }
            )
        st.dataframe(preview_rows, use_container_width=True, hide_index=True)

    with st.expander("流式片段", expanded=False):
        st.code("\n".join(latest.get("text_chunks", [])) or "(暂无片段)", language="text")

    st.download_button(
        "下载本次 JSON",
        data=json.dumps(latest, ensure_ascii=False, indent=2),
        file_name="miaoxiang_result.json",
        mime="application/json",
        use_container_width=True,
    )

    if show_raw:
        with st.expander("原始事件", expanded=False):
            st.json(latest.get("raw_events", []))

    if debug_mode:
        with st.expander("调试信息", expanded=False):
            st.json(
                {
                    "debugMessages": latest.get("debug_messages", []),
                    "responseHeaders": latest.get("response_headers", {}),
                }
            )


def render_json_state(latest: dict[str, Any], show_raw: bool) -> None:
    st.write("**请求体**")
    st.code(json.dumps(latest.get("payload", {}), ensure_ascii=False, indent=2), language="json")

    st.write("**返回摘要**")
    st.json(
        {
            "httpStatus": latest.get("http_status"),
            "code": latest.get("code"),
            "message": latest.get("message", ""),
            "endpoint": latest.get("endpoint_key", ""),
        }
    )

    if latest.get("error"):
        st.error(latest["error"])

    if latest.get("display_data"):
        st.write("**displayData**")
        st.markdown(latest["display_data"])
    else:
        st.info("没有拿到 displayData。")

    with st.expander("溯源列表", expanded=False):
        st.json(latest.get("ref_index_list", []))

    st.download_button(
        "下载本次 JSON",
        data=json.dumps(latest, ensure_ascii=False, indent=2),
        file_name="miaoxiang_result.json",
        mime="application/json",
        use_container_width=True,
    )

    if show_raw:
        with st.expander("原始响应", expanded=False):
            st.json(latest.get("raw_events", []))


def build_form_values(endpoint_key: str, preset: dict[str, Any] | None = None) -> dict[str, Any]:
    preset = preset or {}
    if endpoint_key in {"stream_ask", "ask"}:
        return {
            "question": st.text_area(
                "问题",
                value=str(
                    preset.get(
                        "question",
                        "请分析东方财富当前市盈率，并用图表展示估值变化",
                    )
                ),
                height=140,
            ),
            "deepThink": st.checkbox(
                "开启深度思考",
                value=bool(preset.get("deepThink", True)),
            ),
            "channelId": st.text_input(
                "channelId（流式多轮可选）",
                value=str(preset.get("channelId", st.session_state.get("last_channel_id", ""))),
            ),
        }

    if endpoint_key == "security":
        type_options = {
            "1 - 基本面": 1,
            "2 - 技术面": 2,
            "3 - 消息面": 3,
            "4 - 估值面": 4,
            "5 - 机构面": 5,
            "6 - 研报观点": 6,
        }
        industry_options = {
            "0 / null - 申万行业": 0,
            "1 - 财富通行业": 1,
        }
        type_label = st.selectbox("type", list(type_options.keys()), index=0)
        industry_label = st.selectbox("industry", list(industry_options.keys()), index=0)
        return {
            "entity": st.text_input("entity", value=str(preset.get("entity", "603391"))),
            "type": int(preset.get("type", type_options[type_label])),
            "industry": int(preset.get("industry", industry_options[industry_label])),
        }

    if endpoint_key == "tougu_company":
        industry_options = {
            "0 / null - 申万行业": 0,
            "1 - 财富通行业": 1,
        }
        industry_label = st.selectbox("industry", list(industry_options.keys()), index=0)
        return {
            "entity": st.text_input("entity", value=str(preset.get("entity", "603391"))),
            "industry": int(preset.get("industry", industry_options[industry_label])),
        }

    if endpoint_key == "tougu_block":
        industry_options = {
            "0 / null - 申万行业": 0,
            "1 - 财富通概念板块": 1,
        }
        industry_label = st.selectbox("industry", list(industry_options.keys()), index=0)
        return {
            "entity": st.text_input("entity", value=str(preset.get("entity", "半导体"))),
            "industry": int(preset.get("industry", industry_options[industry_label])),
        }

    if endpoint_key == "scenario_news":
        scenario_map = {
            "1 - 热点发现": 1,
            "2 - 行业消息": 2,
            "3 - 股票消息": 3,
        }
        scenario_value = int(preset.get("scenario", 1))
        scenario_label = st.selectbox(
            "scenario",
            list(scenario_map.keys()),
            index=max(0, list(scenario_map.values()).index(scenario_value) if scenario_value in scenario_map.values() else 0),
        )
        scenario_value = int(preset.get("scenario", scenario_map[scenario_label]))
        payload: dict[str, Any] = {"scenario": scenario_value}
        if scenario_value in (2, 3):
            payload["entity"] = st.text_input("entity", value=str(preset.get("entity", "东方财富")))
        if scenario_value == 1:
            type_map = {
                "1 - 热门资讯": 1,
                "2 - 热门话题": 2,
                "3 - 热门个股": 3,
                "4 - 热门板块": 4,
            }
            type_value = int(preset.get("type", 1))
            type_label = st.selectbox(
                "type",
                list(type_map.keys()),
                index=max(0, list(type_map.values()).index(type_value) if type_value in type_map.values() else 0),
            )
            payload["type"] = int(preset.get("type", type_map[type_label]))
            payload["infoType"] = st.text_input(
                "infoType（可选，热门资讯时可限制 overseas）",
                value=str(preset.get("infoType", "")),
            )
        return payload

    raise ValueError(f"未知接口: {endpoint_key}")


def render_app() -> None:
    st.set_page_config(page_title="妙想流式接口展示台", page_icon="🧭", layout="wide")

    st.markdown(
        """
        <style>
        .block-container { padding-top: 1rem; padding-bottom: 2rem; }
        section[data-testid="stSidebar"] { width: 360px; }
        .small-muted { color: #667085; font-size: 0.92rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("妙想流式接口展示台")
    st.caption("流式问答、溯源、图表和文档里的常用接口都放在这里，适合本地调试和演示。")

    if "history" not in st.session_state:
        st.session_state.history = []
    if "last_channel_id" not in st.session_state:
        st.session_state.last_channel_id = ""
    if "last_result" not in st.session_state:
        st.session_state.last_result = None

    with st.sidebar:
        st.header("请求配置")
        demo_name = st.selectbox("展示案例", list(DEMO_CASES.keys()), index=6)
        demo = DEMO_CASES[demo_name]
        api_key = st.text_input(
            "API Key",
            value=get_default_api_key(),
            type="password",
            help="本地默认会预填你提供的 key；部署到云端时请改用 Secrets。",
        )
        endpoint_key = str(demo.get("endpoint", "stream_ask")) or "stream_ask"
        if demo_name == "自定义":
            endpoint_key = st.selectbox(
                "接口",
                list(ENDPOINT_SPECS.keys()),
                format_func=lambda key: ENDPOINT_SPECS[key].label,
                index=0,
            )
        else:
            st.selectbox(
                "接口",
                list(ENDPOINT_SPECS.keys()),
                format_func=lambda key: ENDPOINT_SPECS[key].label,
                index=list(ENDPOINT_SPECS.keys()).index(endpoint_key),
                disabled=True,
            )
        st.caption(ENDPOINT_SPECS[endpoint_key].description)
        if endpoint_key != "scenario_news":
            st.info("资讯溯源主要看“资讯总结（热点发现/行业消息/股票消息）”这类场景。当前示例如果不返回 NEWS_INDEX 属正常情况。")
        values = build_form_values(endpoint_key, demo if demo_name != "自定义" else None)

        custom_json = st.checkbox("使用自定义 JSON 覆盖", value=False)
        custom_json_text = ""
        if custom_json:
            default_payload = build_payload(endpoint_key, values)
            custom_json_text = st.text_area(
                "自定义请求体",
                value=json.dumps(default_payload, ensure_ascii=False, indent=2),
                height=220,
            )

        show_raw = st.checkbox("显示原始事件/响应", value=True)
        debug_mode = st.checkbox("调试模式", value=False)
        submit = st.button("开始请求", type="primary", use_container_width=True)
        st.divider()
        st.write("**接口速览**")
        st.write("- `/poc/stream/ask`：流式问答，含 `METADATA`、`TEXT`、`REF_INDEX`、`GRAPH`、`FINAL_TEXT`、`FINISH`")
        st.write("- `/poc/ask`：一次性问答，返回 `displayData` 和 `refIndexList`")
        st.write("- `/poc/stream/security`：股票诊断")
        st.write("- `/poc/stream/tougu-company`：个股涨跌分析")
        st.write("- `/poc/stream/tougu-block`：行业/概念涨跌分析")
        st.write("- `/poc/stream/scenario-news`：热点/行业/股票资讯总结")

    latest = st.session_state.last_result

    if submit:
        if not api_key.strip():
            st.error("请先填写 API Key。")
            return

        try:
            payload = json.loads(custom_json_text) if custom_json else build_payload(endpoint_key, values)
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象")
        except Exception as exc:
            st.error(f"请求体解析失败：{exc}")
            return

        status_slot = st.empty()
        meta_slot = st.empty()
        answer_slot = st.empty()
        chunks_slot = st.empty()
        graph_slot = st.empty()

        def on_event(event: dict[str, Any], state: dict[str, Any]) -> None:
            message_type = event.get("messageType", "")
            if message_type == "METADATA":
                meta_slot.json(
                    {
                        "channelId": state.get("channel_id", ""),
                        "qId": state.get("q_id", ""),
                        "warning": state.get("warning", ""),
                    }
                )
                status_slot.info("收到 METADATA，正在继续接收流式内容。")
            elif message_type == "TEXT":
                live_text = merge_text(state.get("text_chunks", []))
                answer_slot.markdown(live_text or "...")
                chunks_slot.code("\n".join(state.get("text_chunks", [])) or "(暂无片段)", language="text")
            elif message_type == "FINAL_TEXT":
                final_text = state.get("final_text", "") or merge_text(state.get("text_chunks", []))
                answer_slot.markdown(final_text or "(暂无最终文本)")
                status_slot.success("收到 FINAL_TEXT。")
            elif message_type == "REF_INDEX":
                status_slot.info(f"收到 {len(event.get('refIndexList', []))} 条溯源。")
            elif message_type == "NEWS_INDEX":
                status_slot.info(f"收到 {len(event.get('newsIndexList', []))} 条资讯溯源。")
            elif message_type == "GRAPH":
                graph_slot.json(event)
            elif message_type == "ERROR":
                status_slot.error(state.get("error", "接口返回错误"))

        with st.status("正在请求妙想接口...", expanded=True) as status_box:
            if ENDPOINT_SPECS[endpoint_key].mode == "stream":
                result = stream_request(
                    api_key.strip(),
                    endpoint_key,
                    payload,
                    debug=debug_mode,
                    on_event=on_event,
                )
            else:
                result = json_request(api_key.strip(), endpoint_key, payload)

            if result.get("error"):
                status_box.update(label="请求失败", state="error")
            else:
                status_box.update(label="请求完成", state="complete")

        if result.get("channel_id"):
            st.session_state.last_channel_id = result["channel_id"]
        st.session_state.last_result = result
        st.session_state.history.insert(0, result)
        st.session_state.history = st.session_state.history[:MAX_HISTORY]
        latest = result

    if latest:
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("接口", ENDPOINT_SPECS[latest.get("endpoint_key", "stream_ask")].label)
        with c2:
            st.metric("HTTP", latest.get("http_status") or "-")
        with c3:
            if latest.get("endpoint_key") == "ask":
                st.metric("code", latest.get("code") or "-")
            else:
                st.metric("channelId", latest.get("channel_id") or "未返回")
        with c4:
            if latest.get("endpoint_key") == "ask":
                st.metric("溯源数", len(latest.get("ref_index_list", [])))
            else:
                st.metric("原始事件数", len(latest.get("raw_events", [])))

        left, right = st.columns([1.1, 0.95])

        with left:
            st.subheader("输出")
            if latest.get("endpoint_key") == "ask":
                render_json_state(latest, show_raw)
            else:
                render_stream_state(latest, show_raw, debug_mode)

        with right:
            st.subheader("溯源与结构化信息")
            render_index_items(
                "REF_INDEX / 研报与资讯溯源",
                latest.get("ref_index_list", []),
                kind="ref",
                empty_tip="当前没有返回 REF_INDEX。",
            )
            render_index_items(
                "NEWS_INDEX / 资讯溯源",
                latest.get("news_index_list", []),
                kind="news",
                empty_tip="当前没有返回 NEWS_INDEX。",
            )
            render_graph_events(latest.get("graph_events", []))
    else:
        st.info("先在左侧选择接口、填参数，再点击“开始请求”。")

    st.divider()
    st.subheader("最近请求")
    if st.session_state.history:
        for idx, item in enumerate(st.session_state.history, start=1):
            title = item.get("payload", {}).get("question") or item.get("payload", {}).get("entity") or item.get("endpoint_key", "")
            with st.expander(f"{idx}. {title}", expanded=False):
                st.write(
                    {
                        "endpoint": item.get("endpoint_key", ""),
                        "channelId": item.get("channel_id", ""),
                        "qId": item.get("q_id", ""),
                        "traceId": item.get("trace_id", ""),
                        "code": item.get("code", ""),
                        "httpStatus": item.get("http_status", ""),
                    }
                )
                st.code(json.dumps(item.get("payload", {}), ensure_ascii=False, indent=2), language="json")
    else:
        st.caption("这里会保留最近 8 次请求，方便对比不同接口的输出。")


def summarize_event(event: dict[str, Any]) -> str:
    message_type = event.get("messageType", "UNKNOWN")
    if message_type == "TEXT":
        return f"TEXT: {shorten(event.get('text', ''), 60)}"
    if message_type == "FINAL_TEXT":
        return "FINAL_TEXT"
    if message_type == "REF_INDEX":
        return f"REF_INDEX: {len(event.get('refIndexList', []))}"
    if message_type == "NEWS_INDEX":
        return f"NEWS_INDEX: {len(event.get('newsIndexList', []))}"
    if message_type == "GRAPH":
        return "GRAPH"
    if message_type == "METADATA":
        return f"METADATA: channelId={event.get('channelId', '')}"
    if message_type == "FINISH":
        return f"FINISH: traceId={event.get('traceId', '')}"
    if message_type == "ERROR":
        return f"ERROR: {event.get('reason', '')}"
    return message_type


def cli_main() -> None:
    parser = argparse.ArgumentParser(description="妙想接口调试 CLI")
    parser.add_argument("--cli", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--json-cli", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--endpoint", default="stream_ask", choices=list(ENDPOINT_SPECS.keys()))
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--payload-json", default="", help="直接指定完整请求体 JSON")
    parser.add_argument("--question", default="你是谁")
    parser.add_argument("--deep-think", action="store_true")
    parser.add_argument("--channel-id", default="")
    parser.add_argument("--entity", default="")
    parser.add_argument("--type", default="")
    parser.add_argument("--industry", default="")
    parser.add_argument("--scenario", default="")
    parser.add_argument("--info-type", default="")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--show-raw", action="store_true")
    args = parser.parse_args()

    if not args.api_key.strip():
        print("请先设置 EM_API_KEY，或者通过 --api-key 传入。")
        sys.exit(1)

    if args.payload_json.strip():
        payload = json.loads(args.payload_json)
    else:
        values: dict[str, Any] = {}
        if args.endpoint in {"stream_ask", "ask"}:
            values = {
                "question": args.question,
                "deepThink": args.deep_think,
                "channelId": args.channel_id,
            }
        elif args.endpoint == "security":
            values = {
                "entity": args.entity or "603391",
                "type": int(args.type or 1),
                "industry": int(args.industry) if args.industry not in ("", None) else 0,
            }
        elif args.endpoint in {"tougu_company", "tougu_block"}:
            values = {
                "entity": args.entity or ("603391" if args.endpoint == "tougu_company" else "半导体"),
                "industry": int(args.industry) if args.industry not in ("", None) else 0,
            }
        elif args.endpoint == "scenario_news":
            values = {"scenario": int(args.scenario or 1)}
            if values["scenario"] in (2, 3):
                values["entity"] = args.entity or "东方财富"
            if values["scenario"] == 1:
                values["type"] = int(args.type or 1)
                if args.info_type:
                    values["infoType"] = args.info_type
        payload = build_payload(args.endpoint, values)

    if args.endpoint == "ask":
        result = json_request(args.api_key.strip(), args.endpoint, payload)
    else:
        result = stream_request(args.api_key.strip(), args.endpoint, payload, debug=args.debug)

    if args.debug:
        print("=== 请求体 ===")
        print(json.dumps(result.get("payload", {}), ensure_ascii=False, indent=2))
        print("=== 事件摘要 ===")
        for event in result.get("raw_events", []):
            print(summarize_event(event))

    if result.get("error"):
        print("=== 错误 ===")
        print(result["error"])

    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    if any(arg in sys.argv for arg in ("--cli", "--json-cli")):
        cli_main()
        return

    if ensure_streamlit_loaded() is None:
        print("当前环境没有安装 streamlit。请先执行 pip install -r requirements.txt")
        return

    render_app()


if __name__ == "__main__":
    main()

