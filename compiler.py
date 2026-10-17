import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union


DEFAULT_TEMPLATE_PATH = Path(__file__).parent / "template.tex"


def find_latex_compiler(preferred: str = "auto") -> Optional[Tuple[str, str]]:
    """
    Locate an available LaTeX engine ('tectonic' or 'pdflatex').
    Returns a tuple of (engine_name, executable_path) or None if neither is found.
    """
    candidates: List[Tuple[str, List[str]]] = [
        (
            "tectonic",
            [
                "tectonic",
                str(Path.home() / ".cargo" / "bin" / "tectonic.exe"),
                str(Path.home() / "scoop" / "shims" / "tectonic.exe"),
                r"C:\ProgramData\chocolatey\bin\tectonic.exe",
            ],
        ),
        (
            "pdflatex",
            [
                "pdflatex",
                str(
                    Path.home()
                    / "AppData"
                    / "Local"
                    / "Programs"
                    / "MiKTeX"
                    / "miktex"
                    / "bin"
                    / "x64"
                    / "pdflatex.exe"
                ),
                r"C:\Program Files\MiKTeX\miktex\bin\x64\pdflatex.exe",
                r"C:\texlive\2025\bin\windows\pdflatex.exe",
                r"C:\texlive\2024\bin\windows\pdflatex.exe",
                r"C:\texlive\2023\bin\windows\pdflatex.exe",
            ],
        ),
    ]

    if preferred in ("tectonic", "pdflatex"):
        candidates.sort(key=lambda item: 0 if item[0] == preferred else 1)

    for engine_name, paths in candidates:
        for candidate in paths:
            resolved = shutil.which(candidate)
            if resolved:
                return (engine_name, resolved)
            if os.path.isfile(candidate):
                return (engine_name, candidate)

    return None


def _normalize_tikz_block(tikz_code: str) -> str:
    """Ensure the TikZ snippet is wrapped in a tikzpicture environment."""
    code = tikz_code.strip()
    if not code:
        return ""
    if "\\begin{tikzpicture}" not in code:
        code = f"\\begin{{tikzpicture}}\n{code}\n\\end{{tikzpicture}}"
    return f"\\begin{{center}}\n{code}\n\\end{{center}}"


def render_question_latex(
    question: dict,
    q_index: int,
    image_Assets: Optional[Dict[str, bytes]] = None,
) -> str:
    """
    Convert a single standardized question dictionary into LaTeX `exam` class markup.
    """
    body = (question.get("body") or "").strip()
    marks = int(question.get("marks") or 1)
    parts = question.get("parts") or []
    needs_working_lines = bool(question.get("needs_working_lines", False))
    working_height = (question.get("working_lines_height") or "2.0in").strip()
    has_diagram = bool(question.get("has_diagram", False))
    tikz_code = (question.get("tikz_code") or "").strip()
    diagram_mode = question.get("diagram_mode", "auto")  # "auto" | "tikz" | "image"

    lines: List[str] = []

    # Check whether subparts carry their own marks
    part_marks_sum = sum(int(p.get("marks") or 0) for p in parts)
    has_graded_parts = len(parts) > 0 and part_marks_sum > 0

    if has_graded_parts:
        lines.append(f"\\question {body}")
    else:
        lines.append(f"\\question[{marks}] {body}")

    # Insert Diagram (TikZ favored; cropped image fallback)
    if has_diagram:
        use_tikz = bool(tikz_code) and diagram_mode in ("auto", "tikz")
        if use_tikz:
            lines.append(_normalize_tikz_block(tikz_code))
        else:
            img_bytes = question.get("cropped_image_bytes")
            if img_bytes and image_Assets is not None:
                img_filename = f"diagram_q{q_index}.png"
                image_Assets[img_filename] = img_bytes
                lines.append(
                    "\\begin{center}\n"
                    f"\\includegraphics[width=0.58\\textwidth,height=5.5cm,keepaspectratio]{{{img_filename}}}\n"
                    "\\end{center}"
                )
            elif tikz_code:
                lines.append(_normalize_tikz_block(tikz_code))

    # Render subparts if present
    if parts:
        lines.append("\\begin{parts}")
        for part in parts:
            p_text = (part.get("text") or "").strip()
            p_marks = int(part.get("marks") or 0)
            if has_graded_parts and p_marks > 0:
                lines.append(f"    \\part[{p_marks}] {p_text}")
                lines.append("    \\droppoints")
                if needs_working_lines and working_height:
                    lines.append(f"    \\fillwithlines{{{working_height}}}")
            else:
                lines.append(f"    \\part {p_text}")
        lines.append("\\end{parts}")

        if not has_graded_parts:
            lines.append("\\droppoints")
            if needs_working_lines and working_height:
                lines.append(f"\\fillwithlines{{{working_height}}}")
    else:
        lines.append("\\droppoints")
        if needs_working_lines and working_height:
            lines.append(f"\\fillwithlines{{{working_height}}}")

    lines.append("\\vspace{0.35cm}")
    return "\n".join(lines)


