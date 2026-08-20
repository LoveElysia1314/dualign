"""Public report-only alignment pipeline."""

from __future__ import annotations

from pathlib import Path

from dualign.common import load_text_lines
from dualign.config import get_embedding_cache_path
from dualign.core import AlignConfig, AlignmentResult, align
from dualign.services.cached_encoder import CachedEncoder
from dualign.services.embedding_cache import EmbeddingCache
from dualign.services.quality_gate import automatic_repair_blockers
from dualign.services.report_io import (
    ReportError,
    build_report,
    load_report,
    operations_from_report,
    report_matches_alignment,
    save_report,
)


def default_report_path(document_a_path: str | Path) -> Path:
    path = Path(document_a_path)
    stem = path.stem.removesuffix(".source").removesuffix(".target")
    return path.parent / f"{stem}.report.json"


def _provenance(model, config: AlignConfig) -> dict:
    import hashlib
    import json

    from dualign import __version__
    from dualign.core import ALIGN_CACHE_REVISION, ALIGN_CORE_VERSION

    provider = ""
    endpoint = ""
    model_name = getattr(model, "_model", "") if model is not None else ""
    instruction = getattr(model, "_instruction", "") if model is not None else ""
    try:
        from dualign.providers import ProviderManager

        ProviderManager.load()
        active = ProviderManager.active()
        if active is not None:
            provider = active.provider_id
            endpoint = str(active.base_url).rstrip("/")
            model_name = model_name or active.model_name
            instruction = instruction or active.instruction_text
            if not instruction and active.provider_id == "ollama":
                from dualign.config import INSTRUCTION_TEXT

                instruction = INSTRUCTION_TEXT
    except (OSError, ValueError):
        pass
    config_payload = json.dumps(vars(config), sort_keys=True, separators=(",", ":"))
    result = {
        "tool": "dualign",
        "tool_version": __version__,
        "algorithm": {
            "name": "dualign-pairwise",
            "revision": ALIGN_CORE_VERSION,
            "cache_revision": ALIGN_CACHE_REVISION,
            "configuration_sha256": hashlib.sha256(
                config_payload.encode("utf-8")
            ).hexdigest(),
        },
        "embedding": {"provider": provider, "model": str(model_name)},
    }
    if endpoint:
        result["embedding"]["endpoint"] = endpoint
    if instruction:
        result["embedding"]["instruction_sha256"] = hashlib.sha256(
            instruction.encode("utf-8")
        ).hexdigest()
    return result


def _empty_result(source_count: int, target_count: int) -> AlignmentResult:
    operations = []
    if source_count and not target_count:
        operations = [((index,), (), 0.0) for index in range(source_count)]
    elif target_count and not source_count:
        operations = [((), (index,), 0.0) for index in range(target_count)]
    return AlignmentResult(
        all_ops=operations,
        anchors=[],
        anchor_op_indices={},
        stats={
            "n_source": source_count,
            "n_target": target_count,
            "n_ops": len(operations),
            "n_true_anchors": 0,
            "anchor_density": 0.0,
            "avg_similarity": 0.0,
        },
    )


def _safe_repair_mode(strategy: str, model, quality: dict) -> tuple[str, object]:
    """Fall back to the no-encoding strategy for structurally unsafe input."""
    if automatic_repair_blockers(quality):
        return "minimal", None
    return strategy, model


