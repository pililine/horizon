"""Daily summary generation — pure programmatic rendering."""

import re
from typing import List, Dict

from ..models import ContentItem
from .utils import strip_thinking_content


_CJK = r"[\u4e00-\u9fff\u3400-\u4dbf]"
_ASCII = r"[A-Za-z0-9]"


def _pangu(text: str) -> str:
    """Insert a space between CJK and ASCII letters/digits (Pangu spacing)."""
    text = re.sub(rf"({_CJK})({_ASCII})", r"\1 \2", text)
    text = re.sub(rf"({_ASCII})({_CJK})", r"\1 \2", text)
    return text


def _field_label(label: str, language: str) -> str:
    colon = "：" if language == "zh" else ":"
    return f"**{label}{colon}**"


def _clean_text(value) -> str:
    return strip_thinking_content(str(value or ""))


LABELS = {
    "en": {
        "header": "Horizon Daily",
        "source": "Source",
        "background": "Background",
        "discussion": "Discussion",
        "references": "References",
        "none": "No references",
        "tags": "Tags",
        "empty_header": "Fetched {total} items, but no candidates were available.",
        "insufficient_note": (
            "Note: fewer candidates than the minimum output size were available, "
            "so every available candidate is shown below."
        ),
        "empty_body": (
            "No candidates were available in the selected time window. This usually means:\n"
            "- Your tracked sources had no new content in the window\n"
            "- A wider time window may surface more (try a larger --hours value)\n"
            "- Worth checking that your sources and AI model are reachable\n"
        ),
    },
    "zh": {
        "header": "Horizon 每日速递",
        "source": "来源",
        "background": "背景",
        "discussion": "社区讨论",
        "references": "参考链接",
        "none": "无",
        "tags": "标签",
        "empty_header": "已抓取 {total} 条内容，但没有任何候选。",
        "insufficient_note": "说明：本次候选少于最低输出数量，已输出全部候选。",
        "empty_body": (
            "在所选时间窗口内没有任何候选内容，通常说明：\n"
            "- 关注的信息源在该时间窗口内暂无新内容\n"
            "- 可尝试更大的时间窗口（增大 --hours）\n"
            "- 也可顺便确认信息源与 AI 模型是否正常可达\n"
        ),
    },
}


