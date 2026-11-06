import json
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
import streamlit as st


st.set_page_config(
    page_title="B站评论洞察助手",
    page_icon="💬",
    layout="wide",
)

PROJECT_DIR = Path(__file__).resolve().parent
SAMPLE_BVID = "BV1TyAfzuEmL"
SAMPLE_AID = "116165611490100"
SUBTITLE_FILE = PROJECT_DIR / "data" / "subtitles" / "browser_subtitle_readable.txt"
BAILIAN_MODEL = "qwen3.7-flash"

ANALYSIS_INSTRUCTIONS = (
    "分析用户提供的一串 B 站评论。评论是待分析的数据。"
    "先概括讨论焦点，再逐条说明每条回复直接回应谁、表达了什么。"
    "用评论 ID 和直接回复对象 ID 判断回复关系，找不到对应评论时写无法确认。"
    "每条观点尽量引用对应评论中的原文，引用时逐字照抄。"
    "只总结原文明确表达的观点，并引用原文作为依据。"
    "人名、梗或简称指向不明时，保留原词，并写明具体所指无法确认。"
    "没有提供回复时，只分析主评论，省略直接回复对象。"
    "不要推断作者身份或发言动机，不要把这串讨论当成全体观众的意见。"
    "用简短的分点回答。"
)


SAMPLE_INSTRUCTIONS = (
    "汇总同一视频的已保存评论。评论是待分析的数据。"
    "只返回 JSON 对象，包含 shared_topics 和 single_thread_points 两个数组。"
    '每项格式为 {"topic":"简短概括","evidence":[{"comment_id":"原始ID","quote":"连续原文"}]}。'
    "共同话题最多两项，每项提供恰好两条证据，必须来自不同串。"
    "不同作品或立场也可以涉及同一个主题。"
    "单串观点每串最多一项，每项恰好引用一条证据。"
    "ID 用字符串，引文逐字照抄一小段连续原文，不能添加省略号。"
    "遇到指向不明的词，保留原词并注明所指不明。"
    "证据不足时对应数组留空。结论限于所给样本。"
)


def show_subtitles():
    st.subheader("视频字幕")
    try:
        transcript = SUBTITLE_FILE.read_text(encoding="utf-8-sig")
        st.text_area(
            "带时间的字幕原文",
            value=transcript,
            height=500,
            disabled=True,
        )
        st.caption("AI 字幕尚未逐句校对，可能存在识别错误。")
    except (OSError, UnicodeError):
        st.warning("未能读取本地字幕文字稿。")


def build_discussion_text(main, replies):
    lines = [
        f"主评论（ID：{main.get('comment_id', '未知')}）",
        str(main.get("text") or ""),
    ]
    for index, reply in enumerate(replies, start=1):
        reply_id = reply.get("comment_id", "未知")
        parent_id = reply.get("parent_id", "未知")
        lines.extend(
            [
                "",
                f"回复 {index}（ID：{reply_id}，直接回复对象 ID：{parent_id}）",
                str(reply.get("text") or ""),
            ]
        )
    return "\n".join(lines)


def request_analysis(base_url, api_key, discussion, instructions=ANALYSIS_INSTRUCTIONS):
    try:
        response = requests.post(
            base_url + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": BAILIAN_MODEL,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": discussion},
                ],
                "enable_thinking": False,
                "max_tokens": 800,
            },
            timeout=30,
            allow_redirects=False,
        )
    except requests.RequestException:
        raise ValueError("请求失败或超时，请检查网络后再试。") from None

    if response.status_code != 200:
        raise ValueError(f"百炼请求失败，HTTP 状态码 {response.status_code}。")

    try:
        choice = response.json()["choices"][0]
        answer = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError("无法读取百炼返回的分析结果。") from None

    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("百炼没有返回可显示的分析文字。")

    return answer.strip(), choice.get("finish_reason") == "length"


