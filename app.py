import base64
import copy
import os
import uuid
from typing import List

import streamlit as st

from compiler import (
    build_exam_latex,
    compile_latex_to_pdf,
    create_exam_zip_bundle,
    find_latex_compiler,
)
from extractor import crop_diagram_from_image, extract_questions_from_bytes


st.set_page_config(
    page_title="Tutor Assessment Assembly Tool",
    page_icon="📐",
    layout="wide",
    initial_sidebar_state="expanded",
)


SAMPLE_QUESTIONS = [
    {
        "id": "sample-1",
        "source_file": "sample_calculus_mcq.png",
        "section": "Section A",
        "marks": 1,
        "body": "Let $f(x) = x^3 - 6x^2 + 9x + 2$. At which values of $x$ does the graph of $y = f(x)$ have a horizontal tangent line?",
        "parts": [
            {"label": "(A)", "text": "$x = 1$ and $x = 3$", "marks": 0},
            {"label": "(B)", "text": "$x = 0$ and $x = 2$", "marks": 0},
            {"label": "(C)", "text": "$x = -1$ and $x = -3$", "marks": 0},
            {"label": "(D)", "text": "$x = 2$ only", "marks": 0},
        ],
        "needs_working_lines": False,
        "working_lines_height": "1.5in",
        "has_diagram": False,
        "tikz_code": None,
        "diagram_mode": "auto",
        "diagram_bbox": None,
        "source_image_bytes": None,
        "cropped_image_bytes": None,
    },
    {
        "id": "sample-2",
        "source_file": "sample_parabola_graph.png",
        "section": "Section B",
        "marks": 5,
        "body": "The diagram below shows the graph of the quadratic function $f(x) = -(x - 2)^2 + 4$ intersecting the $x$-axis at the origin $O$ and point $A$, with vertex $V(2, 4)$.",
        "parts": [
            {
                "label": "(a)",
                "text": "Find the coordinates of the $x$-intercept $A$.",
                "marks": 2,
            },
            {
                "label": "(b)",
                "text": "Calculate the area of the region bounded by the curve $y = f(x)$ and the $x$-axis for $0 \\le x \\le 4$.",
                "marks": 3,
            },
        ],
        "needs_working_lines": True,
        "working_lines_height": "2.0in",
        "has_diagram": True,
        "tikz_code": (
            "\\begin{tikzpicture}[scale=0.85]\n"
            "  \\begin{axis}[\n"
            "    axis lines=middle,\n"
            "    xlabel={$x$},\n"
            "    ylabel={$y$},\n"
            "    xmin=-1, xmax=5,\n"
            "    ymin=-1.5, ymax=5,\n"
            "    xtick={0,2,4},\n"
            "    ytick={0,4},\n"
            "    grid=both,\n"
            "    minor tick num=1,\n"
            "    grid style={line width=.1pt, draw=gray!20},\n"
            "    major grid style={line width=.2pt,draw=gray!40},\n"
            "    samples=100,\n"
            "    domain=-0.4:4.4,\n"
            "    width=8.5cm, height=6cm\n"
            "  ]\n"
            "    \\addplot[thick, blue!80!black, fill=blue!10, fill opacity=0.5, domain=0:4] {-(x-2)^2 + 4} \\closedcycle;\n"
            "    \\addplot[very thick, blue!80!black] {-(x-2)^2 + 4};\n"
            "    \\node[above right] at (axis cs:2,4) {$V(2,4)$};\n"
            "    \\node[above right] at (axis cs:4,0) {$A$};\n"
            "    \\fill (axis cs:2,4) circle[radius=2pt];\n"
            "    \\fill (axis cs:4,0) circle[radius=2pt];\n"
            "  \\end{axis}\n"
            "\\end{tikzpicture}"
        ),
        "diagram_mode": "auto",
        "diagram_bbox": None,
        "source_image_bytes": None,
        "cropped_image_bytes": None,
    },
]


