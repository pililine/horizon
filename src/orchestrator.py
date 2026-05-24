"""Main orchestrator coordinating the entire workflow."""

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import List, Dict
from urllib.parse import urlparse
import httpx
from rich.console import Console

from .models import Config, ContentItem
from .storage.manager import StorageManager
from .services.email import EmailManager
from .services.webhook import WebhookNotifier
from .scrapers.github import GitHubScraper
from .scrapers.hackernews import HackerNewsScraper
from .scrapers.rss import RSSScraper
from .scrapers.reddit import RedditScraper
from .scrapers.telegram import TelegramScraper
from .scrapers.twitter import TwitterScraper
from .scrapers.openbb import OpenBBScraper
from .scrapers.ossinsight import OSSInsightScraper
from .ai.client import create_ai_client
from .ai.analyzer import ContentAnalyzer
from .ai.summarizer import DailySummarizer
from .ai.enricher import ContentEnricher
from .ai.tokens import get_usage_snapshot


DEFAULT_SOURCE_WEIGHTS = {
    "high": 0.5,
    "medium": 0.0,
    "low": -0.5,
}


class HorizonOrchestrator:
    """Orchestrates the complete workflow for content aggregation and analysis."""

    def __init__(self, config: Config, storage: StorageManager):
        """Initialize orchestrator.

        Args:
            config: Application configuration
            storage: Storage manager
        """
        self.config = config
        self.storage = storage
        self.console = Console()
        self.email_manager = EmailManager(config.email, console=self.console) if config.email else None
        self.webhook_notifier = (
            WebhookNotifier(config.webhook, console=self.console)
            if config.webhook and config.webhook.enabled
            else None
        )

    async def run(self, force_hours: int = None) -> None:
        """Execute the complete workflow.

        Args:
            force_hours: Optional override for time window in hours
        """
        self.console.print("[bold cyan]🌅 Horizon - Starting aggregation...[/bold cyan]\n")

        # Check email subscriptions if configured
        if (
            self.email_manager
            and self.config.email
            and self.config.email.enabled
            and self.config.email.imap_enabled
        ):
            self.console.print("📧 Checking for new email subscriptions...")
            self.email_manager.check_subscriptions(self.storage)

        try:
            # 1. Determine time window
            since = self._determine_time_window(force_hours)
            self.console.print(f"📅 Fetching content since: {since.strftime('%Y-%m-%d %H:%M:%S')}\n")

            # 2. Fetch content from all sources
            all_items = await self.fetch_all_sources(since)
            self.console.print(f"📥 Fetched {len(all_items)} items from all sources\n")

            if all_items:
                # 3. Merge cross-source duplicates (same URL from different sources)
                merged_items = self.merge_cross_source_duplicates(all_items)
                removed = len(all_items) - len(merged_items)
                self.console.print(
                    f"🔗 Rule/url dedupe: {len(all_items)} → {len(merged_items)} "
                    f"items ({removed} removed)\n"
                )

                # 4-6. Score, semantically dedupe, and select the final output set.
                # AI score is used only for ranking and to count high-scoring items;
                # it is never a hard cutoff. See _curate_output_items for the rules.
                important_items, curation = await self._curate_output_items(merged_items)
            else:
                self.console.print(
                    "[yellow]No scraped candidates found; generating empty summaries.[/yellow]\n"
                )
                important_items = []
                curation = {"insufficient": False}

            # 6.1 Optional second-stage Twitter reply expansion + targeted re-analysis
            await self._expand_twitter_discussion(important_items)

            # Show per-sub-source selection breakdown
            selected_counts: Dict[str, int] = defaultdict(int)
            for item in important_items:
                key = f"{item.source_type.value}/{self._sub_source_label(item)}"
                selected_counts[key] += 1
            if selected_counts:
                for source_key, count in sorted(selected_counts.items()):
                    self.console.print(f"      • {source_key}: {count}")
                self.console.print("")
                self._log_source_contribution(important_items)

            # 6.2 Enrich ONLY the final output items (2nd AI pass). Keeping this
            # after selection avoids spending local inference on dropped items.
            await self._enrich_important_items(important_items)
            if important_items:
                self._log_enrichment_by_source_quality(important_items)

            # 7. Generate and save daily summaries for each configured language
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for lang in self.config.ai.languages:
                summarizer = DailySummarizer()
                summary = await summarizer.generate_summary(
                    important_items,
                    today,
                    len(all_items),
                    language=lang,
                    insufficient_candidates=curation["insufficient"],
                )

                # Save to data/summaries/
                summary_path = self.storage.save_daily_summary(today, summary, language=lang)
                self.console.print(f"💾 Saved {lang.upper()} summary to: {summary_path}\n")

                # Copy to docs/ for GitHub Pages
                try:
                    from pathlib import Path

                    post_filename = f"{today}-summary-{lang}.md"
                    posts_dir = Path("docs/_posts")
                    posts_dir.mkdir(parents=True, exist_ok=True)

                    dest_path = posts_dir / post_filename

                    # Add Jekyll front matter
                    front_matter = (
                        "---\n"
                        "layout: default\n"
                        f"title: \"Horizon Summary: {today} ({lang.upper()})\"\n"
                        f"date: {today}\n"
                        f"lang: {lang}\n"
                        "---\n\n"
                    )

                    # Strip leading H1 header to avoid duplication with Jekyll title
                    summary_content = summary
                    first_line = summary_content.strip().split("\n")[0]
                    if first_line.startswith("# "):
                        parts = summary_content.split("\n", 1)
                        if len(parts) > 1:
                            summary_content = parts[1].strip()

                    with open(dest_path, "w", encoding="utf-8") as f:
                        f.write(front_matter + summary_content)

                    self.console.print(f"📄 Copied {lang.upper()} summary to GitHub Pages: {dest_path}\n")
                except Exception as e:
                    self.console.print(f"[yellow]⚠️  Failed to copy {lang.upper()} summary to docs/: {e}[/yellow]\n")

                # Send email if configured
                if self.email_manager and self.config.email and self.config.email.enabled:
                    self.console.print(f"📧 Sending {lang.upper()} email summary...")
                    subscribers = self.storage.load_subscribers()
                    subject = f"Horizon Summary ({lang.upper()}) - {today}"
                    self.email_manager.send_daily_summary(summary, subject, subscribers)

                # Send webhook notification if configured
                if self.webhook_notifier:
                    await self.webhook_notifier.send_daily_summary(
                        summary=summary,
                        important_items=important_items,
                        all_items_count=len(all_items),
                        date=today,
                        lang=lang,
                        summarizer=summarizer,
                    )

            self.console.print("[bold green]✅ Horizon completed successfully![/bold green]")
            usage = get_usage_snapshot()
            if usage.total_tokens > 0:
                self.console.print(
                    f"\n🧮 Token usage this run: "
                    f"{usage.total_tokens} tokens "
                    f"(input: {usage.total_input_tokens}, output: {usage.total_output_tokens})"
                )
                for provider, u in sorted(usage.per_provider.items()):
                    if u.total <= 0:
                        continue
                    self.console.print(
                        f"   • {provider}: {u.total} tokens "
                        f"(in: {u.input_tokens}, out: {u.output_tokens})"
                    )

        except Exception as e:
            self.console.print(f"[bold red]❌ Error: {e}[/bold red]")

            # Send webhook failure notification if configured
            if self.webhook_notifier:
                await self.webhook_notifier.send_failure(
                    date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    error_message=str(e),
                )

            raise

    def _determine_time_window(self, force_hours: int = None) -> datetime:
        if force_hours:
            since = datetime.now(timezone.utc) - timedelta(hours=force_hours)
        else:
            hours = self.config.filtering.time_window_hours
            since = datetime.now(timezone.utc) - timedelta(hours=hours)
        return since

    async def fetch_all_sources(self, since: datetime) -> List[ContentItem]:
        """Fetch content from all configured sources.

        This is a stable stage entry point for integrations such as MCP.

        Args:
            since: Fetch items published after this time

        Returns:
            List[ContentItem]: All fetched items
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = []

            # GitHub sources
            if self.config.sources.github:
                github_scraper = GitHubScraper(self.config.sources.github, client)
                tasks.append(self._fetch_with_progress("GitHub", github_scraper, since))

            # Hacker News
            if self.config.sources.hackernews.enabled:
                hn_scraper = HackerNewsScraper(self.config.sources.hackernews, client)
                tasks.append(self._fetch_with_progress("Hacker News", hn_scraper, since))

            # RSS feeds
            if self.config.sources.rss:
                rss_scraper = RSSScraper(self.config.sources.rss, client)
                tasks.append(self._fetch_with_progress("RSS Feeds", rss_scraper, since))

            # Reddit
            if self.config.sources.reddit.enabled:
                reddit_scraper = RedditScraper(self.config.sources.reddit, client)
                tasks.append(self._fetch_with_progress("Reddit", reddit_scraper, since))

            # Telegram
            if self.config.sources.telegram.enabled:
                telegram_scraper = TelegramScraper(self.config.sources.telegram, client)
                tasks.append(self._fetch_with_progress("Telegram", telegram_scraper, since))

            # Twitter
            if self.config.sources.twitter and self.config.sources.twitter.enabled:
                twitter_scraper = TwitterScraper(self.config.sources.twitter, client)
                tasks.append(self._fetch_with_progress("Twitter", twitter_scraper, since))

            # OpenBB (financial news / filings via the OpenBB Platform SDK)
            if self.config.sources.openbb and self.config.sources.openbb.enabled:
                openbb_scraper = OpenBBScraper(self.config.sources.openbb, client)
                tasks.append(self._fetch_with_progress("OpenBB", openbb_scraper, since))

            # OSS Insight trending repos
            if self.config.sources.ossinsight and self.config.sources.ossinsight.enabled:
                oss_scraper = OSSInsightScraper(self.config.sources.ossinsight, client)
                tasks.append(self._fetch_with_progress("OSS Insight", oss_scraper, since))

            # Fetch all concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Flatten results
            all_items = []
            for result in results:
                if isinstance(result, Exception):
                    self.console.print(f"[red]Error fetching source: {result}[/red]")
                elif isinstance(result, list):
                    all_items.extend(result)

            return all_items

    async def _fetch_with_progress(self, name: str, scraper, since: datetime) -> List[ContentItem]:
        """Fetch from a scraper with progress indication.

        Args:
            name: Source name for display
            scraper: Scraper instance
            since: Fetch items after this time

        Returns:
            List[ContentItem]: Fetched items
        """
        self.console.print(f"🔍 Fetching from {name}...")
        items = await scraper.fetch(since)
        self.console.print(f"   Found {len(items)} items from {name}")

        # Show per-sub-source breakdown when there are multiple sub-sources
        sub_counts: Dict[str, int] = defaultdict(int)
        for item in items:
            sub_counts[self._sub_source_label(item)] += 1
        if len(sub_counts) > 1:
            for sub, count in sorted(sub_counts.items()):
                self.console.print(f"      • {sub}: {count}")

        return items

    @staticmethod
    def _sub_source_label(item: ContentItem) -> str:
        """Return a human-readable sub-source label for an item."""
        meta = item.metadata
        if meta.get("subreddit"):
            return f"r/{meta['subreddit']}"
        if meta.get("feed_name"):
            return meta["feed_name"]
        if meta.get("channel"):
            return f"@{meta['channel']}"
        if meta.get("period") and meta.get("repo"):
            return f"ossinsight:{meta.get('primary_language', 'all')}"
        if meta.get("repo"):
            return meta["repo"]
        if meta.get("watchlist"):
            return meta["watchlist"]
        return item.author or "unknown"

    def merge_cross_source_duplicates(self, items: List[ContentItem]) -> List[ContentItem]:
        """Merge items that point to the same URL from different sources.

        This is a stable stage helper for integrations such as MCP.

        Keeps the item with the richest content and combines metadata.

        Args:
            items: Items to deduplicate

        Returns:
            List[ContentItem]: Deduplicated items
        """
        def normalize_url(url: str) -> str:
            parsed = urlparse(str(url))
            # Strip www prefix, trailing slashes, and fragments
            host = parsed.hostname or ""
            if host.startswith("www."):
                host = host[4:]
            path = parsed.path.rstrip("/")
            return f"{host}{path}"

        # Group by normalized URL
        url_groups: Dict[str, List[ContentItem]] = {}
        for item in items:
            key = normalize_url(str(item.url))
            url_groups.setdefault(key, []).append(item)

        merged = []
        for key, group in url_groups.items():
            if len(group) == 1:
                merged.append(group[0])
                continue

            # Pick the item with the richest content as primary
            primary = max(group, key=lambda x: len(x.content or ""))

            # Merge metadata and source info from other items
            all_sources = set()
            for item in group:
                all_sources.add(item.source_type.value)
                # Merge metadata (engagement, discussion, etc.)
                for mk, mv in item.metadata.items():
                    if mk not in primary.metadata or not primary.metadata[mk]:
                        primary.metadata[mk] = mv

                # Append content (e.g., comments from another source)
                if item is not primary and item.content:
                    if primary.content and item.content not in primary.content:
                        primary.content = (primary.content or "") + f"\n\n--- From {item.source_type.value} ---\n" + item.content

            primary.metadata["merged_sources"] = list(all_sources)
            self._apply_source_profile(primary)
            merged.append(primary)

        return merged

    async def merge_topic_duplicates(self, items: List[ContentItem]) -> List[ContentItem]:
        """Merge items covering the same topic using AI semantic deduplication.

        This is a stable stage helper for integrations such as MCP.

        Sends all item titles, tags, and summaries to AI in a single call.
        Items must already be sorted by ranking_score descending so that the first
        item in each duplicate group is always the highest-ranked one.
        Content (comments) from duplicate items is merged into the primary.

        Falls back to returning items unchanged if the AI call fails.
        """
        if len(items) <= 1:
            return items

        from .ai.prompts import TOPIC_DEDUP_SYSTEM, TOPIC_DEDUP_USER
        from .ai.utils import parse_json_response

        # Build the item list for the prompt
        lines = []
        for i, item in enumerate(items):
            tags = ", ".join(item.ai_tags) if item.ai_tags else "—"
            summary = item.ai_summary or "—"
            lines.append(f"[{i}] {item.title}\n    Tags: {tags}\n    Summary: {summary}")
        items_text = "\n\n".join(lines)

        try:
            ai_client = create_ai_client(self.config.ai)
            response = await ai_client.complete(
                system=TOPIC_DEDUP_SYSTEM,
                user=TOPIC_DEDUP_USER.format(items=items_text),
            )
            result = parse_json_response(response)
            if result is None:
                self.console.print("[yellow]  dedup: could not parse AI response, skipping[/yellow]")
                return items

            duplicate_groups = result.get("duplicates", [])
        except Exception as e:
            self.console.print(f"[yellow]  dedup: AI call failed ({e}), skipping[/yellow]")
            return items

        if not duplicate_groups:
            return items

        # Build a set of indices to drop (all non-primary duplicates)
        drop_indices: set[int] = set()
        for group in duplicate_groups:
            if not isinstance(group, list) or len(group) < 2:
                continue
            primary_idx = group[0]
            if primary_idx < 0 or primary_idx >= len(items):
                continue
            primary = items[primary_idx]
            for dup_idx in group[1:]:
                if not isinstance(dup_idx, int) or dup_idx < 0 or dup_idx >= len(items):
                    continue
                if dup_idx == primary_idx:
                    continue
                dup = items[dup_idx]
                # Merge comments/content from the duplicate into the primary
                if dup.content:
                    if not primary.content or dup.content not in primary.content:
                        label = dup.source_type.value
                        primary.content = (primary.content or "") + f"\n\n--- From {label} ---\n{dup.content}"
                self.console.print(
                    f"   [dim]dedup: keep [{primary_idx}] {primary.title}[/dim]\n"
                    f"   [dim]       drop [{dup_idx}] {dup.title}[/dim]"
                )
                drop_indices.add(dup_idx)

        return [item for i, item in enumerate(items) if i not in drop_indices]

    async def _expand_twitter_discussion(self, items: List[ContentItem]) -> None:
        """Second-stage: fetch reply text for important Twitter items and re-analyze.

        Only runs when sources.twitter.fetch_reply_text is True.
        Bounded by max_tweets_to_expand to control cost.
        """
        tw_cfg = self.config.sources.twitter
        if not tw_cfg or not tw_cfg.enabled or not tw_cfg.fetch_reply_text:
            return

        from .models import SourceType

        twitter_items = [
            item for item in items
            if item.source_type == SourceType.TWITTER
        ][:tw_cfg.max_tweets_to_expand]

        if not twitter_items:
            return

        self.console.print(
            f"💬 Fetching reply text for {len(twitter_items)} Twitter items..."
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            scraper = TwitterScraper(tw_cfg, client)
            expanded = []
            for item in twitter_items:
                try:
                    reply_lines = await scraper.fetch_replies_for_item(item)
                    if TwitterScraper.append_discussion_content(item, reply_lines):
                        expanded.append(item)
                        self.console.print(
                            f"   💬 {len(reply_lines)} replies added to: {item.title[:60]}"
                        )
                except Exception as exc:
                    self.console.print(
                        f"   [yellow]⚠️  Reply fetch failed for {item.id}: {exc}[/yellow]"
                    )

        if not expanded:
            return

        self.console.print(
            f"   Re-analyzing {len(expanded)} Twitter items with reply context...\n"
        )
        ai_client = create_ai_client(self.config.ai)
        analyzer = ContentAnalyzer(ai_client)
        await analyzer.analyze_batch(expanded)

    async def _enrich_important_items(self, items: List[ContentItem]) -> None:
        """Enrich items with background knowledge (2nd AI pass).

        Enrichment mode is configurable so local LLM runs can avoid spending
        long background-generation calls on lower-priority filler items.

        Args:
            items: Important items to enrich (modified in-place)
        """
        if not items:
            self.console.print("📚 Enrichment skipped: 0 final output items\n")
            return

        ai_config = self.config.ai
        mode = ai_config.enrichment_mode
        timeout = ai_config.enrichment_timeout_seconds
        self.console.print(
            f"📚 Enrichment mode: {mode} "
            f"(timeout {timeout}s, full≥{ai_config.enrichment_full_threshold}, "
            f"brief≥{ai_config.enrichment_brief_threshold}, "
            f"max_full={ai_config.enrichment_max_full_items})"
        )

        if mode == "none":
            for item in items:
                item.metadata["enrichment_mode"] = "none"
            self.console.print(
                f"📚 Enrichment skipped by config: {len(items)} final output items\n"
            )
            self.console.print(
                "   full_enrichment_count=0, brief_enrichment_count=0, "
                f"skipped_enrichment_count={len(items)}\n"
            )
            return

        ai_client = create_ai_client(self.config.ai)
        enricher = ContentEnricher(ai_client)

        if mode == "full":
            self.console.print(
                f"📚 Enriching {len(items)} final output items with full background knowledge..."
            )
            await enricher.enrich_batch(items, mode="full", timeout_seconds=timeout)
            self.console.print(
                f"   full_enrichment_count={len(items)}, brief_enrichment_count=0, "
                "skipped_enrichment_count=0\n"
            )
            return

        if mode == "brief":
            self.console.print(
                f"📚 Brief-enriching {len(items)} final output items..."
            )
            await enricher.enrich_batch(items, mode="brief", timeout_seconds=timeout)
            self.console.print(
                f"   full_enrichment_count=0, brief_enrichment_count={len(items)}, "
                "skipped_enrichment_count=0\n"
            )
            return

        full_items, brief_items, skipped_items = self._split_enrichment_tiers(items)
        for item in skipped_items:
            item.metadata["enrichment_mode"] = "none"

        self.console.print(
            f"📚 Tiered enrichment: full={len(full_items)}, brief={len(brief_items)}, "
            f"skipped={len(skipped_items)}\n"
        )
        if full_items:
            await enricher.enrich_batch(full_items, mode="full", timeout_seconds=timeout)
        if brief_items:
            await enricher.enrich_batch(brief_items, mode="brief", timeout_seconds=timeout)

        self.console.print(
            f"   full_enrichment_count={len(full_items)}, "
            f"brief_enrichment_count={len(brief_items)}, "
            f"skipped_enrichment_count={len(skipped_items)}\n"
        )

    def _split_enrichment_tiers(
        self, items: List[ContentItem]
    ) -> tuple[List[ContentItem], List[ContentItem], List[ContentItem]]:
        """Split final output items into full, brief, and skipped enrichment tiers."""
        ai_config = self.config.ai
        ranked = sorted(items, key=lambda item: item.ai_score or 0, reverse=True)
        full_items: List[ContentItem] = []
        brief_items: List[ContentItem] = []
        skipped_items: List[ContentItem] = []

        for item in ranked:
            score = item.ai_score or 0
            if (
                score >= ai_config.enrichment_full_threshold
                and len(full_items) < ai_config.enrichment_max_full_items
            ):
                full_items.append(item)
            elif score >= ai_config.enrichment_brief_threshold:
                brief_items.append(item)
            else:
                skipped_items.append(item)

        return full_items, brief_items, skipped_items

    async def _analyze_content(self, items: List[ContentItem]) -> List[ContentItem]:
        """Analyze content items with AI.

        Args:
            items: Items to analyze

        Returns:
            List[ContentItem]: Analyzed items
        """
        self.console.print("🤖 Analyzing content with AI...")

        ai_client = create_ai_client(self.config.ai)
        analyzer = ContentAnalyzer(ai_client)

        return await analyzer.analyze_batch(items)

    @staticmethod
    def _source_profile(item: ContentItem) -> tuple[str, float]:
        quality = item.metadata.get("source_quality") or "medium"
        if quality not in DEFAULT_SOURCE_WEIGHTS:
            quality = "medium"
        raw_weight = item.metadata.get("source_weight")
        if raw_weight is None:
            weight = DEFAULT_SOURCE_WEIGHTS[quality]
        else:
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError):
                weight = DEFAULT_SOURCE_WEIGHTS[quality]
        weight = max(-1.0, min(1.0, weight))
        return quality, weight

    @classmethod
    def _apply_source_profile(cls, item: ContentItem) -> None:
        quality, weight = cls._source_profile(item)
        item.metadata["source_quality"] = quality
        item.metadata["source_weight"] = weight
        item.metadata["ranking_score"] = cls._ranking_score(item)

    @classmethod
    def _ranking_score(cls, item: ContentItem) -> float:
        _, weight = cls._source_profile(item)
        return float(item.ai_score or 0.0) + weight

    @classmethod
    def _rank_items(cls, items: List[ContentItem]) -> List[ContentItem]:
        for item in items:
            cls._apply_source_profile(item)
        return sorted(
            items,
            key=lambda item: (
                cls._ranking_score(item),
                item.ai_score or 0.0,
                item.metadata.get("source_priority", 0) or 0,
            ),
            reverse=True,
        )

    def _log_ranking_details(self, items: List[ContentItem]) -> None:
        if not items:
            return
        self.console.print("📈 Ranking details:")
        for item in items:
            quality, weight = self._source_profile(item)
            self.console.print(
                f"      • {item.source_type.value}/{self._sub_source_label(item)}: "
                f"ai_score={item.ai_score or 0:.1f}, "
                f"source_quality={quality}, source_weight={weight:+.1f}, "
                f"ranking_score={self._ranking_score(item):.1f}"
            )
        self.console.print("")

    def _log_source_contribution(self, items: List[ContentItem]) -> None:
        by_source: Dict[str, Dict[str, float]] = defaultdict(lambda: {"count": 0, "score": 0.0})
        by_quality: Dict[str, Dict[str, float]] = defaultdict(lambda: {"count": 0, "score": 0.0})
        for item in items:
            self._apply_source_profile(item)
            score = float(item.ai_score or 0.0)
            source_key = f"{item.source_type.value}/{self._sub_source_label(item)}"
            quality = item.metadata.get("source_quality", "medium")
            by_source[source_key]["count"] += 1
            by_source[source_key]["score"] += score
            by_quality[quality]["count"] += 1
            by_quality[quality]["score"] += score

        self.console.print("📊 Source contribution by source:")
        for source_key, data in sorted(by_source.items()):
            avg = data["score"] / data["count"] if data["count"] else 0
            self.console.print(
                f"      • {source_key}: {int(data['count'])} items, avg score {avg:.1f}"
            )
        self.console.print("📊 Source contribution by source_quality:")
        for quality in ["high", "medium", "low"]:
            data = by_quality.get(quality, {"count": 0, "score": 0.0})
            avg = data["score"] / data["count"] if data["count"] else 0
            self.console.print(
                f"      • {quality}: {int(data['count'])} items, avg score {avg:.1f}"
            )
        self.console.print("")

    def _log_enrichment_by_source_quality(self, items: List[ContentItem]) -> None:
        counts: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"full": 0, "brief": 0, "skipped": 0}
        )
        for item in items:
            quality, _ = self._source_profile(item)
            mode = item.metadata.get("enrichment_mode") or "none"
            if mode == "full":
                counts[quality]["full"] += 1
            elif mode == "brief":
                counts[quality]["brief"] += 1
            else:
                counts[quality]["skipped"] += 1

        self.console.print("📊 Enrichment by source_quality:")
        for quality in ["high", "medium", "low"]:
            data = counts.get(quality, {"full": 0, "brief": 0, "skipped": 0})
            self.console.print(
                f"      • {quality}: full={data['full']}, "
                f"brief={data['brief']}, skipped={data['skipped']}"
            )
        self.console.print("")

    @staticmethod
    def _select_output_items(
        candidates: List[ContentItem],
        *,
        threshold: float,
        min_items: int,
        max_items: int,
    ) -> tuple[List[ContentItem], int]:
        """Pick the final output set by score rank.

        Score ranks items and counts high-scoring ones; it never hard-filters.

            high_score_count = items with ai_score >= threshold
            output_count = min(max_items, max(min_items, high_score_count))
            output_count = min(output_count, len(candidates))

        So we emit at least `min_items` when enough candidates exist, expand up
        to `max_items` when many items score high, and emit every candidate when
        fewer than `min_items` are available. Returns (output_items, high_score_count).
        """
        ranked = HorizonOrchestrator._rank_items(candidates)
        high_score_count = sum(
            1 for it in ranked if it.ai_score is not None and it.ai_score >= threshold
        )
        output_count = min(max_items, max(min_items, high_score_count))
        output_count = min(output_count, len(ranked))
        return ranked[:output_count], high_score_count

    async def _curate_output_items(
        self, merged_items: List[ContentItem]
    ) -> tuple[List[ContentItem], Dict[str, int | bool]]:
        """Score, dedupe, and select the items that go into the report.

        Pipeline: AI score -> rank by score -> take a bounded candidate pool ->
        semantic dedupe -> select top N (min/max). Score is never a hard cutoff,
        so an empty report is produced only when there are zero candidates.

        Returns (output_items, stats) where stats carries the counts logged below
        plus an `insufficient` flag (True when fewer candidates than the minimum
        output size were available).
        """
        filtering = self.config.filtering
        threshold = filtering.ai_score_threshold
        min_items = filtering.min_items_per_report
        max_items = filtering.max_items_per_report
        candidate_limit = filtering.semantic_dedupe_candidate_limit
        self.console.print(
            f"⚙️  Selection config: threshold={threshold}, "
            f"min_items={min_items}, max_items={max_items}, "
            f"semantic_dedupe_candidate_limit={candidate_limit}\n"
        )

        # 4. Analyze with AI (every item is scored)
        scored_items = await self._analyze_content(merged_items)
        high_score_total = sum(
            1 for it in scored_items if it.ai_score is not None and it.ai_score >= threshold
        )
        self.console.print(
            f"🤖 Scored {len(scored_items)} items "
            f"({high_score_total} scored ≥ {threshold})\n"
        )

        # 5. Rank by score, then take a bounded pool for semantic dedup so we
        # never send an unbounded prompt to a slow local model.
        ranked_items = self._rank_items(scored_items)
        candidate_pool = ranked_items[:candidate_limit]
        self.console.print(
            f"🎯 Semantic-dedup candidate pool: {len(candidate_pool)} "
            f"(limit {candidate_limit})\n"
        )

        # 5.5 Semantic deduplication on the candidate pool
        deduped_items = await self.merge_topic_duplicates(candidate_pool)
        self.console.print(f"🧹 Semantic dedupe: {len(candidate_pool)} → {len(deduped_items)} candidates\n")
        if len(deduped_items) < len(candidate_pool):
            self.console.print(
                f"🧹 Removed {len(candidate_pool) - len(deduped_items)} topic duplicates "
                f"→ {len(deduped_items)} unique candidates\n"
            )

        # 6. Select the final output set (score ranks, never hard-filters)
        output_items, high_score_count = self._select_output_items(
            deduped_items,
            threshold=threshold,
            min_items=min_items,
            max_items=max_items,
        )
        insufficient = 0 < len(deduped_items) < min_items

        self.console.print(
            f"📊 Output: {len(output_items)} items selected "
            f"(high-score {high_score_count}, candidates {len(deduped_items)}, "
            f"min {min_items}/max {max_items})\n"
        )
        self._log_ranking_details(output_items)
        if insufficient:
            self.console.print(
                "[yellow]⚠️  Fewer candidates than the minimum output size; "
                "emitting all available candidates.[/yellow]\n"
            )

        stats: Dict[str, int | bool] = {
            "scored_count": len(scored_items),
            "high_score_count": high_score_count,
            "candidate_pool_count": len(candidate_pool),
            "deduped_count": len(deduped_items),
            "output_count": len(output_items),
            "min_items": min_items,
            "max_items": max_items,
            "insufficient": insufficient,
        }
        return output_items, stats

    async def _generate_summary(
        self,
        items: List[ContentItem],
        date: str,
        total_fetched: int,
        language: str = "en",
    ) -> str:
        """Generate daily summary.

        Args:
            items: Important items to include (already enriched with background/related)
            date: Date string
            total_fetched: Total items fetched
            language: Output language ("en" or "zh")

        Returns:
            str: Markdown summary
        """
        self.console.print("📝 Generating daily summary...")

        summarizer = DailySummarizer()

        return await summarizer.generate_summary(items, date, total_fetched, language=language)
