from __future__ import annotations

import os


def pytest_configure(config) -> None:  # type: ignore[no-untyped-def]
    """Keep mutmut source paths stable when tests intentionally change cwd."""
    if os.environ.get("MUTANT_UNDER_TEST") != "stats":
        return

    try:
        import mutmut
        import mutmut.__main__ as mutmut_main
    except ImportError:
        return

    original_record_trampoline_hit = mutmut_main.record_trampoline_hit

    def record_trampoline_hit_without_unused_source_path_resolution(name: str) -> None:
        if mutmut_main.Config.get().max_stack_depth == -1:
            assert not name.startswith("src."), (
                "Failed trampoline hit. Module name starts with `src.`, which is invalid"
            )
            mutmut._stats.add(name)  # type: ignore[attr-defined]
            return

        original_record_trampoline_hit(name)

    mutmut_main.record_trampoline_hit = record_trampoline_hit_without_unused_source_path_resolution

    try:
        import mutmut.mutation.trampoline as mutmut_trampoline
    except ImportError:
        return

    mutmut_trampoline.record_trampoline_hit = record_trampoline_hit_without_unused_source_path_resolution