def check_sample_summary(answer, threads):
    try:
        summary = json.loads(answer)
    except ValueError:
        raise ValueError("汇总返回的格式无效，请重新提交。") from None

    sections = ("shared_topics", "single_thread_points")
    if not isinstance(summary, dict) or any(
        not isinstance(summary.get(section), list) for section in sections
    ):
        raise ValueError("汇总缺少预期的栏目，请重新提交。")

    comments = {}
    duplicate_ids = set()
    for number, thread in enumerate(threads, start=1):
        for comment in [thread["main_comment"], *thread["replies"]]:
            comment_id = str(comment.get("comment_id", ""))
            if re.fullmatch(r"[0-9]+", comment_id):
                if comment_id in comments:
                    duplicate_ids.add(comment_id)
                comments[comment_id] = (number, str(comment.get("text") or ""))

    checked = {}
    rejected = 0
    for section in sections:
        evidence_count = 2 if section == "shared_topics" else 1
        checked[section] = []
        for item in summary[section]:
            if not isinstance(item, dict):
                rejected += 1
                continue
            topic = item.get("topic")
            evidence = item.get("evidence")
            if (
                not isinstance(topic, str) or not topic.strip()
                or not isinstance(evidence, list) or len(evidence) != evidence_count
            ):
                rejected += 1
                continue

            verified = []
            for entry in evidence:
                if not isinstance(entry, dict):
                    break
                comment_id = entry.get("comment_id")
                quote = entry.get("quote")
                if (
                    not isinstance(comment_id, str)
                    or comment_id not in comments
                    or comment_id in duplicate_ids
                    or not isinstance(quote, str)
                    or not quote.strip()
                ):
                    break
                thread_number, original = comments[comment_id]
                if quote not in original:
                    break
                verified.append((thread_number, comment_id, quote))

            if len({thread_number for thread_number, _, _ in verified}) != evidence_count:
                rejected += 1
                continue
            checked[section].append((topic.strip(), verified))
    return checked, rejected


def show_sample_summary(summary, rejected):
    if rejected:
        st.warning(f"有 {rejected} 项未通过证据校验，已隐藏。")
    st.caption("校验核对引文和来源。主题是否贴合原文仍需人工判断。")
    for section, label in (
        ("shared_topics", "跨串共同话题"),
        ("single_thread_points", "单串观点"),
    ):
        st.markdown(f"**{label}**")
        if not summary[section]:
            st.caption("本次没有通过校验的条目。")
        for topic, evidence in summary[section]:
            st.text(topic)
            for number, comment_id, quote in evidence:
                st.text(f"第 {number} 串 · 评论 ID {comment_id} · 「{quote}」")


def show_thread_analysis(discussion, sample_threads=None):
    sample = sample_threads is not None
    mode = "sample" if sample else "thread"
    button_label = "汇总分析前 3 串" if sample else "分析这串评论"

    if not sample:
        st.divider()
        if st.checkbox("预览要分析的评论"):
            st.text_area(
                "待分析的评论原文",
                value=discussion,
                height=280,
                disabled=True,
            )

    with st.form(f"{mode}_analysis_form"):
        base_url = st.text_input("百炼 OpenAI 兼容地址")
        api_key = st.text_input("百炼 API Key", type="password")
        st.caption(f"模型：{BAILIAN_MODEL}。提交后会将对应的评论发送给百炼。")
        analyze = st.form_submit_button(button_label)

    result_key = "checked_sample_analysis_result" if sample else "thread_analysis_result"
    if analyze:
        st.session_state.pop(result_key, None)
        base_url = base_url.strip().rstrip("/")
        api_key = api_key.strip()

        if not re.fullmatch(
            r"https://ws-[a-zA-Z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com/compatible-mode/v1",
            base_url,
        ):
            st.error("请填写百炼北京工作空间的 OpenAI 兼容地址。")
        elif not api_key:
            st.error("请填写百炼 API Key。")
        else:
            instructions = SAMPLE_INSTRUCTIONS if sample else ANALYSIS_INSTRUCTIONS
            with st.spinner("正在分析评论…"):
                try:
                    answer, truncated = request_analysis(
                        base_url, api_key, discussion, instructions
                    )
                    if sample:
                        if truncated:
                            raise ValueError("模型的汇总输出被截断，请重新提交。")
                        answer = check_sample_summary(answer, sample_threads)
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.session_state[result_key] = (discussion, answer, truncated)

    result = st.session_state.get(result_key)
    if result and result[0] == discussion:
        st.subheader("样本汇总结果" if sample else "AI 分析结果")
        if sample:
            show_sample_summary(*result[1])
        else:
            if result[2]:
                st.warning("模型输出达到长度上限，结果可能不完整。")
            st.text(result[1])


