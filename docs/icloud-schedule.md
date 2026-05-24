# iCloud Morning/Evening Schedule

This guide shows how to export Horizon's local daily Markdown reports to an iCloud Drive directory. It does not require email or webhook delivery.

## Output Directory

Default output directory:

```text
/Users/chenxin/Library/Mobile Documents/com~apple~CloudDocs/1、iCloud work/AI（iCloud）/ai-news-radar
```

Each day can have two main Chinese reports:

- `daily/YYYY-MM-DD/YYYY-MM-DD-HHMM-morning-zh.md`
- `daily/YYYY-MM-DD/YYYY-MM-DD-HHMM-evening-zh.md`

English reports are exported too:

- `daily/YYYY-MM-DD/YYYY-MM-DD-HHMM-morning-en.md`
- `daily/YYYY-MM-DD/YYYY-MM-DD-HHMM-evening-en.md`

If the exact `YYYY-MM-DD-HHMM-slot-lang.md` file already exists, the script
does not overwrite it. It falls back to a unique seconds-level filename such as
`YYYY-MM-DD-HHMMSS-evening-zh.md`, then adds a numeric suffix if needed.

`latest.md` is not updated by default because launchd may be allowed to create
new iCloud files but denied when replacing existing files. Enable it only for a
manual run when you know the current process has iCloud replacement permission:

```bash
HORIZON_UPDATE_LATEST=1 ./scripts/run-and-export-icloud.sh 12 evening
```

GitHub Pages post files are not copied by default. To export them too, set:

```bash
HORIZON_EXPORT_POSTS=1 ./scripts/run-and-export-icloud.sh 12 evening
```

Posts are written append-only under:

```text
posts/YYYY-MM-DD/
```

Older root-level files are not migrated automatically. You can move or archive
them manually in Finder after confirming the new `daily/YYYY-MM-DD/` layout.

The launchd environment is smaller than an interactive terminal environment.
The export script therefore loads the project `.env` automatically, checks the
local Ollama OpenAI-compatible endpoint, verifies that `qwen2.5:14b` is
available, and refuses to copy Markdown files whose modification time is older
than the current run. This prevents a failed scheduled run from re-exporting
stale reports.

The script also checks that the iCloud output directories can accept new files
before starting Horizon. The preflight is append-only: it creates a unique test
file and warns if cleanup fails, but it does not test replacement of existing
files because iCloud may deny that operation under launchd.

If copying a report or writing `latest.md` fails with `Operation not permitted`,
the export exits non-zero and does not print `iCloud export completed`. Check
macOS Privacy & Security settings:

- System Settings → Privacy & Security → Files and Folders → grant Terminal / iTerm access to iCloud Drive
- System Settings → Privacy & Security → Full Disk Access → add Terminal / iTerm if Files and Folders alone is not enough
- Confirm iCloud Drive is enabled in System Settings → Apple Account → iCloud → iCloud Drive
- For launchd-triggered runs, the process running the plist may need the same access; granting Terminal usually covers it

Manual write permission test:

```bash
ICLOUD_DIR="/Users/chenxin/Library/Mobile Documents/com~apple~CloudDocs/1、iCloud work/AI（iCloud）/ai-news-radar"
echo test > "$ICLOUD_DIR/permission-test.txt"
cat "$ICLOUD_DIR/permission-test.txt"
rm "$ICLOUD_DIR/permission-test.txt"
```

If the `echo` step fails with `Operation not permitted`, the iCloud directory is not writable from the current process and the launchd job will fail at the preflight check before running Horizon.

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

Manually update `latest.md` for a one-off run:

```bash
HORIZON_UPDATE_LATEST=1 ./scripts/run-and-export-icloud.sh 12 evening
```

Export GitHub Pages post files as well:

```bash
HORIZON_EXPORT_POSTS=1 ./scripts/run-and-export-icloud.sh 12 evening
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

Trigger a run manually without waiting for the schedule:

```bash
launchctl kickstart -k gui/$(id -u)/com.horizon.daily.icloud
```

View logs:

```bash
tail -f logs/horizon-icloud.out.log
tail -f logs/horizon-icloud.err.log
```

Each run also writes a timestamped log:

```bash
ls -lt logs/horizon-icloud-*.log
tail -f logs/horizon-icloud-YYYYMMDD-HHMMSS.log
```

Before installing or after changing paths, run a full manual test:

```bash
./scripts/run-and-export-icloud.sh 12 morning
```

Notes:

- The template is not installed automatically.
- Email and webhook delivery are not needed.
- iCloud Drive syncs the exported Markdown files.
- Scheduled runs use append-only filenames under `daily/YYYY-MM-DD/`.
- Re-running the same date and slot creates another uniquely named file instead of overwriting existing files.
- `latest.md` and `posts/` export are opt-in through `HORIZON_UPDATE_LATEST=1` and `HORIZON_EXPORT_POSTS=1`.
