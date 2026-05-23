# iCloud Morning/Evening Schedule

This guide shows how to export Horizon's local daily Markdown reports to an iCloud Drive directory. It does not require email or webhook delivery.

## Output Directory

Default output directory:

```text
/Users/chenxin/Library/Mobile Documents/com~apple~CloudDocs/1、iCloud work/AI（iCloud）/ai-news-radar
```

Each day can have two main Chinese reports:

- `YYYY-MM-DD-morning-zh.md`
- `YYYY-MM-DD-evening-zh.md`

English reports are exported too:

- `YYYY-MM-DD-morning-en.md`
- `YYYY-MM-DD-evening-en.md`

The script also updates `latest.md` and copies GitHub Pages post files into `posts/`.

## Manual Runs

Generate a morning report:

```bash
./scripts/run-and-export-icloud.sh 12 morning
```

Generate an evening report:

```bash
./scripts/run-and-export-icloud.sh 12 evening
```

Let the script choose the slot from local time:

```bash
./scripts/run-and-export-icloud.sh
```

Use a custom iCloud/output directory:

```bash
HORIZON_ICLOUD_DIR="/path/to/dir" ./scripts/run-and-export-icloud.sh 12 morning
```

The default model remains `qwen2.5:14b` with `enrichment_mode=tiered` and `enable_thinking=false`.

## launchd Schedule

A template is provided at:

```text
scripts/com.horizon.daily.icloud.plist.example
```

It runs Horizon twice a day:

- 08:30
- 20:30

Install manually:

```bash
cp scripts/com.horizon.daily.icloud.plist.example ~/Library/LaunchAgents/com.horizon.daily.icloud.plist
launchctl load ~/Library/LaunchAgents/com.horizon.daily.icloud.plist
```

Unload and remove:

```bash
launchctl unload ~/Library/LaunchAgents/com.horizon.daily.icloud.plist
rm ~/Library/LaunchAgents/com.horizon.daily.icloud.plist
```

View logs:

```bash
tail -f logs/horizon-icloud.out.log
tail -f logs/horizon-icloud.err.log
```

Notes:

- The template is not installed automatically.
- Email and webhook delivery are not needed.
- iCloud Drive syncs the exported Markdown files.
- Re-running the same date and slot overwrites that slot's files, but historical dates are not deleted.
