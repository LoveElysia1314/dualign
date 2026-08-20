"""Public report-only alignment pipeline."""

from __future__ import annotations

from pathlib import Path

from dualign.common import load_text_lines
from dualign.config import get_embedding_cache_path
from dualign.core import AlignConfig, AlignmentResult, align
from dualign.services.cached_encoder import CachedEncoder
from dualign.services.embedding_cache import EmbeddingCache
from dualign.services.report_io import (
    ReportError,
    build_report,
    load_report,
    operations_from_report,
    report_matches_documents,
    report_matches_provenance,
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
    from dualign.core import ALIGN_CORE_VERSION

    provider = ""
    model_name = getattr(model, "_model", "") if model is not None else ""
    instruction = getattr(model, "_instruction", "") if model is not None else ""
    try:
        from dualign.providers import ProviderManager

        ProviderManager.load()
        active = ProviderManager.active()
        if active is not None:
            provider = active.provider_id
            model_name = model_name or active.model_name
            instruction = instruction or active.instruction_text
    except (OSError, ValueError):
        pass
    config_payload = json.dumps(vars(config), sort_keys=True, separators=(",", ":"))
    result = {
        "tool": "dualign",
        "tool_version": __version__,
        "algorithm": {
            "name": "dualign-pairwise",
            "revision": ALIGN_CORE_VERSION,
            "configuration_sha256": hashlib.sha256(
                config_payload.encode("utf-8")
            ).hexdigest(),
        },
        "embedding": {"provider": provider, "model": str(model_name)},
    }
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


def align_documents(
    document_a_path: str,
    document_b_path: str,
    report_path: str = "",
    *,
    model=None,
    config=None,
    strategy: str = "minimal",
) -> dict:
    """Align two documents and persist only their replayable work report."""

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

    if target.is_file():
        try:
            cached = load_report(target)
            if report_matches_documents(
                cached, path_a, path_b
            ) and report_matches_provenance(cached, provenance):
                return {
                    "success": True,
                    "ops": operations_from_report(cached),
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
    if result.all_ops and assessment["quality"] != "unreliable":
        from dualign.models.state import AlignmentSnapshot
        from dualign.services.repair import RepairService, RepairState

        state = RepairState(
            AlignmentSnapshot.from_alignment(result.all_ops, lines_a, lines_b)
        )
        repair_log = RepairService.auto_repair(
            state, strategy=strategy, model=encoder
        ).repair_log

    previous = None
    if target.is_file():
        try:
            candidate = load_report(target)
            if report_matches_documents(candidate, path_a, path_b):
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
