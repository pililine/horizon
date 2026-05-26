"""Tests for score-ranked output selection (min/max items per report).

Covers the behavior change from a hard ai_score_threshold filter to:
score ranks and counts high-scoring items, each report emits between
min_items_per_report and max_items_per_report items, and an empty report is
produced only when there are zero candidates.
"""

import asyncio
from datetime import datetime, timezone

from src.ai.summarizer import DailySummarizer
from src.models import (
    AIConfig,
    AIProvider,
    Config,
    ContentItem,
    FilteringConfig,
    RSSSourceConfig,
    SourcesConfig,
    SourceType,
)
from src.orchestrator import HorizonOrchestrator


def _item(
    idx: int,
    score: float,
    *,
    title: str | None = None,
    content: str = "content",
    source_type: SourceType = SourceType.RSS,
) -> ContentItem:
    it = ContentItem(
        id=f"rss:{idx}",
        source_type=source_type,
        title=title or f"Item {idx}",
        url=f"https://example.com/{idx}",
        content=content,
        author="tester",
        published_at=datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc),
    )
    it.ai_score = score
    it.ai_summary = f"Summary for item {idx}."
    it.ai_tags = ["AI"]
    return it


def _make_orchestrator(
    *,
    min_items=10,
    max_items=15,
    threshold=7.0,
    candidate_limit=25,
    enrichment_mode="tiered",
    full_threshold=8.0,
    brief_threshold=7.0,
    max_full=3,
) -> HorizonOrchestrator:
    config = Config(
        ai=AIConfig(
            provider=AIProvider.OLLAMA,
            model="qwen2.5:14b",
            api_key_env="LOCAL_LLM_API_KEY",
            languages=["zh", "en"],
            enrichment_mode=enrichment_mode,
            enrichment_full_threshold=full_threshold,
            enrichment_brief_threshold=brief_threshold,
            enrichment_max_full_items=max_full,
            enrichment_timeout_seconds=120,
        ),
        sources=SourcesConfig(),
        filtering=FilteringConfig(
            ai_score_threshold=threshold,
            min_items_per_report=min_items,
            max_items_per_report=max_items,
            semantic_dedupe_candidate_limit=candidate_limit,
        ),
    )
    return HorizonOrchestrator(config, storage=None)


class _FakeEnricher:
    calls = []

    def __init__(self, ai_client):
        self.ai_client = ai_client

    async def enrich_batch(self, items, mode="full", timeout_seconds=None):
        self.calls.append(
            {
                "mode": mode,
                "ids": [item.id for item in items],
                "timeout_seconds": timeout_seconds,
            }
        )
        for item in items:
            item.metadata["enrichment_mode"] = mode


def _patch_fake_enricher(monkeypatch):
    _FakeEnricher.calls = []
    monkeypatch.setattr("src.orchestrator.create_ai_client", lambda config: object())
    monkeypatch.setattr("src.orchestrator.ContentEnricher", _FakeEnricher)


# --- Scenarios 1-5: pure selection rules -----------------------------------


def test_select_zero_candidates_returns_empty():
    out, high = HorizonOrchestrator._select_output_items(
        [], threshold=7.0, min_items=10, max_items=15
    )
    assert out == []
    assert high == 0


def test_promo_filter_removes_jd_red_packet_ad():
    orch = _make_orchestrator()
    jd = _item(
        100,
        0,
        title="打开京东 App 搜索「待领红包 963」，每日可领 3 次，最高 26618 元。",
        source_type=SourceType.TELEGRAM,
    )
    kept = orch._apply_promotional_prefilter([jd])
    assert kept == []


def test_promo_filter_removes_coupon_cashback_ad():
    orch = _make_orchestrator()
    ad = _item(
        101,
        0,
        title="限时优惠券返利，立即下单立减，最高 cashback 50%",
        source_type=SourceType.TELEGRAM,
    )
    kept = orch._apply_promotional_prefilter([ad])
    assert kept == []


