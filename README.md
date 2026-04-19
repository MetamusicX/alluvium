# Alluvium

**Flow becomes knowledge**

---

### What it is

Alluvium operates like a perceptual system.

As we move through the world, our senses and mind continuously register impressions—external events, internal thoughts, fleeting ideas, emotions. This stream is not pre-organized; it is dense, heterogeneous, and often chaotic. Yet, over time, the brain does not store this as noise. It differentiates, associates, and stabilizes patterns, gradually forming a structured understanding of the world.

Alluvium mirrors this process.

It captures experience as it unfolds—moment by moment—without requiring any prior structure. From this continuous flow, the system progressively extracts, organizes, and stabilizes elements into a coherent and navigable knowledge landscape.

---

### What it does

- Captures your day as a **continuous stream of thought**
- Extracts people, ideas, projects, and concepts automatically
- Generates structured knowledge from raw input
- Preserves the original flow while building an organized system

---

### Core principles

- **A flow writing itself continuously**
    No interruption, no fragmentation—just time unfolding into inscription

- **Flow becomes structure through time**
    Organization is not imposed; it crystallizes

- **No prior taxonomy**
    You do not classify—structure emerges from the material itself

- **Pure alluvial logic**
    Deposition, differentiation, accumulation

- **From nothing to terrain**
    You start with a blank page; within days, a structured landscape appears

- **The end of folders and tags**
    The system organizes itself—precisely, dynamically, continuously

---

### In one sentence

Alluvium is more than a notebook—it is a terrain formed by the continuous deposition of experience in time.

---

## How it works

```
You write    → Journal/2026-04-17.md
Extract      → concepts, people, ideas, tasks, reflections
Cluster      → notes sort into emergent subfolders with Maps of Content
Ripple       → new notes send connections backward through the entire vault
```

1. **Write** your daily journal in `Journal/YYYY-MM-DD.md` — freely, without structure
2. **Run** the processor: `python process_journal.py`
3. **Browse** the results in Obsidian — atomic notes, linked and tagged, with an emergent graph

The system uses Claude's API to read your journal entry and extract distinct items — each becomes its own Obsidian-compatible markdown file. Over time, as the same people, projects, and ideas recur across entries, a knowledge landscape emerges without you ever tagging, categorizing, or filing anything.

## Folder structure

```
Alluvium/
├── 00 Journal/                   ← Your daily entries (you write here)
├── 01 Inbox/                     ← New extracted notes land here
├── 1 Projects/                   ← Active efforts with deliverables
├── 2 Areas/                      ← Ongoing responsibilities
├── 3 Resources/                  ← Reference material, topics of interest
├── 4 Archive/                    ← Completed or inactive items
├── Day Summaries/                ← De-fragmented daily summaries
├── Authors/                      ← Creators: writers, philosophers, composers
├── People/                       ← Friends, family, colleagues
├── config.yaml                   ← Domain context map (customize to your life)
├── process_journal.py            ← Extraction engine
├── cluster_notes.py              ← PARA clustering engine
├── ripple.py                     ← Knowledge compounding engine
├── summarize.py                  ← Daily summary generator (+ Day One)
├── setup.sh                      ← Setup and scheduling script
├── com.alluvium.process.plist    ← macOS LaunchAgent template
└── README.md
```

## Generated note format

Each extracted note is an Obsidian-compatible markdown file:

```yaml
---
title: "Note Title"
aliases: []
date_created: 2026-04-17
date_modified: 2026-04-17
type: event | idea | task | reflection | practice-log | meeting | reading | person
domain: work | writing | sport | personal
tags:
  - emergent-tag
source_entries:
  - "[[2026-04-17]]"
related:
  - "[[Related Note]]"
status: active
---

Note content with [[wikilinks]] to other notes.

Extracted from [[2026-04-17]].
```

## Setup

### Requirements

