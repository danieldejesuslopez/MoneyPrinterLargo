# MoneyPrinter Brain

The learning layer over the generation pipeline. Brain does not generate
anything: it watches what the pipeline produces, learns from what the
audience did with it, and — once it has earned the right — reorders the
choices the pipeline was going to make anyway.

Nothing in `src/brain/` imports MoviePy, Selenium or the LLM providers, and
nothing outside it has to change for it to run. It is stdlib-only.

---

## Quick start

```bash
python scripts/brain.py status      # what Brain knows right now
python scripts/brain.py gaps        # what to start recording
python scripts/brain.py snapshot    # record today's metrics (run daily)
python scripts/brain.py dataset     # rebuild the training set
python scripts/brain.py train       # fit a challenger, promote if it earns it
python scripts/brain.py backtest    # would Brain have chosen better?
python scripts/brain.py rank "topic a" "topic b" --account <uuid>
```

The daily loop is `snapshot` then `dataset`. The weekly one adds `train`.
Every command takes `--json` for scripted use.

On a fresh install every one of these works and reports honestly that there
is nothing to learn from yet. That is the intended starting state.

---

## Layering

```
data/         read .mp/youtube.json, snapshot metrics, build the dataset
features/     candidate -> numbers, with a hard pre-publish boundary
models/       the rule table, ridge heads, the versioned model registry
inference/    predict, rank, explain
training/     fit, evaluate, backtest, promote
memory/       per-channel statistical profile
experiments/  the decision ledger that makes shadow mode measurable
```

Public API — every entry point is safe to call inside a generation run:

```python
import brain

brain.status(account_id)                  # what Brain currently knows
brain.rank_topics(account_id, topics)     # score a candidate set
brain.suggest_topic(account_id, topics)   # one pick, or "" if unsure
brain.observe()                           # snapshot today's metrics
brain.briefing(account_id)                # prompt-ready channel facts
```

---

## The rules it will not bend

These are what make the numbers mean something.

**No leakage.** A feature must declare when it becomes knowable.
`features/base.py` has two availabilities, and only `PRE_PUBLISH` features
are ever vectorized. Channel-level features read
`ChannelContext.before(moment)`, so a row for a video published in March
cannot be influenced by an April hit. `tests/brain/test_no_leakage.py`
proves it three ways, including empirically: every result metric is
multiplied by 37 and the feature vector must not move.

**No training on immature labels.** A video measured two hours after upload
has not finished happening. Maturity is measured at the last metrics *sync*,
not at "now" — a year-old video whose stats were scraped an hour after
publishing still only carries one-hour numbers.

**No head without data.** A target with fewer than 30 real labels gets no
head at all, and `predict` returns nothing for it rather than a
confident-looking zero. There is no CTR head in this repo today because
there is no CTR data.

**No promotion on training loss.** A challenger has to beat the champion on
held-out *ranking* quality and regress no head, or the champion stays
(`models/registry.py`). Ranking is what Brain is used for; a model that fits
better and orders worse is a downgrade.

**No random splits.** Train on older videos, validate on newer ones. The
environment moves — the algorithm, the audience, your own prompt tweaks — so
a model must be judged on videos published after the ones it learned from.

**No deciding when unsure.** Below the confidence floor Brain abstains and
the pipeline's own choice stands.

---

## What it looks at

87 features across seven groups, all interpretable:

| group | what it measures |
|---|---|
| `text` | headline and hook shape: curiosity gap, specificity, length, numbers, second person, whether the hook names its own subject |
| `script` | narration structure: pacing against target duration, fact density, reveal rhythm, filler, sentence-length variance, seconds to the first concrete figure |
| `plan` | format, duration, cost, and the scores `RetentionLab` already produced |
| `temporal` | publication hour and weekday, cyclically encoded |
| `channel` | size, cadence, momentum, and novelty against this channel's own history, winners and losers |
| `thumbnail` | brightness, contrast, saturation, busyness, plus composition: central-vs-spread detail, top-heaviness, skin-tone share, text bands |

