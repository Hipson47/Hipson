import json
import re
from pathlib import Path

from hipson.skills import validate_skill_file

REPO_ROOT = Path(__file__).resolve().parents[1]
CV_SKILLS_ROOT = REPO_ROOT / "skills" / "computer-vision"
EXPECTED_SKILLS = (
    "cv-project-router",
    "cv-webapp-starter",
    "dataset-builder",
    "mediapipe-human-interface",
    "opencv-realtime-camera",
    "vision-demo-builder",
    "vision-verifier",
    "yolo-detector",
)
REQUIRED_SECTIONS = (
    "Purpose",
    "Use When",
    "Do Not Use When",
    "Inputs",
    "Default Stack",
    "Workflow",
    "Output Contract",
    "Verification",
    "Failure Modes",
    "Safety Notes",
)
FORBIDDEN_ARTIFACT_SUFFIXES = {
    ".avi",
    ".bin",
    ".ckpt",
    ".engine",
    ".gif",
    ".jpeg",
    ".jpg",
    ".mkv",
    ".mov",
    ".mp4",
    ".onnx",
    ".png",
    ".pt",
    ".pth",
    ".safetensors",
    ".sh",
    ".weights",
}


def test_computer_vision_skill_pack_has_complete_valid_contracts():
    discovered = tuple(sorted(path.parent.name for path in CV_SKILLS_ROOT.glob("*/SKILL.md")))

    assert discovered == EXPECTED_SKILLS
    for name in EXPECTED_SKILLS:
        skill_path = CV_SKILLS_ROOT / name / "SKILL.md"
        validation = validate_skill_file(skill_path)
        text = skill_path.read_text(encoding="utf-8")

        assert validation.ok, validation.errors
        assert f"name: {name}\n" in text
        assert "description: Use when " in text
        positions = [text.index(f"## {section}\n") for section in REQUIRED_SECTIONS]
        assert positions == sorted(positions)
        assert len(positions) == len(set(positions))
        assert text.find("\n## ", positions[-1] + 1) == -1


def test_computer_vision_package_docs_cover_sources_experiments_and_roadmap():
    readme = (CV_SKILLS_ROOT / "README.md").read_text(encoding="utf-8")
    sources = (CV_SKILLS_ROOT / "source-candidates.md").read_text(encoding="utf-8")
    roadmap = (REPO_ROOT / "docs" / "computer-vision" / "hipson-cv-roadmap.md").read_text(encoding="utf-8")

    for experiment in (
        "Image upload object detector",
        "Local webcam YOLO detector",
        "Hand gesture web controller",
        "Pose checker",
        "Dataset frame extractor",
    ):
        assert experiment in readme

    for status_heading in (
        "## Accepted Sources",
        "## Maybe Sources",
        "## Rejected Sources",
        "## Unavailable Or Not Used",
        "## Search Queries Used",
    ):
        assert status_heading in sources
    assert sources.count("| Source | URL | License | Status | Reason | Adapted into |") == 4
    for status in ("Accepted", "Maybe", "Rejected", "Unavailable"):
        assert f"| {status} |" in sources

    assert "Next.js POST /api/cv/detections" in roadmap
    assert "FastAPI POST /v1/detections" in roadmap
    assert "## Deferred Work" in roadmap


def test_computer_vision_markdown_contains_valid_json_examples_only():
    markdown_files = sorted(CV_SKILLS_ROOT.rglob("*.md"))
    json_blocks = []
    for path in markdown_files:
        text = path.read_text(encoding="utf-8")
        json_blocks.extend((path, block) for block in re.findall(r"```json\n(.*?)\n```", text, flags=re.DOTALL))

    assert json_blocks
    for path, block in json_blocks:
        try:
            json.loads(block)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"Invalid JSON example in {path}: {exc}") from exc


def test_computer_vision_package_does_not_vendor_executable_or_binary_artifacts():
    files = [path for path in CV_SKILLS_ROOT.rglob("*") if path.is_file()]

    assert files
    assert all(path.suffix.lower() not in FORBIDDEN_ARTIFACT_SUFFIXES for path in files)
    assert all(path.suffix.lower() == ".md" for path in files)
