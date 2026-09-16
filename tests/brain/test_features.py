"""Feature extraction: lexicon primitives, each group, and the registry."""

from __future__ import annotations

import datetime as dt

from brain.features import lexicon as lx
from brain.features import registry as fr
from brain.features.base import POST_PUBLISH, PRE_PUBLISH, FeatureSpec, FeatureVector
from brain.schema import Candidate, ChannelContext

from conftest import NOW


# -- lexicon ------------------------------------------------------------


def test_normalize_strips_accents_and_case():
    # Punctuation survives normalize(); tokens() is what drops it.
    assert lx.normalize("¿Qué PASÓ  con Oumuamua?") == "¿que paso con oumuamua?"
    assert lx.tokens("¿Qué PASÓ?") == ["que", "paso"]


def test_count_markers_matches_whole_words_not_substrings():
    # "pero" is a reveal marker; "peronismo" must not fire it.
    assert lx.count_markers("peronismo", lx.REVEAL) == 0
    assert lx.count_markers("pero entonces", lx.REVEAL) == 2


def test_count_markers_handles_multiword_phrases():
    assert lx.count_markers("sin embargo, algo cambio", lx.REVEAL) == 1


def test_content_tokens_drops_stopwords_and_short_words():
    tokens = lx.content_tokens("El misterio de los agujeros negros")
    assert "misterio" in tokens and "agujeros" in tokens
    assert "de" not in tokens and "el" not in tokens


def test_jaccard_is_zero_for_empty_sides():
    assert lx.jaccard(set(), {"a"}) == 0.0
    assert lx.jaccard({"a", "b"}, {"b"}) == 0.5


# -- text ---------------------------------------------------------------


def test_title_features_fall_back_to_topic_at_topic_stage():
    candidate = Candidate(topic="El secreto de Oumuamua")
    vector = fr.vectorize(candidate, ChannelContext())
    values = vector.as_dict()
    assert values["title_curiosity"] >= 1.0
    assert values["title_chars"] == len("El secreto de Oumuamua")


def test_hook_subject_anchor_rewards_naming_the_subject():
    anchored = Candidate(topic="El cometa Halley", hook="El cometa Halley volvera")
    vague = Candidate(topic="El cometa Halley", hook="Esto te va a sorprender")
    context = ChannelContext()
    assert (
        fr.vectorize(anchored, context).as_dict()["hook_subject_anchor"]
        > fr.vectorize(vague, context).as_dict()["hook_subject_anchor"]
    )


# -- script -------------------------------------------------------------


def test_script_features_are_missing_without_a_script():
    vector = fr.vectorize(Candidate(topic="algo"), ChannelContext())
    assert "script_words" in vector.missing
    assert "script_words_per_second" in vector.missing


def test_words_per_second_reflects_the_target_duration():
    candidate = Candidate(
        topic="t",
        script=" ".join(["palabra"] * 120),
        target_duration_seconds=60.0,
    )
    values = fr.vectorize(candidate, ChannelContext()).as_dict()
    assert values["script_words_per_second"] == 2.0


def test_density_features_are_length_independent():
    short = Candidate(topic="t", script="En 1999 pasaron 3 cosas.")
    long = Candidate(topic="t", script="En 1999 pasaron 3 cosas. " * 10)
    context = ChannelContext()
    a = fr.vectorize(short, context).as_dict()["script_number_density"]
    b = fr.vectorize(long, context).as_dict()["script_number_density"]
    assert abs(a - b) < 1e-6


# -- channel ------------------------------------------------------------


def test_channel_features_only_see_prior_videos(channel):
    """A candidate dated before the whole history sees an empty channel."""
    early = Candidate(
        topic="nuevo tema",
        planned_publish_at=dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc),
    )
    late = Candidate(topic="nuevo tema", planned_publish_at=NOW)

    early_values = fr.vectorize(early, channel).as_dict()
    late_values = fr.vectorize(late, channel).as_dict()
    assert early_values["channel_history_count"] == 0.0
    assert late_values["channel_history_count"] == 60.0