class DailySummarizer:
    """Generates daily Markdown summaries from pre-analyzed content items."""

    def __init__(self):
        pass

    async def generate_summary(
        self,
        items: List[ContentItem],
        date: str,
        total_fetched: int,
        language: str = "en",
        insufficient_candidates: bool = False,
    ) -> str:
        """Generate daily summary in Markdown format.

        Items are rendered in score-descending order (already sorted by orchestrator).

        Args:
            items: Selected content items (already enriched)
            date: Date string (YYYY-MM-DD)
            total_fetched: Total number of items fetched before selection
            language: Output language, either "en" or "zh"
            insufficient_candidates: True when fewer candidates than the minimum
                output size were available, so all of them are shown

        Returns:
            str: Markdown formatted summary
        """
        labels = LABELS.get(language, LABELS["en"])

        if not items:
            return self._generate_empty_summary(date, total_fetched, labels)

        header = (
            f"# {labels['header']} - {date}\n\n"
            f"> From {total_fetched} items, {len(items)} important content pieces were selected\n\n"
        )
        if insufficient_candidates:
            header += f"> {labels['insufficient_note']}\n\n"
        header += "---\n\n"

        # TOC
        toc_entries = []
        for i, item in enumerate(items):
            _t = item.metadata.get(f"title_{language}") or item.title
            t = _clean_text(_t).replace("[", "(").replace("]", ")")
            if language == "zh":
                t = _pangu(t)
            score = item.ai_score or "?"
            toc_entries.append(f"{i + 1}. [{t}]({item.url}) \u2b50\ufe0f {score}/10")
        toc = "\n".join(toc_entries) + "\n\n---\n\n"

        parts = [self._format_item(item, labels, language, i + 1) for i, item in enumerate(items)]

        return header + toc + "".join(parts)

    def generate_webhook_overview(
        self,
        items: List[ContentItem],
        date: str,
        total_fetched: int,
        language: str = "en",
    ) -> str:
        """Generate a compact overview for multi-message webhook delivery."""
        labels = LABELS.get(language, LABELS["en"])
        if not items:
            return self._generate_empty_summary(date, total_fetched, labels)

        if language == "zh":
            header = (
                f"# {labels['header']} - {date}\n\n"
                f"> 从 {total_fetched} 条内容中筛选出 {len(items)} 条重要资讯。\n\n"
                "下面会按新闻逐条发送详情，你可以只看感兴趣的标题。\n\n"
            )
        else:
            header = (
                f"# {labels['header']} - {date}\n\n"
                f"> Selected {len(items)} important items from {total_fetched} fetched items.\n\n"
                "Details will be sent item by item so you can read only the topics you care about.\n\n"
            )

        entries = []
        for i, item in enumerate(items, start=1):
            title = _clean_text(item.metadata.get(f"title_{language}") or item.title).replace("[", "(").replace("]", ")")
            if language == "zh":
                title = _pangu(title)
            score = item.ai_score or "?"
            entries.append(f"{i}. [{title}]({item.url}) \u2b50\ufe0f {score}/10")

        return header + "\n".join(entries)

    def generate_webhook_item(
        self,
        item: ContentItem,
        language: str,
        index: int,
        total: int,
    ) -> str:
        """Generate one item message for multi-message webhook delivery."""
        labels = LABELS.get(language, LABELS["en"])
        prefix = f"第 {index}/{total} 条\n\n" if language == "zh" else f"Item {index}/{total}\n\n"
        return prefix + self._format_item(item, labels, language, index).rstrip("-\n ")

    def _format_item(self, item: ContentItem, labels: dict, language: str, index: int) -> str:
        """Format a single ContentItem into Markdown."""
        _title = item.metadata.get(f"title_{language}") or item.title
        title = _clean_text(_title).replace("[", "(").replace("]", ")")
        url = str(item.url)
        score = item.ai_score or "?"
        meta = item.metadata

        summary = _clean_text(
            meta.get(f"detailed_summary_{language}")
            or meta.get("detailed_summary")
            or item.ai_summary
            or ""
        )
        background = _clean_text(meta.get(f"background_{language}") or meta.get("background") or "")
        discussion = _clean_text(
            meta.get(f"community_discussion_{language}")
            or meta.get("community_discussion")
            or ""
        )

        if language == "zh":
            title = _pangu(title)
            summary = _pangu(summary)
            background = _pangu(background)
            discussion = _pangu(discussion)

        # Source line with parts joined by " · ", link appended at end
        source_type = item.source_type.value
        source_parts = [source_type]
        if meta.get("subreddit"):
            source_parts.append(f"r/{meta['subreddit']}")
        if meta.get("feed_name"):
            source_parts.append(meta["feed_name"])
        else:
            source_parts.append(item.author or "unknown")
        if item.published_at:
            day = item.published_at.strftime("%d").lstrip("0")
            source_parts.append(item.published_at.strftime(f"%b {day}, %H:%M"))
        source_line = " \u00b7 ".join(source_parts)  # ·

        discussion_url = meta.get("discussion_url")
        if discussion_url:
            discussion_url = str(discussion_url)
            if discussion_url != url:
                source_line += f' · [{labels["discussion"]}]({discussion_url})'

        lines = [
            f"## \U0001f310 [{title}]({url}) \u2b50\ufe0f {score}/10",
            "",
            summary,
            "",
            f"{_field_label(labels['source'], language)} {source_line}",
        ]

        if background:
            lines.append("")
            lines.append(f"{_field_label(labels['background'], language)} {background}")

        sources = meta.get("sources") or []
        lines.append("")
        if sources:
            lines.append(_field_label(labels["references"], language))
            for source in sources:
                source_title = _clean_text(source.get("title") or source.get("url") or "")
                source_url = str(source.get("url") or "").strip()
                if source_title and source_url:
                    lines.append(f"- [{source_title}]({source_url})")
        else:
            lines.append(f"{_field_label(labels['references'], language)} {labels['none']}")

        if discussion:
            lines.append("")
            lines.append(f"{_field_label(labels['discussion'], language)} {discussion}")

        if item.ai_tags:
            tags_str = ", ".join([f"#{t}" for t in item.ai_tags])
            lines.append("")
            lines.append(f"{_field_label(labels['tags'], language)} {tags_str}")

        lines.append("")
        lines.append("---")

        return "\n".join(lines) + "\n\n"

    def _generate_empty_summary(self, date: str, total_fetched: int, labels: dict) -> str:
        """Generate summary when there were zero candidates."""
        return (
            f"# {labels['header']} - {date}\n\n"
            f"> {labels['empty_header'].format(total=total_fetched)}\n\n"
            + labels["empty_body"]
        )