def test_promo_filter_removes_english_promo_referral():
    orch = _make_orchestrator()
    ad = _item(
        102,
        0,
        title="Limited offer coupon and promo code with referral bonus",
    )
    kept = orch._apply_promotional_prefilter([ad])
    assert kept == []


def test_promo_filter_keeps_security_spam_incident_news():
    orch = _make_orchestrator()
    security_news = _item(
        103,
        0,
        title="Scammers are abusing an internal Microsoft account to send spam links",
        source_type=SourceType.HACKERNEWS,
    )
    kept = orch._apply_promotional_prefilter([security_news])
    assert kept == [security_news]


def test_promo_filter_keeps_telegram_backdoor_security_news():
    orch = _make_orchestrator()
    security_news = _item(
        104,
        0,
        title="Telegram official APKPure version injected with spyware backdoor",
        source_type=SourceType.TELEGRAM,
    )
    kept = orch._apply_promotional_prefilter([security_news])
    assert kept == [security_news]


def test_promo_filter_keeps_apple_google_ai_product_news():
    orch = _make_orchestrator()
    normal = _item(
        105,
        0,
        title="Google Docs Live adds AI voice drafting for document workflows",
        source_type=SourceType.TELEGRAM,
    )
    kept = orch._apply_promotional_prefilter([normal])
    assert kept == [normal]


def test_select_six_low_score_candidates_outputs_all_six():
    items = [_item(i, 3.0) for i in range(6)]
    out, high = HorizonOrchestrator._select_output_items(
        items, threshold=7.0, min_items=10, max_items=15
    )
    assert len(out) == 6
    assert high == 0


def test_select_twelve_candidates_three_high_outputs_top_ten():
    items = [_item(i, 8.0) for i in range(3)] + [_item(i, 3.0) for i in range(3, 12)]
    out, high = HorizonOrchestrator._select_output_items(
        items, threshold=7.0, min_items=10, max_items=15
    )
    assert len(out) == 10
    assert high == 3
    # Top of the list is the high-scoring set
    assert all(o.ai_score == 8.0 for o in out[:3])


def test_select_twelve_high_candidates_outputs_twelve():
    items = [_item(i, 8.0) for i in range(12)]
    out, high = HorizonOrchestrator._select_output_items(
        items, threshold=7.0, min_items=10, max_items=15
    )
    assert len(out) == 12
    assert high == 12


def test_select_twenty_candidates_eighteen_high_outputs_top_fifteen():
    items = [_item(i, 8.0) for i in range(18)] + [_item(i, 3.0) for i in range(18, 20)]
    out, high = HorizonOrchestrator._select_output_items(
        items, threshold=7.0, min_items=10, max_items=15
    )
    assert len(out) == 15
    assert high == 18
    assert all(o.ai_score == 8.0 for o in out)


def test_high_quality_source_can_rank_above_medium_with_slightly_lower_score():
    high = _item(1, 6.8)
    high.metadata["source_quality"] = "high"
    high.metadata["source_weight"] = 0.5
    medium = _item(2, 6.5)
    medium.metadata["source_quality"] = "medium"

    out, high_count = HorizonOrchestrator._select_output_items(
        [medium, high], threshold=6.0, min_items=1, max_items=2
    )

    assert out == [high, medium]
    assert high_count == 2
    assert high.ai_score == 6.8
    assert high.metadata["ranking_score"] == 7.3


def test_low_quality_source_with_same_score_ranks_below_high_quality_source():
    high = _item(1, 7.0)
    high.metadata["source_quality"] = "high"
    high.metadata["source_weight"] = 0.5
    low = _item(2, 7.0)
    low.metadata["source_quality"] = "low"
    low.metadata["source_weight"] = -0.5

    out, high_count = HorizonOrchestrator._select_output_items(
        [low, high], threshold=7.0, min_items=1, max_items=2
    )

    assert out == [high, low]
    assert high_count == 2
    assert high.ai_score == low.ai_score == 7.0
    assert high.metadata["ranking_score"] == 7.5
    assert low.metadata["ranking_score"] == 6.5


