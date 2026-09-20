"""Find ffmpeg/ffprobe and prove the build can run our filtergraph.

Discovery order matters: an explicitly configured path wins, then PATH, then the
two places winget actually drops the binaries, then the pip-installable static
build. Note that `winget install Gyan.FFmpeg` does NOT always create a shim in
WinGet/Links -- on this machine it only unpacked into WinGet/Packages -- so both
are searched.

ffprobe is optional by design. Audio durations come from the WAV header via the
stdlib `wave` module and clip metadata is parsed out of `ffmpeg -i` stderr, so a
build that ships ffmpeg alone (imageio-ffmpeg) is still fully functional.
"""
from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import FfmpegMissing

#: Every filter the pipeline emits. Checked up front so a missing one surfaces
#: as a clear message instead of a parse error 30 seconds into an encode.
REQUIRED_FILTERS = (
    "overlay", "adelay", "amix", "tpad", "fade", "loudnorm",
    "sidechaincompress", "tblend", "signalstats", "metadata", "atempo",
    "asplit", "aresample", "anullsrc", "eq", "crop", "scale", "setsar",
    "format", "volume", "aformat",
)

INSTALL_HINT = "winget install --id=Gyan.FFmpeg -e --source winget"

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass(frozen=True)
class FfmpegTools:
    ffmpeg: Path
    ffprobe: Path | None
    version: str
    filters: frozenset[str]

    @property
    def has_ffprobe(self) -> bool:
        return self.ffprobe is not None

    def missing_filters(self) -> tuple[str, ...]:
        return tuple(f for f in REQUIRED_FILTERS if f not in self.filters)

    def supports(self, encoder: str) -> bool:
        return encoder in _encoders(self.ffmpeg)

    def filter_script_args(self, path: Path) -> list[str]:
        """Args that read the filtergraph from a file.

        ``-filter_complex_script`` was deprecated in ffmpeg 7 and **removed** in
        ffmpeg 8; the replacement is the generic read-option-from-file syntax
        ``-/filter_complex <file>``. Both are probed rather than inferred from
        the version string, which is not always parseable.
        """
        flag = "-filter_complex_script" if _has_legacy_script_flag(self.ffmpeg) else "-/filter_complex"
        return [flag, str(path)]


def run_quiet(argv: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Run a tool without a console window and without a shell.

    ``shell=False`` is not an optimisation here -- PowerShell mangles the commas,
    semicolons and brackets that fill an ffmpeg command line, so the shell must
    never see one.
    """
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, shell=False, creationflags=_CREATE_NO_WINDOW,
    )


def _winget_candidates(name: str) -> list[Path]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    base = Path(local) / "Microsoft" / "WinGet"
    found = [base / "Links" / f"{name}.exe"]
    pkgs = base / "Packages"
    if pkgs.is_dir():
        # Gyan.FFmpeg_.../ffmpeg-<ver>-full_build/bin/<name>.exe
        found.extend(sorted(pkgs.glob(f"Gyan.FFmpeg*/**/bin/{name}.exe"), reverse=True))
    return found


def _find(name: str, configured: Path | None) -> Path | None:
    if configured and Path(configured).is_file():
        return Path(configured)
    on_path = shutil.which(name)
    if on_path:
        return Path(on_path)
    for cand in _winget_candidates(name):
        if cand.is_file():
            return cand
    return None


#: ``ffmpeg -filters`` rows look like " TS overlay  VV->V  description".
#: The flags column width changed between releases (3 chars through ffmpeg 7,
#: 2 chars in ffmpeg 9), so the stable discriminator is the I/O spec column.
_IO_SPEC_RE = re.compile(r"^[AVN|]+->[AVN|]+$")
_CODEC_FLAGS_RE = re.compile(r"^[A-Z.]{5,7}$")


@functools.lru_cache(maxsize=4)
def _filters(ffmpeg: Path) -> frozenset[str]:
    proc = run_quiet([str(ffmpeg), "-hide_banner", "-filters"])
    names = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and _IO_SPEC_RE.match(parts[2]):
            names.add(parts[1])
    return frozenset(names)


@functools.lru_cache(maxsize=4)
def _encoders(ffmpeg: Path) -> frozenset[str]:
    proc = run_quiet([str(ffmpeg), "-hide_banner", "-encoders"])
    return frozenset(
        parts[1]
        for line in proc.stdout.splitlines()
        if len(parts := line.split()) >= 2 and _CODEC_FLAGS_RE.match(parts[0])
    )


@functools.lru_cache(maxsize=4)
def _has_legacy_script_flag(ffmpeg: Path) -> bool:
    proc = run_quiet([str(ffmpeg), "-hide_banner", "-h", "full"])
    return "filter_complex_script" in (proc.stdout or "")


@functools.lru_cache(maxsize=4)
def _version(ffmpeg: Path) -> str:
    proc = run_quiet([str(ffmpeg), "-version"])
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    m = re.search(r"ffmpeg version (\S+)", first)
    return m.group(1) if m else "unknown"


def resolve(
    ffmpeg_path: Path | None = None,
    ffprobe_path: Path | None = None,
    *,
    allow_bundled: bool = True,
) -> FfmpegTools:
    """Locate the tools or raise :class:`FfmpegMissing` with the install command."""
    ffmpeg = _find("ffmpeg", ffmpeg_path)
    if ffmpeg is None and allow_bundled:
        try:
            import imageio_ffmpeg

            exe = Path(imageio_ffmpeg.get_ffmpeg_exe())
            if exe.is_file():
                ffmpeg = exe
        except Exception:  # pragma: no cover - optional dependency
            ffmpeg = None
    if ffmpeg is None:
        raise FfmpegMissing(
            "Không tìm thấy ffmpeg trên máy.", hint=INSTALL_HINT,
        )
    return FfmpegTools(
        ffmpeg=ffmpeg,
        ffprobe=_find("ffprobe", ffprobe_path),
        version=_version(ffmpeg),
        filters=_filters(ffmpeg),
    )


def preflight(tools: FfmpegTools) -> list[str]:
    """Return human-readable warnings; an empty list means everything is ready."""
    warnings: list[str] = []
    missing = tools.missing_filters()
    if missing:
        warnings.append(
            f"Bản ffmpeg này thiếu filter: {', '.join(missing)}. "
            f"Cài bản full build: {INSTALL_HINT}"
        )
    if not tools.has_ffprobe:
        warnings.append(
            "Không có ffprobe (không bắt buộc) — thời lượng sẽ đọc từ header WAV "
            "và metadata clip parse từ stderr của ffmpeg."
        )
    if not tools.supports("libx264"):
        warnings.append("Bản ffmpeg này không có libx264 — không encode được H.264.")
    return warnings