def test_novelty_drops_when_the_topic_repeats_the_channel(channel):
    fresh = Candidate(topic="La musica barroca del siglo XVII", planned_publish_at=NOW)
    repeat = Candidate(
        topic="El misterio del cometa 12", title="datos sobre el cometa Halley",
        planned_publish_at=NOW,
    )
    fresh_novelty = fr.vectorize(fresh, channel).as_dict()["topic_novelty"]
    repeat_novelty = fr.vectorize(repeat, channel).as_dict()["topic_novelty"]
    assert repeat_novelty < fresh_novelty


def test_winner_similarity_beats_loser_similarity_for_a_winning_shape(channel):
    """The synthetic channel's winners are the numbered comet titles."""
    candidate = Candidate(
        topic="El misterio del cometa Halley",
        title="9 datos que nadie te conto sobre el cometa Halley",
        planned_publish_at=NOW,
    )
    values = fr.vectorize(candidate, channel).as_dict()
    assert values["topic_winner_similarity"] > values["topic_loser_similarity"]


# -- temporal -----------------------------------------------------------


def test_hour_encoding_is_cyclic():
    late = Candidate(
        topic="t",
        planned_publish_at=dt.datetime(2026, 1, 1, 23, tzinfo=dt.timezone.utc),
    )
    early = Candidate(
        topic="t",
        planned_publish_at=dt.datetime(2026, 1, 2, 0, tzinfo=dt.timezone.utc),
    )
    context = ChannelContext()
    a = fr.vectorize(late, context).as_dict()
    b = fr.vectorize(early, context).as_dict()
    distance = (a["publish_hour_sin"] - b["publish_hour_sin"]) ** 2 + (
        a["publish_hour_cos"] - b["publish_hour_cos"]
    ) ** 2
    assert distance < 0.1  # 23:00 and 00:00 are neighbours


# -- registry -----------------------------------------------------------


def test_registry_exposes_only_pre_publish_features():
    assert all(spec.availability == PRE_PUBLISH for spec in fr.INPUT_SPECS)
    assert len(fr.FEATURE_NAMES) == fr.FEATURE_COUNT
    assert len(set(fr.FEATURE_NAMES)) == fr.FEATURE_COUNT


def test_schema_hash_changes_with_the_feature_list():
    from brain.features.base import schema_hash

    assert schema_hash(list(fr.FEATURE_NAMES)) == fr.SCHEMA_HASH
    assert schema_hash(list(fr.FEATURE_NAMES) + ["extra"]) != fr.SCHEMA_HASH


def test_coverage_reports_what_could_not_be_measured():
    vector = fr.vectorize(Candidate(topic="solo un tema"), ChannelContext())
    assert 0.0 < vector.coverage < 1.0
    assert set(vector.missing) <= set(fr.FEATURE_NAMES)


def test_a_broken_extractor_degrades_one_feature_only():
    def explode(candidate, context):
        raise RuntimeError("boom")

    spec = FeatureSpec("boom", "test", explode, PRE_PUBLISH, 1.5)
    assert spec.extract(Candidate(), ChannelContext()) is None


def test_manifest_describes_every_input_feature():
    manifest = fr.manifest()
    assert manifest["feature_count"] == fr.FEATURE_COUNT
    assert {entry["name"] for entry in manifest["features"]} == set(fr.FEATURE_NAMES)


def test_feature_vector_coverage_is_zero_when_empty():
    assert FeatureVector().coverage == 0.0


def test_post_publish_specs_are_rejected_from_input(monkeypatch):
    """The availability contract is enforced, not merely documented."""
    spec = FeatureSpec("cheat", "test", lambda c, ctx: 1.0, POST_PUBLISH)
    assert spec.availability == POST_PUBLISH
    assert spec.name not in fr.FEATURE_NAMES