def test_source_profile_defaults_to_medium_and_zero_weight():
    source = RSSSourceConfig(name="Example", url="https://example.com/feed.xml")
    item = _item(1, 7.0)

    assert source.source_quality == "medium"
    assert source.source_weight == 0.0
    assert HorizonOrchestrator._source_profile(item) == ("medium", 0.0)


# --- Scenario 6: semantic dedup happens before final selection -------------


def test_dedup_runs_before_selection_and_cap_applies_to_deduped_set():
    orch = _make_orchestrator()
    items = [_item(i, 8.0) for i in range(20)]
    dropped_ids = {"rss:0", "rss:1", "rss:2"}

    async def fake_analyze(to_analyze):
        return to_analyze

    async def fake_dedup(to_dedup):
        return [it for it in to_dedup if it.id not in dropped_ids]

    orch._analyze_content = fake_analyze
    orch.merge_topic_duplicates = fake_dedup

    out, stats = asyncio.run(orch._curate_output_items(items))

    out_ids = {o.id for o in out}
    # Dropped duplicates must not survive into the final selection
    assert dropped_ids.isdisjoint(out_ids)
    # 20 - 3 = 17 deduped candidates; high-score count is computed post-dedup
    assert stats["deduped_count"] == 17
    assert stats["high_score_count"] == 17
    # Selection caps the deduped set at max_items
    assert len(out) == 15
    assert stats["output_count"] == 15
    assert stats["insufficient"] is False


def test_curate_flags_insufficient_when_below_min():
    orch = _make_orchestrator()
    items = [_item(i, 3.0) for i in range(6)]

    async def fake_analyze(to_analyze):
        return to_analyze

    async def fake_dedup(to_dedup):
        return to_dedup

    orch._analyze_content = fake_analyze
    orch.merge_topic_duplicates = fake_dedup

    out, stats = asyncio.run(orch._curate_output_items(items))
    assert len(out) == 6
    assert stats["insufficient"] is True


def test_candidate_pool_is_bounded_before_dedup():
    orch = _make_orchestrator(candidate_limit=25)
    items = [_item(i, 8.0) for i in range(40)]
    seen = {}

    async def fake_analyze(to_analyze):
        return to_analyze

    async def fake_dedup(to_dedup):
        seen["pool_size"] = len(to_dedup)
        return to_dedup

    orch._analyze_content = fake_analyze
    orch.merge_topic_duplicates = fake_dedup

    asyncio.run(orch._curate_output_items(items))
    assert seen["pool_size"] == 25


def test_source_contribution_logs_quality_summary(capsys):
    orch = _make_orchestrator()
    high = _item(1, 8.0)
    high.metadata["source_quality"] = "high"
    high.metadata["source_weight"] = 0.5
    medium = _item(2, 6.0)

    orch._log_source_contribution([high, medium])
    captured = capsys.readouterr().out

    assert "Source contribution by source_quality" in captured
    assert "high: 1 items, avg score 8.0" in captured
    assert "medium: 1 items, avg score 6.0" in captured


# --- Scenarios 7 & 8: enrichment scope + non-empty zh/en reports -----------


class _FakeStorage:
    def __init__(self):
        self.saved = {}

    def save_daily_summary(self, date, markdown, language="en"):
        self.saved[language] = markdown
        return f"data/summaries/{date}-{language}.md"

    def load_subscribers(self):
        return []