- Python 3.8+
- An [Anthropic API key](https://console.anthropic.com/)
- [Obsidian](https://obsidian.md/) (for browsing the knowledge graph)

### Quick setup

```bash
# Clone the repository
git clone https://github.com/MetamusicX/alluvium.git
cd alluvium

# Run the setup script (installs dependencies, configures daily auto-processing)
bash setup.sh
```

The setup script will:
- Install Python dependencies (`anthropic`, `pyyaml`)
- Ask for your Anthropic API key
- Ask what time you want daily auto-processing (default: 9 PM)
- Install a macOS LaunchAgent that runs the processor automatically every day

### Manual setup

If you prefer to set things up yourself:

```bash
pip install anthropic pyyaml
export ANTHROPIC_API_KEY="your-key-here"
```

### Configure your domains

Edit `config.yaml` to reflect your own life domains. The system uses these to intelligently categorize extracted notes without you ever having to tag anything:

```yaml
domains:
  work:
    name: My Job
    keywords: [office, meeting, project, deadline]
    description: Main professional work

  writing:
    name: Writing Projects
    keywords: [article, book, draft, manuscript]
    description: Writing and publishing

  sport:
    name: Training
    keywords: [run, swim, bike, workout, gym]
    description: Athletic training
```

Add as many domains as you need. The system will learn to route your thoughts to the right place.

## Usage

```bash
# Process today's journal
python process_journal.py

# Process a specific date
python process_journal.py 2026-04-17
```

### Daily auto-processing

Alluvium automatically processes your journal every day at the time you choose during setup. A macOS LaunchAgent (`com.alluvium.process.plist`) runs the extraction and deposits your notes while you sleep — or whenever you set it.

To change the processing time, re-run `bash setup.sh`.

You can also process manually at any time:

```bash
python process_journal.py           # today
python process_journal.py 2026-04-17  # specific date
```

### Auto-launch (macOS)

The included `open-today.sh` script creates today's journal file and opens it in Obsidian. Add it as a macOS Login Item to start every morning with a blank page ready for writing.

### Open as Obsidian vault

Open the `Alluvium/` folder as an Obsidian vault. Your daily journal entries and all extracted notes live in the same vault — write, process, and browse in one place.

### Emergent clustering

After each processing run, Alluvium automatically scans all notes and identifies natural clusters — groups of notes that share enough in common to deserve their own subfolder. When a cluster reaches a threshold (3+ notes), the system:

1. Creates a subfolder inside `Notes/`
2. Moves related notes into it
3. Generates a **Map of Content** — an index note linking to everything in the cluster

```
Notes/
├── Training Log/
│   ├── _Training Log.md          ← Map of Content (auto-generated)
│   ├── 12km-easy-run.md
│   ├── morning-swim.md
│   └── bike-intervals.md
├── ERC Evaluations/
│   ├── _ERC Evaluations.md       ← Map of Content
│   ├── panel-review-split.md
│   └── ...
├── emergent-structure-idea.md     ← not yet clustered
```

You never create a folder. You never move a file. The terrain forms its own ridges.

You can also run clustering independently: `python cluster_notes.py`

### Knowledge compounding (Ripple Engine)

After each processing run, the ripple engine propagates new knowledge backward through the entire vault. Each new note sends connections into existing notes — cross-references, enrichments, insights, and evolution markers.

This is what makes Alluvium a second brain rather than a filing system:

- **Cross-references** — new notes link to existing topics they relate to
- **Enrichment** — existing notes gain new context from today's thinking
- **Cross-domain insights** — a concept from one domain illuminates another (a training principle that parallels a compositional technique)
- **Evolution tracking** — plans link to their outcomes, ideas link to their developments

Over weeks and months, the vault becomes dense with connections you never created manually. Knowledge compounds.

You can also run the ripple engine independently: `python ripple.py`

### Daily summary (+ Day One integration)

After rippling, Alluvium generates a structured summary of your day — de-fragmented by theme, not chronology. Training notes from morning and evening merge into one section. Scattered work mentions consolidate under one heading.

The summary is saved to `Day Summaries/` and automatically sent to **Day One** (if installed) with full markdown formatting. Your daily journal and your knowledge vault close the loop without copy-pasting.

You can also generate a summary independently: `python summarize.py` or for a specific date: `python summarize.py 2026-04-17`

### Voice input

Alluvium accepts any text — including dictated text. Use any dictation tool (Wispr Flow, macOS Dictation, or similar) to speak directly into your journal file. There is nothing to configure; the input is just markdown.

## The shift from PKM to PKA

Traditional Personal Knowledge Management asks you to be the librarian of your own mind — tagging, filing, linking, organizing. Sooner or later, the system collapses under its own weight.

Alluvium proposes a different model: a **Personal Knowledge Assistant**. You write freely. The AI handles the bookkeeping. Structure is not something you impose — it is something that emerges from the continuous accumulation of your experience.

## Tech stack

- **Python 3** — processing script
- **Anthropic Claude API** — concept extraction
- **Obsidian** — reading, browsing, graph view
- **Markdown + YAML** — universal, portable, future-proof

## Try it in your browser

Don't want to install anything? Try the web version:

**[alluvium-flow.netlify.app](https://alluvium-flow.netlify.app/)**

Write or speak in your own language. Alluvium will understand you.

Bring your own Anthropic API key — it stays in your browser and is never sent to any server.

## License

MIT

---

*Alluvium was built by [Paulo de Assis](https://github.com/MetamusicX) with Claude Code.*
