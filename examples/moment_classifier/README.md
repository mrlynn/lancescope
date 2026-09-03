# A model trained on a Lance table

Two scripts. They train a classifier on `data/lance/moments.lance`, use it, and write
the results back as a second Lance table.

```bash
uv run python examples/moment_classifier/train.py
uv run python examples/moment_classifier/predict.py
uv run python examples/moment_classifier/predict.py --query "kubernetes cluster testing"
```

Nothing writes to `moments.lance`. Predictions land in a new dataset,
`data/lance/moment_predictions.lance`, which you can then open in LanceScope beside
the table it came from.

## The task

Given the SigLIP embedding of one keyframe, say which of 16 FOSDEM talks it came
from. 1,114 rows, 768 dimensions, one hidden layer, ~200k parameters. About four
seconds on an M4 Pro. The model is small on purpose: SigLIP already learned the
representation, and all that is left is a boundary in a space someone else's GPUs
paid for.

## What it is actually demonstrating

The maths is the least interesting part. Three properties of the storage layer decide
more about this run than any hyperparameter, and the scripts print all three:

**Columns are what you pay for.** The run needs `vector`, `talk_id` and `ts_s` —
three of twelve. That reads 3.3 MB where the whole table is 18.9 MB, and the
thumbnails are never opened. On this corpus that is 6x. On the `segments` table,
where the video lives in Blob V2 side files, the same distinction is four orders of
magnitude.

**Fragments are the ceiling on workers.** `moments` is one fragment, so a loader gets
one useful worker no matter what `num_workers` says. Nothing in the row count reveals
this; it is the first thing the Training tab reports.

**Versions are free reproducibility.** `train.py` records the dataset version in the
checkpoint and `predict.py` reopens *that version*, not the table. Rows appended
later are not silently scored. This is a property of the format rather than
discipline you have to maintain.

## What the numbers came out as

| | |
|---|---|
| always guess the biggest class | 17.3% |
| random 80/20 split | 97.3% |
| contiguous block held out per talk | 97.7% |

The two splits agreeing is the result worth reading. The usual failure here is that
keyframes seconds apart straddle a shuffled cut, so the test set is near-duplicates
of the training set and the score is flattered. Holding out a contiguous stretch of
each talk's timeline removes that, and the number does not move — so the shuffle was
not what made this easy.

Something else is. Each talk is one fixed camera on one slide template, so a model
can separate 16 talks by separating 16 rooms without ever reading a slide. **98% says
the frames are separable, not that the model understands them.** A layout tool cannot
tell you that and neither can an accuracy; you get it by knowing what the rows are.

The five misses are all low-confidence — 30% to 48%, against a model that is usually
above 90% when it is right. That is the useful behaviour: it is wrong where it is
unsure.

## The text path

`--query` embeds a sentence with SigLIP's text tower and pushes it through the same
head. It half works. The head was fitted on image embeddings and text ones sit
elsewhere in the shared space, so it drifts off its training distribution and leans
on class priors — the right talk usually reaches the top two, and the largest class
usually outranks it. The 97.7% does not transfer. The flag is there to show the
modality gap, not to paper over it.

## Where to go next

- Point `--uri` at a bigger table and watch the byte figures move rather than the
  accuracy.
- Re-embed with a different model and compare: the vector column is a sixth of this
  table, so swapping embedding models rewrites 3.4 MB and leaves the transcripts
  alone. That is a much cheaper decision than it feels like.
- Fine-tune the encoder itself instead of a head on top of it — the next real step
  up, and still a laptop-sized job.