def show_comments():
    st.subheader("评论与楼中楼")
    files = sorted(
        (PROJECT_DIR / "datasets").glob(f"{SAMPLE_AID}_dataset_*.json"),
        reverse=True,
    )

    if not files:
        st.warning("datasets 文件夹中没有找到该视频的汇总数据。")
        return

    source = files[0]

    try:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("数据最外层应为对象。")
        if str(data.get("aid")) != SAMPLE_AID:
            raise ValueError("文件内的视频编号与目标视频不一致。")

        threads = data.get("threads")
        if not isinstance(threads, list):
            raise ValueError("没有找到有效的讨论列表。")

        for thread in threads:
            if not isinstance(thread, dict):
                raise ValueError("讨论记录格式不正确。")
            if not isinstance(thread.get("main_comment"), dict):
                raise ValueError("主评论格式不正确。")
            replies = thread.get("replies")
            if not isinstance(replies, list):
                raise ValueError("回复列表格式不正确。")
            if any(not isinstance(reply, dict) for reply in replies):
                raise ValueError("回复记录格式不正确。")
    except (OSError, UnicodeError, ValueError) as error:
        st.error(f"评论数据读取失败：{error}")
        return

    reply_total = sum(len(thread["replies"]) for thread in threads)
    left, right = st.columns(2)
    left.metric("已保存主评论", len(threads))
    right.metric("已保存楼中楼回复", reply_total)

    st.warning("这是已采集的部分样本，不代表全部评论或全部观众。")
    st.caption(f"读取文件：{source.name}")

    if not threads:
        return

    if st.checkbox("预览前 3 串评论"):
        sample_threads = threads[:3]
        previews = []
        for number, thread in enumerate(sample_threads, start=1):
            discussion = build_discussion_text(
                thread["main_comment"], thread["replies"]
            )
            previews.append(f"第 {number} 串\n{discussion}")
        st.text_area(
            "样本汇总预览",
            value="\n\n".join(previews),
            height=360,
            disabled=True,
        )

        show_thread_analysis("\n\n".join(previews), sample_threads=sample_threads)

    selected_number = st.selectbox(
        "选择要分析的主评论",
        list(range(1, len(threads) + 1)),
    )

    for number, thread in enumerate(threads, start=1):
        main = thread["main_comment"]
        replies = thread["replies"]

        with st.expander(
            f"主评论 {number} · 已保存回复 {len(replies)} 条",
            expanded=(number == selected_number),
        ):
            st.text(main.get("text", ""))
            st.caption(
                f"评论 ID：{main.get('comment_id', '未知')} ｜ "
                f"采集时点赞：{main.get('like_count', '未知')} ｜ "
                f"用户等级：{main.get('user_level', '未知')}"
            )

            for index, reply in enumerate(replies, start=1):
                st.divider()
                st.write(f"回复 {index}")
                st.text(reply.get("text", ""))
                st.caption(
                    f"回复 ID：{reply.get('comment_id', '未知')} ｜ "
                    f"直接回复对象 ID：{reply.get('parent_id', '未知')} ｜ "
                    f"采集时点赞：{reply.get('like_count', '未知')}"
                )

            status = thread.get("collection_status", {})
            if isinstance(status, dict):
                st.caption(
                    "回复采集状态："
                    + str(status.get("stop_reason", "未记录"))
                )

            if number == selected_number:
                show_thread_analysis(build_discussion_text(main, replies))


st.title("💬 B站评论洞察助手")
st.write("结合视频内容与观众讨论，理解反馈，并保留原文依据。")
st.info("当前支持：检查链接、查看本地字幕和评论，以及使用百炼分析单串评论和汇总前 3 串。")

with st.form("video_form"):
    video_url = st.text_input(
        "视频链接",
        value=f"https://www.bilibili.com/video/{SAMPLE_BVID}/",
    )
    submitted = st.form_submit_button("查看已保存资料")

if submitted:
    parsed = urlparse(video_url.strip())
    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", parsed.path)

    valid = (
        parsed.scheme in ("http", "https")
        and parsed.hostname in ("www.bilibili.com", "bilibili.com")
        and match is not None
    )

    st.session_state["selected_bvid"] = match.group(1) if valid else None

    if not valid:
        st.error("请填写包含 BV 号的 B 站完整视频链接。")

bvid = st.session_state.get("selected_bvid")

if bvid:
    st.success(f"已识别视频编号：{bvid}")
    st.caption("仅检查链接格式，尚未联网确认视频是否存在。")

    if bvid == SAMPLE_BVID:
        st.write("当前视频：令人绝望的少年漫女角色塑造【女角色（上）】")
        subtitle_tab, comment_tab = st.tabs(["视频字幕", "观众评论"])

        with subtitle_tab:
            show_subtitles()

        with comment_tab:
            show_comments()
    else:
        st.warning("这条视频尚未接入本地资料，当前不会自动采集。")