Deliberately no sentence embeddings yet. 384 dense dimensions over a few
hundred videos is more parameters than signal, and none of them can be read
back as "why this video". `features/text_embeddings.py` can be added later
without changing the contract.

---

## What it predicts

One head per target, trained only where labels exist:

`normalized_views` (views as a multiple of the channel's recent median),
`retention`, `watch_minutes`, `like_rate`, `comment_rate`, `ctr`,
`subscriber_rate`.

Raw view counts are never a target. A 3,000-view video is a hit on a channel
whose median is 800 and a failure on one whose median is 20,000, so every
label is relative to *the videos published before it on the same channel*.

The score you see is a blend of four independent opinions
(`inference/predictor.py`):

- **neural** — the trained heads, converted to a reward and scaled 0-10
- **heuristic** — Brain's own rule table, which works from row zero
- **rules** — the `RetentionLab` scores this candidate already carries
- **novelty** — distance from what the channel already published

Components that cannot be computed drop out and the rest are renormalized,
so a topic-stage candidate with no script and no model still gets a
meaningful score — just with lower confidence.

---

## Configuration

All optional, under `"brain"` in `config.json`. See `config.example.json`.

```json
"brain": {
    "enabled": true,
    "mode": "shadow",
    "abstain_confidence": 0.45,
    "diversity_threshold": 0.72,
    "reward_weights": { "retention": 0.25, "...": 0 },
    "hybrid_weights": { "neural": 0.65, "...": 0 },
    "channels": { "<account_uuid>": { "mode": "advisory" } }
}
```

Per-channel entries override the global ones key by key, so a channel that
lives on watch time can weight it heavily without touching the rest.
`MP_BRAIN_MODE` and `MP_BRAIN_ENABLED` in the environment win over the file.

### Modes

| mode | what Brain is allowed to do |
|---|---|
| `off` | nothing but snapshots |
| `shadow` | predict and log; never influence *(default)* |
| `advisory` | recommend; the caller may ignore it |
| `assisted` | filter and rerank candidate sets |
| `auto` | pick, above the confidence floor |
| `aggressive` | pick and run its own experiments |

`suggest_topic` returns `""` in every non-deciding mode, when Brain is
disabled, and whenever confidence is under the floor — so the caller's
fallback is always its existing behaviour.

---

## Wiring it into the pipeline

Not done yet, deliberately: in `shadow` mode the call is a no-op, and
`src/classes/YouTube.py` is 9,000 lines with work in flight. When you want
it, the integration is three lines at the end of `generate_topic`:

```python
# after the pipeline has chosen `completion`
import brain
brain.rank_topics(self._account_uuid, [completion] + rejected,
                  pipeline_choice=completion)
choice = brain.suggest_topic(self._account_uuid, [completion] + rejected,
                             pipeline_choice=completion)
completion = choice or completion
```

The first call logs what Brain thought (shadow mode's whole point); the
second changes the outcome only in a deciding mode above the confidence
floor. Both swallow their own errors.

The other integration worth doing is calling `brain.observe()` wherever
stats are synced (`webapp/api/auto_sync.py`, `scripts/sync_youtube_cache.py`)
— or just scheduling `python scripts/brain.py snapshot` daily.

---

## Measuring whether it works

```bash
python scripts/brain.py backtest --predictive
```

One video at a time: fit on everything published before it, predict it, keep
the residual. Residuals are pooled into a single RMSE rather than averaged
per batch — averaging weights a three-video batch like a thirty-video one and
lets one outlier batch write the headline.

### Two null models, both reported

- **The historical mean.** Carry forward the average of everything published
  so far. This is the classical null and the one the head-selection gate uses.
- **The same-format mean.** Carry forward the average of prior *Shorts* when
  scoring a Short. Much harder, because format is known before publishing and
  the two formats behave nothing alike on a small channel.

Reporting only the first would let the Shorts-vs-long-form gap masquerade as
predictive skill. On Savirox the model is well clear of the first null and
still behind the second, and the tool says both.

### The baseline used to cheat

For a while `baseline_comparison` averaged the rows it was *evaluating on* —
the best constant achievable on the very data being predicted, a number no
forecaster can see. It made the null unbeatable and was quietly dropping
heads that beat a real one. The null is now always the training mean applied
to held-out rows. Every figure measured before that fix understated the model
by roughly fifteen points.

---

## What the model does, and why

Each choice below was measured on the walk-forward, not assumed.

**Blend the best three regularization strengths.** Picking one alpha on a
validation split of a dozen rows is a coin toss. Because all three are
standardized against the same matrix, averaging their predictions equals
averaging their weights — so this stays one linear model and per-feature
contributions remain exact.

**Refit on the full history once alpha is chosen.** The validation rows were
held back to rank ten alphas, not because they had nothing to teach. Worth
about three points of RMSE.

**Winsorize the training labels at 5%.** Squared error lets the extremes
write the model: ten long-form videos sitting at 0.02x the channel median
pulled the fit away from the seventy-three Shorts. Applied only while
fitting — labels a model is scored against are never touched, so it cannot
flatter an evaluation. Worth about five points.

**Choose the training population per head.** Whether other channels help is
not one decision for the whole model. On Savirox the view head is hurt by
reference channels (0.31 own-only against 0.47 pooled) while the engagement
heads are helped (0.013 against 0.022). Both are fitted and the one that
predicts *your* videos better is kept.

**Scale the "must beat the null" gate to the evidence.** The gate is right in
principle, but early in a channel's life it is decided on eight rows, which
cannot separate a head three percent better from one three percent worse.
The slack is `1 + 0.5/sqrt(n)` — about 18% at eight rows, 5% at a hundred,
tending to the strict rule as evidence accumulates.

---

## Results on Savirox

83 videos, 40 scored predictions, features as of schema 4:

| | RMSE | vs model |
|---|---|---|
| Brain | 0.2236 | — |
| Historical mean | 0.3154 | **+29.1%** (95% CI +10.2% to +48.2%) |
| Same-format mean | 0.2001 | −11.7% (95% CI −59.2% to +1.3%) |

Rank correlation +0.50. Clear of the classical null with the whole interval
above zero; still behind a format-aware null, which is the honest statement
of where this stands.

Within Shorts alone, only three features clear the noise threshold at n=73 —
`thumb_text_bands` (+0.27), `thumb_edge_density` (−0.26) and a channel time
trend. Choosing *which Short* beats which other Short is the open problem,
and two thumbnail statistics are not enough to close it. That is a data
ceiling, not a modelling one: it needs retention, which is intrinsic to the
video rather than to whether the algorithm sampled it into browse.

---

## The research loop: Brain asks Claude how to improve

```bash
python scripts/brain.py research --dry-run     # see exactly what would be sent
python scripts/brain.py research               # one round
python scripts/brain.py research --history     # every experiment ever tried
python scripts/brain.py research --apply ROUND:NAME
python scripts/brain.py research --revert
```

A round measures the current model, writes a briefing, asks Claude for
experiments, runs each one, and records the outcome. It **never changes how
Brain behaves**. The only way an idea becomes part of the model is `--apply`,
run by a person — and even then the next `brain train` must still beat the
champion.

### What Claude sees

The dossier (`src/brain/research/dossier.py`) is what a person would ask for
first: current performance against both null models in both eras, which
features carry signal *within* a format with the noise threshold beside each
number, coverage by feature group, data gaps, every past experiment and its
fate, and the exact menu of changes available. Everything is aggregated; six
of the channel's own public titles are the only raw content. `--dry-run`
writes the full prompt to `.mp/brain/research_prompt.txt` without calling
anyone.

### What Claude can change: a closed menu

Claude does not edit code. It names knobs from `research/knobs.py`:

| knob | range | what it does |
|---|---|---|
| `peer_window` | 4–60 | prior videos that define "normal" for the label (rebuilds the dataset) |
| `label_winsor` | 0–0.2 | tail share pulled in before fitting |
| `alpha_blend` | 1–6 | ridge strengths averaged |
| `baseline_tolerance_k` | 0–1.5 | slack in the must-beat-the-null gate |
| `validation_fraction` | 0.1–0.35 | own videos held out to choose alpha |
| `exclude_feature_groups` | ≤4 groups | groups switched off (weights become exactly zero) |

Unknown knobs, out-of-range values, repeats of past experiments and anything
over the per-round cap are rejected before a model is trained. Ideas the menu
cannot express come back as `feature_ideas` for a human to read; they are
never executed. The CLI call has every tool disabled — Claude can read the
briefing and write text, nothing else.

### Why a menu and not free rein

An unrestricted "try whatever helps" loop run against forty-three videos will
find something that scores well by chance every round and report it as
progress. This session already showed how easily that happens: a label window
of 6 looked 6 points better, and its neighbours 5 and 7 were 11 points worse.
A small, fixed set of levers keeps the number of things tried *countable*,
which is what makes the next step meaningful.

### How an answer is judged

Each experiment runs through the real training code — the same walk-forward
as `brain backtest --predictive` — and is compared with the current setup on
the same videos:

1. **Search era** (older two thirds): must gain at least 1 percentage point.
2. **Confirmation era** (newest third, unseen by the search step): must still
   be better, with paired-bootstrap probability ≥ 0.8.

Gains are relative to each configuration's *own* null model, so a knob that
redefines the label is still comparable. Only experiments that pass both come
out as `recommended`.

### The budget

The confirmation era is small, and every experiment judged on it is another
draw from the same lottery. The ledger counts experiments per era and stops
evaluating after 12. New videos move the era forward and reset the count —
which is the honest answer to "the loop has run out of ideas": it has run out
of *unseen videos to check ideas against*.

---

## Reference channels

A new channel cannot train anything: 83 videos is far below the floor. The
fastest way out of that is other people's history in the same niche — the
global -> niche -> channel transfer the plan describes (spec §84).

```bash
python scripts/brain.py reference --add @Quepasaria --limit 300
python scripts/brain.py reference               # what is in the store
python scripts/brain.py reference --remove @jodisea
```

They are stored in `.mp/brain/reference_channels.json`, **not** in
`.mp/youtube.json`. That file is the list of channels the pipeline can
generate for and upload to; a competitor's channel has no business in it.
`collector.load_channels()` hides them unless asked, so they never appear as
a publishing target, and `brain snapshot` never records them.

### Why the full history, not "the best videos"

The obvious idea — import only a channel's hits — is the one thing that
cannot work. `normalized_views` is a video's views over its own channel's
recent median, so a dataset of only winners has a median made of winners and
every label collapses to about 1.0. There is nothing left to separate. A
winner is only recognisable next to the losers it beat, so the whole recent
history goes in and the label marks the good ones.
`tests/brain/test_reference.py` pins both halves of that: a winners-only
import flattens the label spread below 0.7, a full history keeps it above
1.0.

### What to watch for

Reference channels are usually far bigger than yours, so a pooled model is
mostly a model of *them*. Two guards are in place, and one caveat is not
fixable by code:

- Labels are channel-relative, so a 500k-view video on a 500k-view channel
  is a 1.0, not a triumph.
- The cold-start fallback median is computed from your channels only —
  otherwise a 3M-subscriber channel would define "normal" and your first
  uploads would be labelled catastrophic failures.
- **The caveat:** what makes a video over-perform on a 3M-subscriber channel
  is not necessarily what does it at 258 subscribers. Always check whether
  pooling actually improved predictions *on your own held-out videos*, not
  just overall. If it did not, keep the smaller model — that is exactly the
  comparison the promotion gate exists to make.

---

## Live network viewer

```bash
python scripts/brain_viz.py --demo     # a trained synthetic channel, instantly
python scripts/brain_viz.py            # your real channels
python scripts/brain_viz.py --allow-train
```

Then open <http://localhost:8777>. Stdlib `http.server`, binds to localhost,
no new dependencies.

It serves the real thing rather than a diagram of it: the feature schema,
the fitted weights of whatever model is champion right now, the rule table,
and the blend that turns all of it into one score. The page re-reads state
every 2.5s, so promoting a model in another terminal changes the graph in
front of you.

Reading the graph, left to right:

- **74 feature nodes**, banded and coloured by group.
- **Learned weights** fan out to the model's heads. Cyan pushes the
  prediction up, orange down; thickness is magnitude. The density slider
  keeps only the strongest N%.
- **Rule nodes** below them — the cold-start layer is literally a sparse
  single-connection layer, so it draws as one.
- **Blend components** (model / rules Brain / RetentionLab / novelty) and
  the final score, with the confidence ring.

Type a topic in the sidebar and the network lights up: every feature shows
its measured value and how many standard deviations from the training mean
it sits, unmeasured features render hollow, the rules that fired glow, and
switching to **Este candidato** re-weights every edge by its actual
contribution instead of its learned weight. Hover or click any node for the
numbers behind it.

`--demo` builds 180 synthetic videos in a temp directory, trains on them and
serves that, so the full network is visible before your own history is big
enough to train anything. It never touches `.mp/`. The Train button is
enabled in demo mode, and elsewhere only with `--allow-train`.

---

## Data gaps

`python scripts/brain.py gaps` reports these against your actual history.
Five things the pipeline does not record yet, in value order:

0. ~~**thumbnails**~~ — solved. `brain` downloads every published video's cover
   from YouTube's CDN, so the visual group went from 0% to 100% coverage and
   is now the strongest signal group in the model.
1. **`script`** — the narration is never persisted, so all 17 script-structure
   features are blank on historical videos. Highest-value fix: write the
   final script into the cache entry (or the `<video>.meta.json` sidecar the
   reupload path already reads).
2. **`duration_seconds`** — blocks watch-time labels. Write the rendered
   clip's duration at upload time.
3. **`ctr` / `impressions`** — no source at all today, so the CTR head cannot
   exist and title/thumbnail ranking stays heuristic. Same Studio page
   `RetentionSync` already scrapes for average-percentage-viewed.
4. **`subscribers_gained`** — blocks the subscriber-conversion head.
5. **`production_cost`** — blocks cost-efficiency scoring.

And one that cannot be backfilled: **velocity**. `views_1h`, `views_24h` and
the rest only start existing once `brain snapshot` runs regularly. Historical
velocity is gone forever; today is the earliest it can start.

---

## How much data before it does anything

| labeled videos | what is honest |
|---|---|
| < 50 | heuristic rules only |
| 50 – 199 | ridge heads (`basic_ml`) |
| 200 – 999 | neural ranker |
| 1000+ | multi-task model |

Below 40 mature rows `train` refuses and says why. This is not a limitation
to work around — a model fitted on 20 videos is worse than the rules it
would replace.

---

## Where it is in the plan

Built: data collection and snapshots, channel-relative labels, the 74-feature
store with the leakage boundary, the rule table, ridge baselines, the
multi-head model, the versioned registry with champion/challenger promotion,
prediction with calibrated confidence and abstention, ranking with diversity
and exploration, explanations and counterfactuals, walk-forward backtesting,
per-channel statistical memory, the decision ledger for shadow mode, a CLI,
and 129 tests.

Not built: pipeline integration (above), embeddings, the retention-curve
head and rewrite loop, title/hook/thumbnail rankers as separate stages,
bandits, the webapp Brain page, and scheduled retraining.

The order in the plan still holds. The next step that pays for itself is
persisting scripts and durations, because several of the remaining stages
are blocked on data that is not being written down.