def _init_session_state() -> None:
    if "questions" not in st.session_state:
        st.session_state.questions = []
    if "compiled_pdf_bytes" not in st.session_state:
        st.session_state.compiled_pdf_bytes = None
    if "compiled_tex" not in st.session_state:
        st.session_state.compiled_tex = ""
    if "compile_log" not in st.session_state:
        st.session_state.compile_log = ""
    if "zip_bundle_bytes" not in st.session_state:
        st.session_state.zip_bundle_bytes = None


def _move_question(index: int, direction: int) -> None:
    questions = st.session_state.questions
    target = index + direction
    if 0 <= target < len(questions):
        questions[index], questions[target] = questions[target], questions[index]


def _delete_question(index: int) -> None:
    if 0 <= index < len(st.session_state.questions):
        st.session_state.questions.pop(index)


def _add_blank_question(section: str = "Section B") -> None:
    st.session_state.questions.append(
        {
            "id": str(uuid.uuid4())[:8],
            "source_file": "manual_entry",
            "section": section,
            "marks": 2 if section == "Section B" else 1,
            "body": "Solve for $x$: $2x + 5 = 17$.",
            "parts": [],
            "needs_working_lines": section == "Section B",
            "working_lines_height": "2.0in",
            "has_diagram": False,
            "tikz_code": None,
            "diagram_mode": "auto",
            "diagram_bbox": None,
            "source_image_bytes": None,
            "cropped_image_bytes": None,
        }
    )


def _render_pdf_preview(pdf_bytes: bytes) -> None:
    """Display an inline PDF preview in Streamlit without Chrome blocking."""
    if hasattr(st, "pdf"):
        st.pdf(pdf_bytes, height=750)
    else:
        # Fallback for older Streamlit environments
        b64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
        st.markdown(
            f'<object data="data:application/pdf;base64,{b64_pdf}" type="application/pdf" width="100%" height="750px">'
            f'<p>Your browser could not embed the PDF directly. Please use the <b>📥 Download .PDF</b> button above to view it.</p>'
            f'</object>',
            unsafe_allow_html=True,
        )