def build_exam_latex(
    questions: List[dict],
    subject: str = "Mathematics",
    exam_title: str = "Topic Assessment",
    time_allowed: str = "45 Minutes",
    template_path: Union[str, Path] = DEFAULT_TEMPLATE_PATH,
) -> Tuple[str, Dict[str, bytes]]:
    """
    Inject questions and assessment metadata into `template.tex`.
    Returns (compiled_tex_string, image_assets_dict).
    """
    template_text = Path(template_path).read_text(encoding="utf-8")
    image_assets: Dict[str, bytes] = {}

    section_a_questions = [
        (idx + 1, q)
        for idx, q in enumerate(questions)
        if q.get("section", "Section B") == "Section A"
    ]
    section_b_questions = [
        (idx + 1, q)
        for idx, q in enumerate(questions)
        if q.get("section", "Section B") != "Section A"
    ]

    # Build Section A Block
    section_a_lines: List[str] = []
    if section_a_questions:
        section_a_lines.append("\\section*{Section A --- Multiple Choice}")
        section_a_lines.append(
            "\\noindent\\textit{Indicate the best answer for each question. Marks are shown in the right margin.}\\\\[0.2cm]"
        )
        section_a_lines.append("\\begin{questions}")
        for q_idx, q in section_a_questions:
            section_a_lines.append(render_question_latex(q, q_idx, image_assets))
        section_a_lines.append("\\end{questions}")
        section_a_lines.append("\\vspace{0.5cm}")

    # Build Section B Block
    section_b_lines: List[str] = []
    if section_b_questions:
        section_b_lines.append("\\section*{Section B --- Short Answer}")
        section_b_lines.append(
            "\\noindent\\textit{Show all necessary working in the spaces provided. Full marks may not be awarded for unsupported answers.}\\\\[0.2cm]"
        )
        section_b_lines.append("\\begin{questions}")
        if section_a_questions:
            # Continue question numbering seamlessly from Section A
            next_q_num = len(section_a_questions) + 1
            section_b_lines.append(f"\\setcounter{{question}}{{{next_q_num - 1}}}")
        for q_idx, q in section_b_questions:
            section_b_lines.append(render_question_latex(q, q_idx, image_assets))
        section_b_lines.append("\\end{questions}")

    if not section_a_questions and not section_b_questions:
        section_b_lines.append(
            "\\begin{center}\\textit{No questions added yet.}\\end{center}"
        )

    tex_content = (
        template_text.replace("%%SUBJECT%%", subject)
        .replace("%%EXAM_TITLE%%", exam_title)
        .replace("%%TIME_ALLOWED%%", time_allowed)
        .replace("%%SECTION_A_BLOCK%%", "\n".join(section_a_lines))
        .replace("%%SECTION_B_BLOCK%%", "\n".join(section_b_lines))
    )

    return tex_content, image_assets


def compile_latex_to_pdf(
    tex_content: str,
    image_assets: Optional[Dict[str, bytes]] = None,
    output_dir: Optional[Union[str, Path]] = None,
    preferred_engine: str = "auto",
) -> Tuple[Optional[Path], str]:
    """
    Write `exam.tex` and any diagram images to `output_dir` (or a temporary folder)
    and compile to `exam.pdf` using `tectonic` or `pdflatex`.

    Returns (pdf_path_or_None, compiler_log).
    """
    build_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="exam_build_"))
    build_dir.mkdir(parents=True, exist_ok=True)

    tex_file = build_dir / "exam.tex"
    tex_file.write_text(tex_content, encoding="utf-8")

    if image_assets:
        for filename, data in image_assets.items():
            (build_dir / filename).write_bytes(data)

    compiler_info = find_latex_compiler(preferred=preferred_engine)
    if not compiler_info:
        msg = (
            "No local LaTeX compiler (`tectonic` or `pdflatex`) was detected on the system PATH.\n"
            "You can still inspect and download the complete `.tex` source and image bundle (.zip), "
            "or install Tectonic (`winget install Tectonic.Tectonic` / `scoop install tectonic`) or MiKTeX to enable live PDF compilation."
        )
        return None, msg

    engine_name, executable = compiler_info
    logs: List[str] = [f"Using LaTeX engine: {engine_name} ({executable})"]

    try:
        if engine_name == "tectonic":
            proc = subprocess.run(
                [executable, "exam.tex"],
                cwd=str(build_dir),
                capture_output=True,
                text=True,
                timeout=90,
            )
            logs.append(proc.stdout)
            if proc.stderr:
                logs.append(proc.stderr)
        else:
            # Run pdflatex twice so exam class \numpoints and \numpages resolve accurately
            for pass_num in (1, 2):
                proc = subprocess.run(
                    [executable, "-interaction=nonstopmode", "-halt-on-error", "exam.tex"],
                    cwd=str(build_dir),
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                logs.append(f"--- pdflatex pass {pass_num} ---")
                logs.append(proc.stdout)
                if proc.stderr:
                    logs.append(proc.stderr)
                if proc.returncode != 0:
                    break

        pdf_path = build_dir / "exam.pdf"
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            return pdf_path, "\n".join(logs)
        return None, "\n".join(logs)
    except Exception as exc:
        logs.append(f"Compilation exception: {exc}")
        return None, "\n".join(logs)


def create_exam_zip_bundle(
    tex_content: str,
    image_assets: Optional[Dict[str, bytes]] = None,
    pdf_bytes: Optional[bytes] = None,
    template_path: Union[str, Path] = DEFAULT_TEMPLATE_PATH,
) -> bytes:
    """
    Create an in-memory ZIP archive containing `exam.tex`, `template.tex`,
    any cropped diagram images, and `exam.pdf` (if compiled).
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("exam.tex", tex_content.encode("utf-8"))
        if Path(template_path).exists():
            zf.writestr("template.tex", Path(template_path).read_bytes())
        if image_assets:
            for name, img_bytes in image_assets.items():
                zf.writestr(name, img_bytes)
        if pdf_bytes:
            zf.writestr("exam.pdf", pdf_bytes)
    buffer.seek(0)
    return buffer.getvalue()