def test_run_enriches_only_final_items_and_emits_nonempty_reports(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    orch = _make_orchestrator()
    storage = _FakeStorage()
    orch.storage = storage

    # 18 high-scoring items; dedup drops 1 -> 17 candidates -> top 15 selected
    items = [_item(i, 8.0) for i in range(18)]
    captured = {}

    async def fake_fetch(since):
        return items

    async def fake_analyze(to_analyze):
        return to_analyze

    async def fake_dedup(to_dedup):
        return [it for it in to_dedup if it.id != "rss:0"]

    async def fake_expand(to_expand):
        return None

    async def fake_enrich(to_enrich):
        captured["enriched"] = list(to_enrich)

    orch.fetch_all_sources = fake_fetch
    orch._analyze_content = fake_analyze
    orch.merge_topic_duplicates = fake_dedup
    orch._expand_twitter_discussion = fake_expand
    orch._enrich_important_items = fake_enrich

    asyncio.run(orch.run(force_hours=12))

    # Scenario 7: enrichment runs only over the final 15 output items
    assert len(captured["enriched"]) == 15

    # Scenario 8: both languages produced a non-empty (non-threshold) report
    assert set(storage.saved.keys()) == {"zh", "en"}
    for lang, md in storage.saved.items():
        assert "Horizon" in md
        assert "no candidates were available" not in md
        assert "没有任何候选" not in md


def test_run_zero_candidates_writes_empty_reports_and_passes_no_items_to_enrichment(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    orch = _make_orchestrator()
    storage = _FakeStorage()
    orch.storage = storage
    called = {"analyze": False, "dedup": False}
    captured = {"enrich_items": None}

    async def fake_fetch(since):
        return []

    async def fake_analyze(to_analyze):
        called["analyze"] = True
        return to_analyze

    async def fake_dedup(to_dedup):
        called["dedup"] = True
        return to_dedup

    async def fake_expand(to_expand):
        return None

    original_enrich = orch._enrich_important_items

    async def fake_enrich(to_enrich):
        captured["enrich_items"] = list(to_enrich)
        await original_enrich(to_enrich)

    orch.fetch_all_sources = fake_fetch
    orch._analyze_content = fake_analyze
    orch.merge_topic_duplicates = fake_dedup
    orch._expand_twitter_discussion = fake_expand
    orch._enrich_important_items = fake_enrich

    asyncio.run(orch.run(force_hours=12))

    assert called == {"analyze": False, "dedup": False}
    assert captured["enrich_items"] == []
    assert set(storage.saved.keys()) == {"zh", "en"}
    assert "no candidates were available" in storage.saved["en"]
    assert "没有任何候选" in storage.saved["zh"]


def test_run_prefilter_happens_before_ai_scoring(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    orch = _make_orchestrator()
    storage = _FakeStorage()
    orch.storage = storage
    seen = {"curate_ids": None}
    items = [
        _item(
            200,
            0.0,
            title="打开京东 App 搜索「待领红包 963」，每日可领 3 次，最高 26618 元。",
            source_type=SourceType.TELEGRAM,
        ),
        _item(201, 8.0, title="Real AI Infra Update", source_type=SourceType.HACKERNEWS),
    ]

    async def fake_fetch(since):
        return items

    async def fake_curate(to_curate):
        seen["curate_ids"] = [i.id for i in to_curate]
        return (to_curate, {"insufficient": False})

    async def fake_expand(to_expand):
        return None

    async def fake_enrich(to_enrich):
        return None

    orch.fetch_all_sources = fake_fetch
    orch._curate_output_items = fake_curate
    orch._expand_twitter_discussion = fake_expand
    orch._enrich_important_items = fake_enrich

    asyncio.run(orch.run(force_hours=12))
    assert seen["curate_ids"] == ["rss:201"]


# --- Enrichment modes -------------------------------------------------------


def test_full_enrichment_mode_enriches_all_final_items(monkeypatch):
    _patch_fake_enricher(monkeypatch)
    orch = _make_orchestrator(enrichment_mode="full")
    items = [_item(i, 8.0) for i in range(4)]

    asyncio.run(orch._enrich_important_items(items))

    assert _FakeEnricher.calls == [
        {
            "mode": "full",
            "ids": [item.id for item in items],
            "timeout_seconds": 120,
        }
    ]


def test_none_enrichment_mode_skips_llm_and_marks_items(monkeypatch):
    _patch_fake_enricher(monkeypatch)
    orch = _make_orchestrator(enrichment_mode="none")
    items = [_item(i, 6.0) for i in range(3)]

    asyncio.run(orch._enrich_important_items(items))

    assert _FakeEnricher.calls == []
    assert [item.metadata["enrichment_mode"] for item in items] == ["none", "none", "none"]


def test_brief_enrichment_mode_uses_brief_path(monkeypatch):
    _patch_fake_enricher(monkeypatch)
    orch = _make_orchestrator(enrichment_mode="brief")
    items = [_item(i, 7.0) for i in range(2)]

    asyncio.run(orch._enrich_important_items(items))

    assert _FakeEnricher.calls == [
        {
            "mode": "brief",
            "ids": [item.id for item in items],
            "timeout_seconds": 120,
        }
    ]


def test_tiered_enrichment_applies_full_brief_and_none(monkeypatch):
    _patch_fake_enricher(monkeypatch)
    orch = _make_orchestrator(
        enrichment_mode="tiered",
        full_threshold=8.0,
        brief_threshold=7.0,
        max_full=2,
    )
    items = [
        _item(0, 9.0),
        _item(1, 8.5),
        _item(2, 8.1),
        _item(3, 7.2),
        _item(4, 6.9),
    ]

    asyncio.run(orch._enrich_important_items(items))

    assert _FakeEnricher.calls == [
        {"mode": "full", "ids": ["rss:0", "rss:1"], "timeout_seconds": 120},
        {"mode": "brief", "ids": ["rss:2", "rss:3"], "timeout_seconds": 120},
    ]
    assert items[4].metadata["enrichment_mode"] == "none"


def test_tiered_enrichment_respects_max_full_items(monkeypatch):
    _patch_fake_enricher(monkeypatch)
    orch = _make_orchestrator(enrichment_mode="tiered", max_full=1)
    items = [_item(i, 9.0 - i * 0.1) for i in range(4)]

    asyncio.run(orch._enrich_important_items(items))

    assert _FakeEnricher.calls[0]["mode"] == "full"
    assert _FakeEnricher.calls[0]["ids"] == ["rss:0"]
    assert _FakeEnricher.calls[1]["mode"] == "brief"
    assert _FakeEnricher.calls[1]["ids"] == ["rss:1", "rss:2", "rss:3"]


def test_tiered_low_score_items_are_not_sent_to_llm(monkeypatch):
    _patch_fake_enricher(monkeypatch)
    orch = _make_orchestrator(enrichment_mode="tiered", brief_threshold=7.0)
    items = [_item(i, 6.0) for i in range(3)]

    asyncio.run(orch._enrich_important_items(items))

    assert _FakeEnricher.calls == []
    assert all(item.metadata["enrichment_mode"] == "none" for item in items)


# --- Scenario 1 at the rendering layer: empty report wording ----------------


def test_empty_summary_does_not_suggest_lowering_threshold():
    summarizer = DailySummarizer()
    en = asyncio.run(summarizer.generate_summary([], "2026-05-23", 0, language="en"))
    zh = asyncio.run(summarizer.generate_summary([], "2026-05-23", 0, language="zh"))

    assert "ai_score_threshold" not in en
    assert "ai_score_threshold" not in zh
    assert "no candidates were available" in en
    assert "没有任何候选" in zh


def test_summary_renders_insufficient_note_when_flagged():
    summarizer = DailySummarizer()
    items = [_item(i, 3.0) for i in range(4)]
    zh = asyncio.run(
        summarizer.generate_summary(
            items, "2026-05-23", 4, language="zh", insufficient_candidates=True
        )
    )
    assert "本次候选少于最低输出数量" in zh
