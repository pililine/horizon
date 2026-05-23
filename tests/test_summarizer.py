"""Unit tests for daily summary rendering."""

from datetime import datetime, timezone
import asyncio

from src.ai.summarizer import DailySummarizer
from src.models import ContentItem, SourceType


def _make_item(idx: int) -> ContentItem:
    item = ContentItem(
        id=f"rss:item-{idx}",
        source_type=SourceType.RSS,
        title=f"Important Item {idx}",
        url=f"https://example.com/items/{idx}",
        content="content",
        author="tester",
        published_at=datetime(2026, 4, 25, 8, 0, tzinfo=timezone.utc),
    )
    item.ai_score = 8.0
    item.ai_summary = f"Summary for item {idx}."
    item.ai_tags = ["AI", "News"]
    return item


def test_generate_webhook_overview_lists_items_without_full_details():
    summarizer = DailySummarizer()
    items = [_make_item(1), _make_item(2)]

    result = summarizer.generate_webhook_overview(
        items,
        date="2026-04-25",
        total_fetched=10,
        language="en",
    )

    assert "Selected 2 important items from 10 fetched items" in result
    assert "1. [Important Item 1](https://example.com/items/1)" in result
    assert "2. [Important Item 2](https://example.com/items/2)" in result
    assert "Summary for item 1." not in result


def test_generate_webhook_item_renders_single_item_detail():
    summarizer = DailySummarizer()

    result = summarizer.generate_webhook_item(
        _make_item(1),
        language="en",
        index=1,
        total=2,
    )

    assert result.startswith("Item 1/2")
    assert "## 🌐 [Important Item 1](https://example.com/items/1)" in result
    assert "Summary for item 1." in result
    assert "**Tags:** #AI, #News" in result


def test_generate_webhook_item_includes_discussion_link_when_distinct():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.metadata["discussion_url"] = "https://news.ycombinator.com/item?id=1"

    result = summarizer.generate_webhook_item(
        item,
        language="en",
        index=1,
        total=1,
    )

    assert "**Source:** rss · tester · Apr 25, 08:00 · [Discussion](https://news.ycombinator.com/item?id=1)" in result


def test_generate_webhook_item_omits_discussion_link_when_same_as_item_url():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.metadata["discussion_url"] = item.url

    result = summarizer.generate_webhook_item(
        item,
        language="en",
        index=1,
        total=1,
    )

    assert "[Discussion](https://example.com/items/1)" not in result


def test_generate_webhook_item_uses_localized_discussion_label():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.metadata["discussion_url"] = "https://www.reddit.com/r/python/comments/abc123/test/"

    result = summarizer.generate_webhook_item(
        item,
        language="zh",
        index=1,
        total=1,
    )

    assert "[社区讨论](https://www.reddit.com/r/python/comments/abc123/test/)" in result


def test_generate_summary_renders_references_as_markdown_links_en():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.metadata["sources"] = [
        {"title": "Example Reference", "url": "https://example.com/reference"}
    ]

    result = asyncio.run(summarizer.generate_summary(
        [item],
        date="2026-04-25",
        total_fetched=1,
        language="en",
    ))

    assert "## 🌐 [Important Item 1](https://example.com/items/1) ⭐️ 8.0/10" in result
    assert "**References:**\n- [Example Reference](https://example.com/reference)" in result
    assert "[Important Item 1](https://example.com/items/1)" in result
    for forbidden in ("<details>", "<summary>", "<ul>", "<li>", '<a href=', '<a id='):
        assert forbidden not in result


def test_generate_summary_renders_references_as_markdown_links_zh():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.metadata["title_zh"] = "重要新闻"
    item.metadata["sources"] = [
        {"title": "示例参考", "url": "https://example.com/reference"}
    ]

    result = asyncio.run(summarizer.generate_summary(
        [item],
        date="2026-04-25",
        total_fetched=1,
        language="zh",
    ))

    assert "## 🌐 [重要新闻](https://example.com/items/1) ⭐️ 8.0/10" in result
    assert "**参考链接：**\n- [示例参考](https://example.com/reference)" in result
    for forbidden in ("<details>", "<summary>", "<ul>", "<li>", '<a href=', '<a id='):
        assert forbidden not in result


def test_generate_summary_renders_none_when_references_are_missing():
    summarizer = DailySummarizer()

    en = asyncio.run(summarizer.generate_summary(
        [_make_item(1)],
        date="2026-04-25",
        total_fetched=1,
        language="en",
    ))
    zh = asyncio.run(summarizer.generate_summary(
        [_make_item(1)],
        date="2026-04-25",
        total_fetched=1,
        language="zh",
    ))

    assert "**References:** No references" in en
    assert "**参考链接：** 无" in zh


def test_generate_summary_strips_thinking_text_from_markdown():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.ai_summary = "<think>private reasoning</think>Final summary."
    item.metadata["background_en"] = (
        "Thinking...\nprivate reasoning\n...done thinking.\nFinal background."
    )
    item.metadata["sources"] = [
        {"title": "<think>hidden</think>Reference", "url": "https://example.com/ref"}
    ]

    result = asyncio.run(summarizer.generate_summary(
        [item],
        date="2026-04-25",
        total_fetched=1,
        language="en",
    ))

    assert "Final summary." in result
    assert "Final background." in result
    assert "- [Reference](https://example.com/ref)" in result
    for forbidden in ("<think>", "</think>", "Thinking...", "done thinking"):
        assert forbidden not in result


def test_generate_summary_handles_no_enrichment_without_placeholder_text():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.metadata["enrichment_mode"] = "none"

    result = asyncio.run(summarizer.generate_summary(
        [item],
        date="2026-04-25",
        total_fetched=1,
        language="en",
    ))

    assert "Summary for item 1." in result
    assert "**References:** No references" in result
    for forbidden in ("None\n", "null", "undefined", "<empty string>"):
        assert forbidden not in result