def align_documents(
    document_a_path: str,
    document_b_path: str,
    report_path: str = "",
    *,
    model=None,
    config=None,
    strategy: str = "minimal",
    reset_work_state: bool = False,
    reuse_alignment: bool = True,
    preserve_work_state: bool = False,
) -> dict:
    """Align two documents and persist only their replayable work report.

    ``reuse_alignment`` controls whether a matching report may supply its
    expensive alignment relations. ``reset_work_state`` rebuilds the report;
    with ``preserve_work_state`` it retains existing review decisions and only
    auto-repairs unresolved relations, otherwise it starts from clean state.
    """

    path_a = Path(document_a_path)
    path_b = Path(document_b_path)
    if not path_a.is_file():
        return {"success": False, "error": f"文档 A 不存在: {path_a}"}
    if not path_b.is_file():
        return {"success": False, "error": f"文档 B 不存在: {path_b}"}
    target = Path(report_path) if report_path else default_report_path(path_a)
    cfg = config or AlignConfig()
    lines_a = load_text_lines(str(path_a))
    lines_b = load_text_lines(str(path_b))

    encoder = model
    if lines_a and lines_b:
        encoder = _ensure_model(model)
        if encoder is None:
            return {"success": False, "error": "模型未加载"}
    provenance = _provenance(encoder, cfg)

    if reuse_alignment and target.is_file():
        try:
            cached = load_report(target)
            if report_matches_alignment(cached, path_a, path_b, provenance):
                cached_operations = operations_from_report(cached)
                if reset_work_state:
                    from dualign.models.action import RepairAction
                    from dualign.models.state import AlignmentSnapshot
                    from dualign.services.repair import RepairService, RepairState

                    existing_actions = (
                        [
                            RepairAction.from_dict(item)
                            for item in cached.get("repair_log", [])
                        ]
                        if preserve_work_state
                        else []
                    )
                    quality = dict(cached.get("quality") or {})
                    state = RepairState(
                        AlignmentSnapshot.from_alignment(
                            cached_operations, lines_a, lines_b
                        ),
                        existing_actions,
                    )
                    repair_strategy, repair_model = _safe_repair_mode(
                        strategy, encoder, quality
                    )
                    repair_log = RepairService.auto_repair(
                        state,
                        strategy=repair_strategy,
                        model=repair_model,
                        unresolved_only=preserve_work_state,
                    ).repair_log
                    report = build_report(
                        chapter_id=path_a.stem.split(".")[0],
                        document_a_path=path_a,
                        document_b_path=path_b,
                        operations=cached_operations,
                        stats=dict(cached.get("stats") or {}),
                        quality=quality,
                        provenance=provenance,
                        repair_log=repair_log,
                        previous=cached if preserve_work_state else None,
                    )
                    save_report(report, target)
                    return {
                        "success": True,
                        "ops": cached_operations,
                        "report_path": str(target),
                        "quality": quality.get("level", ""),
                        "rejections": quality.get("rejections", []),
                        "cache_hit": True,
                        "work_state_reset": True,
                        "work_state_preserved": preserve_work_state,
                    }
                return {
                    "success": True,
                    "ops": cached_operations,
                    "report_path": str(target),
                    "quality": (cached.get("quality") or {}).get("level", ""),
                    "rejections": (cached.get("quality") or {}).get("rejections", []),
                    "cache_hit": True,
                }
        except ReportError:
            pass

    if lines_a and lines_b:
        with EmbeddingCache(get_embedding_cache_path()) as cache:
            cached_encoder = CachedEncoder(encoder, cache)
            result = align(
                lines_a,
                lines_b,
                cached_encoder.encode(lines_a),
                cached_encoder.encode(lines_b),
                cfg,
                encode_fn=cached_encoder.encode,
            )
    else:
        result = _empty_result(len(lines_a), len(lines_b))

    from dualign.services.quality_gate import _gap_row_ratio, assess_alignment_quality

    assessment = assess_alignment_quality(
        result.stats or {},
        len(lines_a),
        len(lines_b),
        _gap_row_ratio(result.all_ops, len(lines_a), len(lines_b)),
        (result.stats or {}).get("n_overflow_rows", 0),
    )
    quality = {
        "level": assessment["quality"],
        "rejections": assessment.get("rejections", []),
        "indicators": assessment["indicators"],
    }
    repair_log = []
    if result.all_ops:
        from dualign.models.state import AlignmentSnapshot
        from dualign.services.repair import RepairService, RepairState

        state = RepairState(
            AlignmentSnapshot.from_alignment(result.all_ops, lines_a, lines_b)
        )
        repair_strategy, repair_model = _safe_repair_mode(strategy, encoder, quality)
        repair_log = RepairService.auto_repair(
            state, strategy=repair_strategy, model=repair_model
        ).repair_log

    previous = None
    if reuse_alignment and target.is_file() and not reset_work_state:
        try:
            candidate = load_report(target)
            if report_matches_alignment(candidate, path_a, path_b, provenance):
                previous = candidate
        except ReportError:
            pass
    report = build_report(
        chapter_id=path_a.stem.split(".")[0],
        document_a_path=path_a,
        document_b_path=path_b,
        operations=result.all_ops,
        stats=result.stats or {},
        quality=quality,
        provenance=provenance,
        repair_log=repair_log,
        previous=previous,
    )
    save_report(report, target)
    return {
        "success": True,
        "ops": result.all_ops,
        "report_path": str(target),
        "quality": quality["level"],
        "rejections": quality["rejections"],
        "cache_hit": False,
        "work_state_reset": reset_work_state,
    }


def _ensure_model(model):
    if model is not None:
        return model
    from dualign.services.embedding import _try_lazy_load_model, load_model_for_provider

    model = _try_lazy_load_model()
    if model is None:
        try:
            model = load_model_for_provider()
        except Exception:
            return None
    return model