def main() -> None:
    _init_session_state()

    st.title("📐 Tutor Assessment Assembly Tool")
    st.caption(
        "Upload raw screenshots, photos, or scanned PDFs of math questions, parse them with Gemini Vision into standardized `exam` class LaTeX, and compile a unified test paper."
    )

    # -------------------------------------------------------------------------
    # SIDEBAR: Configuration, Assessment Header & Interactive Question Order
    # -------------------------------------------------------------------------
    with st.sidebar:
        st.header("⚙️ Configuration")
        env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        api_key = st.text_input(
            "Gemini API Key",
            value=env_key,
            type="password",
            help="Required for extracting questions from images/PDFs via google-genai.",
        )
        model_options = [
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3.1-pro",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "Enter custom model...",
        ]
        selected_model = st.selectbox(
            "Vision Model",
            options=model_options,
            index=0,
            help="Select a Gemini vision model or type any custom model ID supported by your API key.",
        )
        if selected_model == "Enter custom model...":
            custom_input = st.text_input(
                "Custom Model Identifier",
                value="gemini-3.8-flash",
                help="Type any valid Gemini model ID (e.g. gemini-3.8-flash, gemini-3.7-flash, gemini-3.1-pro).",
            ).strip()
            # Normalize inputs like '3.8 flash' -> 'gemini-3.8-flash'
            clean_input = custom_input.replace(" ", "-").lower()
            if not clean_input.startswith("gemini-") and clean_input:
                model_name = f"gemini-{clean_input}"
            else:
                model_name = clean_input or "gemini-3.8-flash"
        else:
            model_name = selected_model


        compiler_found = find_latex_compiler()
        if compiler_found:
            st.success(f"LaTeX Engine: `{compiler_found[0]}` detected")
        else:
            st.warning(
                "No local `tectonic` or `pdflatex` found on PATH. `.tex` generation & ZIP export are active; install Tectonic or MiKTeX for live PDF compilation."
            )

        st.divider()
        st.header("📄 Exam Header Settings")
        subject = st.text_input("Subject", value="Mathematics — Methods")
        exam_title = st.text_input("Assessment Title", value="Unit 3 Topic Test")
        time_allowed = st.text_input("Time Allowed", value="45 Minutes")
        default_line_height = st.selectbox(
            "Default Working Lines Height",
            options=["1.5in", "2.0in", "2.5in", "3.0in", "4.0cm", "6.0cm"],
            index=1,
        )

        st.divider()
        st.header("🗂️ Question Order & Sections")
        total_q = len(st.session_state.questions)
        total_marks = sum(int(q.get("marks") or 0) for q in st.session_state.questions)
        sec_a_count = sum(
            1 for q in st.session_state.questions if q.get("section") == "Section A"
        )
        sec_b_count = total_q - sec_a_count

        st.markdown(
            f"**Total Questions:** `{total_q}` &nbsp;|&nbsp; **Full Marks:** `{total_marks}`\n\n"
            f"- **Section A (MCQ):** {sec_a_count}\n"
            f"- **Section B (Short Answer):** {sec_b_count}"
        )

        if st.session_state.questions:
            for idx, q in enumerate(st.session_state.questions):
                sec_badge = "🅰️ MCQ" if q.get("section") == "Section A" else "🅱️ Short"
                q_marks = int(q.get("marks") or 0)
                preview_snippet = (q.get("body") or "")[:34].replace("\n", " ")
                with st.container(border=True):
                    st.markdown(
                        f"**Q{idx + 1}** ({sec_badge} · `{q_marks}m`)  \n"
                        f"<small>{preview_snippet}...</small>",
                        unsafe_allow_html=True,
                    )
                    b_col1, b_col2, b_col3, b_col4 = st.columns(4)
                    with b_col1:
                        st.button(
                            "↑",
                            key=f"side_up_{q['id']}_{idx}",
                            disabled=(idx == 0),
                            help="Move question up",
                            on_click=_move_question,
                            args=(idx, -1),
                        )
                    with b_col2:
                        st.button(
                            "↓",
                            key=f"side_dn_{q['id']}_{idx}",
                            disabled=(idx == len(st.session_state.questions) - 1),
                            help="Move question down",
                            on_click=_move_question,
                            args=(idx, 1),
                        )
                    with b_col3:
                        if st.button(
                            "⇄",
                            key=f"side_sec_{q['id']}_{idx}",
                            help="Toggle Section A / Section B",
                        ):
                            q["section"] = (
                                "Section B"
                                if q.get("section") == "Section A"
                                else "Section A"
                            )
                            q["needs_working_lines"] = q["section"] == "Section B"
                            st.rerun()
                    with b_col4:
                        st.button(
                            "🗑️",
                            key=f"side_del_{q['id']}_{idx}",
                            help="Remove question",
                            on_click=_delete_question,
                            args=(idx,),
                        )

        add_col1, add_col2 = st.columns(2)
        with add_col1:
            if st.button("➕ Blank Q", use_container_width=True):
                _add_blank_question("Section B")
                st.rerun()
        with add_col2:
            if st.button("🧪 Load Demo", use_container_width=True):
                st.session_state.questions = copy.deepcopy(SAMPLE_QUESTIONS)
                st.rerun()

    # -------------------------------------------------------------------------
    # STEP 1: Multi-File Uploader & Multimodal Extraction
    # -------------------------------------------------------------------------
    st.subheader("1. Upload Question Sources")
    up_col1, up_col2 = st.columns([3, 1])
    with up_col1:
        uploaded_files = st.file_uploader(
            "Upload screenshots, photos, or single-page PDFs of math questions",
            type=["png", "jpg", "jpeg", "pdf"],
            accept_multiple_files=True,
        )
    with up_col2:
        st.write("")
        st.write("")
        extract_clicked = st.button(
            "✨ Extract Questions",
            type="primary",
            use_container_width=True,
            disabled=not uploaded_files,
        )

    if extract_clicked and uploaded_files:
        if not api_key:
            st.error("Please enter your Gemini API Key in the sidebar before extracting.")
        else:
            progress = st.progress(0, text="Parsing uploaded files with Gemini Vision...")
            extracted_total = 0
            import time
            for f_idx, uploaded_file in enumerate(uploaded_files):
                if f_idx > 0:
                    time.sleep(2.0)  # Gentle delay to stay within free-tier requests-per-minute limits
                progress.progress(
                    (f_idx) / len(uploaded_files),
                    text=f"Extracting from `{uploaded_file.name}` ({f_idx + 1}/{len(uploaded_files)})...",
                )
                try:
                    raw_bytes = uploaded_file.getvalue()
                    extracted_list = extract_questions_from_bytes(
                        file_bytes=raw_bytes,
                        filename=uploaded_file.name,
                        api_key=api_key,
                        model_name=model_name,
                    )
                    for item in extracted_list:
                        cropped_bytes = None
                        if item.get("has_diagram"):
                            cropped_bytes = crop_diagram_from_image(
                                raw_bytes, item.get("diagram_bbox")
                            )
                        part_marks_sum = sum(
                            int(p.get("marks") or 0) for p in (item.get("parts") or [])
                        )
                        inferred_marks = (
                            part_marks_sum
                            if part_marks_sum > 0
                            else int(item.get("marks") or 1)
                        )
                        st.session_state.questions.append(
                            {
                                "id": str(uuid.uuid4())[:8],
                                "source_file": uploaded_file.name,
                                "section": item.get("section", "Section B"),
                                "marks": inferred_marks,
                                "body": item.get("body", ""),
                                "parts": item.get("parts", []),
                                "needs_working_lines": bool(
                                    item.get(
                                        "needs_working_lines",
                                        item.get("section") == "Section B",
                                    )
                                ),
                                "working_lines_height": default_line_height,
                                "has_diagram": bool(item.get("has_diagram", False)),
                                "tikz_code": item.get("tikz_code"),
                                "diagram_mode": "auto",
                                "diagram_bbox": item.get("diagram_bbox"),
                                "source_image_bytes": raw_bytes
                                if not uploaded_file.name.lower().endswith(".pdf")
                                else None,
                                "cropped_image_bytes": cropped_bytes,
                            }
                        )
                        extracted_total += 1
                except Exception as exc:
                    st.error(f"Error extracting `{uploaded_file.name}`: {exc}")

            progress.progress(1.0, text="Extraction complete!")
            if extracted_total > 0:
                st.success(f"Successfully extracted {extracted_total} question(s)!")
                st.rerun()

    st.divider()

    # -------------------------------------------------------------------------
    # STEP 2: Interactive Question Editor & Step 3: Live Exam Compilation
    # -------------------------------------------------------------------------
    editor_col, preview_col = st.columns([1.15, 1], gap="large")

    with editor_col:
        st.subheader("2. Review, Edit & Format Questions")
        if not st.session_state.questions:
            st.info(
                "No questions in the exam yet. Upload files above and click **Extract Questions**, or click **🧪 Load Demo** in the sidebar to inspect a sample assessment."
            )
        else:
            for idx, q in enumerate(st.session_state.questions):
                qid = q["id"]
                sec_label = (
                    "Section A — Multiple Choice"
                    if q.get("section") == "Section A"
                    else "Section B — Short Answer"
                )
                header_title = (
                    f"Question {idx + 1}  |  {sec_label}  |  [{q.get('marks', 1)} marks]  "
                    f"({q.get('source_file', 'upload')})"
                )

                with st.expander(header_title, expanded=(idx < 2)):
                    top_c1, top_c2, top_c3, top_c4 = st.columns([2.2, 1.2, 1.3, 1.5])
                    with top_c1:
                        new_sec = st.selectbox(
                            "Section",
                            options=["Section A", "Section B"],
                            index=0 if q.get("section") == "Section A" else 1,
                            format_func=lambda s: (
                                "Section A (Multiple Choice)"
                                if s == "Section A"
                                else "Section B (Short Answer)"
                            ),
                            key=f"sec_{qid}_{idx}",
                        )
                        q["section"] = new_sec

                    with top_c2:
                        part_sum = sum(
                            int(p.get("marks") or 0) for p in (q.get("parts") or [])
                        )
                        if part_sum > 0:
                            q["marks"] = part_sum
                            st.number_input(
                                "Total Marks (Σ parts)",
                                value=part_sum,
                                disabled=True,
                                key=f"marks_ro_{qid}_{idx}",
                            )
                        else:
                            q["marks"] = st.number_input(
                                "Marks",
                                min_value=1,
                                max_value=100,
                                value=int(q.get("marks") or 1),
                                step=1,
                                key=f"marks_{qid}_{idx}",
                            )

                    with top_c3:
                        q["needs_working_lines"] = st.checkbox(
                            "Working Lines",
                            value=bool(q.get("needs_working_lines", False)),
                            help="Insert \\fillwithlines{...} ruled space",
                            key=f"wl_chk_{qid}_{idx}",
                        )

                    with top_c4:
                        q["working_lines_height"] = st.text_input(
                            "\\fillwithlines{...}",
                            value=q.get("working_lines_height") or default_line_height,
                            disabled=not q["needs_working_lines"],
                            help="LaTeX dimension such as 2.0in, 4cm, 1.5in",
                            key=f"wl_ht_{qid}_{idx}",
                        )

                    q["body"] = st.text_area(
                        "Question Stem (LaTeX)",
                        value=q.get("body", ""),
                        height=100,
                        key=f"body_{qid}_{idx}",
                    )

                    # Subparts Editor (\begin{parts} \part[marks] ... \end{parts})
                    st.markdown("**Subparts (`\\begin{parts} \\part[marks] ... \\end{parts}`)**")
                    parts: List[dict] = q.get("parts") or []
                    parts_to_remove: List[int] = []
                    for p_idx, part in enumerate(parts):
                        p_col1, p_col2, p_col3, p_col4 = st.columns([1.0, 4.2, 1.2, 0.6])
                        with p_col1:
                            part["label"] = st.text_input(
                                "Label",
                                value=part.get("label", f"({chr(97 + p_idx)})"),
                                key=f"plbl_{qid}_{idx}_{p_idx}",
                                label_visibility="collapsed",
                            )
                        with p_col2:
                            part["text"] = st.text_input(
                                "Subpart LaTeX",
                                value=part.get("text", ""),
                                key=f"ptxt_{qid}_{idx}_{p_idx}",
                                label_visibility="collapsed",
                            )
                        with p_col3:
                            part["marks"] = st.number_input(
                                "Marks",
                                min_value=0,
                                max_value=50,
                                value=int(part.get("marks") or 0),
                                key=f"pmk_{qid}_{idx}_{p_idx}",
                                label_visibility="collapsed",
                                help="Part marks (0 for ungraded MCQ choices)",
                            )
                        with p_col4:
                            if st.button("✖", key=f"pdel_{qid}_{idx}_{p_idx}", help="Remove subpart"):
                                parts_to_remove.append(p_idx)

                    if parts_to_remove:
                        for rem_i in sorted(parts_to_remove, reverse=True):
                            parts.pop(rem_i)
                        st.rerun()

                    if st.button(f"➕ Add Subpart to Q{idx + 1}", key=f"padd_{qid}_{idx}"):
                        next_letter = chr(97 + len(parts))
                        parts.append(
                            {
                                "label": f"({next_letter})",
                                "text": "Show that ...",
                                "marks": 2 if q["section"] == "Section B" else 0,
                            }
                        )
                        q["parts"] = parts
                        st.rerun()

                    # Diagram & TikZ Controls
                    st.markdown("---")
                    q["has_diagram"] = st.checkbox(
                        "Includes Coordinate Axes / Geometry Diagram",
                        value=bool(q.get("has_diagram", False)),
                        key=f"diag_chk_{qid}_{idx}",
                    )
                    if q["has_diagram"]:
                        d_col1, d_col2 = st.columns([1.5, 2.5])
                        with d_col1:
                            q["diagram_mode"] = st.radio(
                                "Diagram Rendering Mode",
                                options=["auto", "tikz", "image"],
                                index=["auto", "tikz", "image"].index(
                                    q.get("diagram_mode", "auto")
                                ),
                                format_func=lambda m: {
                                    "auto": "Auto (Favor TikZ, fallback to Cropped Image)",
                                    "tikz": "Force TikZ / pgfplots Code",
                                    "image": "Force Cropped Source Image",
                                }[m],
                                key=f"dmode_{qid}_{idx}",
                            )
                            if q.get("cropped_image_bytes"):
                                st.image(
                                    q["cropped_image_bytes"],
                                    caption="Cropped Diagram Fallback",
                                    use_container_width=True,
                                )
                        with d_col2:
                            q["tikz_code"] = st.text_area(
                                "TikZ / pgfplots Markup (`\\begin{tikzpicture}...\\end{tikzpicture}`)",
                                value=q.get("tikz_code") or "",
                                height=180,
                                key=f"tikz_{qid}_{idx}",
                            )

    with preview_col:
        st.subheader("3. Compile & Export Assessment")

        # Always keep live LaTeX synchronized with current state
        live_tex, live_assets = build_exam_latex(
            questions=st.session_state.questions,
            subject=subject,
            exam_title=exam_title,
            time_allowed=time_allowed,
        )
        st.session_state.compiled_tex = live_tex

        action_c1, action_c2, action_c3 = st.columns([1.4, 1.3, 1.3])
        with action_c1:
            compile_clicked = st.button(
                "🚀 Compile Exam PDF",
                type="primary",
                use_container_width=True,
                disabled=not st.session_state.questions,
            )

        if compile_clicked:
            with st.spinner("Compiling LaTeX with exam document class..."):
                pdf_path, log_output = compile_latex_to_pdf(
                    tex_content=live_tex,
                    image_assets=live_assets,
                )
                st.session_state.compile_log = log_output
                if pdf_path and pdf_path.exists():
                    st.session_state.compiled_pdf_bytes = pdf_path.read_bytes()
                    st.toast("Exam PDF compiled successfully!", icon="✅")
                else:
                    st.session_state.compiled_pdf_bytes = None

        st.session_state.zip_bundle_bytes = create_exam_zip_bundle(
            tex_content=live_tex,
            image_assets=live_assets,
            pdf_bytes=st.session_state.compiled_pdf_bytes,
        )

        with action_c2:
            st.download_button(
                label="📥 Download .PDF",
                data=st.session_state.compiled_pdf_bytes or b"",
                file_name=f"{exam_title.lower().replace(' ', '_')}.pdf",
                mime="application/pdf",
                disabled=st.session_state.compiled_pdf_bytes is None,
                use_container_width=True,
            )
        with action_c3:
            st.download_button(
                label="📦 Download .TEX + Assets (.zip)",
                data=st.session_state.zip_bundle_bytes,
                file_name=f"{exam_title.lower().replace(' ', '_')}_latex_bundle.zip",
                mime="application/zip",
                use_container_width=True,
            )

        tab_pdf, tab_tex, tab_log = st.tabs(
            ["📄 Live PDF Preview", "📝 Generated LaTeX (`exam.tex`)", "🖥️ Compiler Log"]
        )

        with tab_pdf:
            if st.session_state.compiled_pdf_bytes:
                _render_pdf_preview(st.session_state.compiled_pdf_bytes)
            else:
                st.info(
                    "Click **🚀 Compile Exam PDF** above to render the live PDF preview. "
                    "You can also inspect or download the generated `exam` class LaTeX source in the **📝 Generated LaTeX** tab at any time."
                )

        with tab_tex:
            st.code(st.session_state.compiled_tex, language="latex", line_numbers=True)

        with tab_log:
            if st.session_state.compile_log:
                st.code(st.session_state.compile_log, language="text")
            else:
                st.caption("No compilation run yet in this session.")


if __name__ == "__main__":
    main()
